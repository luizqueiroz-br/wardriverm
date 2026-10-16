"""Teste de fumaça pela interface HTTP: percorre todas as telas, dispara a
varredura de arquivos, gera um lote e roda envios (simulado e real contra o
servidor de mentira), conferindo o resultado no banco.

Uso: .venv/Scripts/python.exe tools/smoke.py [http://127.0.0.1:5077]
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.servidor_falso import ESTADO, ServidorFalso  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5077"
sessao = requests.Session()
falhas: list[str] = []


def checar(condicao, mensagem):
    print(("  ok    " if condicao else "  FALHA ") + mensagem)
    if not condicao:
        falhas.append(mensagem)


def token(html: str) -> str:
    encontrado = re.search(r'data-csrf="([^"]+)"', html)
    return encontrado.group(1) if encontrado else ""


def esperar_job(tempo: int = 120):
    limite = time.time() + tempo
    while time.time() < limite:
        jobs = sessao.get(BASE + "/api/jobs", timeout=20).json()["jobs"]
        if jobs and jobs[0]["status"] in ("ok", "erro"):
            return jobs[0]
        time.sleep(1)
    raise TimeoutError("a tarefa não terminou no tempo esperado")


def configurar(csrf: str, **extra):
    dados = {
        "csrf_token": csrf,
        "ward_dirs": str(Path.home() / "OneDrive" / "Área de Trabalho" / "ward" / "send"),
        "auto_import_on_start": "1",
        "rows_per_chunk": "10000", "wdgwars_rows_per_chunk": "10000",
        "wigle_max_mb": "38", "wdgwars_max_mb": "14", "dedupe": "exact",
        "header_mode": "source",
        "device_appRelease": "wardrive-manager-1.0.0", "device_model": "Marauder v8 C5",
        "device_release": "1.0.0", "device_device": "ESP32-C5",
        "device_display": "ILI9341", "device_board": "ESP32C5", "device_brand": "WDGoWars",
        "timeout": "120", "map_max_points": "40000", "dry_run_default": "1",
        "wigle_base": "", "wdgwars_base": "",
    }
    dados.update(extra)
    return sessao.post(BASE + "/config", data=dados, timeout=60)


print("== páginas ==")
paginas = {
    "/": "Painel",
    "/mapa": "Mapa das coletas",
    "/redes": "Redes descobertas",
    "/sessoes": "Sessões de wardriving",
    "/importar": "Importar dados de wardriving",
    "/exportar": "Exportar CSVs compatíveis",
    "/uploads": "Chaves de API",
    "/config": "Configurações",
    "/atividade": "Atividade",
}
csrf = ""
for caminho, esperado in paginas.items():
    resposta = sessao.get(BASE + caminho, timeout=30)
    checar(resposta.status_code == 200, f"GET {caminho} → {resposta.status_code}")
    if esperado:
        checar(esperado in resposta.text, f"{caminho} contém “{esperado}”")
    if not csrf and resposta.status_code == 200:
        csrf = token(resposta.text)
checar(bool(csrf), "token CSRF presente na página")

print("== endpoints JSON ==")
checar(sessao.get(BASE + "/healthz", timeout=20).json().get("ok") is True, "GET /healthz")
resumo = sessao.get(BASE + "/api/resumo", timeout=20).json()
print("     resumo:", json.dumps({k: resumo.get(k) for k in
      ("arquivos", "observacoes", "redes", "wifi", "ble", "sem_gps", "pendentes")},
      ensure_ascii=False))
checar(resumo["arquivos"] > 0, "o banco tem sessões importadas")
pontos = sessao.get(BASE + "/api/mapa/pontos?tipo=WIFI&limite=500", timeout=60).json()
checar(pontos.get("total", 0) > 0, f"GET /api/mapa/pontos → {pontos.get('total')} pontos")

print("== varredura de pastas ==")
resposta = sessao.post(BASE + "/importar/scan", data={"csrf_token": csrf}, timeout=60)
checar(resposta.status_code == 200, "POST /importar/scan aceito")
job = esperar_job()
print("     tarefa:", job["status"], job["progress"], "—", job["label"])
checar(job["status"] == "ok", "varredura concluída sem erro")

print("== geração de lote ==")
sessao.post(BASE + "/exportar", data={
    "csrf_token": csrf, "target": "wigle", "mode": "all",
    "types": ["WIFI", "BLE", "BT"], "with_gps_only": "1", "dedupe": "exact",
    "header_mode": "source", "label": "fumaca",
}, timeout=60)
job = esperar_job()
checar(job["status"] == "ok", "exportação concluída")
criado = job.get("result") or {}
print("     lote #" + str(criado.get("export_id")), criado.get("rows"), "linhas em",
      criado.get("chunks"), "arquivo(s)")
checar(criado.get("rows", 0) > 0 and criado.get("chunks", 0) > 0, "o lote saiu com linhas e arquivos")

print("== envio simulado ==")
sessao.post(BASE + "/uploads/enviar", data={
    "csrf_token": csrf, "target": "wigle", "mode": "new", "types": ["WIFI", "BLE"],
    "with_gps_only": "1", "dedupe": "exact", "dry_run": "1", "label": "fumaca-simulada",
}, timeout=60)
job = esperar_job()
checar(job["status"] == "ok", "simulação concluída")
simulado = ((job.get("result") or {}).get("upload") or {})
checar(simulado.get("simulados", 0) >= 1, f"simulação registrou {simulado.get('simulados')} lote(s)")

print("== configurações ==")
resposta = configurar(csrf)
checar(resposta.status_code == 200 and "Configurações salvas" in resposta.text, "POST /config salvou")

print("== uploads reais contra o servidor de mentira ==")
falso = ServidorFalso()
try:
    ESTADO.reiniciar()
    configurar(csrf, wigle_base=falso.base, wdgwars_base=falso.base, timeout="60")
    sessao.post(BASE + "/uploads/chaves", data={
        "csrf_token": csrf, "wigle_api_name": "usuario_teste",
        "wigle_api_token": "token_de_teste", "wdgwars_api_key": "b" * 64,
    }, timeout=60)
    for destino in ("wigle", "wdgwars"):
        resposta = sessao.post(BASE + "/uploads/validar",
                               data={"csrf_token": csrf, "target": destino}, timeout=60)
        checar("✓" in resposta.text, f"validação da credencial {destino}")
    resposta = sessao.post(BASE + "/uploads/enviar", data={
        "csrf_token": csrf, "target": "wdgwars", "mode": "new", "types": ["WIFI", "BLE"],
        "with_gps_only": "1", "dedupe": "exact", "label": "fumaca-real",
    }, timeout=60)
    checar(resposta.status_code == 200, "POST /uploads/enviar (envio real) aceito")
    job = esperar_job(240)
    checar(job["status"] == "ok", "envio concluído")
    envio = ((job.get("result") or {}).get("upload") or {})
    print("     envio:", json.dumps({k: envio.get(k) for k in
          ("enviados", "ok", "duplicados", "erros", "linhas")}, ensure_ascii=False))
    checar(envio.get("ok", 0) >= 1, "o servidor aceitou pelo menos um lote")
    checar(len(ESTADO.recebidos) >= 1, "o servidor de mentira recebeu os arquivos")
    checar(envio.get("enviados", 0) == len(ESTADO.recebidos),
           "cada POST registrado no histórico corresponde a um recebimento")

    # segunda rodada: o modo incremental não pode reenviar o que já subiu
    sessao.post(BASE + "/uploads/enviar", data={
        "csrf_token": csrf, "target": "wdgwars", "mode": "new", "types": ["WIFI", "BLE"],
        "with_gps_only": "1", "dedupe": "exact", "label": "fumaca-real-2",
    }, timeout=60)
    job = esperar_job(240)
    segundo = job.get("result") or {}
    checar(segundo.get("rows", 0) == 0 and segundo.get("upload") is None,
           "o modo incremental não reenviou nada na segunda rodada")
finally:
    falso.parar()
    configurar(csrf)   # devolve os endereços oficiais

print()
if falhas:
    print(f"{len(falhas)} verificação(ões) falharam:")
    for item in falhas:
        print(" -", item)
    sys.exit(1)
print("todas as verificações passaram")
