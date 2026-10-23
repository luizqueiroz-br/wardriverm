"""Testes da consulta ao WiGLE (issue #9)."""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import ESTADO, ServidorFalso, isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import config, db, wigle  # noqa: E402

MAC = "98:2A:0A:69:57:FB"
CRED = {"name": "usuario_teste", "token": "token_de_teste"}


class WigleConsultaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("wigle")
        isolar_dados(Path(cls.tmp.name))
        cls.servidor = ServidorFalso()
        config.save_secrets({"wigle_api_name": "usuario_teste",
                             "wigle_api_token": "token_de_teste"})
        cls.conn = db.connect()
        db.init_db(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def setUp(self):
        ESTADO.reiniciar()
        self.conn.execute("DELETE FROM wigle_consultas")
        self.conn.commit()

    def _resposta_global(self):
        return [{
            "netid": MAC, "ssid": "Rede Conhecida", "trilat": -23.28, "trilong": -47.29,
            "firsttime": "2019-05-01T12:00:00.000Z", "lasttime": "2026-09-30T08:00:00.000Z",
            "channel": 11, "encryption": "WPA2", "country": "BR",
            "region": "SP", "city": "Itu",
        }]

    def test_rede_conhecida(self):
        ESTADO.redes_globais[MAC] = self._resposta_global()
        r = wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED)
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["encontrada"])
        self.assertEqual(r["total"], 1)
        self.assertEqual(r["resultados"][0]["cidade"], "Itu")
        self.assertEqual(r["resultados"][0]["canal"], 11)

    def test_rede_desconhecida_e_informacao(self):
        r = wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED)
        self.assertEqual(r["status"], "nao_encontrada")
        self.assertFalse(r["encontrada"])
        self.assertEqual(r["total"], 0)

    def test_chave_invalida(self):
        ESTADO.modo_busca = "401"
        r = wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED)
        self.assertEqual(r["status"], "erro")
        self.assertEqual(r["http_status"], 401)
        self.assertIn("inválida", r["erro"])

    def test_cota_estourada(self):
        ESTADO.modo_busca = "429"
        r = wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED)
        self.assertEqual(r["status"], "erro")
        self.assertEqual(r["http_status"], 429)
        self.assertIn("Cota", r["erro"])

    def test_erro_de_credencial_nao_vira_cache(self):
        ESTADO.modo_busca = "401"
        wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED)
        self.assertEqual(len(ESTADO.consultas), 1)
        ESTADO.modo_busca = "ok"
        ESTADO.redes_globais[MAC] = self._resposta_global()
        r = wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED)
        self.assertTrue(r["encontrada"], "a segunda tentativa precisa bater na API de novo")
        self.assertEqual(len(ESTADO.consultas), 2)

    def test_cache_evita_gastar_cota(self):
        ESTADO.redes_globais[MAC] = self._resposta_global()
        primeira = wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED)
        segunda = wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED)
        self.assertFalse(primeira["cache"])
        self.assertTrue(segunda["cache"])
        self.assertEqual(len(ESTADO.consultas), 1, "cache válido não pode consultar de novo")
        self.assertEqual(segunda["total"], 1)

    def test_atualizar_forca_nova_consulta(self):
        ESTADO.redes_globais[MAC] = self._resposta_global()
        wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED)
        ESTADO.redes_globais[MAC] = self._resposta_global() + self._resposta_global()
        r = wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED, forcar=True)
        self.assertFalse(r["cache"])
        self.assertEqual(len(ESTADO.consultas), 2)
        self.assertEqual(r["total"], 2)

    def test_cache_vencido_consulta_de_novo(self):
        ESTADO.redes_globais[MAC] = self._resposta_global()
        wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED)
        antigo = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute("UPDATE wigle_consultas SET consultado_em = ? WHERE mac = ?", (antigo, MAC))
        self.conn.commit()
        r = wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED)
        self.assertFalse(r["cache"])
        self.assertEqual(len(ESTADO.consultas), 2)

    def test_sem_chave_nao_chama_a_api(self):
        r = wigle.consultar(self.conn, MAC, base=self.servidor.base, creds={"name": "", "token": ""})
        self.assertEqual(r["status"], "erro")
        self.assertIn("secrets.json", r["erro"])
        self.assertEqual(len(ESTADO.consultas), 0)

    def test_chave_nao_aparece_no_resultado(self):
        ESTADO.redes_globais[MAC] = self._resposta_global()
        r = wigle.consultar(self.conn, MAC, base=self.servidor.base, creds=CRED)
        texto = json.dumps(r, ensure_ascii=False)
        self.assertNotIn("token_de_teste", texto)
        self.assertNotIn("Authorization", texto)


if __name__ == "__main__":
    unittest.main()
