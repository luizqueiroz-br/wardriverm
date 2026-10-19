"""Testes das métricas de percurso (issue #4)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import db, queries  # noqa: E402

# 3 pontos: dois trechos de ~1,1 km na mesma rua (0,01° de latitude ≈ 1,11 km)
TRACK = [(-23.2800, -47.2900, "2026-10-14 12:00:00"),
         (-23.2900, -47.2900, "2026-10-14 12:05:00"),
         (-23.3000, -47.2900, "2026-10-14 12:10:00")]


class PercursoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("percurso")
        isolar_dados(Path(cls.tmp.name))
        cls.conn = db.connect()
        db.init_db(cls.conn)
        cls._sessao(1, "com_gps.log", TRACK, extra=[], obs_count=3)
        cls._sessao(2, "sem_gps.log", [], extra=[], obs_count=0, no_gps=2)
        cls._sessao(3, "salto.log", [(-23.2800, -47.2900, "2026-10-14 13:00:00"),
                                     (-23.2800, -47.2900, "2026-10-14 13:01:00"),
                                     # 50 km de "salto" de GPS no meio
                                     (-22.8300, -47.2900, "2026-10-14 13:02:00")],
                    extra=[], obs_count=3)

    @classmethod
    def _sessao(cls, file_id, nome, track, *, extra, obs_count, no_gps=0):
        cls.conn.execute(
            """INSERT INTO files (id, name, sha256, imported_at, first_seen, last_seen,
                                  obs_count, no_gps_count) VALUES (?,?,?,?,?,?,?,?)""",
            (file_id, nome, f"sha{file_id}", "2026-10-14",
             track[0][2] if track else None, track[-1][2] if track else None,
             obs_count, no_gps))
        for i, (lat, lon, quando) in enumerate(track, start=1):
            cls.conn.execute(
                """INSERT INTO observations (file_id, mac, ssid, type, first_seen, channel,
                       rssi, lat, lon, has_gps) VALUES (?,?,?,?,?,?,?,?,?,1)""",
                (file_id, f"AA:BB:CC:00:00:{i:02d}", "Rede", "WIFI", quando, 6, -60, lat, lon))
        # sessão sem GPS: linhas existem, mas sem coordenada
        for i in range(no_gps):
            cls.conn.execute(
                """INSERT INTO observations (file_id, mac, ssid, type, first_seen, channel,
                       rssi, lat, lon, has_gps) VALUES (?,?,?,?,?,?,?,NULL,NULL,0)""",
                (file_id, f"AA:BB:CC:11:00:{i:02d}", "SemGps", "WIFI",
                 f"2026-10-14 14:0{i}:00", 1, -70))
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_distancia_de_tres_pontos(self):
        r = queries.stats_percurso(self.conn, 1)
        # 0,02° de latitude ≈ 2,22 km
        self.assertAlmostEqual(r["distancia_km"], 2.22, delta=0.06)
        self.assertEqual(r["pontos"], 3)

    def test_duracao_e_velocidade(self):
        r = queries.stats_percurso(self.conn, 1)
        self.assertEqual(r["duracao_s"], 600)                 # 12:00 → 12:10
        self.assertAlmostEqual(r["velocidade_kmh"], 13.3, delta=0.6)   # 2,22 km em 10 min
        self.assertGreater(r["redes_por_km"], 0)

    def test_salto_de_gps_nao_conta(self):
        r = queries.stats_percurso(self.conn, 3)
        # só o trecho de 60 s no mesmo ponto entra; o "salto" de 50 km é descartado
        self.assertLess(r["distancia_km"], 0.05)
        self.assertEqual(r["saltos_descartados"], 1)

    def test_sessao_sem_gps_fica_vazia(self):
        r = queries.stats_percurso(self.conn, 2)
        self.assertIsNone(r["distancia_km"])
        self.assertIsNone(r["velocidade_kmh"])
        self.assertEqual(r["pontos"], 0)
        # e não é marcada como calculada (a tela mostra "—")
        self.assertIsNone(self.conn.execute(
            "SELECT distancia_km FROM files WHERE id = 2").fetchone()[0])

    def test_grava_nas_colunas_do_arquivo(self):
        queries.stats_percurso(self.conn, 1)
        linha = self.conn.execute(
            "SELECT distancia_km, duracao_s, velocidade_kmh FROM files WHERE id = 1").fetchone()
        self.assertIsNotNone(linha["distancia_km"])
        self.assertEqual(linha["duracao_s"], 600)

    def test_progresso_geral_soma_so_quem_tem_gps(self):
        queries.recalcular_percursos(self.conn)
        total = queries.progresso_geral(self.conn)
        self.assertEqual(total["sessoes"], 2)          # 1 e 3; a 2 não tem GPS
        self.assertGreaterEqual(total["km"], 2.2)
        self.assertGreater(total["velocidade_kmh"], 0)

    def test_recalcular_nao_repete_sessao_sem_gps(self):
        queries.recalcular_percursos(self.conn)
        resultado = queries.recalcular_percursos(self.conn)   # segunda passada
        self.assertEqual(resultado["sessoes"], 0)


if __name__ == "__main__":
    unittest.main()
