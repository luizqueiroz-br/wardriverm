"""Modo ao vivo: mapa pintando enquanto o carro anda.

Três peças: uma **fonte** de linhas (serial, ou um arquivo que se repete para
demonstrar), um **coletor** que converte e grava em lotes curtos, e uma **fila de
pontos** que o navegador consome por SSE (`text/event-stream`).

Regras que vieram do diagnóstico e valem aqui:

- ler serial **só** neste worker, nunca no processo do Flask;
- gravar em lotes (50 linhas ou 2 s), em transação curta;
- dedupe por `mac + first_seen` (o mesmo pacote pode chegar duas vezes);
- queda da fonte não derruba o serviço: vira estado "erro" na tela.
"""
from __future__ import annotations

import csv
import threading
import time
from collections import deque
from datetime import datetime

from . import db, parser

LIMITE_PONTOS_MEMORIA = 5000
LOTE_LINHAS = 50
LOTE_SEGUNDOS = 2.0


# --------------------------------------------------------------------------- #
# Fontes de linha
# --------------------------------------------------------------------------- #
class FonteArquivo:
    """Reproduz um arquivo linha a linha — é como o modo ao vivo é testado."""

    def __init__(self, caminho, *, atraso: float = 0.0):
        self.caminho = str(caminho)
        self.atraso = atraso

    def abrir(self):
        return None

    def fechar(self):
        return None

    def linhas(self):
        with open(self.caminho, "r", encoding="utf-8", errors="replace") as arquivo:
            for linha in arquivo:
                if self.atraso:
                    time.sleep(self.atraso)
                yield linha.rstrip("\r\n")


class FonteSerial:
    """Lê a serial continuamente; o `Porta` é o mesmo do pull de arquivos."""

    def __init__(self, porta_obj, *, silencio: float = 1.0):
        self.porta = porta_obj
        self.silencio = silencio

    def abrir(self):
        self.porta.abrir()

    def fechar(self):
        self.porta.fechar()

    def linhas(self):
        while True:
            bloco = self.porta.ler_ate_silencio(self.silencio, limite=5)
            if not bloco:
                yield ""            # sinaliza "nada agora" para o coletor respirar
                continue
            for linha in bloco:
                yield linha


# --------------------------------------------------------------------------- #
# Coletor
# --------------------------------------------------------------------------- #
class Coletor:
    """Um coletor por vez; guarda estado e os últimos pontos em memória."""

    def __init__(self):
        self._trava = threading.Lock()
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self._pontos: deque = deque(maxlen=LIMITE_PONTOS_MEMORIA)
        self._sequencia = 0
        self.estado = {"ativo": False, "erro": None, "fonte": "", "sessao": "",
                       "obs": 0, "redes": 0, "ignoradas": 0, "iniciado_em": None,
                       "arquivo": None}

    # ------------------------------------------------------------------ API
    def ativo(self) -> bool:
        return bool(self.estado["ativo"])

    def iniciar(self, fonte, *, nome: str | None = None) -> dict:
        if self.ativo():
            return {"erro": "já existe uma coleta ao vivo em andamento"}
        nome = nome or f"ao-vivo-{datetime.now():%Y%m%d-%H%M%S}.log"
        self._parar.clear()
        self._pontos.clear()
        with self._trava:
            self.estado.update({"ativo": True, "erro": None, "fonte": getattr(fonte, "caminho", None)
                                or getattr(getattr(fonte, "porta", None), "nome", "serial"),
                                "sessao": nome, "obs": 0, "redes": 0, "ignoradas": 0,
                                "iniciado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        self._thread = threading.Thread(target=self._laco, args=(fonte, nome), daemon=True,
                                        name="ao-vivo")
        self._thread.start()
        return {"ok": True, "sessao": nome}

    def parar(self) -> dict:
        self._parar.set()
        if self._thread is not None:
            self._thread.join(timeout=8)
        with self._trava:
            self.estado["ativo"] = False
        return dict(self.estado)

    def estado_atual(self) -> dict:
        with self._trava:
            dados = dict(self.estado)
            dados["sequencia"] = self._sequencia
        return dados

    def eventos(self, desde: int = 0, limite: int = 500) -> list[dict]:
        with self._trava:
            return [p for p in list(self._pontos)[-limite:] if p["seq"] > desde]

    # --------------------------------------------------------------- interno
    def _laco(self, fonte, nome: str):
        cmap = None
        colunas: list[str] = []
        lote: list[dict] = []
        ultimo_envio = time.monotonic()
        vistos: set[tuple] = set()
        file_id = None
        conn = None
        try:
            fonte.abrir()
            conn = db.connect()
            file_id = self._abrir_sessao(conn, nome)
            for linha in fonte.linhas():
                if self._parar.is_set():
                    break
                if not linha.strip():
                    continue
                if cmap is None:
                    cabecalho = next(csv.reader([linha]), [])
                    mapa = parser.build_column_map([(c or "").strip() for c in cabecalho])
                    if "mac" not in mapa:
                        continue                     # pre-header ou lixo do console
                    cmap, colunas = mapa, [(c or "").strip() for c in cabecalho]
                    continue
                registro = parser.converter_linha(next(csv.reader([linha]), []), cmap)
                if registro is None:
                    with self._trava:
                        self.estado["ignoradas"] += 1
                    continue
                chave = (registro["mac"], registro["first_seen"])
                if chave in vistos:
                    with self._trava:
                        self.estado["ignoradas"] += 1
                    continue
                vistos.add(chave)
                lote.append(registro)
                self._publicar(registro)
                agora = time.monotonic()
                if len(lote) >= LOTE_LINHAS or (lote and agora - ultimo_envio >= LOTE_SEGUNDOS):
                    self._gravar(conn, file_id, lote)
                    lote = []
                    ultimo_envio = agora
            if lote:
                self._gravar(conn, file_id, lote)
            if file_id:
                from . import queries
                queries.stats_percurso(conn, file_id)
        except Exception as erro:                     # nunca deixa a thread morrer calada
            with self._trava:
                self.estado["erro"] = str(erro)[:300]
        finally:
            try:
                fonte.fechar()
            except Exception:
                pass
            if conn is not None:
                conn.close()
            with self._trava:
                self.estado["ativo"] = False

    def _abrir_sessao(self, conn, nome: str) -> int:
        """Cria a linha em `files` já no início: a sessão existe antes de terminar."""
        existente = conn.execute("SELECT id FROM files WHERE name = ?", (nome,)).fetchone()
        if existente:
            return existente["id"]
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = conn.execute(
            """INSERT INTO files (name, path, sha256, size, format_version, preheader, columns,
                                  has_frequency, obs_count, source, imported_at)
               VALUES (?,?,?,?,?,?,?,?,0,'ao-vivo',?)""",
            (nome, None, f"ao-vivo:{nome}", 0, "WigleWifi-1.6",
             "WigleWifi-1.6,appRelease=ao-vivo,model=serial,release=1.0,device=esp32,"
             "display=none,board=esp32,brand=ao-vivo", "", 0, agora))
        conn.commit()
        return cursor.lastrowid

    def _gravar(self, conn, file_id: int, lote: list[dict]) -> None:
        if not lote:
            return
        conn.executemany(
            """INSERT INTO observations(file_id, mac, ssid, auth_mode, first_seen, channel,
                   frequency, rssi, lat, lon, altitude, accuracy, rcois, mfgr_id, type, has_gps)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(file_id, r["mac"], r["ssid"], r["auth_mode"], r["first_seen"], r["channel"],
              r["frequency"], r["rssi"], r["lat"], r["lon"], r["altitude"], r["accuracy"],
              r["rcois"], r["mfgr_id"], r["type"], r["has_gps"]) for r in lote])
        db.rebuild_networks(conn, [r["mac"] for r in lote])
        self._atualizar_contadores(conn, file_id)
        conn.commit()
        with self._trava:
            self.estado["obs"] += len(lote)

    def _atualizar_contadores(self, conn, file_id: int) -> None:
        linha = conn.execute(
            """SELECT COUNT(*) obs, COUNT(DISTINCT mac) redes, MIN(first_seen) primeiro,
                      MAX(first_seen) ultimo, SUM(has_gps = 0) sem_gps,
                      MIN(lat) min_lat, MAX(lat) max_lat, MIN(lon) min_lon, MAX(lon) max_lon,
                      SUM(type = 'WIFI') wifi, SUM(type IN ('BLE','BT')) ble,
                      SUM(type NOT IN ('WIFI','BLE','BT')) cel
                 FROM observations WHERE file_id = ?""", (file_id,)).fetchone()
        conn.execute(
            """UPDATE files SET obs_count = ?, network_count = ?, first_seen = ?, last_seen = ?,
                   no_gps_count = ?, min_lat = ?, max_lat = ?, min_lon = ?, max_lon = ?,
                   wifi_count = ?, ble_count = ?, cell_count = ? WHERE id = ?""",
            (linha["obs"], linha["redes"], linha["primeiro"], linha["ultimo"], linha["sem_gps"],
             linha["min_lat"], linha["max_lat"], linha["min_lon"], linha["max_lon"],
             linha["wifi"], linha["ble"], linha["cel"], file_id))
        with self._trava:
            self.estado["redes"] = linha["redes"]

    def _publicar(self, registro: dict) -> None:
        if not registro["has_gps"]:
            return
        with self._trava:
            self._sequencia += 1
            self._pontos.append({
                "seq": self._sequencia,
                "lat": registro["lat"], "lon": registro["lon"],
                "rssi": registro["rssi"], "tipo": registro["type"],
                "ssid": registro["ssid"], "mac": registro["mac"],
                "canal": registro["channel"], "quando": registro["first_seen"],
            })


#: instância única usada pelas rotas
coletor = Coletor()
