"""Fluxo completo de upload contra o servidor de mentira (WiGLE + WDGoWars).

Cobre: validação de credenciais, envio com 429 (fila do WDGoWars), 409 (arquivo
repetido), 413 e 524 (lote grande demais → divisão automática), marcação do
modo incremental e a garantia de que nenhum lote passa dos limites do destino.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import (  # noqa: E402
    ESTADO, ServidorFalso, csv_wigle, isolar_dados, limpar_dados, pasta_temporaria,
)
from wardrive import config, db, exporter, importer, upload_service, uploaders  # noqa: E402


class FluxoUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("upload")
        isolar_dados(Path(cls.tmp.name))
        cls.servidor = ServidorFalso()
        config.save_secrets({
            "wigle_api_name": "usuario_teste",
            "wigle_api_token": "token_de_teste",
            "wdgwars_api_key": "b" * 64,
        })
        config.save_settings({
            "export": {"rows_per_chunk": 10000, "wdgwars_rows_per_chunk": 10000},
            "upload": {"wigle_base": cls.servidor.base, "wdgwars_base": cls.servidor.base,
                       "timeout": 20, "dry_run_default": True},
            "ward_dirs": [],
            "auto_import_on_start": False,
        })

    @classmethod
    def tearDownClass(cls):
        cls.servidor.parar()
        cls.tmp.cleanup()

    def setUp(self):
        ESTADO.reiniciar()
        self.conn = db.connect()
        db.init_db(self.conn)
        limpar_dados(self.conn)
        config.save_secrets({"wigle_api_name": "usuario_teste",
                             "wigle_api_token": "token_de_teste",
                             "wdgwars_api_key": "b" * 64})

    def tearDown(self):
        self.conn.close()

    # ------------------------------------------------------------------ apoio
    def _importar(self, linhas: int = 10, inicio: int = 0):
        return importer.import_bytes(self.conn, f"teste_{inicio}_{linhas}.log",
                                     csv_wigle(linhas, inicio=inicio), source="teste")

    def _exportar(self, target: str, selection: dict):
        settings = config.load_settings()
        return exporter.create_export(self.conn, target=target, selection=selection,
                                      settings=settings, label="teste")

    # ------------------------------------------------------------------ testes
    def test_validacao_de_credenciais(self):
        wigle = uploaders.wigle_validate("usuario_teste", "token_de_teste", base=self.servidor.base)
        self.assertTrue(wigle["ok"], wigle)
        self.assertEqual(wigle["last_upload"]["transid"], "20261014-00001")

        wdg = uploaders.wdgwars_validate("b" * 64, base=self.servidor.base)
        self.assertTrue(wdg["ok"], wdg)
        self.assertEqual(wdg["stats"]["username"], "tester")

        ruim = uploaders.wigle_validate("", "", base=self.servidor.base)
        self.assertFalse(ruim["ok"])

    def test_export_respeita_formato_e_limites(self):
        self._importar(25)
        selection = {"mode": "all", "types": ["WIFI"], "with_gps_only": True, "dedupe": "none"}
        criado = self._exportar("wigle", selection)
        self.assertEqual(criado["rows"], 25)
        self.assertEqual(criado["chunks"], 1)

        lote = Path(criado["dir"])
        arquivo = lote / criado["manifest"]["chunks"][0]["file"]
        conteudo = arquivo.read_bytes()
        self.assertTrue(conteudo.startswith(b"WigleWifi-1.6,"))
        self.assertLessEqual(len(conteudo), config.TARGETS["wigle"]["max_bytes"])
        self.assertTrue((lote / "manifest.json").exists())
        self.assertTrue((lote / "upload.sh").exists())
        self.assertTrue((lote / "upload.ps1").exists())
        # a parte é relida pelo próprio leitor: nada de linha perdida
        self.assertEqual(exporter.fetch_rows(self.conn, selection).__len__(), 25)

    def test_envio_wigle_com_retentativa_429(self):
        self._importar(12)
        selection = {"mode": "new", "types": ["WIFI"], "with_gps_only": True, "dedupe": "none"}
        ESTADO.wigle_429_restante = 1
        settings = config.load_settings()
        resultado = upload_service.export_and_upload(
            self.conn, target="wigle", selection=selection, settings=settings,
            label="wigle-429", dry_run=False)
        self.assertEqual(resultado["upload"]["ok"], 1, resultado)
        self.assertEqual(resultado["upload"]["erros"], 0)
        envio = self.conn.execute("SELECT * FROM uploads ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(envio["status"], "ok")
        self.assertTrue(envio["target_ref"].startswith("2026"))
        self.assertEqual(envio["rows"], 12)

    def test_modo_incremental_nao_reenvia(self):
        self._importar(8, inicio=100)
        settings = config.load_settings()
        selecao = {"mode": "new", "types": ["WIFI"], "with_gps_only": True, "dedupe": "none"}

        primeira = upload_service.export_and_upload(self.conn, target="wigle", selection=selecao,
                                                    settings=settings, label="inc-1", dry_run=False)
        self.assertEqual(primeira["export"]["rows"], 8)
        self.assertEqual(primeira["upload"]["ok"], 1)

        segunda = upload_service.export_and_upload(self.conn, target="wigle", selection=selecao,
                                                   settings=settings, label="inc-2", dry_run=False)
        self.assertEqual(segunda["export"]["rows"], 0)
        self.assertEqual(segunda["message"], "nenhuma linha corresponde à seleção — nada foi enviado")

        # o WDGoWars ainda não recebeu nada desse lote: o incremental é por destino
        terceira = upload_service.export_and_upload(self.conn, target="wdgwars", selection=selecao,
                                                    settings=settings, label="inc-3", dry_run=False)
        self.assertEqual(terceira["export"]["rows"], 8)
        self.assertEqual(terceira["upload"]["ok"], 1)
        marcadas = self.conn.execute(
            "SELECT target, COUNT(*) c FROM obs_uploads GROUP BY target").fetchall()
        self.assertEqual({r["target"]: r["c"] for r in marcadas}, {"wigle": 8, "wdgwars": 8})

    def test_envio_wdgwars_com_arquivo_repetido(self):
        self._importar(6, inicio=200)
        settings = config.load_settings()
        selecao = {"mode": "all", "types": ["WIFI"], "with_gps_only": True, "dedupe": "none",
                   "date_from": "", "date_to": ""}
        criado = self._exportar("wdgwars", selecao)
        primeiro = upload_service.upload_export(self.conn, criado["export_id"], "wdgwars",
                                                settings, dry_run=False)
        self.assertEqual(primeiro["ok"], 1, primeiro)
        # o mesmo arquivo, byte a byte, de novo → o servidor responde 409 duplicate_upload
        segundo = upload_service.upload_export(self.conn, criado["export_id"], "wdgwars",
                                               settings, dry_run=False)
        self.assertEqual(segundo["duplicados"], 1, segundo)
        self.assertEqual(segundo["erros"], 0)
        status = [r["status"] for r in self.conn.execute(
            "SELECT status FROM uploads ORDER BY id").fetchall()]
        self.assertIn("duplicado", status)

    def test_lote_grande_e_dividido_automaticamente(self):
        """O servidor falso responde 524 acima de 20 linhas: o cliente divide em metades."""
        self._importar(80, inicio=300)
        ESTADO.wdg_limite_linhas = 20
        settings = config.load_settings()
        selecao = {"mode": "all", "types": ["WIFI"], "with_gps_only": True, "dedupe": "none"}
        resultado = upload_service.export_and_upload(
            self.conn, target="wdgwars", selection=selecao, settings=settings,
            label="grande", dry_run=False)

        self.assertEqual(resultado["export"]["rows"], 80)
        envios = self.conn.execute(
            "SELECT filename, status, rows FROM uploads WHERE dry_run = 0 ORDER BY id").fetchall()
        status = [e["status"] for e in envios]
        self.assertIn("timeout", status)              # o primeiro POST bateu no 524
        self.assertGreaterEqual(status.count("ok"), 4)  # e as partes subiram divididas
        self.assertEqual(sum(e["rows"] for e in envios if e["status"] == "ok"), 80)
        # nada ficou pendente para o próximo envio incremental
        restantes = self.conn.execute(
            """SELECT COUNT(*) FROM observations o WHERE o.has_gps = 1 AND NOT EXISTS (
                   SELECT 1 FROM obs_uploads u WHERE u.obs_id = o.id AND u.target = 'wdgwars')"""
        ).fetchone()[0]
        self.assertEqual(restantes, 0)

    def test_lote_413_do_wigle_tambem_e_dividido(self):
        self._importar(40, inicio=400)
        ESTADO.wigle_limite_linhas = 10
        settings = config.load_settings()
        selecao = {"mode": "all", "types": ["WIFI"], "with_gps_only": True, "dedupe": "none"}
        resultado = upload_service.export_and_upload(
            self.conn, target="wigle", selection=selecao, settings=settings,
            label="grande-wigle", dry_run=False)
        linhas = self.conn.execute(
            "SELECT status, rows FROM uploads WHERE dry_run = 0").fetchall()
        status = [r["status"] for r in linhas]
        self.assertEqual(status.count("grande"), 3)      # 40 seguido de 20+20, todos grandes
        self.assertEqual(status.count("ok"), 4)          # 20 → 10+10 em cada metade
        self.assertEqual(sum(r["rows"] for r in linhas if r["status"] == "ok"), 40)

    def test_duplicatas_descartadas_tambem_saem_da_fila(self):
        """O dedupe tira linhas repetidas do CSV, mas elas precisam sair da fila
        também — senão cada rodada incremental reenviaria um resto pequeno."""
        dados = csv_wigle(10, inicio=700)
        importer.import_bytes(self.conn, "original.log", dados, source="teste")
        importer.import_bytes(self.conn, "copia.log", dados, source="teste")   # mesmo conteúdo, outro nome
        total = self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        self.assertEqual(total, 20)

        settings = config.load_settings()
        selecao = {"mode": "new", "types": ["WIFI"], "with_gps_only": True, "dedupe": "exact"}
        primeira = upload_service.export_and_upload(self.conn, target="wdgwars", selection=selecao,
                                                    settings=settings, label="dup-1", dry_run=False)
        self.assertEqual(primeira["export"]["rows"], 10)     # metade é duplicata exata
        self.assertEqual(primeira["upload"]["ok"], 1)

        marcadas = self.conn.execute(
            "SELECT COUNT(*) FROM obs_uploads WHERE target = 'wdgwars'").fetchone()[0]
        self.assertEqual(marcadas, 20, "as 20 observações (incluindo as duplicatas) devem sair da fila")

        segunda = upload_service.export_and_upload(self.conn, target="wdgwars", selection=selecao,
                                                   settings=settings, label="dup-2", dry_run=False)
        self.assertEqual(segunda["export"]["rows"], 0)
        self.assertIsNone(segunda["upload"])

    def test_simulacao_nao_envia_nada(self):
        self._importar(5, inicio=500)
        settings = config.load_settings()
        selecao = {"mode": "all", "types": ["WIFI"], "with_gps_only": True, "dedupe": "none"}
        resultado = upload_service.export_and_upload(
            self.conn, target="wigle", selection=selecao, settings=settings,
            label="simulacao", dry_run=True)
        self.assertEqual(resultado["upload"]["simulados"], 1)
        self.assertEqual(ESTADO.recebidos, [])
        pendentes = self.conn.execute(
            "SELECT COUNT(*) FROM obs_uploads").fetchone()[0]
        self.assertEqual(pendentes, 0)

    def test_chave_ausente_gera_erro_claro(self):
        self._importar(3, inicio=600)
        config.save_secrets({"wigle_api_token": ""})
        settings = config.load_settings()
        selecao = {"mode": "new", "types": ["WIFI"], "with_gps_only": True, "dedupe": "none"}
        resultado = upload_service.export_and_upload(
            self.conn, target="wigle", selection=selecao, settings=settings,
            label="sem-chave", dry_run=False)
        self.assertEqual(resultado["upload"]["erros"], 1)
        mensagem = resultado["upload"]["resultados"][0]["message"]
        self.assertTrue("401" in mensagem or "auth" in mensagem.lower(), mensagem)
        # repõe a credencial para os demais testes
        config.save_secrets({"wigle_api_token": "token_de_teste"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
