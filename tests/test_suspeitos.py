"""Testes do detector de SSID duplicado / evil twin (issue #7)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import db, suspeitos  # noqa: E402

LAT, LON = -23.2800, -47.2900


class DetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("suspeitos")
        isolar_dados(Path(cls.tmp.name))
        cls.conn = db.connect()
        db.init_db(cls.conn)
        cls.conn.execute(
            """INSERT INTO files (id, name, sha256, imported_at, first_seen, obs_count)
               VALUES (1,'s.log','x','2026-10-14','2026-10-14 12:00:00',10)""")

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def setUp(self):
        self.conn.execute("DELETE FROM observations")
        self.conn.commit()

    def _obs(self, mac, ssid, auth, rssi, canal, lat=LAT, lon=LON, repeticoes=2,
             primeira="2026-10-14 12:00:00"):
        for _ in range(repeticoes):
            self.conn.execute(
                """INSERT INTO observations (file_id, mac, ssid, auth_mode, type, first_seen,
                       channel, rssi, lat, lon, has_gps)
                   VALUES (1,?,?,?,'WIFI',?,?,?,?,?,1)""",
                (mac, ssid, auth, primeira, canal, rssi, lat, lon))
        self.conn.commit()

    def test_dois_fabricantes_no_mesmo_canal_aponta(self):
        """Critério de aceite: 2 BSSIDs, mesmo SSID, mesma célula, RSSI próximo."""
        self._obs("98:2A:0A:69:57:FB", "Loja Centro", "[WPA2_PSK]", -76, 11)   # Intelbras
        self._obs("3C:6A:D2:73:BF:68", "Loja Centro", "[WPA2_PSK]", -79, 11)   # outra marca
        r = suspeitos.detectar(self.conn)
        self.assertEqual(r["total"], 1)
        item = r["itens"][0]
        self.assertEqual(item["nivel"], "forte")
        self.assertEqual(item["ssid"], "Loja Centro")
        self.assertIn("mesmo canal", item["motivos"])
        self.assertIn("fabricante diferente", item["motivos"])

    def test_mesmo_fabricante_nao_aponta(self):
        """Mesh / 2,4 + 5 GHz da mesma caixa: mesmo OUI, não é clone."""
        self._obs("98:2A:0A:69:57:FB", "Casa", "[WPA2_PSK]", -60, 1)
        self._obs("98:2A:0A:69:57:FC", "Casa", "[WPA2_PSK]", -61, 1)
        self.assertEqual(suspeitos.detectar(self.conn)["total"], 0)

    def test_mac_aleatorio_nao_aponta(self):
        """Celular com hotspot pessoal repete o nome com MAC novo (bit local)."""
        self._obs("52:A5:5D:94:7A:29", "iPhone do Joao", "[WPA2_PSK]", -82, 6)
        self._obs("1E:63:98:4B:50:BD", "iPhone do Joao", "[WPA2_PSK]", -86, 6)
        self.assertEqual(suspeitos.detectar(self.conn)["total"], 0)

    def test_sinal_muito_diferente_nao_aponta(self):
        self._obs("98:2A:0A:69:57:FB", "Padaria", "[WPA2_PSK]", -55, 6)
        self._obs("3C:6A:D2:73:BF:68", "Padaria", "[WPA2_PSK]", -95, 6)
        self.assertEqual(suspeitos.detectar(self.conn)["total"], 0)

    def test_muitos_bssids_na_celula_nao_entra(self):
        """Cinco placas no mesmo nome é hotel/rede corporativa, não clone."""
        for indice, mac in enumerate(("98:2A:0A:00:00:01", "3C:6A:D2:00:00:02",
                                      "AC:84:C6:00:00:03", "18:E8:29:00:00:04",
                                      "FC:EC:DA:00:00:05")):
            self._obs(mac, "Hotel Centro", "[WPA2_PSK]", -70 - indice, 6)
        self.assertEqual(suspeitos.detectar(self.conn)["total"], 0)

    def test_celula_diferente_nao_aponta(self):
        self._obs("98:2A:0A:69:57:FB", "Rede", "[WPA2_PSK]", -70, 6)
        self._obs("3C:6A:D2:73:BF:68", "Rede", "[WPA2_PSK]", -72, 6, lat=LAT + 0.01)
        self.assertEqual(suspeitos.detectar(self.conn)["total"], 0)

    def test_para_rede_encontra_o_bssid_na_lista(self):
        self._obs("98:2A:0A:69:57:FB", "Clinica", "[WPA2_PSK]", -78, 1)
        self._obs("3C:6A:D2:73:BF:68", "Clinica", "[OPEN]", -80, 1)
        achados = suspeitos.para_rede(self.conn, "98:2a:0a:69:57:fb")
        self.assertEqual(len(achados), 1)
        self.assertEqual(achados[0]["outros"][0]["mac"], "3C:6A:D2:73:BF:68")


if __name__ == "__main__":
    unittest.main()
