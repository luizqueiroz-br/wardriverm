"""Testes da cobertura (issue #2): grade de ~100 m, km e área da seleção."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import db, queries  # noqa: E402


class CoberturaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("cobertura")
        isolar_dados(Path(cls.tmp.name))
        cls.conn = db.connect()
        db.init_db(cls.conn)
        for i, nome in enumerate(("a.log", "b.log"), start=1):
            cls.conn.execute(
                """INSERT INTO files (id, name, sha256, imported_at, first_seen, obs_count)
                   VALUES (?,?,?,?,?,1)""", (i, nome, f"sha{i}", "2026-10-14", "2026-10-14 12:00:00"))

        def obs(file_id, mac, quando, lat, lon, tipo="WIFI", gps=1):
            cls.conn.execute(
                """INSERT INTO observations (file_id, mac, ssid, type, first_seen, channel,
                       rssi, lat, lon, has_gps) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (file_id, mac, "Rede", tipo, quando, 6, -60, lat, lon, gps))

        # célula A: 3 leituras dentro de 0,001° (mesma célula)
        obs(1, "AA:00:00:00:00:01", "2026-10-14 12:00:00", -23.28000, -47.29000)
        obs(1, "AA:00:00:00:00:02", "2026-10-14 12:00:10", -23.28001, -47.29001)
        obs(1, "AA:00:00:00:00:03", "2026-10-14 12:00:20", -23.28002, -47.29002)
        # célula vizinha (~110 m ao norte), 1 leitura
        obs(1, "AA:00:00:00:00:04", "2026-10-14 12:01:00", -23.28100, -47.29000)
        # segunda sessão, longe (outra célula), e uma linha sem GPS que não conta
        obs(2, "AA:00:00:00:00:05", "2026-10-14 13:00:00", -23.30000, -47.30000)
        obs(2, "AA:00:00:00:00:06", "2026-10-14 13:01:00", -23.50000, -47.50000, gps=0)
        # BLE na célula A, para testar filtro de tipo
        obs(1, "AA:00:00:00:00:07", "2026-10-14 12:00:30", -23.28000, -47.29000, tipo="BLE")
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_agrupa_por_celula_de_100m(self):
        dados = queries.cobertura(self.conn, types=["WIFI"])
        self.assertEqual(dados["total_celulas"], 3)      # A (3 leituras), vizinha, longe
        maior = dados["celulas"][0]                      # ordenado por n
        self.assertEqual((round(maior["lat"], 3), maior["n"]), (-23.28, 3))
        self.assertEqual(dados["obs"], 5)

    def test_faixas_de_densidade(self):
        dados = queries.cobertura(self.conn, types=["WIFI"])
        self.assertEqual(dados["por_faixa"]["2-5"], 1)   # a célula com 3
        self.assertEqual(dados["por_faixa"]["1"], 2)     # vizinha e a sessão b

    def test_filtro_por_tipo(self):
        wifi = queries.cobertura(self.conn, types=["WIFI"])
        todos = queries.cobertura(self.conn)
        self.assertEqual(todos["obs"], 6)                # inclui a linha BLE
        self.assertEqual(wifi["obs"], 5)

    def test_filtro_por_sessao(self):
        dados = queries.cobertura(self.conn, file_ids=[2])
        self.assertEqual(dados["total_celulas"], 1)
        self.assertEqual(dados["obs"], 1)

    def test_km_soma_trechos_por_sessao_e_corta_salto(self):
        dados = queries.cobertura(self.conn, types=["WIFI"])
        # sessão 1: 3 leituras na mesma célula + ~110 m até a vizinha
        self.assertAlmostEqual(dados["km"], 0.11, delta=0.03)
        # a sessão 2 começa ~2,2 km depois, mas NÃO se conecta à sessão 1:
        # se conectasse, o total passaria de 2 km
        self.assertLess(dados["km"], 0.5)
        self.assertEqual(dados["saltos_descartados"], 0)

    def test_area_cresce_com_numero_de_celulas(self):
        dados = queries.cobertura(self.conn, types=["WIFI"])
        # 3 células de ~111 m × ~102 m ≈ 0,034 km²
        self.assertAlmostEqual(dados["km2"], 0.034, delta=0.01)

    def test_selecao_vazia_nao_quebra(self):
        dados = queries.cobertura(self.conn, date_from="2030-01-01")
        self.assertEqual(dados["celulas"], [])
        self.assertEqual(dados["km"], 0)
        self.assertEqual(dados["km2"], 0.0)


if __name__ == "__main__":
    unittest.main()
