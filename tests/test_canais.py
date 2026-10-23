"""Testes da análise de canais (issue #8)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import canais, db  # noqa: E402

LAT, LON = -23.2800, -47.2900


class FuncoesTests(unittest.TestCase):
    def test_potencia_cresce_com_o_sinal(self):
        self.assertGreater(canais.potencia_mw(-40), canais.potencia_mw(-60))
        self.assertGreater(canais.potencia_mw(-60), canais.potencia_mw(-90))
        self.assertAlmostEqual(canais.potencia_mw(-60), 100.0, places=3)

    def test_faixa_pela_frequencia(self):
        self.assertEqual(canais.faixa_de(6, 2437), "2.4")
        self.assertEqual(canais.faixa_de(36, 5180), "5")
        self.assertEqual(canais.faixa_de(11), "2.4")        # sem freq: palpite pelo número
        self.assertEqual(canais.faixa_de(149), "5")

    def test_sobreposicao(self):
        self.assertEqual(canais.fator_sobreposicao(0, "2.4"), 1.0)
        self.assertAlmostEqual(canais.fator_sobreposicao(1, "2.4"), 0.8)
        self.assertAlmostEqual(canais.fator_sobreposicao(3, "2.4"), 0.4)
        self.assertEqual(canais.fator_sobreposicao(5, "2.4"), 0.0)
        self.assertEqual(canais.fator_sobreposicao(1, "5"), 0.0)
        self.assertAlmostEqual(canais.fator_sobreposicao(4, "5"), 0.25)


class SugestaoTests(unittest.TestCase):
    def test_vizinho_forte_no_6_nao_e_sugerido(self):
        """Critério de aceite: um AP a -40 dBm no canal 6 derruba o 6."""
        aps = [
            {"canal": 6, "rssi": -40},
            {"canal": 1, "rssi": -90},
        ]
        sugestao = canais.sugestao(aps, "2.4")
        self.assertEqual(sugestao[0]["canal"], 11)   # ninguém por perto
        self.assertEqual(sugestao[-1]["canal"], 6)   # o vizinho forte
        self.assertGreater(sugestao[-1]["energia"], sugestao[0]["energia"])

    def test_contagem_nao_ganha_de_energia(self):
        """Três vizinhos fracos no 11 pesam menos que um forte no 1."""
        aps = [
            {"canal": 1, "rssi": -42},
            {"canal": 11, "rssi": -88},
            {"canal": 11, "rssi": -87},
            {"canal": 11, "rssi": -89},
        ]
        sugestao = canais.sugestao(aps, "2.4")
        canais_por_posicao = [s["canal"] for s in sugestao]
        self.assertNotEqual(canais_por_posicao[0], 1)
        self.assertLess(canais_por_posicao.index(11), canais_por_posicao.index(1))

    def test_canal_vizinho_conta_na_sobreposicao(self):
        """AP no 4 invade o 1 e o 6; o 11 fica limpo."""
        aps = [{"canal": 4, "rssi": -50}]
        sugestao = {s["canal"]: s for s in canais.sugestao(aps, "2.4")}
        self.assertEqual(sugestao[11]["energia"], 0.0)
        self.assertGreater(sugestao[6]["energia"], sugestao[1]["energia"])  # 4→6 = 2, 4→1 = 3

    def test_5ghz_so_canais_sem_dfs(self):
        sugestao = canais.sugestao([{"canal": 36, "rssi": -60}], "5")
        canais_oferecidos = {s["canal"] for s in sugestao}
        self.assertEqual(canais_oferecidos, set(canais.CANAIS_5_SEM_DFS))
        self.assertNotIn(52, canais_oferecidos)      # DFS: pode sumir com radar
        self.assertEqual(sugestao[0]["canal"] != 36, True)


class RelatorioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("canais")
        isolar_dados(Path(cls.tmp.name))
        cls.conn = db.connect()
        db.init_db(cls.conn)
        cls.conn.execute(
            """INSERT INTO files (id, name, sha256, imported_at, first_seen, obs_count)
               VALUES (1,'c.log','x','2026-10-14','2026-10-14 12:00:00',6)""")
        observacoes = [
            # dentro do raio (≤ 100 m): dois APs no 6 (um forte), um no 11 e um no 36
            ("AA:00:00:00:00:01", 6, 2437, -45, LAT, LON),
            ("AA:00:00:00:00:01", 6, 2437, -80, LAT, LON),      # mesma rede, mais fraca
            ("AA:00:00:00:00:02", 6, 2437, -75, LAT + 0.0005, LON),
            ("AA:00:00:00:00:03", 11, 2462, -70, LAT, LON - 0.0004),
            ("AA:00:00:00:00:04", 36, 5180, -60, LAT + 0.0002, LON),
            # fora do raio de 100 m (~1,1 km ao sul)
            ("AA:00:00:00:00:05", 1, 2412, -30, LAT - 0.01, LON),
        ]
        for mac, canal, freq, rssi, lat, lon in observacoes:
            cls.conn.execute(
                """INSERT INTO observations (file_id, mac, ssid, type, first_seen, channel,
                       frequency, rssi, lat, lon, has_gps)
                   VALUES (1,?,'rede','WIFI','2026-10-14 12:00:00',?,?,?,?,?,1)""",
                (mac, canal, freq, rssi, lat, lon))
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_raio_recorta_o_que_esta_longe(self):
        relatorio = canais.relatorio(self.conn, LAT, LON, 100)
        self.assertEqual(relatorio["aps"], 4)          # o AP de -30 dBm a 1,1 km fica fora
        canais_24 = {c["canal"] for c in relatorio["faixas"]["2.4"]["canais"]}
        self.assertNotIn(1, canais_24)

    def test_uma_linha_por_rede_com_o_sinal_mais_forte(self):
        relatorio = canais.relatorio(self.conn, LAT, LON, 100)
        canal6 = [c for c in relatorio["faixas"]["2.4"]["canais"] if c["canal"] == 6][0]
        self.assertEqual(canal6["aps"], 2)             # MAC 01 e MAC 02, não a repetição
        self.assertEqual(canal6["rssi_forte"], -45)

    def test_separa_2g_e_5g(self):
        relatorio = canais.relatorio(self.conn, LAT, LON, 100)
        self.assertEqual(relatorio["faixas"]["5"]["aps"], 1)
        self.assertEqual(relatorio["faixas"]["5"]["canais"][0]["canal"], 36)

    def test_excluir_a_propria_rede(self):
        relatorio = canais.relatorio(self.conn, LAT, LON, 100, excluir_mac="AA:00:00:00:00:01")
        canal6 = [c for c in relatorio["faixas"]["2.4"]["canais"] if c["canal"] == 6][0]
        self.assertEqual(canal6["aps"], 1)
        self.assertEqual(canal6["rssi_forte"], -75)

    def test_sugestao_completa_do_ponto(self):
        relatorio = canais.relatorio(self.conn, LAT, LON, 100)
        posicoes = [s["canal"] for s in relatorio["faixas"]["2.4"]["sugestao"]]
        self.assertEqual(len(posicoes), 3)
        self.assertEqual(set(posicoes), {1, 6, 11})
        self.assertNotEqual(posicoes[0], 6)            # tem dois vizinhos no 6, um deles forte


if __name__ == "__main__":
    unittest.main()
