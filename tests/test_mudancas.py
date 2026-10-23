"""Testes da comparação entre passagens (issue #12)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import db, mudancas  # noqa: E402

LAT, LON = -23.2800, -47.2900


class ComparacaoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("mudancas")
        isolar_dados(Path(cls.tmp.name))
        cls.conn = db.connect()
        db.init_db(cls.conn)
        cls.conn.execute(
            """INSERT INTO files (id, name, sha256, imported_at, first_seen, obs_count)
               VALUES (1,'a.log','x','2026-10-14','2026-10-14 12:00:00',8)""")
        observacoes = [
            # só na janela antiga (14/10) -> "sumiu"
            ("AA:00:00:00:00:01", "Rede Antiga", "[WPA2_PSK]", "2026-10-14 12:00:00", -60, LAT, LON),
            # nas duas janelas, SSID trocado -> "mudou o nome"
            ("AA:00:00:00:00:02", "Nome Velho", "[WPA2_PSK]", "2026-10-14 12:00:00", -60, LAT, LON),
            ("AA:00:00:00:00:02", "Nome Novo", "[WPA2_PSK]", "2026-10-18 12:00:00", -60, LAT, LON),
            # nas duas janelas, segurança piorou -> "mudou a segurança"
            ("AA:00:00:00:00:03", "Do Vizinho", "[WPA2_PSK]", "2026-10-14 12:00:00", -70, LAT, LON),
            ("AA:00:00:00:00:03", "Do Vizinho", "[OPEN]", "2026-10-18 12:00:00", -70, LAT, LON),
            # só na janela nova -> "nova"
            ("AA:00:00:00:00:04", "Rede Nova", "[WPA2_PSK]", "2026-10-18 12:00:00", -50, LAT, LON),
            # igual nas duas janelas -> não aparece em lista nenhuma
            ("AA:00:00:00:00:05", "Estavel", "[WPA2_PSK]", "2026-10-14 12:00:00", -65, LAT, LON),
            ("AA:00:00:00:00:05", "Estavel", "[WPA2_PSK]", "2026-10-18 12:00:00", -65, LAT, LON),
        ]
        for mac, ssid, auth, quando, rssi, lat, lon in observacoes:
            cls.conn.execute(
                """INSERT INTO observations (file_id, mac, ssid, auth_mode, type, first_seen,
                       channel, rssi, lat, lon, has_gps)
                   VALUES (1,?,?,?,'WIFI',?,6,?,?,?,1)""",
                (mac, ssid, auth, quando, rssi, lat, lon))
            cls.conn.execute(
                """INSERT OR REPLACE INTO networks (mac, type, ssid, auth_mode, obs_count,
                       best_rssi, best_lat, best_lon, categoria)
                   VALUES (?, 'WIFI', ?, ?, 1, ?, ?, ?, 'roteador')""",
                (mac, ssid, auth, rssi, lat, lon))
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_tres_listas(self):
        r = mudancas.comparar(self.conn, "2026-10-14", "2026-10-14", "2026-10-18", "2026-10-18")
        self.assertEqual([x["mac"] for x in r["novas"]], ["AA:00:00:00:00:04"])
        self.assertEqual([x["mac"] for x in r["sumiram"]], ["AA:00:00:00:00:01"])
        self.assertEqual(sorted(x["mac"] for x in r["mudaram"]),
                         ["AA:00:00:00:00:02", "AA:00:00:00:00:03"])
        self.assertEqual(r["comuns"], 3)          # 02, 03 e 05

    def test_mudou_distingue_nome_de_seguranca(self):
        r = mudancas.comparar(self.conn, "2026-10-14", "2026-10-14", "2026-10-18", "2026-10-18")
        por_mac = {x["mac"]: x for x in r["mudaram"]}
        nome = por_mac["AA:00:00:00:00:02"]
        self.assertTrue(nome["mudou_ssid"])
        self.assertFalse(nome["mudou_auth"])
        self.assertEqual((nome["ssid_antes"], nome["ssid_agora"]), ("Nome Velho", "Nome Novo"))

        seguranca = por_mac["AA:00:00:00:00:03"]
        self.assertFalse(seguranca["mudou_ssid"])
        self.assertTrue(seguranca["mudou_auth"])
        self.assertEqual((seguranca["auth_antes"], seguranca["auth_agora"]), ("[WPA2_PSK]", "[OPEN]"))

    def test_rede_estavel_nao_entra_em_nenhuma_lista(self):
        r = mudancas.comparar(self.conn, "2026-10-14", "2026-10-14", "2026-10-18", "2026-10-18")
        todos = [x["mac"] for lista in ("novas", "sumiram", "mudaram") for x in r[lista]]
        self.assertNotIn("AA:00:00:00:00:05", todos)

    def test_area_em_raio_exclui_o_que_esta_longe(self):
        self.conn.execute(
            """INSERT INTO observations (file_id, mac, ssid, auth_mode, type, first_seen,
                   channel, rssi, lat, lon, has_gps)
               VALUES (1,'BB:00:00:00:00:09','Longe','[WPA2_PSK]','WIFI','2026-10-18 12:00:00',
                       6,-70,?, ?,1)""", (LAT - 0.02, LON))
        self.conn.commit()
        try:
            sem_area = mudancas.comparar(self.conn, "2026-10-14", "2026-10-14",
                                         "2026-10-18", "2026-10-18")
            com_area = mudancas.comparar(self.conn, "2026-10-14", "2026-10-14",
                                         "2026-10-18", "2026-10-18",
                                         area={"lat": LAT, "lon": LON, "raio_m": 300})
            self.assertIn("BB:00:00:00:00:09", [x["mac"] for x in sem_area["novas"]])
            self.assertNotIn("BB:00:00:00:00:09", [x["mac"] for x in com_area["novas"]])
        finally:
            self.conn.execute("DELETE FROM observations WHERE mac = 'BB:00:00:00:00:09'")
            self.conn.commit()

    def test_sugestao_de_janelas_nao_se_sobrepoe(self):
        janelas = mudancas.janelas_sugeridas(self.conn, dias=2)
        self.assertLessEqual(janelas["ate_a"], janelas["de_b"])
        self.assertLess(janelas["de_a"], janelas["ate_a"])
        self.assertLess(janelas["de_b"], janelas["ate_b"])


if __name__ == "__main__":
    unittest.main()
