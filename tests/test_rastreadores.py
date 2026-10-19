"""Testes do detector de rastreadores (issue #1)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import db, queries  # noqa: E402

# pontos ~1 km a leste um do outro
PONTOS = [(-23.2800, -47.2900), (-23.2800, -47.2800),
          (-23.2800, -47.2700), (-23.2800, -47.2600), (-23.2800, -47.2500)]


class RastreadoresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("rastreadores")
        isolar_dados(Path(cls.tmp.name))
        cls.conn = db.connect()
        db.init_db(cls.conn)
        for i in range(1, 6):
            cls.conn.execute(
                "INSERT INTO files (id, name, sha256, imported_at, first_seen, obs_count) "
                "VALUES (?,?,?,?,?,1)",
                (i, f"sessao_{i}.log", f"sha{i}", "2026-10-14", f"2026-10-1{i} 12:00:00"))

        def obs(mac, sessao, quando, ponto, tipo="BLE", ssid=""):
            cls.conn.execute(
                """INSERT INTO observations
                   (file_id, mac, ssid, type, first_seen, channel, rssi, lat, lon, has_gps)
                   VALUES (?,?,?,?,?,0,-70,?,?,1)""",
                (sessao, mac, ssid, tipo, quando, ponto[0], ponto[1]))

        # A) acompanha o percurso: 4 sessões, 4 lugares diferentes, 3 dias
        for i in range(4):
            obs("AA:AA:AA:00:00:01", i + 1, f"2026-10-1{i + 1} 12:0{i}:00", PONTOS[i])
        # B) sempre no mesmo lugar (4 sessões, mesmo ponto): é aparelho fixo
        for i in range(4):
            obs("AA:AA:AA:00:00:02", i + 1, f"2026-10-1{i + 1} 12:0{i}:00", PONTOS[0])
        # C) 4 locais, mas tudo em uma hora só (não é "acompanhamento")
        for i in range(4):
            obs("AA:AA:AA:00:00:03", 1, f"2026-10-11 12:0{i}:00", PONTOS[i])
        # D) Wi-Fi no mesmo padrão de A: não é rastreador BLE
        for i in range(4):
            obs("AA:AA:AA:00:00:04", i + 1, f"2026-10-1{i + 1} 12:0{i}:00", PONTOS[i],
                tipo="WIFI", ssid="Rede Movel")
        # E) MAC inválido anunciado por aparelho quebrado
        for i in range(4):
            obs("00:00:00:00:00:00", i + 1, f"2026-10-1{i + 1} 12:0{i}:00", PONTOS[i])
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def macs(self, **kw):
        itens, _ = queries.rastreadores(self.conn, limit=100, **kw)
        return [i["mac"] for i in itens]

    def test_dispositivo_que_acompanha_entra(self):
        itens, total = queries.rastreadores(self.conn, minimo_sessoes=3, minimo_locais=3)
        self.assertEqual(total, 1)
        self.assertEqual(itens[0]["mac"], "AA:AA:AA:00:00:01")
        self.assertEqual(itens[0]["sessoes"], 4)
        self.assertEqual(itens[0]["locais"], 4)
        self.assertAlmostEqual(itens[0]["extensao_km"], 3.1, delta=0.4)

    def test_aparelho_fixo_no_mesmo_lugar_nao_entra(self):
        self.assertNotIn("AA:AA:AA:00:00:02", self.macs(minimo_sessoes=3, minimo_locais=3))

    def test_tudo_na_mesma_hora_nao_entra(self):
        # 4 locais, mas em 1 hora: é deslocamento pontual, não acompanhamento
        self.assertNotIn("AA:AA:AA:00:00:03", self.macs(minimo_sessoes=3, minimo_locais=3))

    def test_wifi_nao_entra(self):
        self.assertNotIn("AA:AA:AA:00:00:04", self.macs(minimo_sessoes=3, minimo_locais=3))

    def test_mac_invalido_nao_entra(self):
        self.assertNotIn("00:00:00:00:00:00", self.macs(minimo_sessoes=3, minimo_locais=3))

    def test_marcar_como_conhecido_tira_da_lista_e_volta(self):
        queries.marcar_conhecido(self.conn, "aa-aa-aa-00-00-01", "meu fone")
        self.assertNotIn("AA:AA:AA:00:00:01", self.macs(minimo_sessoes=3, minimo_locais=3))
        conhecidos = queries.conhecidos(self.conn)
        self.assertEqual(conhecidos[0]["mac"], "AA:AA:AA:00:00:01")
        self.assertEqual(conhecidos[0]["rotulo"], "meu fone")
        # "incluir_conhecidos" continua contando tudo (para a contagem da tela)
        _, total = queries.rastreadores(self.conn, minimo_sessoes=3, minimo_locais=3,
                                        incluir_conhecidos=True)
        self.assertEqual(total, 1)
        queries.esquecer_conhecido(self.conn, "AA:AA:AA:00:00:01")
        self.assertIn("AA:AA:AA:00:00:01", self.macs(minimo_sessoes=3, minimo_locais=3))

    def test_janela_de_datas(self):
        # só no último dia: uma sessão, não qualifica
        self.assertEqual(self.macs(minimo_sessoes=3, minimo_locais=3, de="2026-10-14"), [])
        self.assertEqual(self.macs(minimo_sessoes=3, minimo_locais=3, de="2026-10-10",
                                   ate="2026-10-16"), ["AA:AA:AA:00:00:01"])

    def test_limiar_mais_alto_esvazia(self):
        self.assertEqual(self.macs(minimo_sessoes=5, minimo_locais=5), [])


if __name__ == "__main__":
    unittest.main()
