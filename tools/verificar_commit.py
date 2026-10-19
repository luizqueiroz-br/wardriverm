"""Trava de segurança executada antes de cada commit.

Motivo: um commit anterior subiu por acidente os prints brutos do sistema, com
MACs reais, SSIDs de redes vizinhas e o mapa com ruas. Este verificador barra
esse tipo de arquivo (e o banco de dados, as chaves de API e os lotes) antes de
ele entrar no histórico.

Uso direto (para revisar antes de commitar):
    .venv/Scripts/python.exe tools/verificar_commit.py

Instalado como hook:
    git config core.hooksPath .githooks     # já é o padrão neste repositório
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# caminhos que nunca podem ser versionados
PADROES_PROIBIDOS = [
    (re.compile(r"^data/(?!\.gitkeep)"), "banco e dados de coleta (MAC, SSID, coordenadas)"),
    (re.compile(r"\.db(-wal|-shm)?$"), "arquivo de banco SQLite"),
    (re.compile(r"secrets\.json$"), "chaves de API"),
    (re.compile(r"servidor\.(log|err\.log|pid)$"), "log do serviço"),
    (re.compile(r"^\.venv/"), "ambiente virtual"),
    (re.compile(r"__pycache__|\.pyc$"), "cache do Python"),
    (re.compile(r"bruto-|_raw\.|-sem-tratamento\."), "print sem tratamento (MAC/SSID à mostra)"),
]

# prints tratados que podem ir para o repositório
PRINTS_PERMITIDOS = re.compile(r"^docs/(linkedin/final/|tela-)")
EXTENSOES_IMAGEM = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
LIMITE_SUSPEITO_KB = 100

# padrões que revelam dado real em arquivo de texto
PADROES_CONTEUDO = [
    (re.compile(rb"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}"), "endereço MAC"),
    (re.compile(rb"-2[0-9]\.\d{4,},\s*-?[0-9]{2}\.\d{4,}"), "par de coordenadas"),
]
# arquivos onde esses padrões são intencionais (dados sintéticos de teste ou documentação)
ISENTOS = re.compile(r"^(tests/|tools/servidor_mentira\.py$|wardrive/parser\.py$|docs/|README\.md$)")


def arquivos_em_stage() -> list[str]:
    saida = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                           capture_output=True, text=True, check=True)
    return [linha.strip() for linha in saida.stdout.splitlines() if linha.strip()]


def conteudo_em_stage(caminho: str) -> bytes:
    saida = subprocess.run(["git", "show", f":{caminho}"], capture_output=True, check=True)
    return saida.stdout


def verificar() -> list[str]:
    problemas: list[str] = []
    for caminho in arquivos_em_stage():
        for padrao, motivo in PADROES_PROIBIDOS:
            if padrao.search(caminho):
                problemas.append(f"{caminho}: {motivo}")
                break
        else:
            if caminho.lower().endswith(EXTENSOES_IMAGEM) and not PRINTS_PERMITIDOS.search(caminho):
                tamanho = Path(caminho).stat().st_size // 1024 if Path(caminho).exists() else 0
                if tamanho >= LIMITE_SUSPEITO_KB:
                    problemas.append(
                        f"{caminho}: print fora de docs/linkedin/final/ ({tamanho} KB) — "
                        "pode conter MAC, SSID ou coordenada; gere a versão tratada antes de commitar")
                    continue

        if ISENTOS.search(caminho):
            continue
        if Path(caminho).exists() and Path(caminho).stat().st_size > 2_000_000:
            continue
        try:
            dados = conteudo_em_stage(caminho)
        except subprocess.CalledProcessError:
            continue
        if b"\x00" in dados[:2000]:      # binário
            continue
        for padrao, motivo in PADROES_CONTEUDO:
            achado = padrao.search(dados)
            if achado:
                problemas.append(f"{caminho}: parece conter {motivo} real "
                                 f"(trecho: {achado.group(0)[:40].decode('utf-8', 'replace')})")
                break
    return problemas


def main() -> int:
    problemas = verificar()
    if not problemas:
        print("verificação de segurança: nada sensível nos arquivos deste commit")
        return 0
    print("COMMIT BLOQUEADO — arquivos com dado potencialmente identificável:\n")
    for item in problemas:
        print("  •", item)
    print("\nAjuste o .gitignore, remova o arquivo do stage (git restore --staged <arquivo>) ou,")
    print("se for mesmo necessário, confirme com: git commit --no-verify")
    return 1


if __name__ == "__main__":
    sys.exit(main())
