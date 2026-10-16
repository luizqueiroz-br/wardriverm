"""Infraestrutura comum aos testes: isola os dados em pasta temporária e
sobe um servidor de mentira que imita WiGLE e WDGoWars.

O servidor falso existe para verificar o caminho de upload de ponta a ponta sem
tocar nos serviços reais: ele responde igual às APIs documentadas, inclusive os
casos ruins (429 de fila, 409 de arquivo repetido, 413/524 de lote grande).
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wardrive import config  # noqa: E402


def isolar_dados(temporario: Path) -> None:
    """Faz o aplicativo usar uma pasta temporária em vez de data/ do projeto."""
    config.DATA_DIR = temporario
    config.EXPORT_DIR = temporario / "exports"
    config.INBOX_DIR = temporario / "inbox"
    config.DB_PATH = temporario / "wardrive.db"
    config.SETTINGS_PATH = temporario / "settings.json"
    config.SECRETS_PATH = temporario / "secrets.json"
    config.SECRET_KEY_PATH = temporario / "flask_secret.key"
    config.ensure_dirs()


def limpar_dados(conn) -> None:
    """Zera as tabelas para que cada teste comece do zero."""
    for tabela in ("obs_uploads", "observations", "networks", "export_chunks", "exports",
                   "uploads", "files", "events"):
        conn.execute(f"DELETE FROM {tabela}")


def pasta_temporaria(nome: str) -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix=f"wardrive-teste-{nome}-")


def csv_wigle(linhas: int, prefixo: str = "AA:BB:CC:DD", inicio: int = 0) -> bytes:
    partes = [
        "WigleWifi-1.6,appRelease=teste-1.0,model=FakeDevice,release=1.0,device=fake,"
        "display=none,board=fake,brand=Testes",
        "MAC,SSID,AuthMode,FirstSeen,Channel,Frequency,RSSI,CurrentLatitude,CurrentLongitude,"
        "AltitudeMeters,AccuracyMeters,RCOIs,MfgrId,Type",
    ]
    for i in range(linhas):
        n = i + inicio
        mac = f"{prefixo}:{(n // 256) % 256:02X}:{n % 256:02X}"
        partes.append(
            f"{mac},Rede-{n},[WPA2_PSK],"
            f"2026-10-14 {(i // 60) % 24:02d}:{i % 60:02d}:00,6,2437,-5{i % 10},"
            f"-23.279{200 + i:04d},-47.287{400 + i:04d},600,5.0,,,WIFI"
        )
    return ("\n".join(partes) + "\n").encode("utf-8")


def parse_multipart(corpo: bytes, boundary: bytes) -> dict:
    partes: dict = {}
    for bloco in corpo.split(b"--" + boundary):
        if not bloco or bloco.strip() in (b"", b"--"):
            continue
        cabecalho, _, dados = bloco.partition(b"\r\n\r\n")
        dados = dados.rstrip(b"\r\n")
        nome_arquivo = re.search(rb'filename="([^"]*)"', cabecalho)
        nome_campo = re.search(rb'name="([^"]*)"', cabecalho)
        if nome_arquivo:
            partes["file"] = dados
            partes["filename"] = nome_arquivo.group(1).decode("utf-8", "replace")
        elif nome_campo:
            partes[nome_campo.group(1).decode("utf-8", "replace")] = dados.decode("utf-8", "replace")
    return partes


def contar_linhas_csv(corpo: bytes) -> int:
    """Conta as linhas de dados (ignora o pre-header e o cabeçalho)."""
    texto = corpo.decode("utf-8", "replace")
    linhas = [l for l in texto.splitlines()[2:] if l.strip()]
    return len(linhas)


class EstadoFalso:
    """Estado compartilhado do servidor de mentira entre os testes."""

    def __init__(self):
        self.wigle_429_restante = 0
        self.wdg_429_restante = 0
        self.wdg_limite_linhas = 0          # acima disso responde 524 (testa a divisão do lote)
        self.wigle_limite_linhas = 0        # acima disso responde 413
        self.recebidos: list[dict] = []
        self.arquivos_wdg: set[str] = set()
        self.transid = 0
        self.credencial_wigle = "usuario_teste:token_de_teste"
        self.credencial_wdg = "b" * 64
        self.wigle_estilo_resultado = "lista"   # lista | objeto | string | sem_success
        self.travar = threading.Lock()

    def reiniciar(self):
        with self.travar:
            self.wigle_429_restante = 0
            self.wdg_429_restante = 0
            self.wdg_limite_linhas = 0
            self.wigle_limite_linhas = 0
            self.recebidos = []
            self.arquivos_wdg = set()
            self.wigle_estilo_resultado = "lista"

    @staticmethod
    def decodificar_basic(cabecalho: str) -> str:
        import base64
        try:
            return base64.b64decode(cabecalho.replace("Basic ", "", 1)).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001 - cabeçalho malformado conta como credencial inválida
            return ""


ESTADO = EstadoFalso()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silencia o log do http.server
        return

    # ------------------------------------------------------------------ util
    def _responder(self, codigo: int, payload: dict | None = None, extra_headers=None):
        corpo = json.dumps(payload or {}).encode("utf-8") if payload is not None else b""
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        for chave, valor in (extra_headers or {}).items():
            self.send_header(chave, valor)
        self.end_headers()
        if corpo:
            self.wfile.write(corpo)

    def _resposta_wigle(self, transid: str, linhas: int) -> dict:
        """Resposta de upload do WiGLE, na forma escolhida por ESTADO.

        `real` reproduz o corpo que o serviço devolveu de verdade em 16/10/2026:
        `results` é um objeto e o transid vem em `results.transids[0].transId`.
        """
        registro = {"transid": transid, "networks": linhas, "message": "ok"}
        estilo = ESTADO.wigle_estilo_resultado
        if estilo == "real":
            return {
                "success": True, "warning": None,
                "results": {"timeTaken": "0", "filesize": 975,
                            "filename": f"1792161846_{transid}.csv",
                            "transids": [{"file": f"1792161846_{transid}.csv",
                                          "size": 975, "transId": transid}]},
                "observer": "tester",
            }
        if estilo == "objeto":
            return {"success": True, "results": {transid: registro}}
        if estilo == "objeto_simples":
            return {"success": True, "results": registro}
        if estilo == "string":
            return {"success": True, "results": "processando"}
        if estilo == "sem_success":
            return {"results": [registro]}
        return {"success": True, "results": [registro]}

    def _ler_corpo(self) -> tuple[bytes, dict]:
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else b""
        tipo = self.headers.get("Content-Type", "")
        boundary = b""
        if "boundary=" in tipo:
            boundary = tipo.split("boundary=", 1)[1].strip().strip('"').encode()
        return corpo, parse_multipart(corpo, boundary) if boundary else {}

    # ------------------------------------------------------------------- GET
    def do_GET(self):
        if self.path.startswith("/api/v2/profile/uploads"):
            cabecalho = self.headers.get("Authorization", "")
            if not cabecalho.startswith("Basic "):
                return self._responder(401, {"success": False, "message": "no auth"})
            if ESTADO.decodificar_basic(cabecalho) != ESTADO.credencial_wigle:
                return self._responder(401, {"success": False, "message": "Authentication required"})
            return self._responder(200, {
                "success": True,
                "results": [{
                    "transid": "20261014-00001", "status": "DONE", "fileName": "wardrive_9.log",
                    "networks": 2246, "fileSize": 223637,
                }],
            })
        if self.path.startswith("/api/me"):
            if not self.headers.get("X-API-Key"):
                return self._responder(401, {"error": "Invalid API key"})
            if self.headers.get("X-API-Key") != ESTADO.credencial_wdg:
                return self._responder(401, {"error": "Invalid API key"})
            return self._responder(200, {
                "ok": True, "username": "tester", "gang": "Testes", "country": "BR",
                "wifi": 1234, "ble": 5678, "aircraft": 0, "mesh": 0, "total": 6912,
                "recent_today": 100, "recent_7d": 900, "badges": ["wifi_100"],
            })
        return self._responder(404, {"error": "not found"})

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        corpo, campos = self._ler_corpo()
        dados = campos.get("file", b"")
        nome = campos.get("filename", "upload.csv")
        linhas = contar_linhas_csv(dados) if dados else 0

        if self.path.startswith("/api/v2/file/upload"):
            cabecalho = self.headers.get("Authorization", "")
            if not cabecalho.startswith("Basic "):
                return self._responder(401, {"success": False, "message": "Authentication required"})
            if ESTADO.decodificar_basic(cabecalho) != ESTADO.credencial_wigle:
                return self._responder(401, {"success": False, "message": "Authentication required"})
            with ESTADO.travar:
                if ESTADO.wigle_429_restante > 0:
                    ESTADO.wigle_429_restante -= 1
                    return self._responder(429, {"success": False, "message": "too many requests"},
                                           {"Retry-After": "1"})
                ESTADO.transid += 1
                transid = f"20261014-{ESTADO.transid:05d}"
                ESTADO.recebidos.append({"destino": "wigle", "arquivo": nome, "linhas": linhas,
                                         "donate": campos.get("donate")})
                if ESTADO.wigle_limite_linhas and linhas > ESTADO.wigle_limite_linhas:
                    return self._responder(413, {"success": False,
                                                 "message": "payload too large"})
            return self._responder(200, self._resposta_wigle(transid, linhas))

        if self.path.startswith("/endpoint/upload-csv") or self.path.startswith("/api/upload-csv"):
            if not self.headers.get("X-API-Key"):
                return self._responder(401, {"error": "Invalid API key"})
            if self.headers.get("X-API-Key") != ESTADO.credencial_wdg:
                return self._responder(401, {"error": "Invalid API key"})
            if not dados or not dados.lstrip().startswith(b"WigleWifi-"):
                return self._responder(400, {"error": "Invalid data format"})
            with ESTADO.travar:
                if ESTADO.wdg_429_restante > 0:
                    ESTADO.wdg_429_restante -= 1
                    return self._responder(429, {"error": "Another upload is already being processed",
                                                 "retry_after": 1})
                if ESTADO.wdg_limite_linhas and linhas > ESTADO.wdg_limite_linhas:
                    return self._responder(524, {})
                if nome in ESTADO.arquivos_wdg:
                    return self._responder(409, {"error": "duplicate_upload",
                                                 "duplicate_at": "2026-10-14 21:00:00"})
                ESTADO.arquivos_wdg.add(nome)
                ESTADO.recebidos.append({"destino": "wdgwars", "arquivo": nome, "linhas": linhas})
            return self._responder(200, {
                "ok": True, "imported": linhas, "captured": 0, "updated": 0, "duplicates": 0,
                "no_gps": 0, "bad_rows": 0, "cooldown": 0, "merged_samples": linhas,
                "total": 48421278,
            })

        return self._responder(404, {"error": "not found"})


class ServidorFalso:
    """Sobe o servidor em uma thread e devolve a URL base."""

    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        host, porta = self.httpd.server_address[:2]
        return f"http://{host}:{porta}"

    def parar(self):
        self.httpd.shutdown()
        self.httpd.server_close()
