"""Testes de importação de Kismet (.netxml) e do banco do app WiGLE Android (issue #10).

Fixtures são sintéticas — nenhum dado de terceiro entra no repositório.
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import db, importer, origens  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _criar_sqlite(caminho: Path) -> Path:
    """Reproduz o essencial do `wiglewifi.sqlite` do app Android."""
    conn = sqlite3.connect(caminho)
    conn.execute(
        """CREATE TABLE network (_id INTEGER PRIMARY KEY, bssid TEXT, ssid TEXT,
               frequency INTEGER, level INTEGER, latitude REAL, longitude REAL,
               altitude REAL, accuracy REAL, time INTEGER, type TEXT, capabilities TEXT)""")
    linhas = [
        ("02:AA:BB:CC:DD:01", "Rede Android", 2437, -55, 10.5, 20.5, 700.0, 4.0,
         1776000000000, "WIFI", "[WPA2-PSK-CCMP][ESS]"),
        ("02:AA:BB:CC:DD:02", "Outra Aberta", 2412, -70, 10.501, 20.501, 700.0, 5.0,
         1776000060000, "WIFI", "[ESS]"),
        ("02:AA:BB:CC:DD:03", "Tag BLE", 2402, -80, 10.502, 20.502, 700.0, 6.0,
         1776000120000, "BLE", ""),
        ("02:AA:BB:CC:DD:04", "Sem GPS", 2462, -65, None, None, None, None,
         1776000180000, "WIFI", "[WPA2-PSK-CCMP][ESS]"),
    ]
    conn.executemany("INSERT INTO network (bssid, ssid, frequency, level, latitude, longitude,"
                      " altitude, accuracy, time, type, capabilities) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      linhas)
    conn.commit()
    conn.close()
    return caminho


class DeteccaoTests(unittest.TestCase):
    def test_detecta_pelo_conteudo(self):
        self.assertEqual(origens.detectar("qualquer.txt", (FIXTURES / "kismet-exemplo.netxml").read_bytes()),
                         "netxml")
        self.assertEqual(origens.detectar("x.bin", b"SQLite format 3\x00resto"), "sqlite")
        self.assertIsNone(origens.detectar("wardrive_1.log", b"WigleWifi-1.6,appRelease=x\nMAC,SSID\n"))


class KismetTests(unittest.TestCase):
    def test_converte_para_csv_do_wigle(self):
        dados = (FIXTURES / "kismet-exemplo.netxml").read_bytes()
        csv_texto = origens.netxml_para_csv(dados).decode()
        linhas = csv_texto.splitlines()
        self.assertTrue(linhas[0].startswith("WigleWifi-1.6"))
        self.assertIn("brand=kismet", linhas[0])
        self.assertTrue(linhas[1].startswith("MAC,SSID,AuthMode,FirstSeen"))
        # 2 snapshots da primeira rede + 1 da segunda + 1 da terceira (sem GPS)
        self.assertEqual(len(linhas), 2 + 4)

    def test_mapeia_criptografia_e_hora(self):
        dados = (FIXTURES / "kismet-exemplo.netxml").read_bytes()
        texto = origens.netxml_para_csv(dados).decode()
        self.assertIn("[WPA2_PSK]", texto)
        self.assertIn("[OPEN]", texto)
        self.assertIn("[WEP]", texto)
        self.assertIn("2026-10-12 10:00:00", texto)     # hora convertida do formato do Kismet
        self.assertIn("2437", texto)                   # freqmhz preservada

    def test_nao_importa_estacao_cliente(self):
        dados = (FIXTURES / "kismet-exemplo.netxml").read_bytes()
        texto = origens.netxml_para_csv(dados).decode()
        self.assertNotIn("02:11:22:33:44:99", texto)

    def test_ssid_oculto_fica_vazio(self):
        dados = (FIXTURES / "kismet-exemplo.netxml").read_bytes()
        texto = origens.netxml_para_csv(dados).decode()
        linha = [l for l in texto.splitlines() if "02:11:22:33:44:03" in l][0]
        self.assertEqual(linha.split(",")[1], "")


class ImportacaoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pasta_temporaria("origens")
        isolar_dados(Path(self.tmp.name))
        self.conn = db.connect()
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_importa_netxml_de_ponta_a_ponta(self):
        caminho = FIXTURES / "kismet-exemplo.netxml"
        resultado = importer.import_path(self.conn, caminho)
        self.assertEqual(resultado["status"], "ok")
        stats = resultado["stats"]
        self.assertEqual(stats["obs_count"], 4)
        self.assertEqual(stats["network_count"], 3)
        self.assertEqual(stats["no_gps_count"], 1)          # a terceira rede não tinha fix
        sessao = self.conn.execute("SELECT brand, preheader FROM files").fetchone()
        self.assertIn("kismet", sessao["preheader"])

    def test_importa_sqlite_do_app(self):
        caminho = _criar_sqlite(Path(self.tmp.name) / "wiglewifi.sqlite")
        resultado = importer.import_path(self.conn, caminho)
        self.assertEqual(resultado["status"], "ok")
        stats = resultado["stats"]
        self.assertEqual(stats["obs_count"], 4)
        self.assertEqual(stats["ble_count"], 1)
        self.assertEqual(stats["no_gps_count"], 1)
        self.assertEqual(stats["wifi_count"], 3)

    def test_sqlite_sem_tabela_certa_explica(self):
        caminho = Path(self.tmp.name) / "outro.sqlite"
        conn = sqlite3.connect(caminho)
        conn.execute("CREATE TABLE nada (a TEXT)")
        conn.commit()
        conn.close()
        resultado = importer.import_path(self.conn, caminho)
        self.assertEqual(resultado["status"], "erro")
        self.assertIn("BSSID", resultado["message"])

    def test_dedupe_continua_valendo_para_netxml(self):
        caminho = FIXTURES / "kismet-exemplo.netxml"
        importer.import_path(self.conn, caminho)
        segundo = importer.import_path(self.conn, caminho)
        self.assertEqual(segundo["status"], "ignorado")


if __name__ == "__main__":
    unittest.main()
