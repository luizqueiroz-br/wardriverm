"""Orquestração de uploads: envia chunks, trata limites e marca o incremental."""
from __future__ import annotations

import hashlib
import io
import json
import time
from datetime import datetime
from pathlib import Path

from . import config, db, exporter, uploaders
from .uploaders import (
    STATUS_DUPLICATE, STATUS_DRY_RUN, STATUS_ERROR, STATUS_OK, STATUS_RATE_LIMITED,
    STATUS_TIMEOUT, STATUS_TOO_LARGE,
)

SUCCESS_STATUSES = {STATUS_OK, STATUS_DUPLICATE}
MAX_SPLIT_DEPTH = 5
MAX_RETRIES = 3
MAX_SLEEP_SECONDS = 900


def _settings_bases(settings: dict) -> dict:
    return {
        "wigle": (settings["upload"].get("wigle_base") or config.WIGLE_API_BASE),
        "wdgwars": (settings["upload"].get("wdgwars_base") or config.WDGWARS_API_BASE),
    }


def upload_chunk(conn, chunk, target: str, settings: dict, *, dry_run: bool = True, job=None,
                 depth: int = 0) -> dict:
    """Envia um chunk respeitando os limites do destino.

    Em 413 (payload grande demais) e 524 (timeout do Cloudflare no WDGoWars),
    divide o lote em duas metades e reenvia — cada metade recebe sua própria
    linha de histórico.
    """
    bases = _settings_bases(settings)
    timeout = int(settings["upload"].get("timeout") or 300)
    path = Path(chunk["path"])
    if not path.exists():
        result = {"status": STATUS_ERROR, "message": f"arquivo não encontrado: {path}"}
        upload_id = uploaders.record_upload(
            conn, target=target, export_id=chunk["export_id"], chunk_id=chunk["id"],
            filename=chunk["filename"], sha256=chunk["sha256"], rows=chunk["rows"],
            dry_run=dry_run, result=result)
        return {"upload_id": upload_id, **result}

    content = path.read_bytes()
    obs_ids = uploaders.load_chunk_obs_ids(chunk)

    if dry_run:
        result = {
            "status": STATUS_DRY_RUN,
            "message": f"simulação: {len(content)} bytes, {chunk['rows']} linhas — nenhum POST enviado",
            "duration_ms": 0,
        }
        upload_id = uploaders.record_upload(
            conn, target=target, export_id=chunk["export_id"], chunk_id=chunk["id"],
            filename=chunk["filename"], sha256=chunk["sha256"], rows=chunk["rows"],
            dry_run=True, result=result)
        if job:
            job.write(f"[simulação] {chunk['filename']} → {target}: {chunk['rows']} linhas ({len(content)} bytes)")
        return {"upload_id": upload_id, **result}

    if job:
        job.write(f"enviando {chunk['filename']} → {config.TARGETS[target]['label']} "
                  f"({chunk['rows']} linhas, {len(content) // 1024} KB)")

    attempt = 0
    while True:
        attempt += 1
        try:
            if target == "wigle":
                creds = uploaders.creds_for("wigle")
                result = uploaders.wigle_post(
                    api_name=creds["name"], api_token=creds["token"], filename=chunk["filename"],
                    content=content, donate=bool(settings["upload"].get("wigle_donate")),
                    base=bases["wigle"], timeout=timeout)
            else:
                creds = uploaders.creds_for("wdgwars")
                result = uploaders.wdgwars_post(
                    api_key=creds["key"], filename=chunk["filename"], content=content,
                    base=bases["wdgwars"], timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - uma resposta estranha não pode derrubar o lote todo
            result = {"status": STATUS_ERROR,
                      "message": f"falha ao interpretar a resposta do servidor: {type(exc).__name__}: {exc}",
                      "duration_ms": None}
            if job:
                job.write(result["message"], "erro")

        if result["status"] == STATUS_RATE_LIMITED and attempt <= MAX_RETRIES:
            wait = min(int(result.get("retry_after") or 20), MAX_SLEEP_SECONDS)
            if job:
                job.write(f"destino ocupado (429) — aguardando {wait}s antes de tentar novamente", "aviso")
            time.sleep(wait)
            continue
        break

    upload_id = uploaders.record_upload(
        conn, target=target, export_id=chunk["export_id"], chunk_id=chunk["id"],
        filename=chunk["filename"], sha256=chunk["sha256"], rows=chunk["rows"],
        dry_run=False, result=result)

    if job:
        job.write(f"{chunk['filename']}: {result['status']} — {result.get('message', '')[:200]}",
                  "ok" if result["status"] in SUCCESS_STATUSES else "erro")

    if result["status"] in SUCCESS_STATUSES and obs_ids:
        marcadas = uploaders.load_cover_ids(chunk)
        if marcadas is None:
            marcadas = exporter.cover_ids_for(conn, chunk["export_id"], obs_ids)
        exporter.mark_uploaded(conn, marcadas, target, upload_id)
        if job and len(marcadas) > len(obs_ids):
            job.write(f"{len(obs_ids)} linha(s) enviada(s) cobrem {len(marcadas)} observações equivalentes "
                      f"(duplicatas descartadas pelo dedupe também saíram da fila)")

    if result["status"] == STATUS_OK and result.get("cooldown"):
        wait = min(int(result["cooldown"]), 120)
        if wait > 0:
            if job:
                job.write(f"o servidor pediu {wait}s de intervalo entre lotes", "aviso")
            time.sleep(wait)

    if result["status"] in (STATUS_TOO_LARGE, STATUS_TIMEOUT) and depth < MAX_SPLIT_DEPTH and chunk["rows"] > 1:
        parts = split_chunk(conn, chunk, settings, job=job)
        if parts:
            if job:
                job.write(f"{chunk['filename']} excedeu o limite — dividido em {len(parts)} partes")
            sub_results = []
            for part in parts:
                sub_results.append(upload_chunk(conn, part, target, settings, dry_run=dry_run,
                                                job=job, depth=depth + 1))
            result["split"] = sub_results
    return {"upload_id": upload_id, **result}


def split_chunk(conn, chunk, settings: dict, *, job=None) -> list[dict]:
    """Divide um chunk em duas metades no disco, registrando cada parte no banco."""
    rows = _chunk_rows(conn, chunk)
    if len(rows) < 2:
        return []
    obs_ids = [r["id"] for r in rows]
    half = len(rows) // 2
    halves = [(rows[:half], obs_ids[:half]), (rows[half:], obs_ids[half:])]
    fields = _preheader_for(conn, chunk)

    out_dir = Path(chunk["path"]).parent / "resplit"
    out_dir.mkdir(parents=True, exist_ok=True)
    parts: list[dict] = []
    for i, (part_rows, part_ids) in enumerate(halves, start=1):
        base = f"{Path(chunk['filename']).stem}_s{chunk['idx']}{i}"
        content = exporter.build_csv(part_rows, fields)
        csv_path = out_dir / f"{base}.csv"
        csv_path.write_bytes(content)
        ids_path = out_dir / f"{base}.ids.jsonl"
        with ids_path.open("w", encoding="utf-8") as fh:
            for rid, fid in zip(part_ids, [r["file_id"] for r in part_rows]):
                fh.write(json.dumps([rid, fid]) + "\n")
        idx = chunk["idx"] * 1000 + i
        cursor = conn.execute(
            """INSERT INTO export_chunks(export_id, idx, filename, path, rows, bytes, sha256, obs_file, gzipped)
               VALUES(?,?,?,?,?,?,?,?,0)""",
            (chunk["export_id"], idx, csv_path.name, str(csv_path), len(part_rows),
             len(content), hashlib.sha256(content).hexdigest(), str(ids_path)),
        )
        row = conn.execute("SELECT * FROM export_chunks WHERE id = ?", (cursor.lastrowid,)).fetchone()
        parts.append(dict(row))
        if job:
            job.write(f"parte gerada: {csv_path.name} ({len(part_rows)} linhas)")
    return parts


def _chunk_rows(conn, chunk) -> list[dict]:
    ids = uploaders.load_chunk_obs_ids(chunk)
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""SELECT id, file_id, mac, ssid, auth_mode, first_seen, channel, frequency, rssi,
                   lat, lon, altitude, accuracy, rcois, mfgr_id, type, has_gps
              FROM observations WHERE id IN ({placeholders})""",
        tuple(ids),
    ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def _preheader_for(conn, chunk) -> dict:
    from .parser import parse_preheader

    export = conn.execute("SELECT * FROM exports WHERE id = ?", (chunk["export_id"],)).fetchone()
    raw = (export["preheader"] if export else "") or ""
    fields = parse_preheader(raw) if raw else {}
    fields["format_version"] = config.WIGLE_FORMAT_VERSION
    return fields


# --------------------------------------------------------------------------- #
# envio de um export inteiro
# --------------------------------------------------------------------------- #
def upload_export(conn, export_id: int, target: str, settings: dict, *, dry_run: bool = True,
                  job=None, chunk_ids: list[int] | None = None) -> dict:
    if chunk_ids:
        placeholders = ",".join("?" * len(chunk_ids))
        chunks = conn.execute(
            f"SELECT * FROM export_chunks WHERE id IN ({placeholders}) ORDER BY idx", tuple(chunk_ids)
        ).fetchall()
    else:
        chunks = conn.execute(
            "SELECT * FROM export_chunks WHERE export_id = ? ORDER BY idx", (export_id,)
        ).fetchall()

    chunks = [dict(c) for c in chunks if "resplit" not in Path(c["path"]).parts]
    summary = {"enviados": 0, "ok": 0, "duplicados": 0, "erros": 0, "simulados": 0,
               "linhas": 0, "resultados": []}
    if job:
        job.set_total(len(chunks), "lotes")
    for chunk in chunks:
        result = upload_chunk(conn, chunk, target, settings, dry_run=dry_run, job=job)
        summary["resultados"].append({"chunk": chunk["filename"], **{k: result.get(k) for k in
                                     ("status", "message", "http_status", "target_ref", "upload_id")}})
        summary["enviados"] += 1
        summary["linhas"] += chunk["rows"]
        if result["status"] == STATUS_OK:
            summary["ok"] += 1
        elif result["status"] == STATUS_DUPLICATE:
            summary["duplicados"] += 1
        elif result["status"] == STATUS_DRY_RUN:
            summary["simulados"] += 1
        else:
            summary["erros"] += 1
        if job:
            job.tick()
    db.log_event(conn, "info", "upload",
                 f"Upload {target} export #{export_id}: {summary['ok']} ok, "
                 f"{summary['duplicados']} dup, {summary['erros']} erro(s)",
                 {"linhas": summary["linhas"], "dry_run": dry_run})
    return summary


def export_and_upload(conn, *, target: str, selection: dict, settings: dict, label: str | None = None,
                      dry_run: bool = True, job=None) -> dict:
    """Cria o export (seleção → arquivos) e envia em seguida."""
    if job:
        job.write(f"selecionando dados e montando lote para {config.TARGETS[target]['label']}")
    created = exporter.create_export(conn, target=target, selection=selection, settings=settings, label=label)
    if job:
        job.write(f"export #{created['export_id']}: {created['rows']} linhas em {created['chunks']} arquivo(s) "
                  f"({created['bytes'] // 1024} KB) em {created['dir']}")
        priv = created.get("privacidade") or {}
        if priv.get("omitidas") or priv.get("zeradas"):
            job.write(f"áreas privadas: {priv.get('omitidas', 0)} linha(s) omitida(s) e "
                      f"{priv.get('zeradas', 0)} sem coordenada")
    if created["rows"] == 0:
        return {"export": created, "upload": None,
                "message": "nenhuma linha corresponde à seleção — nada foi enviado"}
    upload = upload_export(conn, created["export_id"], target, settings, dry_run=dry_run, job=job)
    return {"export": created, "upload": upload}
