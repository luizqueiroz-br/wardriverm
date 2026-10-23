"""Testes do modo ao vivo (issue #11) — sem hardware, com fonte de arquivo."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import csv_wigle, isolar_dados, pasta_temporaria  # noqa: E402
from wardrive import ao_vivo, db, parser  # noqa: E402


def _esperar(condicao, segundos: float = 10.0) -> bool:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        if condicao():
            return True
        time.sleep(0.05)
    return False


class ColetorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pasta_temporaria("aovivo")
        isolar_dados(Path(self.tmp.name))
        self.arquivo = Path(self.tmp.name) / "stream.log"
        self.arquivo.write_bytes(csv_wigle(12, prefixo="02:AA:BB:CC"))
        inicial = db.connect()
        db.init_db(inicial)
        inicial.close()
        # a conexão do setUp precisa ser fechada, senão o Windows trava o arquivo
        # e o diretório temporário não pode ser apagado
        self.coletor = ao_vivo.Coletor()

    def tearDown(self):
        self.coletor.parar()
        self.tmp.cleanup()

    def _stream_em_movimento(self, linhas: int = 12) -> None:
        """CSV com o carro andando ~11 m por linha (a fixture padrão quase não sai do lugar)."""
        cabecalho = ("WigleWifi-1.6,appRelease=teste,model=fake,release=1.0,device=fake,"
                     "display=none,board=fake,brand=teste\n"
                     "MAC,SSID,AuthMode,FirstSeen,Channel,Frequency,RSSI,CurrentLatitude,"
                     "CurrentLongitude,AltitudeMeters,AccuracyMeters,RCOIs,MfgrId,Type\n")
        corpo = []
        for i in range(linhas):
            corpo.append(
                f"02:AA:BB:CC:{i:02X}:01,Rede-{i},[WPA2_PSK],2026-10-14 12:{i:02d}:00,6,2437,"
                f"-{50 + i},{-23.2790 + i * 0.0001:.7f},-47.2870400,600,5.0,,,WIFI")
        self.arquivo.write_text(cabecalho + "\n".join(corpo) + "\n")

    def test_coleta_do_arquivo_e_grava_a_sessao(self):
        self._stream_em_movimento(12)
        self.coletor.iniciar(ao_vivo.FonteArquivo(self.arquivo), nome="teste-ao-vivo.log")
        assert _esperar(lambda: not self.coletor.ativo())
        estado = self.coletor.estado_atual()
        self.assertIsNone(estado["erro"])
        self.assertEqual(estado["obs"], 12)

        conn = db.connect()
        try:
            sessao = conn.execute("SELECT * FROM files WHERE name = 'teste-ao-vivo.log'").fetchone()
            self.assertIsNotNone(sessao)
            self.assertEqual(sessao["obs_count"], 12)
            self.assertEqual(sessao["source"], "ao-vivo")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 12)
            self.assertGreater(sessao["distancia_km"] or 0, 0)     # percurso calculado no fim
        finally:
            conn.close()

    def test_pontos_chegam_na_fila_antes_de_terminar(self):
        self.coletor.iniciar(ao_vivo.FonteArquivo(self.arquivo, atraso=0.02), nome="parcial.log")
        assert _esperar(lambda: len(self.coletor.eventos(0)) >= 3, 8), "nenhum ponto publicado"
        eventos = self.coletor.eventos(0)
        self.assertEqual(eventos[0]["seq"] >= 1, True)
        self.assertTrue(all("lat" in e and "lon" in e for e in eventos))
        marcador = eventos[-1]["seq"]
        self.assertTrue(_esperar(lambda: not self.coletor.ativo()))
        self.assertGreater(self.coletor.eventos(marcador)[0]["seq"], marcador)

    def test_linha_repetida_nao_duplica(self):
        original = csv_wigle(2).decode().splitlines()
        self.arquivo.write_text("\n".join(original + original[-2:]) + "\n")
        self.coletor.iniciar(ao_vivo.FonteArquivo(self.arquivo), nome="dup.log")
        assert _esperar(lambda: not self.coletor.ativo())
        estado = self.coletor.estado_atual()
        self.assertEqual(estado["obs"], 2)
        self.assertEqual(estado["ignoradas"], 2)

    def test_fonte_que_falha_vira_estado_de_erro(self):
        self.coletor.iniciar(ao_vivo.FonteArquivo(Path(self.tmp.name) / "nao-existe.log"),
                             nome="erro.log")
        assert _esperar(lambda: not self.coletor.ativo())
        estado = self.coletor.estado_atual()
        self.assertIn("nao-existe.log", estado["erro"] or "")

    def test_nao_inicia_duas_vezes(self):
        self.coletor.iniciar(ao_vivo.FonteArquivo(self.arquivo, atraso=0.05), nome="a.log")
        segundo = self.coletor.iniciar(ao_vivo.FonteArquivo(self.arquivo), nome="b.log")
        self.assertIn("andamento", segundo["erro"])
        self.coletor.parar()


class EndpointSSETests(unittest.TestCase):
    """O critério de aceite pede um cliente que consome 2 eventos e desconecta."""

    def setUp(self):
        self.tmp = pasta_temporaria("sse")
        isolar_dados(Path(self.tmp.name))
        inicial = db.connect()
        db.init_db(inicial)
        inicial.close()
        self.arquivo = Path(self.tmp.name) / "stream.log"
        cabecalho = ("WigleWifi-1.6,appRelease=x,model=x,release=1,device=x,display=x,board=x,brand=x\n"
                     "MAC,SSID,AuthMode,FirstSeen,Channel,Frequency,RSSI,CurrentLatitude,"
                     "CurrentLongitude,AltitudeMeters,AccuracyMeters,RCOIs,MfgrId,Type\n")
        corpo = [f"02:EE:0F:{i:02X}:00:01,R{i},[WPA2_PSK],2026-10-23 09:00:{i:02d},6,2437,-55,"
                 f"{-23.2790 + i * 0.0001:.7f},-47.2870,600,5.0,,,WIFI" for i in range(6)]
        self.arquivo.write_text(cabecalho + "\n".join(corpo) + "\n")
        ao_vivo.coletor.parar()
        ao_vivo.coletor.iniciar(ao_vivo.FonteArquivo(self.arquivo, atraso=0.05), nome="sse.log")

    def tearDown(self):
        ao_vivo.coletor.parar()
        # no Windows o SQLite pode soltar o arquivo um instante depois do close
        for _ in range(6):
            try:
                self.tmp.cleanup()
                break
            except PermissionError:
                time.sleep(0.25)

    def test_dois_eventos_e_desconecta(self):
        from wardrive import create_app

        app = create_app()
        cliente = app.test_client()
        resposta = cliente.get("/api/ao-vivo/eventos?desde=0")
        self.assertEqual(resposta.mimetype, "text/event-stream")
        eventos = []
        for bloco in resposta.response:
            texto = bloco.decode() if isinstance(bloco, bytes) else bloco
            if texto.startswith("data:"):
                eventos.append(texto)
            if len(eventos) >= 2:
                break
        resposta.close()
        self.assertGreaterEqual(len(eventos), 2)
        self.assertIn("pontos", eventos[0])
        self.assertIn("estado", eventos[0])


class ConsistenciaParserTests(unittest.TestCase):
    def test_converter_linha_concorda_com_parse_bytes(self):
        """A conversão por linha não pode divergir do parser de arquivo inteiro."""
        import csv
        import io

        dados = csv_wigle(6, prefixo="02:CC:DD:EE")
        parsed = parser.parse_bytes(dados, "x.log")
        linhas = dados.decode().splitlines()
        cabecalho = next(csv.reader([linhas[1]]))
        cmap = parser.build_column_map([(c or "").strip() for c in cabecalho])
        for indice, linha in enumerate(linhas[2:]):
            registro = parser.converter_linha(next(csv.reader([linha])), cmap)
            self.assertEqual(registro, parsed["rows"][indice])


if __name__ == "__main__":
    unittest.main()
