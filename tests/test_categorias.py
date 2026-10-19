"""Testes da classificação em tipos de objeto (roteador, veículo, smart tag…)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import categorias, db, queries  # noqa: E402

CABECALHO_OUI = "Registry,Assignment,Organization Name,Organization Address\n"


class RegrasTests(unittest.TestCase):
    def test_roteador_de_operadora_pelo_nome(self):
        categoria, confianca, origem = categorias.classificar("WIFI", "00:11:22:33:44:55", "VIVOFIBRA-1A2B", "")
        self.assertEqual((categoria, confianca, origem), ("roteador", "alta", "ssid"))

    def test_celular_pelo_nome(self):
        self.assertEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "iPhone de Luiz", "")[0], "celular")
        self.assertEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "Galaxy A34 5G", "")[0], "celular")

    def test_smart_tag_pelo_nome(self):
        self.assertEqual(categorias.classificar("BLE", "00:11:22:33:44:55", "SmartTag", "")[0], "smart_tag")
        self.assertEqual(categorias.classificar("BLE", "00:11:22:33:44:55", "AirTag", "")[0], "smart_tag")

    def test_marca_dentro_de_outra_palavra_nao_conta(self):
        # "Claudio" contém "audi" e "Stile Papelaria" contém "tile": não são carro/tag
        self.assertNotEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "CLAUDINO", "")[0], "veiculo")
        self.assertNotEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "Stile Papelaria", "")[0], "smart_tag")

    def test_carro_com_underscore_no_nome(self):
        self.assertEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "VW_MIB_6343", "")[0], "veiculo")
        self.assertEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "Toyota_2477", "")[0], "veiculo")

    def test_impressora_por_direct(self):
        self.assertEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "DIRECT-8C-HP Laser 107w", "")[0], "impressora")

    def test_fabricante_especifico_tem_confianca_alta(self):
        categoria, confianca, origem = categorias.classificar("WIFI", "00:11:22:33:44:55", "", "TP-LINK TECHNOLOGIES CO.,LTD.")
        self.assertEqual((categoria, confianca, origem), ("roteador", "alta", "oui"))

    def test_fabricante_generico_tem_confianca_media(self):
        # Apple faz celular, computador, relógio e TV: o OUI só sugere
        self.assertEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "", "Apple, Inc."),
                         ("celular", "media", "oui"))

    def test_drone_pelo_nome_e_pelo_fabricante(self):
        # o próprio drone/controle anuncia o nome; a OUI da DJI cobre o resto
        self.assertEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "TELLO-8C4D2A", "")[0], "drone")
        self.assertEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "DJI-MavicMini-2A3F", "")[0], "drone")
        self.assertEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "",
                                               "SZ DJI TECHNOLOGY CO.,LTD")[0], "drone")
        self.assertEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "", "Parrot SA")[0], "drone")

    def test_palavra_comum_nao_vira_drone(self):
        # "Spark"/"Mavic" sem sufixo de série não são pista suficiente
        self.assertNotEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "Spark Net", "")[0], "drone")
        self.assertNotEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "Casa do Phantom", "")[0], "drone")

    def test_mac_aleatorio_sem_outra_pista(self):
        # 0x46 tem o bit localmente administrado ligado (BLE embaralhado)
        self.assertEqual(categorias.classificar("BLE", "46:1A:2B:3C:4D:5E", "", "")[0], "aleatorio")

    def test_desconhecido_quando_nada_bate(self):
        self.assertEqual(categorias.classificar("WIFI", "00:11:22:33:44:55", "", "Fabricante Estranho Ltda")[0],
                         "desconhecido")


class OuiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pasta_temporaria("oui")
        isolar_dados(Path(self.tmp.name))
        self.pasta = categorias.pasta_oui()
        self.pasta.mkdir(parents=True, exist_ok=True)
        (self.pasta / "ma-l.csv").write_text(
            CABECALHO_OUI + "MA-L,AABBCC,Fabricante Grande S.A.,rua 1\n", encoding="utf-8")
        (self.pasta / "ma-m.csv").write_text(
            CABECALHO_OUI + "MA-M,AABBCC1,Fabricante Medio,rua 2\n", encoding="utf-8")
        (self.pasta / "ma-s.csv").write_text(
            CABECALHO_OUI + "MA-S,AABBCC123,Fabricante Pequeno,rua 3\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_le_os_tres_registros(self):
        tabela = categorias.ler_oui()
        self.assertEqual(len(tabela), 3)
        self.assertEqual(tabela["AABBCC"], "Fabricante Grande S.A.")

    def test_prefixo_mais_especifico_vence(self):
        tabela = categorias.ler_oui()
        self.assertEqual(categorias.fabricante("AA:BB:CC:12:34:56", tabela), "Fabricante Pequeno")
        self.assertEqual(categorias.fabricante("AA:BB:CC:1F:34:56", tabela), "Fabricante Medio")
        self.assertEqual(categorias.fabricante("AA:BB:CC:FF:34:56", tabela), "Fabricante Grande S.A.")
        self.assertEqual(categorias.fabricante("11:22:33:44:55:66", tabela), "")


class CategorizarBancoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = pasta_temporaria("categorizar")
        isolar_dados(Path(cls.tmp.name))
        pasta = categorias.pasta_oui()
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "ma-l.csv").write_text(
            CABECALHO_OUI
            + "MA-L,000C29,Roteadores do Brasil Ltda,rua\n"
            + "MA-L,525400,Tesla Motors,rua\n"
            + "MA-L,DC4F22,Espressif Inc.,rua\n",
            encoding="utf-8")
        cls.conn = db.connect()
        db.init_db(cls.conn)
        # a observação tem FK para files: cria a sessão "dona" do ponto
        cls.conn.execute(
            "INSERT INTO files (id, name, sha256, imported_at, first_seen, obs_count) "
            "VALUES (1, 'sessao.log', 'x', '2026-10-14', '2026-10-14', 1)")
        cls.conn.commit()
        for mac, tipo, ssid in (
            ("00:0C:29:00:00:01", "WIFI", "AP do Escritorio"),
            ("52:54:00:00:00:02", "BLE", ""),                # OUI da Tesla
            ("DC:4F:22:00:00:03", "BLE", ""),                # Espressif
            ("46:1A:2B:3C:4D:5E", "BLE", ""),                # aleatório
            ("00:0C:29:00:00:09", "WIFI", "SmartTag"),        # nome vence o fabricante
        ):
            cls.conn.execute("INSERT INTO networks (mac, type, ssid, obs_count) VALUES (?,?,?,1)",
                             (mac, tipo, ssid))
        cls.conn.commit()
        cls.resultado = categorias.categorizar_redes(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_classificou_todas(self):
        self.assertEqual(self.resultado["redes"], 5)
        self.assertEqual(self.resultado["por_categoria"]["roteador"], 1)
        self.assertEqual(self.resultado["por_categoria"]["veiculo"], 1)
        self.assertEqual(self.resultado["por_categoria"]["devkit"], 1)
        self.assertEqual(self.resultado["por_categoria"]["aleatorio"], 1)
        self.assertEqual(self.resultado["por_categoria"]["smart_tag"], 1)

    def test_gravou_fabricante_e_confianca(self):
        linha = self.conn.execute("SELECT * FROM networks WHERE mac = '52:54:00:00:00:02'").fetchone()
        self.assertEqual(linha["vendor"], "Tesla Motors")
        self.assertEqual(linha["categoria"], "veiculo")
        self.assertEqual(linha["categoria_conf"], "alta")
        self.assertEqual(linha["categoria_origem"], "oui")

    def test_resumo_soma_as_redes(self):
        resumo = categorias.resumo_categorias(self.conn)
        self.assertEqual(sum(item["redes"] for item in resumo), 5)

    def test_filtro_do_mapa_por_categoria(self):
        self.conn.execute("""INSERT INTO observations
            (file_id, mac, ssid, type, first_seen, channel, rssi, lat, lon, has_gps)
            VALUES (1, '52:54:00:00:00:02', '', 'BLE', '2026-10-14 18:00:00', 0, -70,
                    -23.28, -47.28, 1)""")
        self.conn.commit()
        pontos = queries.map_points(self.conn, categoria="veiculo")
        self.assertEqual(len(pontos), 1)
        self.assertEqual(pontos[0]["mac"], "52:54:00:00:00:02")
        self.assertEqual(queries.map_points(self.conn, categoria="roteador"), [])


if __name__ == "__main__":
    unittest.main()
