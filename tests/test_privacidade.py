"""Testes das áreas privadas (issue #3): nada de casa/trabalho no CSV publicado."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import config, db, exporter, geo, privacidade  # noqa: E402

CENTRO = (-23.2800, -47.2900)
# 0,00045° de latitude ≈ 50 m; 0,0027° ≈ 300 m
PERTO = (CENTRO[0] + 0.00045, CENTRO[1])
LONGE = (CENTRO[0] + 0.0027, CENTRO[1])


def _obs(mac, lat, lon, gps=1):
    return {"id": 0, "file_id": 1, "mac": mac, "ssid": "Rede", "auth_mode": "[WPA2_PSK]",
            "first_seen": "2026-10-14 12:00:00", "channel": 6, "frequency": 2437,
            "rssi": -60, "lat": lat, "lon": lon, "altitude": 600, "accuracy": 5,
            "rcois": "", "mfgr_id": "", "type": "WIFI", "has_gps": gps}


class GeoTests(unittest.TestCase):
    def test_distancias_conhecidas(self):
        # mesmo ponto
        self.assertEqual(geo.distancia_m(*CENTRO, *CENTRO), 0)
        # 0,00045° de latitude ≈ 50 m
        self.assertAlmostEqual(geo.distancia_m(*CENTRO, *PERTO), 50, delta=2)
        self.assertAlmostEqual(geo.distancia_m(*CENTRO, *LONGE), 300, delta=4)

    def test_caixa_envolve_o_circulo(self):
        lat0, lat1, lon0, lon1 = geo.caixa_do_raio(*CENTRO, 200)
        self.assertLess(lat0, CENTRO[0])
        self.assertGreater(lat1, CENTRO[0])
        # longitude alarga mais que latitude (cosseno da latitude)
        self.assertGreater(abs(lon1 - lon0), abs(lat1 - lat0))


class AplicarTests(unittest.TestCase):
    def setUp(self):
        self.areas = [{"id": 1, "lat": CENTRO[0], "lon": CENTRO[1], "raio_m": 200,
                       "rotulo": "casa", "modo": "excluir"}]

    def test_linha_dentro_do_raio_sai(self):
        rows = [_obs("AA:AA:AA:00:00:01", *PERTO), _obs("AA:AA:AA:00:00:02", *LONGE)]
        filtradas, resumo = privacidade.aplicar(rows, self.areas)
        self.assertEqual([r["mac"] for r in filtradas], ["AA:AA:AA:00:00:02"])
        self.assertEqual(resumo["omitidas"], 1)
        self.assertEqual(resumo["por_area"], {"casa": 1})

    def test_linha_fora_do_raio_fica(self):
        rows = [_obs("AA:AA:AA:00:00:02", *LONGE)]
        filtradas, resumo = privacidade.aplicar(rows, self.areas)
        self.assertEqual(len(filtradas), 1)
        self.assertEqual(resumo["omitidas"], 0)

    def test_modo_zerar_mantem_a_linha_sem_gps(self):
        areas = [dict(self.areas[0], modo="zerar")]
        filtradas, resumo = privacidade.aplicar([_obs("AA:AA:AA:00:00:01", *PERTO)], areas)
        self.assertEqual(len(filtradas), 1)
        self.assertIsNone(filtradas[0]["lat"])
        self.assertIsNone(filtradas[0]["lon"])
        self.assertEqual(resumo["zeradas"], 1)
        self.assertEqual(resumo["omitidas"], 0)

    def test_sem_area_nada_muda(self):
        rows = [_obs("AA:AA:AA:00:00:01", *PERTO)]
        filtradas, resumo = privacidade.aplicar(rows, [])
        self.assertEqual(filtradas, rows)
        self.assertEqual(resumo["omitidas"], 0)

    def test_linha_sem_gps_nao_e_afetada(self):
        rows = [_obs("AA:AA:AA:00:00:03", None, None, gps=0)]
        filtradas, resumo = privacidade.aplicar(rows, self.areas)
        self.assertEqual(len(filtradas), 1)
        self.assertEqual(resumo["omitidas"], 0)


class SalvarAreasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pasta_temporaria("privacidade")
        isolar_dados(Path(self.tmp.name))
        self.conn = db.connect()
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_crud(self):
        area_id = privacidade.salvar(self.conn, lat=CENTRO[0], lon=CENTRO[1], raio_m=200,
                                     rotulo="casa")
        self.assertEqual(privacidade.listar(self.conn)[0]["rotulo"], "casa")
        privacidade.remover(self.conn, area_id)
        self.assertEqual(privacidade.listar(self.conn), [])

    def test_recusa_coordenada_e_raio_invalidos(self):
        with self.assertRaises(ValueError):
            privacidade.salvar(self.conn, lat=123, lon=0, raio_m=200)
        with self.assertRaises(ValueError):
            privacidade.salvar(self.conn, lat=0, lon=0, raio_m=10)
        with self.assertRaises(ValueError):
            privacidade.salvar(self.conn, lat=0, lon=0, raio_m=200, modo="apagar")


class ExportComAreaTests(unittest.TestCase):
    """O caso que importa: o dado da área privada não sai no CSV."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("export-privacidade")
        isolar_dados(Path(cls.tmp.name))
        cls.conn = db.connect()
        db.init_db(cls.conn)
        cls.conn.execute("""INSERT INTO files (id, name, sha256, imported_at, first_seen,
                              obs_count, format_version) VALUES (1,'s.log','x','2026-10-14',
                              '2026-10-14 12:00:00', 3, 'WigleWifi-1.4')""")
        for i, (mac, ponto) in enumerate((("AA:AA:AA:00:00:01", PERTO),
                                          ("AA:AA:AA:00:00:02", LONGE),
                                          ("AA:AA:AA:00:00:03", LONGE)), start=1):
            cls.conn.execute(
                """INSERT INTO observations (id, file_id, mac, ssid, auth_mode, first_seen,
                       channel, frequency, rssi, lat, lon, altitude, accuracy, type, has_gps)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (i, 1, mac, "Rede", "[WPA2_PSK]", f"2026-10-14 12:0{i}:00", 6, 2437, -60,
                 ponto[0], ponto[1], 600, 5, "WIFI"))
        cls.conn.execute("UPDATE networks SET obs_count = 1")
        cls.conn.commit()
        cls.settings = config.load_settings()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def _exportar(self):
        return exporter.create_export(
            self.conn, target="wigle",
            selection={"mode": "all", "types": ["WIFI"], "with_gps_only": True,
                       "dedupe": "none", "header_mode": "source"},
            settings=self.settings, label="teste")

    def test_sem_area_exporta_tudo_e_csv_igual(self):
        antes = self._exportar()
        self.assertEqual(antes["rows"], 3)
        self.assertEqual(antes["privacidade"]["omitidas"], 0)
        # segunda vez, com o mesmo conteúdo, produz o mesmo CSV (sem áreas)
        depois = self._exportar()
        arquivo_a = Path(antes["dir"]) / "part01.csv"
        arquivo_b = Path(depois["dir"]) / "part01.csv"
        self.assertEqual(arquivo_a.read_bytes(), arquivo_b.read_bytes())

    def test_area_omite_a_linha_e_registra_no_manifesto(self):
        area_id = privacidade.salvar(self.conn, lat=CENTRO[0], lon=CENTRO[1], raio_m=200,
                                     rotulo="casa")
        try:
            criado = self._exportar()
            self.assertEqual(criado["rows"], 2)
            self.assertEqual(criado["privacidade"]["omitidas"], 1)
            self.assertEqual(criado["privacidade"]["por_area"], {"casa": 1})
            self.assertEqual(criado["privacidade"]["total_selecionado"], 3)
            self.assertEqual(criado["manifest"]["privacidade"]["omitidas"], 1)
            csv_texto = (Path(criado["dir"]) / "part01.csv").read_text(encoding="utf-8")
            self.assertNotIn("AA:AA:AA:00:00:01", csv_texto)   # dentro do raio
            self.assertIn("AA:AA:AA:00:00:02", csv_texto)
        finally:
            privacidade.remover(self.conn, area_id)

    def test_linha_omitida_continua_pendente_para_o_envio(self):
        """Não marca como enviada: se a área sair, o dado volta para a fila."""
        area_id = privacidade.salvar(self.conn, lat=CENTRO[0], lon=CENTRO[1], raio_m=200)
        try:
            criado = self._exportar()
            marcadas = {r[0] for r in self.conn.execute(
                "SELECT obs_id FROM obs_uploads WHERE target = 'wigle'")}
            self.assertNotIn(1, marcadas)          # obs id 1 = a de dentro da área
            self.assertTrue({2, 3} <= marcadas or not marcadas)
            self.assertEqual(criado["rows"], 2)
        finally:
            privacidade.remover(self.conn, area_id)


if __name__ == "__main__":
    unittest.main()
