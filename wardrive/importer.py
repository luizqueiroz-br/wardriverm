"""Importação de arquivos de wardriving para o banco.

Fontes suportadas: pasta vigiada (OneDrive/Desktop/SD), upload pelo navegador
e arquivos .gz. A deduplicação é feita por (sha256, nome do arquivo), então
rodar o scan duas vezes não duplica nada.
"""
from __future__ import annotations

import gzip
import hashlib
from datetime import datetime
from pathlib import Path

from . import config, db
from .parser import parse_bytes

IMPORT_EXTENSIONS = {".log", ".csv", ".txt", ".gz", ".nettxt", ".wiglecsv"}
SKIP_NAMES = {"manifest.json", "upload.sh", "upload.ps1"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def import_bytes(conn, name: str, data: bytes, *, source: str = "upload", path: str | None = None,
                 force: bool = False) -> dict:
    """Importa um arquivo (bytes) e devolve o resultado da operação."""
    if name.lower().endswith(".gz"):
        try:
            data = gzip.decompress(data)
            name = name[:-3]
        except OSError as exc:
            return {"status": "erro", "file": name, "message": f"gzip inválido: {exc}"}

    digest = sha256_bytes(data)
    existing = conn.execute(
        "SELECT id FROM files WHERE sha256 = ? AND name = ?", (digest, name)
    ).fetchone()
    if existing and not force:
        return {
            "status": "ignorado",
            "file": name,
            "file_id": existing["id"],
            "message": "arquivo já importado (mesmo conteúdo e nome)",
        }
    if existing and force:
        delete_file(conn, existing["id"], rebuild=False)

    # confere se é mesmo um CSV WiGLE antes de gravar
    try:
        parsed = parse_bytes(data, name)
    except ValueError as exc:
        return {"status": "erro", "file": name, "message": str(exc)}

    stats = parsed["stats"]
    if existing and force:
        delete_file(conn, existing["id"], rebuild=False)
    empty = stats.get("obs_count", 0) == 0

    pre = parsed["preheader"]
    cursor = conn.execute(
        """
        INSERT INTO files(name, path, sha256, size, format_version, app_release, model, device,
                          display, board, brand, preheader, columns, has_frequency, obs_count,
                          wifi_count, ble_count, cell_count, network_count, no_gps_count, bad_count,
                          first_seen, last_seen, min_lat, max_lat, min_lon, max_lon, source, imported_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(sha256, name) DO UPDATE SET
            path=excluded.path, size=excluded.size, format_version=excluded.format_version,
            app_release=excluded.app_release, model=excluded.model, device=excluded.device,
            display=excluded.display, board=excluded.board, brand=excluded.brand,
            preheader=excluded.preheader, columns=excluded.columns, has_frequency=excluded.has_frequency,
            obs_count=excluded.obs_count, wifi_count=excluded.wifi_count, ble_count=excluded.ble_count,
            cell_count=excluded.cell_count, network_count=excluded.network_count,
            no_gps_count=excluded.no_gps_count, bad_count=excluded.bad_count,
            first_seen=excluded.first_seen, last_seen=excluded.last_seen, min_lat=excluded.min_lat,
            max_lat=excluded.max_lat, min_lon=excluded.min_lon, max_lon=excluded.max_lon,
            imported_at=excluded.imported_at
        """,
        (
            name, path, digest, len(data), pre.get("format_version", ""),
            pre.get("appRelease", ""), pre.get("model", ""), pre.get("device", ""),
            pre.get("display", ""), pre.get("board", ""), pre.get("brand", ""),
            pre.get("raw", ""), ",".join(parsed["columns"]), stats.get("has_frequency", 0),
            stats["obs_count"], stats["wifi_count"], stats["ble_count"], stats["cell_count"],
            stats["network_count"], stats["no_gps_count"], stats["bad_count"],
            stats["first_seen"], stats["last_seen"], stats["min_lat"], stats["max_lat"],
            stats["min_lon"], stats["max_lon"], source, _now(),
        ),
    )
    file_id = cursor.lastrowid or conn.execute(
        "SELECT id FROM files WHERE sha256 = ? AND name = ?", (digest, name)
    ).fetchone()["id"]

    conn.execute("DELETE FROM observations WHERE file_id = ?", (file_id,))
    conn.executemany(
        """INSERT INTO observations(file_id, mac, ssid, auth_mode, first_seen, channel, frequency,
                                    rssi, lat, lon, altitude, accuracy, rcois, mfgr_id, type, has_gps)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                file_id, r["mac"], r["ssid"], r["auth_mode"], r["first_seen"], r["channel"],
                r["frequency"], r["rssi"], r["lat"], r["lon"], r["altitude"], r["accuracy"],
                r["rcois"], r["mfgr_id"], r["type"], r["has_gps"],
            )
            for r in parsed["rows"]
        ],
    )
    db.rebuild_networks(conn, [r["mac"] for r in parsed["rows"]])
    db.log_event(
        conn, "aviso" if empty else "info", "import",
        (f"Sessão sem resultados importada: {name}" if empty else f"Importado {name}"),
        {"obs": stats["obs_count"], "redes": stats["network_count"], "sem_gps": stats["no_gps_count"]},
    )
    for warning in parsed["warnings"]:
        db.log_event(conn, "aviso", "import", f"{name}: {warning}")

    return {
        "status": "vazio" if empty else "ok",
        "file": name,
        "file_id": file_id,
        "sha256": digest,
        "stats": stats,
        "message": "arquivo sem linhas de dados (sessão registrada para controle)" if empty else "importado",
        "warnings": parsed["warnings"],
        "bad_rows": parsed["bad_rows"][:10],
        "preheader": pre.get("raw", ""),
        "columns": parsed["columns"],
    }


def import_path(conn, path: Path, *, source: str = "pasta", force: bool = False) -> dict:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return {"status": "erro", "file": str(path), "message": f"não foi possível ler: {exc}"}
    return import_bytes(conn, path.name, data, source=source, path=str(path), force=force)


def iter_candidate_files(dirs: list[str]):
    for raw in dirs:
        base = Path(raw)
        if base.is_file() and base.suffix.lower() in IMPORT_EXTENSIONS:
            yield base
            continue
        if not base.is_dir():
            continue
        for child in sorted(base.rglob("*")):
            if not child.is_file():
                continue
            if child.suffix.lower() not in IMPORT_EXTENSIONS or child.name.lower() in SKIP_NAMES:
                continue
            yield child


def scan_and_import(conn, dirs: list[str], *, force: bool = False, job=None) -> dict:
    """Varre as pastas configuradas e importa o que ainda não está no banco."""
    summary = {"novos": 0, "ignorados": 0, "vazios": 0, "erros": 0, "obs": 0, "arquivos": 0, "detalhes": []}
    files = list(iter_candidate_files(dirs))
    total = len(files)
    if job is not None:
        job.set_total(total, "arquivos")
    for path in files:
        result = import_path(conn, path, force=force)
        summary["arquivos"] += 1
        status = result["status"]
        if status == "ok":
            summary["novos"] += 1
            summary["obs"] += result["stats"]["obs_count"]
        elif status == "ignorado":
            summary["ignorados"] += 1
        elif status == "vazio":
            summary["vazios"] += 1
        else:
            summary["erros"] += 1
        if status in ("ok", "erro", "vazio"):
            summary["detalhes"].append(result)
        if job is not None:
            job.tick(f"{path.name}: {status}")
    return summary


def delete_file(conn, file_id: int, *, rebuild: bool = True) -> dict:
    row = conn.execute("SELECT name FROM files WHERE id = ?", (file_id,)).fetchone()
    if not row:
        return {"status": "erro", "message": "arquivo não encontrado"}
    macs = [r["mac"] for r in conn.execute(
        "SELECT DISTINCT mac FROM observations WHERE file_id = ?", (file_id,)).fetchall()]
    conn.execute("DELETE FROM observations WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    if rebuild:
        db.rebuild_networks(conn, macs)
        # remove redes que ficaram órfãs
        conn.execute("DELETE FROM networks WHERE mac NOT IN (SELECT mac FROM observations)")
    db.log_event(conn, "info", "import", f"Removido {row['name']} do banco")
    return {"status": "ok", "file": row["name"]}


def purge_empty(conn) -> int:
    rows = conn.execute("SELECT id FROM files WHERE obs_count = 0").fetchall()
    for row in rows:
        delete_file(conn, row["id"], rebuild=False)
    return len(rows)
