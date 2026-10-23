"""Testes do diagnóstico de risco (issue #6)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import db, seguranca  # noqa: E402


class ClassificacaoTests(unittest.TestCase):
    def test_faixas_do_auth_mode(self):
        casos = {
            "[OPEN]": "aberta",
            "[ESS]": "aberta",                      # ESS sem criptografia = aberta
            "[WEP]": "wep",
            "[WPA_PSK]": "wpa",
            "[WPA_WPA2_PSK]": "mista",
            "[WPA2_WPA3_PSK]": "mista",
            "[WPA2_PSK]": "wpa2",
            "[WPA2-PSK-CCMP+TKIP][ESS]": "wpa2",
            "[WPA3-SAE-CCMP][ESS]": "wpa3",
            "[WPA3_PSK]": "wpa3",
            "[WPA2-EAP-CCMP+TKIP][ESS]": "empresa",
            "[UNDEFINED]": "desconhecido",
            "": "desconhecido",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(seguranca.risco_de(entrada), esperado)

    def test_wpa2_nao_vira_wpa(self):
        # "WPA2" contém "WPA": o teste garante que o WPA "puro" é reconhecido à parte
        self.assertEqual(seguranca.risco_de("[WPA2_PSK]"), "wpa2")
        self.assertEqual(seguranca.risco_de("[WPA_PSK]"), "wpa")

    def test_pior_risco_da_lista(self):
        self.assertEqual(seguranca.pior_risco(["wpa3", "aberta", "wpa2"]), "aberta")
        self.assertEqual(seguranca.pior_risco(["wpa3", "wpa2"]), "wpa2")
        self.assertEqual(seguranca.pior_risco([]), "desconhecido")

    def test_ssid_de_fabrica(self):
        self.assertEqual(seguranca.ssid_de_fabrica("VIVOFIBRA-1A2B"), "Vivo Fibra")
        self.assertEqual(seguranca.ssid_de_fabrica("TP-Link_5E2A"), "TP-Link")
        self.assertEqual(seguranca.ssid_de_fabrica("NET_2G4A1"), "NET/Claro")
        self.assertEqual(seguranca.ssid_de_fabrica("INTELBRAS"), "Intelbras")
        # nome pessoal não é fábrica
        self.assertIsNone(seguranca.ssid_de_fabrica("Kaique2010"))
        self.assertIsNone(seguranca.ssid_de_fabrica("Casa do Luiz"))
        self.assertIsNone(seguranca.ssid_de_fabrica(""))

    def test_mascara(self):
        self.assertEqual(seguranca.mascarar_ssid("VIVOFIBRA-1A2B"), "VIVO***")
        self.assertEqual(seguranca.mascarar_ssid("Casa"), "***")
        self.assertEqual(seguranca.mascarar_mac("58:2F:F7:17:21:47"), "58:2F:F7:**:**")


class BancoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("risco")
        isolar_dados(Path(cls.tmp.name))
        cls.conn = db.connect()
        db.init_db(cls.conn)
        cls.conn.execute(
            """INSERT INTO files (id, name, sha256, imported_at, first_seen, obs_count)
               VALUES (1,'s.log','x','2026-10-14','2026-10-14 12:00:00',4)""")
        redes = [
            ("AA:00:00:00:00:01", "Rede Aberta", "[OPEN]", -23.2800, -47.2900),
            ("AA:00:00:00:00:02", "WepVelha", "[WEP]", -23.2800, -47.2900),
            ("AA:00:00:00:00:03", "VIVOFIBRA-1A2B", "[WPA2_PSK]", -23.2800, -47.2900),
            ("AA:00:00:00:00:04", "Boa", "[WPA3_SAE_CCMP][ESS]", -23.2850, -47.2900),
        ]
        for mac, ssid, auth, lat, lon in redes:
            cls.conn.execute(
                """INSERT INTO networks (mac, type, ssid, auth_mode, obs_count, best_lat, best_lon)
                   VALUES (?,?,?,?,1,?,?)""", (mac, "WIFI", ssid, auth, lat, lon))
            cls.conn.execute(
                """INSERT INTO observations (file_id, mac, ssid, type, first_seen, channel,
                       rssi, lat, lon, has_gps) VALUES (1,?,?,'WIFI','2026-10-14 12:00:00',6,-60,?,?,1)""",
                (mac, ssid, lat, lon))
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_resumo_conta_redes_fracas(self):
        r = seguranca.resumo(self.conn)
        self.assertEqual(r["total"], 4)
        self.assertEqual(r["fracas"], 2)              # aberta + WEP
        self.assertEqual(r["pct_fracas"], 50.0)

    def test_por_celula_pinta_pelo_pior(self):
        celulas = seguranca.por_celula(self.conn)
        primeira = celulas[0]
        self.assertEqual(primeira["redes"], 3)        # três redes na célula densa
        self.assertEqual(primeira["fracas"], 2)
        self.assertEqual(primeira["pior"], "aberta")
        self.assertAlmostEqual(primeira["pct_fracas"], 66.7, delta=0.2)
        # a célula com só a rede WPA3 fica limpa
        limpa = [c for c in celulas if c["redes"] == 1][0]
        self.assertEqual(limpa["fracas"], 0)
        self.assertEqual(limpa["pior"], "wpa3")

    def test_fabrica_lista_so_nomes_de_fabrica(self):
        itens = seguranca.fabrica(self.conn)
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0]["ssid"], "VIVOFIBRA-1A2B")
        self.assertEqual(itens[0]["padrao"], "Vivo Fibra")

    def test_csv_ranking_e_mascara(self):
        csv_texto = seguranca.csv_ranking(seguranca.por_celula(self.conn)).decode()
        self.assertIn("latitude,longitude,redes_wifi", csv_texto)
        self.assertIn("aberta", csv_texto)

        normal = seguranca.csv_fabrica(seguranca.fabrica(self.conn), mascarar=False).decode()
        mascarado = seguranca.csv_fabrica(seguranca.fabrica(self.conn), mascarar=True).decode()
        self.assertIn("VIVOFIBRA-1A2B", normal)
        self.assertNotIn("VIVOFIBRA-1A2B", mascarado)
        self.assertIn("VIVO***", mascarado)


if __name__ == "__main__":
    unittest.main()
