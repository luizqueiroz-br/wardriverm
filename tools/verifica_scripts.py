"""Verifica o script de upload gerado junto com cada lote (upload.sh / curl).

Gera um lote pequeno apontando para um servidor de mentira local, roda o
`upload.sh` de dentro da pasta do lote exatamente como o usuário faria num
terminal bash, e confere que o servidor recebeu o arquivo com a autenticação
correta de cada serviço.

Uso: .venv/Scripts/python.exe tools/verifica_scripts.py
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import ESTADO, ServidorFalso, csv_wigle, isolar_dados  # noqa: E402
from wardrive import config, db, exporter, importer  # noqa: E402

falhas: list[str] = []


def checar(condicao, mensagem):
    print(("  ok    " if condicao else "  FALHA ") + mensagem)
    if not condicao:
        falhas.append(mensagem)


def raiz_do_bash() -> str:
    """Descobre como o bash enxerga o disco C: (/c em MSYS, /mnt/c no WSL)."""
    sonda = subprocess.run(
        ["bash", "-c", "ls -d /mnt/c >/dev/null 2>&1 && echo /mnt/c || echo /c"],
        capture_output=True, text=True, timeout=30)
    return sonda.stdout.strip() or "/c"


RAIZ_BASH = raiz_do_bash()


def para_msys(pasta: Path) -> str:
    """Converte C:\\Users\\x no caminho que o bash do sistema entende.

    Depende do bash disponível: no MSYS é /c/Users/x, no WSL é /mnt/c/Users/x.
    (Neste ambiente a conversão automática do MSYS está desligada, então
    `cd "C:/Users/..."` cai em /tmp em vez do diretório pedido.)
    """
    texto = str(pasta).replace("\\", "/")
    if len(texto) > 1 and texto[1] == ":":
        return f"{RAIZ_BASH}{texto[2:]}"
    return texto


def rodar_script(pasta: Path, variaveis: dict, depurar: bool = False) -> subprocess.CompletedProcess:
    """Executa upload.sh como num terminal bash.

    As variáveis são definidas *dentro* do bash: o que um processo nativo do
    Windows exporta não chega ao bash do MSYS (limitação do MSYS, não do script).
    """
    exports = " ".join(f"{chave}={shlex.quote(valor)}" for chave, valor in variaveis.items())
    bandeira = "bash -x upload.sh" if depurar else "bash upload.sh"
    comando = f"cd {shlex.quote(para_msys(pasta))} && {exports} {bandeira}"
    return subprocess.run(["bash", "-c", comando], capture_output=True, text=True, timeout=120)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="wardrive-scripts-") as tmp:
        isolar_dados(Path(tmp))
        servidor = ServidorFalso()
        conn = None
        try:
            config.save_secrets({"wigle_api_name": "usuario_teste",
                                 "wigle_api_token": "token_de_teste"})
            config.save_settings({"upload": {"wigle_base": servidor.base, "wigle_donate": False}})

            conn = db.connect()
            db.init_db(conn)
            importer.import_bytes(conn, "lote-teste.log", csv_wigle(25), source="teste")
            criado = exporter.create_export(
                conn, target="wigle",
                selection={"mode": "all", "types": ["WIFI"], "with_gps_only": True, "dedupe": "none"},
                settings=config.load_settings(), label="scripts")
            lote = Path(criado["dir"])
            script = lote / "upload.sh"
            checar(script.exists(), "upload.sh gerado dentro da pasta do lote")
            checar((lote / "upload.ps1").exists(), "upload.ps1 gerado dentro da pasta do lote")
            checar(b"\r" not in script.read_bytes(),
                   "upload.sh usa quebras de linha LF (executável no bash)")

            resultado = rodar_script(lote, {"WIGLE_API_NAME": "usuario_teste",
                                            "WIGLE_API_TOKEN": "token_de_teste"})
            print("     curl respondeu:", (resultado.stdout or "").strip()[:120])
            checar(resultado.returncode == 0,
                   f"upload.sh executou sem erro (código {resultado.returncode})")
            if resultado.returncode != 0:
                print("     stderr:", (resultado.stderr or "").strip()[-400:])
            checar(len(ESTADO.recebidos) == criado["chunks"],
                   f"o servidor recebeu {len(ESTADO.recebidos)} arquivo(s), esperado {criado['chunks']}")
            if ESTADO.recebidos:
                recebido = ESTADO.recebidos[0]
                checar(recebido["linhas"] == 25,
                       f"o arquivo recebido tem {recebido['linhas']} linhas (esperado 25)")
                checar(recebido["donate"] == "false", "campo donate=false enviado como configurado")

            # agora o WDGoWars (cabeçalho X-API-Key)
            ESTADO.reiniciar()
            config.save_secrets({"wdgwars_api_key": "b" * 64})
            config.save_settings({"upload": {"wdgwars_base": servidor.base}})
            criado_wdg = exporter.create_export(
                conn, target="wdgwars",
                selection={"mode": "all", "types": ["WIFI"], "with_gps_only": True, "dedupe": "none"},
                settings=config.load_settings(), label="scripts-wdg")
            resultado = rodar_script(Path(criado_wdg["dir"]), {"WDGWARS_API_KEY": "b" * 64})
            print("     curl respondeu:", (resultado.stdout or "").strip()[:120])
            checar(resultado.returncode == 0, "upload.sh do WDGoWars executou sem erro")
            if resultado.returncode != 0:
                print("     stderr:", (resultado.stderr or "").strip()[-400:])
            checar(len(ESTADO.recebidos) == criado_wdg["chunks"],
                   "servidor do WDGoWars recebeu o arquivo")
        finally:
            if conn is not None:
                conn.close()
            servidor.parar()

    print()
    if falhas:
        print(f"{len(falhas)} verificação(ões) falharam:")
        for item in falhas:
            print(" -", item)
        return 1
    print("scripts de upload conferidos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
