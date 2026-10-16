"""Testes do leitor de CSV WiGLE (formatos 1.4, 1.6 e 14 colunas)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wardrive import parser  # noqa: E402

MARAUder_14 = (
    "WigleWifi-1.4,appRelease=v1.18.0,model=ESP32 Marauder,release=v1.18.0,"
    "device=ESP32 Marauder,display=SPI TFT,board=ESP32 Marauder,brand=JustCallMeKoko\n"
    "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,"
    "AltitudeMeters,AccuracyMeters,Type\n"
    "24:c8:a2:8b:b1:9f,,[BLE],2026-10-13 21:19:29,0,-37,-23.2792358,-47.2874031,596.90,7.00,BLE\n"
    "58:2F:F7:17:21:47,Dali,[WPA2_PSK],2026-10-13 21:19:30,44,-76,-23.2792435,-47.2874069,596.80,7.00,WIFI\n"
    "00:11:22:33:44:55,\"Rede, com vírgula\",[WPA2_PSK],2026-10-13 21:19:31,6,-60,-23.2792500,-47.2874100,597,5,WIFI\n"
    "aa:bb:cc:dd:ee:ff,Forte,[WPA3-SAE-CCMP][ESS],2026-10-13 21:19:32,1,-40,,,0,0,WIFI\n"
)

WDG_16 = (
    "WigleWifi-1.6,appRelease=warrior-0.2,model=Marauder v8 C5,release=warrior-0.2,"
    "device=ESP32-C5,display=ILI9341,board=ESP32C5,brand=WDGoWars\r\n"
    "MAC,SSID,AuthMode,FirstSeen,Channel,Frequency,RSSI,CurrentLatitude,CurrentLongitude,"
    "AltitudeMeters,AccuracyMeters,Type\r\n"
    "94:8F:CF:A5:99:F6,Armazem,[WPA2-PSK-CCMP+TKIP][ESS],2026-10-14 15:58:04,7,2442,-96,"
    "-23.279063,-47.286795,592,4.5,WIFI\r\n"
)

FULL_14 = (
    "WigleWifi-1.6,appRelease=2.78,model=X-37B,release=11.0.0,device=felgercarb,"
    'display=OPSPLS1.76-1-S,board=snodgrass,"brand=Moog, LLC",star=Sol,body=4,subBody=0\r\n'
    "MAC,SSID,AuthMode,FirstSeen,Channel,Frequency,RSSI,CurrentLatitude,CurrentLongitude,"
    "AltitudeMeters,AccuracyMeters,RCOIs,MfgrId,Type\r\n"
    "1a:9f:ee:5c:71:c6,Scampoodle,[WPA2-EAP-CCMP][ESS],2018-08-01 13:08:27,161,5805,-43,"
    "37.76578028,-123.45919439,67,3.2160000801086426,5A03BA0000 BAA2D00000,,WIFI\r\n"
    "63:56:ac:c4:d4:30,,Misc [LE],2018-08-03 18:14:12,0,,-67,37.76090571,-122.44877987,"
    "104,49.3120002746582,,72,BLE\r\n"
)


class Parser14Tests(unittest.TestCase):
    def setUp(self):
        self.parsed = parser.parse_bytes(MARAUder_14.encode("utf-8"), "wardrive_0.log")

    def test_preheader(self):
        pre = self.parsed["preheader"]
        self.assertEqual(pre["format_version"], "WigleWifi-1.4")
        self.assertEqual(pre["brand"], "JustCallMeKoko")
        self.assertEqual(pre["model"], "ESP32 Marauder")

    def test_todas_as_linhas_lidas(self):
        self.assertEqual(self.parsed["stats"]["obs_count"], 4)
        self.assertEqual(self.parsed["stats"]["wifi_count"], 3)
        self.assertEqual(self.parsed["stats"]["ble_count"], 1)

    def test_frequencia_derivada_do_canal(self):
        canais = {r["mac"]: r for r in self.parsed["rows"]}
        self.assertEqual(canais["58:2F:F7:17:21:47"]["frequency"], 5220)   # canal 44 → 5 GHz
        self.assertEqual(canais["00:11:22:33:44:55"]["frequency"], 2437)   # canal 6
        self.assertIsNone(canais["24:C8:A2:8B:B1:9F"]["frequency"])         # BLE sem frequência

    def test_ssid_com_virgula(self):
        linha = [r for r in self.parsed["rows"] if r["mac"] == "00:11:22:33:44:55"][0]
        self.assertEqual(linha["ssid"], "Rede, com vírgula")

    def test_mac_em_maiusculas(self):
        self.assertIn("AA:BB:CC:DD:EE:FF", {r["mac"] for r in self.parsed["rows"]})

    def test_linha_sem_gps_marcada(self):
        self.assertEqual(self.parsed["stats"]["no_gps_count"], 1)
        linha = [r for r in self.parsed["rows"] if r["mac"] == "AA:BB:CC:DD:EE:FF"][0]
        self.assertEqual(linha["has_gps"], 0)
        self.assertIsNone(linha["lat"])

    def test_periodo(self):
        self.assertEqual(self.parsed["stats"]["first_seen"], "2026-10-13 21:19:29")
        self.assertEqual(self.parsed["stats"]["last_seen"], "2026-10-13 21:19:32")
        self.assertFalse(self.parsed["stats"]["has_frequency"])


class Parser16Tests(unittest.TestCase):
    def test_crlf_e_frequencia_presente(self):
        parsed = parser.parse_bytes(WDG_16.encode("utf-8"), "wd_1.log")
        self.assertEqual(parsed["stats"]["obs_count"], 1)
        self.assertTrue(parsed["stats"]["has_frequency"])
        self.assertEqual(parsed["rows"][0]["frequency"], 2442)
        self.assertEqual(parsed["preheader"]["brand"], "WDGoWars")

    def test_layout_completo_14_colunas(self):
        parsed = parser.parse_bytes(FULL_14.encode("utf-8"), "full.csv")
        self.assertEqual(len(parsed["columns"]), 14)
        self.assertEqual(parsed["preheader"]["brand"], "Moog, LLC")  # valor entre aspas
        ble = [r for r in parsed["rows"] if r["type"] == "BLE"][0]
        self.assertEqual(ble["mfgr_id"], "72")
        self.assertEqual(ble["auth_mode"], "Misc [LE]")
        wifi = [r for r in parsed["rows"] if r["type"] == "WIFI"][0]
        self.assertEqual(wifi["rcois"], "5A03BA0000 BAA2D00000")
        self.assertEqual(wifi["frequency"], 5805)


class ParserBordasTests(unittest.TestCase):
    def test_arquivo_vazio_nao_quebra(self):
        parsed = parser.parse_bytes(b"", "vazio.log")
        self.assertEqual(parsed["stats"].get("obs_count", 0), 0)
        self.assertTrue(parsed["warnings"])

    def test_linha_invalida_contabilizada(self):
        dados = (
            "WigleWifi-1.6,appRelease=x,model=y\n"
            "MAC,SSID,AuthMode,FirstSeen,Channel,Frequency,RSSI,CurrentLatitude,CurrentLongitude,"
            "AltitudeMeters,AccuracyMeters,RCOIs,MfgrId,Type\n"
            "XX:YY:not-a-mac,Rede,[OPEN],2026-01-01 10:00:00,1,2412,-50,-23.5,-47.6,600,5,,,WIFI\n"
            "11:22:33:44:55:66,Rede2,[OPEN],data-errada,1,2412,-50,-23.5,-47.6,600,5,,,WIFI\n"
            "77:88:99:AA:BB:CC,Rede3,[OPEN],2026-01-01 10:05:00,1,2412,-50,-23.5,-47.6,600,5,,,WIFI\n"
        ).encode("utf-8")
        parsed = parser.parse_bytes(dados, "misto.log")
        self.assertEqual(parsed["stats"]["obs_count"], 1)
        self.assertEqual(parsed["stats"]["bad_count"], 2)
        self.assertEqual(len(parsed["bad_rows"]), 2)

    def test_sem_preheader_usa_primeira_linha(self):
        dados = (
            "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,"
            "AltitudeMeters,AccuracyMeters,Type\n"
            "58:2F:F7:17:21:47,Rede,[WPA2_PSK],2026-10-13 21:19:30,1,-76,-23.27,-47.28,596,7,WIFI\n"
        ).encode("utf-8")
        parsed = parser.parse_bytes(dados, "sem-preheader.csv")
        self.assertEqual(parsed["stats"]["obs_count"], 1)
        self.assertIn("sem linha de pre-header", " ".join(parsed["warnings"]))

    def test_cabecalho_invalido_levanta_erro(self):
        with self.assertRaises(ValueError):
            parser.parse_bytes(b"WigleWifi-1.6,x\nalguma,coisa\ntexto,mais\n", "ruim.log")

    def test_frequencia_6ghz(self):
        self.assertEqual(parser.freq_from_channel(233), 7115)
        self.assertEqual(parser.freq_from_channel(1), 2412)
        self.assertEqual(parser.freq_from_channel(14), 2484)
        self.assertEqual(parser.freq_from_channel(36), 5180)
        self.assertIsNone(parser.freq_from_channel(0))

    def test_normalizacao_de_tipo(self):
        self.assertEqual(parser.normalize_type("wifi"), "WIFI")
        self.assertEqual(parser.normalize_type("", "[BLE]"), "BLE")
        self.assertEqual(parser.normalize_type("LTE"), "LTE")
        self.assertEqual(parser.normalize_type(""), "WIFI")


if __name__ == "__main__":
    unittest.main(verbosity=2)
