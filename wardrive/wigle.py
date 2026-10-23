"""Consultar uma rede no WiGLE: ela já era conhecida? quantas vezes foi vista?

A mesma credencial do upload (Basic) responde por BSSID em
`/api/v2/network/search?netid=<mac>`. A consulta **gasta cota**, então o resultado
fica em `wigle_consultas` por 30 dias — só o botão "atualizar" força de novo.

Nada aqui automatiza em massa: uma consulta por clique, no máximo uma por rede a
cada 30 dias, como pede a política de uso do WiGLE.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import requests

from . import config, uploaders

TTL_DIAS = 30


def _agora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fresco(quando: str) -> bool:
    try:
        return datetime.fromisoformat(quando) > datetime.now() - timedelta(days=TTL_DIAS)
    except (TypeError, ValueError):
        return False


def _normalizar(payload: dict) -> dict:
    """Só o que interessa na tela — o corpo cru fica no cache."""
    resultados = payload.get("results") or []
    itens = []
    for item in resultados:
        itens.append({
            "ssid": item.get("ssid") or "",
            "lat": item.get("trilat"),
            "lon": item.get("trilong"),
            "primeira": item.get("firsttime") or "",
            "ultima": item.get("lasttime") or "",
            "canal": item.get("channel"),
            "criptografia": item.get("encryption") or "",
            "pais": item.get("country") or "",
            "regiao": item.get("region") or "",
            "cidade": item.get("city") or "",
        })
    itens.sort(key=lambda i: i["ultima"], reverse=True)
    return {
        "encontrada": bool(itens),
        "total": payload.get("totalResults", len(itens)),
        "resultados": itens,
        "status": "ok",
    }


def _buscar(mac: str, creds: dict, base: str | None, timeout: int) -> dict:
    base = (base or config.WIGLE_API_BASE).rstrip("/")
    url = f"{base}{config.WIGLE_SEARCH_PATH}"
    try:
        resposta = requests.get(
            url, params={"netid": mac, "resultsPerPage": 100},
            headers={"Authorization": uploaders.wigle_auth_header(creds["name"], creds["token"]),
                     "Accept": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException as erro:
        return {"status": "erro", "http_status": None,
                "erro": "Sem conexão com o WiGLE.", "detalhe": str(erro)[:200]}

    if resposta.status_code == 401:
        return {"status": "erro", "http_status": 401,
                "erro": "Chave do WiGLE inválida ou expirada (401)."}
    if resposta.status_code == 429:
        return {"status": "erro", "http_status": 429,
                "erro": "Cota do WiGLE atingida (429). Tente mais tarde."}
    if resposta.status_code == 404:
        return {"status": "nao_encontrada", "http_status": 404, "encontrada": False,
                "total": 0, "resultados": [], "erro": None}
    if resposta.status_code != 200:
        return {"status": "erro", "http_status": resposta.status_code,
                "erro": f"WiGLE respondeu {resposta.status_code}."}

    try:
        payload = resposta.json()
    except ValueError:
        return {"status": "erro", "http_status": 200, "erro": "Resposta do WiGLE não é JSON."}

    if not payload.get("success", True) and payload.get("message"):
        return {"status": "erro", "http_status": 200, "erro": str(payload["message"])[:300]}

    normalizado = _normalizar(payload)
    normalizado["http_status"] = 200
    if not normalizado["encontrada"]:
        normalizado["status"] = "nao_encontrada"
    return normalizado


def _ler_cache(conn, mac: str) -> dict | None:
    linha = conn.execute("SELECT * FROM wigle_consultas WHERE mac = ?", (mac,)).fetchone()
    if linha is None:
        return None
    try:
        corpo = json.loads(linha["corpo"]) if linha["corpo"] else {}
    except ValueError:
        corpo = {}
    corpo.update({"consultado_em": linha["consultado_em"], "status": linha["status"],
                  "http_status": linha["http_status"], "erro": linha["erro"]})
    return corpo


def _gravar_cache(conn, mac: str, resultado: dict) -> None:
    corpo = {chave: valor for chave, valor in resultado.items()
             if chave not in ("consultado_em", "cache", "erro", "http_status")}
    conn.execute(
        """INSERT INTO wigle_consultas (mac, consultado_em, status, http_status, corpo, erro)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(mac) DO UPDATE SET consultado_em = excluded.consultado_em,
             status = excluded.status, http_status = excluded.http_status,
             corpo = excluded.corpo, erro = excluded.erro""",
        (mac, _agora(), resultado.get("status", "ok"), resultado.get("http_status"),
         json.dumps(corpo, ensure_ascii=False), resultado.get("erro")),
    )
    conn.commit()


def consultar(conn, mac: str, *, forcar: bool = False, base: str | None = None,
              timeout: int = 20, creds: dict | None = None) -> dict:
    """Devolve o histórico global da rede; usa o cache de 30 dias quando der."""
    mac = (mac or "").strip().upper()
    if not mac:
        return {"status": "erro", "erro": "MAC vazio."}

    cache = _ler_cache(conn, mac)
    if cache and not forcar and _fresco(cache.get("consultado_em", "")):
        cache["cache"] = True
        return cache

    creds = creds or uploaders.creds_for("wigle")
    if not (creds.get("name") and creds.get("token")):
        return {"status": "erro", "erro": "Sem chave do WiGLE configurada (data/secrets.json)."}

    resultado = _buscar(mac, creds, base, timeout)
    # erro de credencial/cota não vira cache: a próxima tentativa deve bater na API
    if resultado.get("status") != "erro":
        _gravar_cache(conn, mac, resultado)
        resultado["consultado_em"] = _agora()
    resultado["cache"] = False
    return resultado


def listar_cache(conn, limite: int = 50) -> list[dict]:
    """Quem já foi consultado, para a tela não consultar de novo sem querer."""
    linhas = conn.execute(
        "SELECT mac, consultado_em, status, http_status, corpo FROM wigle_consultas "
        "ORDER BY consultado_em DESC LIMIT ?", (limite,)).fetchall()
    saida = []
    for linha in linhas:
        try:
            corpo = json.loads(linha["corpo"]) if linha["corpo"] else {}
        except ValueError:
            corpo = {}
        saida.append({"mac": linha["mac"], "consultado_em": linha["consultado_em"],
                      "status": linha["status"], "http_status": linha["http_status"],
                      "total": corpo.get("total", 0),
                      "encontrada": corpo.get("encontrada", False),
                      "valido": _fresco(linha["consultado_em"])})
    return saida
