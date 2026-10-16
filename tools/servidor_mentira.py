"""Servidor local que imita WiGLE e WDGoWars, para conferir o upload.sh gerado.

Sobe na porta informada e grava num arquivo JSON tudo o que recebeu, para que o
teste possa rodar o script de upload em outro processo (o bash do sistema) e
depois conferir o resultado aqui.

Uso: .venv/Scripts/python.exe tools/servidor_mentira.py <porta> <arquivo-de-registro>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import ESTADO, _Handler  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

porta = int(sys.argv[1]) if len(sys.argv) > 1 else 5099
registro = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("mentira.json")

servidor = ThreadingHTTPServer(("127.0.0.1", porta), _Handler)
print(f"servidor de mentira ouvindo em http://127.0.0.1:{porta}", flush=True)
print(f"registrando em {registro}", flush=True)

try:
    while True:
        servidor.handle_request()
        # o servidor usa uma thread por requisição: espera a thread terminar de
        # registrar antes de gravar o arquivo de conferência
        time.sleep(1)
        registro.write_text(json.dumps(ESTADO.recebidos, ensure_ascii=False, indent=2), encoding="utf-8")
except KeyboardInterrupt:
    pass
finally:
    servidor.server_close()
