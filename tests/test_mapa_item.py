"""Testes do filtro do mapa por item (rede) e do resumo da seleção."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import db, importer, queries  # noqa: E402

ALVO = "58:2F:F7:17:21:47"
OUTRA = "00:11:22:33:44:55"

LINHAS = (
    "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,"
    "AltitudeMeters,AccuracyMeters,Type\n"
    f"{ALVO},Cafe,[WPA2_PSK],2026-10-13 21:19:29,6,-71,-23.2790000,-47.2870000,596,7,WIFI\n"
    f"{ALVO},Cafe,[WPA2_PSK],2026-10-13 21:20:29,6,-58,-23.2795000,-47.2875000,596,7,WIFI\n"
    f"{ALVO},Cafe,[WPA2_PSK],2026-10-13 21:21:29,6,-83,-23.2800000,-47.2880000,596,7,WIFI\n"
    f"{OUTRA},Loja,[WPA2_PSK],2026-10-13 21:19:31,1,-60,-23.2792000,-47.2872000,597,5,WIFI\n"
    "aa:bb:cc:dd:ee:ff,Loja Guest,[WPA2_PSK],2026-10-13 21:19:32,1,-66,-23.2793000,-47.2873000,597,5,WIFI\n"
)

SEGUNDA_SESSAO = (
    "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,"
    "AltitudeMeters,AccuracyMeters,Type\n"
    f"{ALVO},Cafe,[WPA2_PSK],2026-10-14 18:00:00,6,-49,-23.3100000,-47.3200000,600,7,WIFI\n"
)

CABECALHO = (
    "WigleWifi-1.4,appRelease=v1.18.0,model=ESP32 Marauder,release=v1.18.0,"
    "device=ESP32 Marauder,display=SPI TFT,board=ESP32 Marauder,brand=JustCallMeKoko\n"
)


class FiltroMapaPorItemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("mapa-item")
        cls.pasta = Path(cls.tmp.name) / "logs"
        cls.pasta.mkdir(parents=True, exist_ok=True)
        (cls.pasta / "wd_20261013-211900.log").write_text(CABECALHO + LINHAS, encoding="utf-8")
        (cls.pasta / "wd_20261014-180000.log").write_text(CABECALHO + SEGUNDA_SESSAO, encoding="utf-8")
        isolar_dados(Path(cls.tmp.name))
        cls.conn = db.connect()
        db.init_db(cls.conn)
        importer.scan_and_import(cls.conn, [str(cls.pasta)])

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_sem_filtro_traz_tudo(self):
        # 5 observações na primeira sessão + 1 na segunda
        self.assertEqual(len(queries.map_points(self.conn)), 6)

    def test_mac_traz_so_os_pontos_daquele_item(self):
        pontos = queries.map_points(self.conn, mac=ALVO)
        self.assertEqual(len(pontos), 4)  # 3 na primeira sessão + 1 na segunda
        self.assertEqual({p["mac"] for p in pontos}, {ALVO})

    def test_mac_aceita_minusculas_e_hifen(self):
        self.assertEqual(len(queries.map_points(self.conn, mac=ALVO.lower())), 4)
        self.assertEqual(len(queries.map_points(self.conn, mac=ALVO.replace(":", "-"))), 4)

    def test_nome_da_rede_filtra_por_ssid(self):
        pontos = queries.map_points(self.conn, mac="Café")
        self.assertEqual(len(pontos), 0)
        pontos = queries.map_points(self.conn, mac="Cafe")
        self.assertEqual(len(pontos), 4)

    def test_modo_redes_com_item_mostra_uma_so_bolinha(self):
        pontos = queries.map_points(self.conn, mac=ALVO, network_mode=True)
        self.assertEqual(len(pontos), 1)
        self.assertEqual(pontos[0]["mac"], ALVO)
        self.assertEqual(pontos[0]["obs_count"], 4)

    def test_resumo_conta_sessoes_locais_e_rssi(self):
        resumo = queries.map_resumo(self.conn, mac=ALVO)
        self.assertEqual(resumo["pontos"], 4)
        self.assertEqual(resumo["sessoes"], 2)       # apareceu em duas varreduras
        self.assertEqual(resumo["redes"], 1)
        self.assertEqual(resumo["locais"], 4)
        self.assertEqual(resumo["rssi_max"], -49)
        self.assertEqual(resumo["por_sessao"][0]["obs"], 3)  # sessão onde mais apareceu
        self.assertEqual(resumo["rede"]["ssid"], "Cafe")

    def test_resumo_do_item_ignora_coordenadas_do_filtro_de_tipo(self):
        resumo = queries.map_resumo(self.conn, mac=ALVO, types=["BLE"])
        self.assertEqual(resumo["pontos"], 0)


if __name__ == "__main__":
    unittest.main()
