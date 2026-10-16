"""Testes do gerador de CSV (ida e volta pelo parser) e do fatiamento em lotes."""
from __future__ import annotations

import gzip
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wardrive import config, exporter, parser  # noqa: E402

LINHAS = [
    {
        "id": 1, "file_id": 1, "mac": "58:2F:F7:17:21:47", "ssid": "Dali",
        "auth_mode": "[WPA2_PSK]", "first_seen": "2026-10-13 21:19:30", "channel": 44,
        "frequency": 5220, "rssi": -76, "lat": -23.2792435, "lon": -47.2874069,
        "altitude": 596.8, "accuracy": 7.0, "rcois": "", "mfgr_id": "", "type": "WIFI", "has_gps": 1,
    },
    {
        "id": 2, "file_id": 1, "mac": "24:C8:A2:8B:B1:9F", "ssid": "", "auth_mode": "[BLE]",
        "first_seen": "2026-10-13 21:19:29", "channel": 0, "frequency": None, "rssi": -37,
        "lat": -23.2792358, "lon": -47.2874031, "altitude": 596.9, "accuracy": 7.0,
        "rcois": "", "mfgr_id": "", "type": "BLE", "has_gps": 1,
    },
    {
        "id": 3, "file_id": 2, "mac": "00:11:22:33:44:55", "ssid": 'Rede, "com" aspas',
        "auth_mode": "[OPEN]", "first_seen": "2026-10-14 08:00:00", "channel": 6,
        "frequency": 2437, "rssi": -60, "lat": -23.5, "lon": -47.6, "altitude": 700,
        "accuracy": 5.0, "rcois": "", "mfgr_id": "", "type": "WIFI", "has_gps": 1,
    },
    {
        "id": 4, "file_id": 2, "mac": "AA:BB:CC:DD:EE:FF", "ssid": "sem gps",
        "auth_mode": "[WPA2_PSK]", "first_seen": "2026-10-14 08:01:00", "channel": 11,
        "frequency": 2462, "rssi": -70, "lat": None, "lon": None, "altitude": None,
        "accuracy": None, "rcois": "", "mfgr_id": "", "type": "WIFI", "has_gps": 0,
    },
]

PREHEADER = {
    "format_version": "WigleWifi-1.4",
    "appRelease": "v1.18.0",
    "model": "ESP32 Marauder",
    "release": "v1.18.0",
    "device": "ESP32 Marauder",
    "display": "SPI TFT",
    "board": "ESP32 Marauder",
    "brand": "JustCallMeKoko, Inc",
}


class EscritorTests(unittest.TestCase):
    def test_preheader_sobe_para_16_e_escapa_virgula(self):
        linha = exporter.build_preheader(PREHEADER)
        self.assertTrue(linha.startswith("WigleWifi-1.6,"))
        self.assertIn('"brand=JustCallMeKoko, Inc"', linha)

    def test_csv_completo_tem_cabecalho_de_14_colunas(self):
        csv_texto = exporter.build_csv(LINHAS, PREHEADER).decode("utf-8")
        linhas = csv_texto.split("\r\n")
        self.assertTrue(linhas[0].startswith("WigleWifi-1.6,"))
        self.assertEqual(linhas[1], ",".join(config.WIGLE_COLUMNS))
        self.assertEqual(len(config.WIGLE_COLUMNS), 14)

    def test_linhas_sem_gps_saem_sem_coordenadas(self):
        parsed = parser.parse_bytes(exporter.build_csv(LINHAS, PREHEADER), "gerado.csv")
        sem_gps = [r for r in parsed["rows"] if r["mac"] == "AA:BB:CC:DD:EE:FF"][0]
        self.assertEqual(sem_gps["has_gps"], 0)
        self.assertIsNone(sem_gps["lat"])
        self.assertEqual(sem_gps["channel"], 11)
        self.assertEqual(sem_gps["frequency"], 2462)

    def test_ida_e_volta_preserva_valores(self):
        parsed = parser.parse_bytes(exporter.build_csv(LINHAS, PREHEADER), "gerado.csv")
        por_mac = {r["mac"]: r for r in parsed["rows"]}
        self.assertEqual(len(por_mac), 4)
        wifi = por_mac["58:2F:F7:17:21:47"]
        self.assertEqual(wifi["ssid"], "Dali")
        self.assertEqual(wifi["rssi"], -76)
        self.assertEqual(wifi["channel"], 44)
        self.assertEqual(wifi["frequency"], 5220)
        self.assertAlmostEqual(wifi["lat"], -23.2792435, places=6)
        self.assertAlmostEqual(wifi["altitude"], 597, places=0)   # altitude arredondada
        self.assertEqual(wifi["auth_mode"], "[WPA2_PSK]")
        self.assertEqual(wifi["type"], "WIFI")

        ble = por_mac["24:C8:A2:8B:B1:9F"]
        self.assertEqual(ble["type"], "BLE")
        self.assertIsNone(ble["frequency"])

        aspas = por_mac["00:11:22:33:44:55"]
        self.assertEqual(aspas["ssid"], 'Rede, "com" aspas')

    def test_tipo_ble_sem_frequencia(self):
        csv_texto = exporter.build_csv([LINHAS[1]], PREHEADER).decode("utf-8")
        campos = csv_texto.split("\r\n")[2].split(",")
        self.assertEqual(campos[5], "")   # Frequency vazio para BLE
        self.assertEqual(campos[13], "BLE")


class FatiamentoTests(unittest.TestCase):
    def test_respeita_limite_de_linhas(self):
        partes = exporter.split_chunks(LINHAS, PREHEADER, rows_per_chunk=2, max_bytes=10_000_000)
        self.assertEqual([len(p) for p in partes], [2, 2])

    def test_respeita_limite_de_bytes(self):
        # limite do tamanho de exatamente uma linha: cada parte deve trazer uma só
        uma_linha = len(exporter.build_csv([LINHAS[0]], PREHEADER))
        partes = exporter.split_chunks(LINHAS, PREHEADER, rows_per_chunk=10_000,
                                       max_bytes=uma_linha)
        self.assertGreater(len(partes), 1)
        self.assertEqual(sum(len(p) for p in partes), len(LINHAS))
        for parte in partes:
            self.assertLessEqual(len(exporter.build_csv(parte, PREHEADER)), uma_linha * 2)

    def test_toda_parte_comeca_com_as_duas_linhas_de_cabecalho(self):
        partes = exporter.split_chunks(LINHAS, PREHEADER, rows_per_chunk=1, max_bytes=10_000_000)
        for parte in partes:
            texto = exporter.build_csv(parte, PREHEADER).decode("utf-8")
            linhas = texto.split("\r\n")
            self.assertTrue(linhas[0].startswith("WigleWifi-1.6,"))
            self.assertEqual(linhas[1], ",".join(config.WIGLE_COLUMNS))
            # toda parte precisa ser legível pelo importador
            self.assertEqual(parser.parse_bytes(texto.encode("utf-8"), "parte.csv")["stats"]["obs_count"],
                             len(parte))

    def test_gzip_redondo(self):
        conteudo = exporter.build_csv(LINHAS, PREHEADER)
        compactado = gzip.compress(conteudo, mtime=0)
        self.assertEqual(gzip.decompress(compactado), conteudo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
