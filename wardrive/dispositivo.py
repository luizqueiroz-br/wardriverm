"""Puxar os logs do cartão SD do ESP32 pela serial, sem tirar o cartão.

O protocolo varia por fork (Marauder original × WDGoWars), então tudo que é
"conversa" fica atrás de `Porta` + `DispositivoSD`: os comandos de listar e de
ler são configuráveis, e o `--diagnostico` despeja a saída crua para descobrir o
que o seu firmware responde.

Limite conhecido: só arquivos de texto (.log, .csv, .txt). Arquivo `.gz` ou
binário não sobrevive a um protocolo de linha — esses continuam no caminho do
cartão SD.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

EXTENSOES = (".log", ".csv", ".txt")
TEXTO = True


class ErroDispositivo(Exception):
    """Falha de porta, desconexão ou resposta inesperada do firmware."""


# --------------------------------------------------------------------------- #
# Porta serial (o que os testes substituem)
# --------------------------------------------------------------------------- #
class Porta:
    """Envelope fino sobre o pyserial — dá para trocar por um duplo nos testes."""

    def __init__(self, nome: str, baud: int = 115200, *, serial_mod=None):
        self.nome = nome
        self.baud = baud
        self._serial_mod = serial_mod
        self._conexao = None

    def abrir(self) -> None:
        if self._conexao is not None:
            return
        modulo = self._serial_mod
        if modulo is None:
            try:
                import serial as modulo
            except ImportError as erro:      # pragma: no cover - depende do ambiente
                raise ErroDispositivo(
                    "pyserial não está instalado — `pip install pyserial`.") from erro
        try:
            self._conexao = modulo.Serial(self.nome, self.baud, timeout=0.4)
        except Exception as erro:
            raise ErroDispositivo(
                f"Não consegui abrir a porta {self.nome}: {erro}. "
                "Confira se o ESP32 está conectado e se nenhum monitor serial está aberto.") from erro

    def fechar(self) -> None:
        if self._conexao is not None:
            try:
                self._conexao.close()
            finally:
                self._conexao = None

    def enviar(self, texto: str) -> None:
        if self._conexao is None:
            raise ErroDispositivo("A porta não está aberta.")
        try:
            self._conexao.write((texto.rstrip("\r\n") + "\r\n").encode())
            self._conexao.flush()
        except Exception as erro:
            raise ErroDispositivo(f"A conexão caiu ao enviar “{texto}”: {erro}") from erro

    def ler_ate_silencio(self, silencio: float = 2.0, max_linhas: int = 50000,
                         limite: float = 120.0) -> list[str]:
        """Lê até a linha parar de chegar por `silencio` segundos."""
        if self._conexao is None:
            raise ErroDispositivo("A porta não está aberta.")
        linhas: list[str] = []
        inicio = time.monotonic()
        ultimo_dado = time.monotonic()
        while time.monotonic() - inicio < limite:
            try:
                bruto = self._conexao.readline()
            except Exception as erro:
                raise ErroDispositivo(f"A conexão caiu durante a leitura: {erro}") from erro
            if bruto:
                texto = bruto.decode(errors="replace").rstrip("\r\n")
                linhas.append(texto)
                ultimo_dado = time.monotonic()
                if len(linhas) >= max_linhas:
                    break
            elif time.monotonic() - ultimo_dado >= silencio:
                break
        return linhas

    def __enter__(self):
        self.abrir()
        return self

    def __exit__(self, *_):
        self.fechar()


# --------------------------------------------------------------------------- #
# Listagem do cartão SD
# --------------------------------------------------------------------------- #
_NOME = re.compile(r"([A-Za-z0-9_\-]+\.(?:log|csv|txt))(?![\w.])", re.IGNORECASE)
_TAMANHO = re.compile(r"(?<![\w.])(\d{2,12})(?![\w.])")


def parse_listagem(linhas) -> list[dict]:
    """Aceita as formas que os forkcostumam imprimir.

    `wardrive_37.log`, `12345 wardrive_37.log`, `FILE: wardrive_37.log 12345`,
    `[SD] wardrive_37.log (12 KB)`. Um tamanho só é aceito se não fizer parte do
    nome, e o maior valor visto para o mesmo arquivo fica valendo.
    """
    achados: dict[str, dict] = {}
    for linha in linhas:
        if not isinstance(linha, str):
            continue
        nome_encontrado = _NOME.search(linha)
        if not nome_encontrado:
            continue
        nome = nome_encontrado.group(1)
        resto = linha.replace(nome, " ")
        tamanho = None
        numero = _TAMANHO.search(resto)
        if numero:
            valor = int(numero.group(1))
            if "kb" in resto.lower():
                valor *= 1024
            elif "mb" in resto.lower():
                valor *= 1024 * 1024
            tamanho = valor
        atual = achados.get(nome)
        if atual is None or (tamanho or 0) > (atual["tamanho"] or 0):
            achados[nome] = {"nome": nome, "tamanho": tamanho}
    return sorted(achados.values(), key=lambda item: item["nome"])


# --------------------------------------------------------------------------- #
# Dispositivo
# --------------------------------------------------------------------------- #
class DispositivoSD:
    """Conversa com o firmware: lista o cartão e lê um arquivo de texto."""

    #: comandos de listagem, na ordem em que são tentados. O Marauder v1.18 usa
    #: `ls /` (saída `nome<TAB>tamanho`); forks mais velhos usam `sd list`.
    CANDIDATOS_LISTAR = ("sd list", "ls /")

    def __init__(self, porta: Porta, *, comando_listar: str = "auto",
                 comando_ler: str = "sd read {arquivo}", silencio: float = 2.0):
        self.porta = porta
        self.comando_listar = comando_listar
        self.comando_ler = comando_ler
        self.silencio = silencio
        self.comando_listar_usado: str | None = None

    def listar(self) -> list[dict]:
        candidatos = self.CANDIDATOS_LISTAR if self.comando_listar == "auto" \
            else (self.comando_listar,)
        tentativas = []
        for comando in candidatos:
            self.porta.enviar(comando)
            linhas = self.porta.ler_ate_silencio(self.silencio)
            arquivos = parse_listagem(linhas)
            if arquivos:
                self.comando_listar_usado = comando
                return arquivos
            tentativas.append(comando)
        raise ErroDispositivo(
            "O firmware não devolveu nenhum arquivo para "
            + " nem ".join(f"“{c}”" for c in tentativas)
            + ". Rode com --diagnostico --comando help para ver os comandos do seu firmware.")

    #: a mensagem usada quando o firmware não tem como mandar o conteúdo
    SEM_LEITURA = ("esta firmware lista o cartão mas não expõe leitura de arquivo pela serial "
                   "(Marauder v1.18: só `ls`). Baixe os .log pelo cartão SD — o app importa da pasta.")

    def baixar(self, nome: str, destino: Path, *, tamanho_esperado: int | None = None) -> Path:
        if not nome.lower().endswith(EXTENSOES):
            raise ErroDispositivo(f"“{nome}” não é texto — baixe pelo cartão SD.")
        self.porta.enviar(self.comando_ler.format(arquivo=nome))
        linhas = self.porta.ler_ate_silencio(self.silencio, limite=600)

        # O console ecoa o comando prefixado de '#' ("#sd read x.log") e termina com
        # o prompt ">". Nenhuma das duas coisas é conteúdo do arquivo.
        prefixo = self.comando_ler.split("{")[0].strip().lower()

        def eh_ruido(linha: str) -> bool:
            texto = linha.strip().lstrip("#").strip().lower()
            if not texto or texto.startswith(">"):
                return True
            return texto.startswith(prefixo) or texto == nome.lower()

        conteudo = [linha for linha in linhas if not eh_ruido(linha)]
        if not conteudo:
            # firmware sem comando de leitura responde só o eco e o prompt
            raise ErroDispositivo(self.SEM_LEITURA)
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporario = destino.with_suffix(destino.suffix + ".parcial")
        temporario.write_text("\n".join(conteudo) + "\n", encoding="utf-8")
        if tamanho_esperado and abs(temporario.stat().st_size - tamanho_esperado) > max(64, tamanho_esperado * 0.05):
            # não descarta: o protocolo pode reformatar linha; só avisa
            temporario.replace(destino)
            return destino
        temporario.replace(destino)
        return destino


def portas_disponiveis() -> list[dict]:
    """Lista as portas seriais do sistema (sem abrir nenhuma)."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [{"porta": p.device, "descricao": p.description, "fabricante": p.manufacturer or ""}
            for p in list_ports.comports()]


# --------------------------------------------------------------------------- #
# O que fazer com o que foi listado
# --------------------------------------------------------------------------- #
def _hash(caminho: Path) -> str:
    import hashlib
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def puxar(conn, porta: str, destino: Path | str, *, importar: bool = True,
          baud: int = 115200, job=None, silencio: float = 2.0,
          comando_listar: str = "auto", comando_ler: str = "sd read {arquivo}",
          porta_obj=None, somente_listar: bool = False) -> dict:
    """Lista o cartão, baixa só o que é novo e (por padrão) importa.

    Devolve `{"baixados": [...], "ignorados": [...], "arquivos": n, "importacao": {...}}`.
    Nunca levanta para falha de porta: o erro volta em `erro` para a tela mostrar.
    """
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    resumo = {"baixados": [], "ignorados": [], "arquivos": 0, "importacao": None, "erro": None}

    def log(mensagem: str, nivel: str = "info"):
        if job is not None:
            job.write(mensagem, nivel)

    conexao = porta_obj or Porta(porta, baud)
    try:
        try:
            conexao.abrir()
        except ErroDispositivo as erro:
            resumo["erro"] = str(erro)
            log(str(erro), "erro")
            return resumo

        aparelho = DispositivoSD(conexao, comando_listar=comando_listar,
                                 comando_ler=comando_ler, silencio=silencio)
        try:
            arquivos = aparelho.listar()
        except ErroDispositivo as erro:
            resumo["erro"] = str(erro)
            log(str(erro), "erro")
            return resumo

        log(f"{len(arquivos)} arquivo(s) no cartão: "
            + ", ".join(f"{a['nome']}" for a in arquivos[:8])
            + ("…" if len(arquivos) > 8 else ""))
        if job is not None:
            job.set_total(len(arquivos), "arquivos")
        resumo["listados"] = arquivos
        resumo["comando_listagem"] = aparelho.comando_listar_usado
        if somente_listar:
            log("só listando — nada foi baixado", "aviso")
            return resumo

        for arquivo in arquivos:
            nome = arquivo["nome"]
            tamanho = arquivo["tamanho"]
            conhecido = conn.execute(
                "SELECT tamanho, sha256 FROM cartao_arquivos WHERE nome = ?", (nome,)).fetchone()
            alvo = destino / nome
            if conhecido and alvo.exists() and (tamanho is None or conhecido["tamanho"] == tamanho):
                resumo["ignorados"].append(nome)
                log(f"{nome}: já baixado antes, pulando")
                if job is not None:
                    job.tick()
                continue
            try:
                caminho = aparelho.baixar(nome, alvo, tamanho_esperado=tamanho)
            except ErroDispositivo as erro:
                # firmware sem comando de leitura: não adianta tentar os próximos
                resumo["erro"] = str(erro)
                log(str(erro), "erro")
                resumo["sem_leitura"] = erro is not None and "não expõe leitura" in str(erro)
                break
            hash_arquivo = _hash(caminho)
            conn.execute(
                """INSERT INTO cartao_arquivos (nome, tamanho, sha256, baixado_em)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(nome) DO UPDATE SET tamanho = excluded.tamanho,
                     sha256 = excluded.sha256, baixado_em = excluded.baixado_em""",
                # o tamanho guardado é o que o **cartão** informou: é ele que permite
                # saber, na próxima, se o arquivo cresceu
                (nome, tamanho if tamanho is not None else caminho.stat().st_size,
                 hash_arquivo))
            conn.commit()
            resumo["baixados"].append(nome)
            resumo["arquivos"] += 1
            log(f"{nome}: {caminho.stat().st_size} bytes")
            if job is not None:
                job.tick()
    finally:
        conexao.fechar()

    if importar and resumo["baixados"]:
        from . import importer
        log("importando o que foi baixado…")
        resumo["importacao"] = importer.scan_and_import(conn, [str(destino)], job=job)
        log(f"{resumo['importacao'].get('novos', 0)} sessão(ões), "
            f"{resumo['importacao'].get('obs', 0)} observação(ões)")
    return resumo


def diagnostico(porta: str, *, baud: int = 115200, segundos: float = 5.0,
                comando: str | None = None) -> list[str]:
    """Abre a porta e despeja o que o firmware falar — para descobrir o protocolo.

    Com `comando`, manda a linha primeiro (ex.: `help`, `sd list`) e mostra a
    resposta: é assim que se descobre os comandos do fork sem abrir monitor serial.
    """
    conexao = Porta(porta, baud)
    conexao.abrir()
    try:
        if comando:
            conexao.enviar(comando)
        return conexao.ler_ate_silencio(silencio=segundos, limite=segundos + 5)
    finally:
        conexao.fechar()
