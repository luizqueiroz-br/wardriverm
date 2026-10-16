"""Integração com os dados reais da pasta de wardriving do usuário.

Este teste só roda quando a pasta existe (C:/Users/<usuário>/OneDrive/Área de
Trabalho/ward/send ou o caminho em WARDRIVE_DIR). Ele confere que a importação
reproduz exatamente os números medidos nos arquivos, que rodar o scan duas vezes
não duplica nada e que os lotes exportados têm tantas linhas quantas o filtro diz.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import config, db, exporter, importer, parser, queries  # noqa: E402

PADRAO = Path.home() / "OneDrive" / "Área de Trabalho" / "ward" / "send"
PASTA = Path(os.environ.get("WARDRIVE_DIR", PADRAO))


def _contagem_referencia(pasta: Path) -> dict:
    """Lê os arquivos de forma independente do sistema, para conferência."""
    total = 0
    arquivos = 0
    macs: set[str] = set()
    vazios = 0
    for arquivo in sorted(pasta.rglob("*")):
        if not arquivo.is_file():
            continue
        arquivos += 1
        dados = parser.decode_bytes(arquivo.read_bytes())
        linhas = dados.splitlines()[2:]
        linhas = [l for l in linhas if l.strip()]
        if not linhas:
            vazios += 1
            continue
        for linha in linhas:
            campos = linha.split(",")
            total += 1
            macs.add(campos[0].strip().upper())
    return {"arquivos": arquivos, "linhas": total, "redes": len(macs), "vazios": vazios}


@unittest.skipUnless(PASTA.is_dir(), f"pasta de wardriving não encontrada em {PASTA}")
class WardrivingRealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("real")
        isolar_dados(Path(cls.tmp.name))
        cls.referencia = _contagem_referencia(PASTA)
        cls.conn = db.connect()
        db.init_db(cls.conn)
        cls.primeira = importer.scan_and_import(cls.conn, [str(PASTA)])

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_importacao_bate_com_a_contagem_independente(self):
        arquivos = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        linhas = self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        self.assertEqual(arquivos, self.referencia["arquivos"])
        self.assertEqual(linhas, self.referencia["linhas"])
        self.assertEqual(self.primeira["novos"] + self.primeira["vazios"],
                         self.referencia["arquivos"])
        self.assertEqual(self.referencia["vazios"] > 0, True,
                         "esperava sessões sem nenhuma linha nos arquivos do usuário")

    def test_redes_unicas(self):
        total = self.conn.execute("SELECT COUNT(*) FROM networks").fetchone()[0]
        self.assertEqual(total, self.referencia["redes"])

    def test_nenhuma_linha_foi_perdida_ou_inventada(self):
        por_arquivo = {
            r["name"]: r for r in self.conn.execute(
                "SELECT name, obs_count, bad_count, no_gps_count FROM files").fetchall()
        }
        for arquivo in sorted(PASTA.rglob("*")):
            if not arquivo.is_file():
                continue
            parsed = parser.parse_file(arquivo)
            linha = por_arquivo[arquivo.name]
            self.assertEqual(linha["obs_count"], parsed["stats"]["obs_count"], arquivo.name)
            self.assertEqual(linha["bad_count"], parsed["stats"]["bad_count"], arquivo.name)
            self.assertEqual(linha["no_gps_count"], parsed["stats"]["no_gps_count"], arquivo.name)

    def test_segunda_varredura_nao_duplica(self):
        antes = self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        segunda = importer.scan_and_import(self.conn, [str(PASTA)])
        depois = self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        self.assertEqual(antes, depois)
        self.assertEqual(segunda["novos"], 0)

    def test_exportacao_tem_as_linhas_esperadas(self):
        settings = config.load_settings()
        selecao = {"mode": "all", "types": ["WIFI", "BLE", "BT"], "with_gps_only": True,
                   "dedupe": "none"}
        esperado = self.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE has_gps = 1").fetchone()[0]
        criado = exporter.create_export(self.conn, target="wigle", selection=selecao,
                                        settings=settings, label="real-tudo")
        self.assertEqual(criado["rows"], esperado)
        soma = 0
        for chunk in criado["manifest"]["chunks"]:
            arquivo = Path(criado["dir"]) / chunk["file"]
            lido = parser.parse_bytes(arquivo.read_bytes(), arquivo.name)
            self.assertEqual(lido["stats"]["obs_count"], chunk["rows"])
            self.assertEqual(lido["stats"]["bad_count"], 0)
            self.assertTrue(arquivo.read_bytes().startswith(b"WigleWifi-1.6,"))
            self.assertLessEqual(arquivo.stat().st_size, config.TARGETS["wigle"]["max_bytes"])
            soma += lido["stats"]["obs_count"]
        self.assertEqual(soma, esperado)

    def test_exportacao_dedgwars_tambem_sob_o_limite(self):
        settings = config.load_settings()
        selecao = {"mode": "all", "types": ["WIFI", "BLE", "BT"], "with_gps_only": True,
                   "dedupe": "exact"}
        criado = exporter.create_export(self.conn, target="wdgwars", selection=selecao,
                                        settings=settings, label="real-wdg")
        for chunk in criado["manifest"]["chunks"]:
            self.assertLessEqual(chunk["bytes"], config.TARGETS["wdgwars"]["max_bytes"])
            self.assertLessEqual(chunk["rows"], settings["export"]["wdgwars_rows_per_chunk"])

    def test_visao_geral_e_consultas(self):
        resumo = queries.overview(self.conn)
        self.assertEqual(resumo["observacoes"], self.referencia["linhas"])
        self.assertEqual(resumo["redes"], self.referencia["redes"])
        self.assertGreater(resumo["wifi"], 0)
        self.assertGreater(resumo["ble"], 0)
        redes, total = queries.networks(self.conn, search="", limit=10, offset=0)
        self.assertEqual(total, self.referencia["redes"])
        self.assertEqual(len(redes), 10)
        pontos = queries.map_points(self.conn, types=["WIFI"])
        self.assertGreater(len(pontos), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
