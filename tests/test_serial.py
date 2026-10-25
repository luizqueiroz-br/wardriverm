"""Testes do pull serial do cartão SD (issue #5) — sem hardware."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import csv_wigle, isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import db, dispositivo  # noqa: E402

LISTAGEM = [
    "marauder> sd list",
    "Files on SD:",
    "[SD] wardrive_37.log 3321",
    "[SD] wardrive_19.log 2410",
    "[SD] velho.csv 900",
    "marauder>",
]

CONTEUDO = {
    "wardrive_37.log": csv_wigle(2, prefixo="AA:BB:CC:DD").decode(),
    "wardrive_19.log": csv_wigle(2, prefixo="AA:BB:CC:EE").decode(),
    "velho.csv": csv_wigle(1, prefixo="FF:EE:DD:CC").decode(),
}


class PortaFalsa(dispositivo.Porta):
    """Faz o papel do pyserial: responde conforme o comando que recebeu."""

    def __init__(self, listagem=None, conteudos=None, falhar_ao_abrir=False):
        super().__init__("COM-FALSA")
        self.listagem = LISTAGEM if listagem is None else listagem
        self.conteudos = CONTEUDO if conteudos is None else conteudos
        self.falhar_ao_abrir = falhar_ao_abrir
        self.enviados: list[str] = []
        self._fila: list[str] = []
        self.aberta = False

    def abrir(self):
        if self.falhar_ao_abrir:
            raise dispositivo.ErroDispositivo("Não consegui abrir a porta COM-FALSA: acesso negado.")
        self.aberta = True

    def fechar(self):
        self.aberta = False

    def enviar(self, texto):
        self.enviados.append(texto)
        if texto.startswith("sd list"):
            self._fila = list(self.listagem)
        else:
            nome = texto.split()[-1]
            corpo = self.conteudos.get(nome, "")
            self._fila = [f"sd read {nome}"] + corpo.splitlines() + [""]

    def ler_ate_silencio(self, silencio=2.0, max_linhas=50000, limite=120.0):
        fila, self._fila = self._fila, []
        return fila


class ParseListagemTests(unittest.TestCase):
    def test_formatos_que_o_firmware_usa(self):
        linhas = [
            "wardrive_37.log",
            "12345 wardrive_19.log",
            "FILE: outro.csv (12 KB)",
            "[SD] quarto.log 900",
            "sem arquivo aqui",
        ]
        arquivos = {a["nome"]: a["tamanho"] for a in dispositivo.parse_listagem(linhas)}
        self.assertEqual(arquivos["wardrive_37.log"], None)
        self.assertEqual(arquivos["wardrive_19.log"], 12345)
        self.assertEqual(arquivos["outro.csv"], 12 * 1024)
        self.assertEqual(arquivos["quarto.log"], 900)

    def test_listagem_do_marauder_v118(self):
        """`ls /` responde `nome<TAB>tamanho`, com o eco `#ls /` e o prompt no fim."""
        saida = ["#ls /", "System Volume Information\t0", "wardrive_0.log\t951",
                 "pingscan_0.log\t86", "> "]
        arquivos = {a["nome"]: a["tamanho"] for a in dispositivo.parse_listagem(saida)}
        self.assertEqual(arquivos, {"wardrive_0.log": 951, "pingscan_0.log": 86})

    def test_ignora_extensao_desconhecida(self):
        self.assertEqual(dispositivo.parse_listagem(["leia.txt.bak", "x.bin 100"]), [])


class PullTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pasta_temporaria("serial")
        isolar_dados(Path(self.tmp.name))
        self.destino = Path(self.tmp.name) / "puxados"
        self.conn = db.connect()
        db.init_db(self.conn)
        self.conn.execute("DELETE FROM cartao_arquivos")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_lista_e_baixa_so_texto(self):
        porta = PortaFalsa()
        r = dispositivo.puxar(self.conn, "COM-FALSA", self.destino, importar=False, porta_obj=porta)
        self.assertIsNone(r["erro"])
        self.assertEqual(sorted(r["baixados"]), ["velho.csv", "wardrive_19.log", "wardrive_37.log"])
        self.assertTrue((self.destino / "wardrive_37.log").exists())
        self.assertEqual(porta.enviados[0], "sd list")

    def test_nao_rebaixa_o_que_ja_esta_no_disco(self):
        porta = PortaFalsa()
        dispositivo.puxar(self.conn, "COM-FALSA", self.destino, importar=False, porta_obj=porta)
        segunda = PortaFalsa()
        r = dispositivo.puxar(self.conn, "COM-FALSA", self.destino, importar=False, porta_obj=segunda)
        self.assertEqual(r["baixados"], [])
        self.assertEqual(len(r["ignorados"]), 3)
        self.assertEqual(len(segunda.enviados), 1, "só o sd list deve ser enviado")

    def test_arquivo_maior_no_cartao_baixa_de_novo(self):
        porta = PortaFalsa()
        dispositivo.puxar(self.conn, "COM-FALSA", self.destino, importar=False, porta_obj=porta)
        # o firmware agora diz que o arquivo cresceu
        porta2 = PortaFalsa(listagem=["[SD] wardrive_37.log 9999", "[SD] wardrive_19.log 2410"])
        r = dispositivo.puxar(self.conn, "COM-FALSA", self.destino, importar=False, porta_obj=porta2)
        self.assertEqual(r["baixados"], ["wardrive_37.log"])

    def test_porta_indisponivel_vira_mensagem(self):
        porta = PortaFalsa(falhar_ao_abrir=True)
        r = dispositivo.puxar(self.conn, "COM9", self.destino, porta_obj=porta)
        self.assertIn("acesso negado", r["erro"])
        self.assertEqual(r["baixados"], [])

    def test_firmware_mudo_explica_o_que_fazer(self):
        porta = PortaFalsa(listagem=["marauder>", "marauder>"])
        r = dispositivo.puxar(self.conn, "COM5", self.destino, porta_obj=porta)
        self.assertIn("--diagnostico", r["erro"])

    def test_firmware_que_so_lista_nao_grava_arquivo_falso(self):
        """Marauder v1.18: `sd read` responde só o eco (`#sd read …`) e o prompt.

        Sem tratar isso, o app grava 29 bytes de lixo e chama de "baixado" — foi
        o que aconteceu no teste com o aparelho de verdade.
        """
        class PortaSemLeitura(PortaFalsa):
            def enviar(self, texto):
                self.enviados.append(texto)
                if texto.startswith("sd list") or texto.startswith("ls"):
                    self._fila = ["wardrive_0.log\t951"]
                else:
                    nome = texto.split()[-1]
                    self._fila = [f"#{texto}", "> "]

        porta = PortaSemLeitura()
        r = dispositivo.puxar(self.conn, "COM3", self.destino, importar=False, porta_obj=porta)
        self.assertTrue(r["sem_leitura"])
        self.assertIn("não expõe leitura", r["erro"])
        self.assertEqual(r["baixados"], [])
        self.assertFalse((self.destino / "wardrive_0.log").exists())

    def test_importa_o_que_baixou(self):
        porta = PortaFalsa()
        r = dispositivo.puxar(self.conn, "COM-FALSA", self.destino, importar=True, porta_obj=porta)
        self.assertIsNotNone(r["importacao"])
        self.assertGreaterEqual(r["importacao"]["novos"], 2)
        total = self.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0]
        self.assertEqual(total, 5)          # 2 + 2 + 1 linhas nos três arquivos válidos

    def test_csv_do_wigle_continua_valido(self):
        """O conteúdo baixado é linha a linha: um CSV real atravessa sem estrago."""
        amostra = csv_wigle(3)
        esperado = amostra.decode().splitlines()
        porta = PortaFalsa(conteudos={"velho.csv": amostra.decode()})
        dispositivo.puxar(self.conn, "COM-FALSA", self.destino, importar=False, porta_obj=porta)
        baixado = (self.destino / "velho.csv").read_text().splitlines()
        self.assertEqual(baixado, esperado)


if __name__ == "__main__":
    unittest.main()
