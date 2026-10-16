"""Clientes de upload para WiGLE e WDGoWars.

Contratos usados (verificados nas fontes citadas em docs/COMPATIBILIDADE.md):

WiGLE
  POST {base}/api/v2/file/upload
    Authorization: Basic <base64(api_name:api_token)>  (ou o token já codificado)
    multipart/form-data: file=<csv> (type=text/csv), donate=true|false
    resposta: {"success": true, "results": [{"transid": "...", "networks": N, ...}]}
    limites: ~40 MB por arquivo (.gz medido descomprimido)
  GET  {base}/api/v2/profile/uploads?pagestart=0&pageend=1  (valida a credencial)

WDGoWars
  GET  {base}/api/me            → valida a chave (X-API-Key)
  POST {base}/endpoint/upload-csv  (ou /api/upload-csv)
    X-API-Key: <chave de 64 hex>; multipart/form-data: file=<csv>
    resposta: {"ok":true,"imported":N,"captured":N,"duplicates":N,"no_gps":N,
               "bad_rows":N,"cooldown":S,"merged_samples":N,"total":N}
    erros: 400 formato inválido | 401 chave inválida | 409 duplicate_upload
           413 payload-too-large (máx 15728640 bytes) | 429 retry_after | 524 timeout Cloudflare
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime
from pathlib import Path

import requests

from . import config, db

STATUS_OK = "ok"
STATUS_DUPLICATE = "duplicado"
STATUS_RATE_LIMITED = "limite"
STATUS_TOO_LARGE = "grande"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "erro"
STATUS_DRY_RUN = "simulado"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# credenciais
# --------------------------------------------------------------------------- #
def wigle_auth_header(api_name: str, api_token: str) -> str:
    """Aceita o par nome:token ou o token já codificado em base64."""
    token = (api_token or "").strip()
    name = (api_name or "").strip()
    if token:
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8", "ignore")
            if ":" in decoded:
                return f"Basic {token}"
        except Exception:  # noqa: BLE001 - token não é base64: segue o fluxo normal
            pass
    raw = f"{name}:{token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def creds_for(target: str) -> dict:
    secrets = config.load_secrets()
    if target == "wigle":
        return {"name": secrets.get("wigle_api_name", ""), "token": secrets.get("wigle_api_token", "")}
    return {"key": secrets.get("wdgwars_api_key", "")}


# --------------------------------------------------------------------------- #
# validação de credenciais
# --------------------------------------------------------------------------- #
def wigle_validate(api_name: str, api_token: str, *, base: str | None = None, timeout: int = 30) -> dict:
    base = (base or config.WIGLE_API_BASE).rstrip("/")
    url = f"{base}{config.WIGLE_UPLOADS_PATH}"
    try:
        resp = requests.get(
            url,
            params={"pagestart": 0, "pageend": 1},
            headers={"Authorization": wigle_auth_header(api_name, api_token), "Accept": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return {"ok": False, "message": f"falha de rede: {exc}"}
    if resp.status_code == 401:
        return {"ok": False, "http_status": 401, "message": "credenciais recusadas pelo WiGLE (401)"}
    if resp.status_code != 200:
        return {"ok": False, "http_status": resp.status_code, "message": resp.text[:300]}
    try:
        payload = resp.json()
    except ValueError:
        return {"ok": False, "http_status": resp.status_code, "message": "resposta não-JSON"}
    uploads = payload.get("results") or []
    last = uploads[0] if uploads else {}
    return {
        "ok": bool(payload.get("success", True)),
        "http_status": resp.status_code,
        "message": f"credencial válida — {len(uploads)} upload(s) listado(s)",
        "last_upload": {
            "transid": last.get("transid"),
            "status": last.get("status"),
            "fileName": last.get("fileName"),
            "networks": last.get("networks"),
            "fileSize": last.get("fileSize"),
        } if last else None,
        "raw": payload,
    }


def wdgwars_validate(api_key: str, *, base: str | None = None, timeout: int = 30) -> dict:
    base = (base or config.WDGWARS_API_BASE).rstrip("/")
    url = f"{base}{config.WDGWARS_ME_PATH}"
    try:
        resp = requests.get(url, headers={"X-API-Key": (api_key or "").strip()}, timeout=timeout)
    except requests.RequestException as exc:
        return {"ok": False, "message": f"falha de rede: {exc}"}
    if resp.status_code == 401:
        return {"ok": False, "http_status": 401, "message": "chave recusada pelo WDGoWars (401)"}
    if resp.status_code != 200:
        return {"ok": False, "http_status": resp.status_code, "message": resp.text[:300]}
    try:
        payload = resp.json()
    except ValueError:
        return {"ok": False, "http_status": resp.status_code, "message": "resposta não-JSON"}
    stats = {k: payload.get(k) for k in
             ("username", "gang", "country", "wifi", "ble", "aircraft", "mesh", "total",
              "recent_today", "recent_7d", "badges")}
    return {"ok": bool(payload.get("ok", True)), "http_status": resp.status_code,
            "message": "chave válida", "stats": stats, "raw": payload}


# --------------------------------------------------------------------------- #
# POSTs de baixo nível
# --------------------------------------------------------------------------- #
def wigle_post(*, api_name: str, api_token: str, filename: str, content: bytes,
               donate: bool = False, base: str | None = None, timeout: int = 300) -> dict:
    base = (base or config.WIGLE_API_BASE).rstrip("/")
    url = f"{base}{config.WIGLE_UPLOAD_PATH}"
    started = time.time()
    try:
        resp = requests.post(
            url,
            headers={"Authorization": wigle_auth_header(api_name, api_token), "Accept": "application/json"},
            data={"donate": "true" if donate else "false"},
            files={"file": (filename, content, "text/csv")},
            timeout=timeout,
        )
    except requests.Timeout as exc:
        return {"status": STATUS_TIMEOUT, "message": f"timeout: {exc}", "duration_ms": _ms(started)}
    except requests.RequestException as exc:
        return {"status": STATUS_ERROR, "message": f"falha de rede: {exc}", "duration_ms": _ms(started)}

    duration = _ms(started)
    body = resp.text[:4000]
    if resp.status_code == 429:
        return {"status": STATUS_RATE_LIMITED, "http_status": 429, "message": body[:300],
                "retry_after": _retry_after(resp), "duration_ms": duration}
    if resp.status_code in (413, 414):
        return {"status": STATUS_TOO_LARGE, "http_status": resp.status_code, "message": body[:300],
                "duration_ms": duration}
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if resp.status_code >= 500:
        return {"status": STATUS_ERROR, "http_status": resp.status_code,
                "message": body[:300], "response": payload, "duration_ms": duration}

    if payload is None or not isinstance(payload, dict):
        return {"status": STATUS_ERROR, "http_status": resp.status_code,
                "message": f"resposta inesperada (HTTP {resp.status_code}): {body[:300]}",
                "duration_ms": duration}

    first = primeiro_resultado_wigle(payload)
    ok = bool(payload.get("success")) and resp.status_code == 200
    message = (first.get("message") or payload.get("message") or "").strip()
    low = message.lower()
    if ok and ("already" in low or "duplicate" in low):
        status = STATUS_DUPLICATE
    elif ok or payload.get("success") is None and resp.status_code == 200:
        # HTTP 200 sem "success" explícito não deve ser tratado como falha:
        # o arquivo pode ter sido aceito e só a forma da resposta ter mudado.
        status = STATUS_OK if resp.status_code == 200 else STATUS_ERROR
    elif "already" in low or "duplicate" in low:
        status, ok = STATUS_DUPLICATE, True
    else:
        status = STATUS_ERROR
    return {
        "status": status,
        "http_status": resp.status_code,
        "message": message or f"HTTP {resp.status_code}",
        "target_ref": first.get("transid"),
        "response": payload,
        "networks": first.get("networks"),
        "duration_ms": duration,
    }


def primeiro_resultado_wigle(payload) -> dict:
    """Normaliza o resultado do upload do WiGLE, aceitando as formas que ele já usou.

    A resposta real (verificada em 16/10/2026) é:

        {"success": true, "warning": null,
         "results": {"timeTaken": "0", "filesize": 975, "filename": "1792161846_part01.csv",
                     "transids": [{"file": "...", "size": 975, "transId": "20261016-00975"}]},
         "observer": "..."}

    ou seja: `results` é um OBJETO (não lista) e o transid vem em `results.transids[0].transId`.
    Ler por posição derrubava o envio com KeyError; aqui a leitura é tolerante e devolve sempre
    um dicionário com as chaves `transid`, `networks` e `message` quando existirem.
    """
    if not isinstance(payload, dict):
        return {}
    results = payload.get("results")

    registro: dict = {}
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict):
                registro = item
                break
    elif isinstance(results, dict):
        if any(chave in results for chave in ("transid", "message", "networks", "status", "fileName")):
            registro = results
        else:
            for valor in results.values():
                if isinstance(valor, dict):
                    registro = valor
                    break

    transid = None
    redes = None
    mensagem = None
    if isinstance(registro, dict):
        transid = registro.get("transid") or registro.get("transId") or registro.get("trans_id")
        redes = registro.get("networks")
        mensagem = registro.get("message")

    # formato real: transids é uma lista dentro de results
    if isinstance(results, dict):
        lista = results.get("transids") or results.get("transactions")
        if isinstance(lista, list) and lista and isinstance(lista[0], dict):
            primeiro = lista[0]
            transid = transid or primeiro.get("transId") or primeiro.get("transid")
            redes = redes if redes is not None else primeiro.get("networks")
        transid = transid or results.get("transId") or results.get("transid")

    for chave in ("result", "upload"):
        valor = payload.get(chave)
        if isinstance(valor, dict):
            transid = transid or valor.get("transId") or valor.get("transid")
            redes = redes if redes is not None else valor.get("networks")
            mensagem = mensagem or valor.get("message")

    saida = {"transid": transid, "networks": redes, "message": mensagem}
    if transid and not saida["message"]:
        saida["message"] = f"aceito pelo WiGLE — transid {transid}"
        if isinstance(results, dict) and results.get("filesize"):
            saida["message"] += f", {results['filesize']} bytes"
    return {chave: valor for chave, valor in saida.items() if valor is not None}


def wdgwars_post(*, api_key: str, filename: str, content: bytes,
                 base: str | None = None, timeout: int = 300) -> dict:
    base = (base or config.WDGWARS_API_BASE).rstrip("/")
    url = f"{base}{config.WDGWARS_CSV_PATH}"
    started = time.time()
    try:
        resp = requests.post(
            url,
            headers={"X-API-Key": (api_key or "").strip()},
            files={"file": (filename, content, "text/csv")},
            timeout=timeout,
        )
    except requests.Timeout as exc:
        return {"status": STATUS_TIMEOUT, "message": f"timeout: {exc}", "duration_ms": _ms(started)}
    except requests.RequestException as exc:
        return {"status": STATUS_ERROR, "message": f"falha de rede: {exc}", "duration_ms": _ms(started)}

    duration = _ms(started)
    body = resp.text[:4000]
    if resp.status_code == 524 or "origin_response_timeout" in body:
        return {"status": STATUS_TIMEOUT, "http_status": resp.status_code,
                "message": "524 (timeout do Cloudflare): o servidor segue ingerindo — lote dividido e reenviado",
                "duration_ms": duration}
    if resp.status_code == 413:
        return {"status": STATUS_TOO_LARGE, "http_status": 413, "message": body[:300], "duration_ms": duration}
    if resp.status_code == 429:
        return {"status": STATUS_RATE_LIMITED, "http_status": 429, "message": body[:300],
                "retry_after": _retry_after(resp), "duration_ms": duration}
    try:
        payload = resp.json()
    except ValueError:
        payload = None
    if resp.status_code >= 500:
        return {"status": STATUS_ERROR, "http_status": resp.status_code, "message": body[:300],
                "response": payload, "duration_ms": duration}

    if payload is None or not isinstance(payload, dict):
        return {"status": STATUS_ERROR, "http_status": resp.status_code,
                "message": f"resposta inesperada (HTTP {resp.status_code}): {body[:300]}",
                "duration_ms": duration}

    if resp.status_code == 409 and payload.get("error") == "duplicate_upload":
        return {"status": STATUS_DUPLICATE, "http_status": 409,
                "message": f"arquivo já estava no servidor (duplicado em {payload.get('duplicate_at', '?')})",
                "response": payload, "duration_ms": duration}
    if resp.status_code == 401:
        return {"status": STATUS_ERROR, "http_status": 401,
                "message": payload.get("error", "chave inválida"), "response": payload, "duration_ms": duration}
    if not payload.get("ok"):
        return {"status": STATUS_ERROR, "http_status": resp.status_code,
                "message": payload.get("error", body[:300]), "response": payload, "duration_ms": duration}

    imported = payload.get("imported", 0)
    duplicates = payload.get("duplicates", 0)
    return {
        "status": STATUS_OK,
        "http_status": resp.status_code,
        "message": (f"importadas {imported} novas, {duplicates} duplicadas no servidor, "
                    f"{payload.get('no_gps', 0)} sem GPS, {payload.get('bad_rows', 0)} inválidas, "
                    f"{payload.get('merged_samples', 0)} amostras mescladas"
                    + (f", capturas: {payload.get('captured')}" if payload.get("captured") else "")),
        "response": payload,
        "cooldown": payload.get("cooldown", 0),
        "duration_ms": duration,
    }


def _retry_after(resp) -> int:
    header = resp.headers.get("Retry-After")
    if header and header.strip().isdigit():
        return int(header.strip())
    try:
        payload = resp.json()
        for key in ("retry_after", "retryAfter", "cooldown"):
            if payload.get(key):
                return int(payload[key])
    except ValueError:
        pass
    return 20


def _ms(started: float) -> int:
    return int((time.time() - started) * 1000)


# --------------------------------------------------------------------------- #
# registro no banco
# --------------------------------------------------------------------------- #
def record_upload(conn, *, target: str, export_id, chunk_id, filename: str, sha256: str, rows: int,
                  dry_run: bool, result: dict) -> int:
    cursor = conn.execute(
        """INSERT INTO uploads(created_at, target, export_id, chunk_id, filename, sha256, rows,
                               dry_run, status, http_status, target_ref, message, response_json, duration_ms)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            _now(), target, export_id, chunk_id, filename, sha256, rows, 1 if dry_run else 0,
            result.get("status", STATUS_ERROR), result.get("http_status"),
            result.get("target_ref"), (result.get("message") or "")[:1000],
            json.dumps(result.get("response"), ensure_ascii=False, default=str)[:8000]
            if result.get("response") is not None else None,
            result.get("duration_ms"),
        ),
    )
    return cursor.lastrowid


def load_chunk_obs_ids(chunk) -> list[int]:
    """Lê a lista de observações de um chunk (usada para marcar o incremental)."""
    path = chunk["obs_file"] if hasattr(chunk, "keys") else chunk.get("obs_file")
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    ids: list[int] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, list) and value:
                ids.append(int(value[0]))
    return ids


def load_cover_ids(chunk) -> list[int] | None:
    """Lê o arquivo de cobertura (linhas equivalentes cobertas por um lote)."""
    try:
        path = chunk["cover_file"]
    except (KeyError, IndexError, TypeError):
        return None
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    import gzip

    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return [int(linha) for linha in fh.read().split() if linha.strip().isdigit()]
