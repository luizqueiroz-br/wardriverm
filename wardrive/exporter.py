"""Geração de CSVs compatíveis com upload no WiGLE e no WDGoWars.

Regras que garantem a compatibilidade (ver docs/COMPATIBILIDADE.md):
  * pre-header `WigleWifi-1.6,appRelease=…,model=…,device=…,board=…,brand=…`
    com escape RFC 4180 (valores com vírgula entre aspas);
  * cabeçalho completo de 14 colunas, sempre repetido em TODA parte do lote
    (um fragmento sem cabeçalho faz o importador do WiGLE ler o layout antigo
     e interpretar RSSI como latitude);
  * quebras de linha CRLF e codificação UTF-8;
  * cada parte respeita os limites de tamanho/linhas do destino
    (WiGLE ~40 MB por arquivo; WDGoWars 15 MB e timeout de 120 s do Cloudflare).
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import re
from datetime import datetime
from pathlib import Path

from . import config, privacidade

TARGET_LIMITS = config.TARGETS


def build_preheader(fields: dict) -> str:
    """Monta a linha de pre-header com escape RFC 4180."""
    order = ["appRelease", "model", "release", "device", "display", "board", "brand",
             "star", "body", "subBody"]
    parts = [config.WIGLE_FORMAT_VERSION if not fields.get("format_version")
             else _ensure_16(fields.get("format_version"))]
    for key in order:
        value = (fields.get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(parts)
    return buf.getvalue()


def _ensure_16(version: str) -> str:
    """Sobe formatos antigos (1.0/1.2/1.4) para 1.6, mantendo 1.6 como está."""
    v = (version or "").strip()
    if not v or not v.lower().startswith("wiglewifi"):
        return config.WIGLE_FORMAT_VERSION
    m = re.match(r"WigleWifi-(\d+)\.(\d+)", v, re.IGNORECASE)
    if not m:
        return config.WIGLE_FORMAT_VERSION
    major, minor = int(m.group(1)), int(m.group(2))
    return config.WIGLE_FORMAT_VERSION if (major, minor) < (1, 6) else v


def format_number(value, decimals: int | None = None, integer: bool = False) -> str:
    if value is None:
        return ""
    if integer:
        return str(int(round(float(value))))
    if decimals is not None:
        text = f"{float(value):.{decimals}f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-") else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def row_to_csv(row: dict) -> list[str]:
    """Converte uma observação do banco nas 14 colunas do WiGLE."""
    type_ = (row.get("type") or "WIFI").upper()
    frequency = row.get("frequency")
    if type_ in ("BLE", "BT") and not frequency:
        frequency = ""  # código de tipo de dispositivo BT é opcional
    return [
        (row.get("mac") or "").upper(),
        row.get("ssid") or "",
        row.get("auth_mode") or "",
        row.get("first_seen") or "",
        format_number(row.get("channel"), integer=True) if row.get("channel") else "",
        format_number(frequency, integer=True) if frequency else "",
        format_number(row.get("rssi"), integer=True) if row.get("rssi") is not None else "",
        format_number(row.get("lat"), decimals=8) if row.get("has_gps") else "",
        format_number(row.get("lon"), decimals=8) if row.get("has_gps") else "",
        format_number(row.get("altitude"), integer=True) if row.get("altitude") is not None else "",
        format_number(row.get("accuracy"), decimals=2) if row.get("accuracy") is not None else "",
        row.get("rcois") or "",
        row.get("mfgr_id") or "",
        type_,
    ]


def build_csv(rows: list[dict], preheader_fields: dict) -> bytes:
    """CSV completo (pre-header + cabeçalho de 14 colunas + linhas)."""
    buf = io.StringIO()
    buf.write(build_preheader(preheader_fields))
    buf.write("\r\n")
    buf.write(",".join(config.WIGLE_COLUMNS))
    buf.write("\r\n")
    writer = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    for row in rows:
        writer.writerow(row_to_csv(row))
    return buf.getvalue().encode("utf-8")


def split_chunks(rows: list[dict], preheader_fields: dict, rows_per_chunk: int, max_bytes: int):
    """Divide as linhas em partes respeitando limites de linhas e de bytes."""
    header_bytes = len(build_csv([], preheader_fields))
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_bytes = header_bytes
    for row in rows:
        line_bytes = len((",".join(row_to_csv(row)) + "\r\n").encode("utf-8")) + 8  # folga p/ aspas
        if current and (len(current) >= rows_per_chunk or current_bytes + line_bytes > max_bytes):
            chunks.append(current)
            current, current_bytes = [], header_bytes
        current.append(row)
        current_bytes += line_bytes
    if current:
        chunks.append(current)
    return chunks


# --------------------------------------------------------------------------- #
# seleção de observações
# --------------------------------------------------------------------------- #
DEDUPE_SQL = {
    # mantém a primeira ocorrência exata (mesmo MAC, instante e ponto)
    "exact": """
        SELECT MIN(o.id) AS id FROM observations o {joins} {where}
         GROUP BY o.mac, o.first_seen, o.lat, o.lon, o.type
    """,
    # uma amostra por MAC por sessão: a de sinal mais forte
    "network": """
        SELECT o.id FROM observations o {joins} {where}
         GROUP BY o.file_id, o.mac
         HAVING o.rssi = MAX(o.rssi)
    """,
    "none": "SELECT o.id FROM observations o {joins} {where}",
}


def _filters(selection: dict, exclude_target: str | None = None) -> tuple[str, list]:
    """Cláusula WHERE comum à seleção e ao cálculo de linhas equivalentes."""
    where: list[str] = []
    params: list = []

    mode = selection.get("mode", "all")
    file_ids = [int(i) for i in selection.get("file_ids") or []]
    if mode == "files" and file_ids:
        placeholders = ",".join("?" * len(file_ids))
        where.append(f"o.file_id IN ({placeholders})")
        params.extend(file_ids)
    date_from = (selection.get("date_from") or "").strip()
    date_to = (selection.get("date_to") or "").strip()
    if date_from:
        where.append("o.first_seen >= ?")
        params.append(date_from)
    if date_to:
        where.append("o.first_seen <= ?")
        params.append(date_to + " 23:59:59" if len(date_to) == 10 else date_to)

    types = [t for t in (selection.get("types") or []) if t]
    if types:
        where.append(f"o.type IN ({','.join('?' * len(types))})")
        params.extend(types)

    if selection.get("with_gps_only", True) or selection.get("exclude_no_gps"):
        where.append("o.has_gps = 1")

    if mode == "new" and exclude_target:
        where.append(
            "NOT EXISTS (SELECT 1 FROM obs_uploads u WHERE u.obs_id = o.id AND u.target = ?)"
        )
        params.append(exclude_target)

    max_rssi = selection.get("min_rssi")
    if max_rssi not in (None, ""):
        where.append("o.rssi <= ?")
        params.append(int(max_rssi))

    return (("WHERE " + " AND ".join(where)) if where else ""), params


def build_selection_sql(conn, selection: dict, exclude_target: str | None = None):
    """Monta o SQL que devolve as observações selecionadas (na ordem de exportação)."""
    where_sql, params = _filters(selection, exclude_target)
    dedupe = selection.get("dedupe") or "exact"
    sql = DEDUPE_SQL.get(dedupe, DEDUPE_SQL["exact"]).format(joins="", where=where_sql)
    sql = f"SELECT o.id, o.file_id, o.mac, o.ssid, o.auth_mode, o.first_seen, o.channel, " \
          f"o.frequency, o.rssi, o.lat, o.lon, o.altitude, o.accuracy, o.rcois, o.mfgr_id, " \
          f"o.type, o.has_gps FROM observations o WHERE o.id IN ({sql}) ORDER BY o.first_seen, o.id"
    return sql, params


def fetch_rows(conn, selection: dict, exclude_target: str | None = None) -> list[dict]:
    sql, params = build_selection_sql(conn, selection, exclude_target)
    rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def group_key(row, dedupe: str):
    """Chave de equivalência usada para saber o que uma linha representa."""
    if dedupe == "network":
        return (row["file_id"], row["mac"])
    return (row["mac"], row["first_seen"], row["lat"], row["lon"], row["type"])


def fetch_covered_map(conn, selection: dict, exclude_target: str | None = None) -> dict:
    """Mapeia cada linha exportada para TODAS as observações equivalentes.

    Sem isso, as linhas descartadas como duplicata (dedupe) nunca seriam marcadas
    como enviadas e voltariam a aparecer na fila incremental a cada rodada.
    """
    dedupe = selection.get("dedupe") or "exact"
    if dedupe == "none":
        return {}
    where_sql, params = _filters(selection, exclude_target)
    grupos: dict[tuple, list[int]] = {}
    linhas = conn.execute(
        f"""SELECT o.id, o.file_id, o.mac, o.first_seen, o.lat, o.lon, o.type
              FROM observations o {where_sql}""",
        tuple(params),
    ).fetchall()
    for linha in linhas:
        grupos.setdefault(group_key(linha, dedupe), []).append(linha["id"])
    return grupos


# --------------------------------------------------------------------------- #
# criação do export
# --------------------------------------------------------------------------- #
def pick_preheader(conn, rows: list[dict], selection: dict, settings: dict, target: str) -> dict:
    """Escolhe o pre-header: do arquivo de origem (fiel aos dados) ou do perfil."""
    header_mode = (selection.get("header_mode") or settings["export"].get("header_mode") or "source")
    profile = settings.get("device") or {}
    if header_mode == "profile":
        return dict(profile)
    file_ids = sorted({r["file_id"] for r in rows if r.get("file_id")})
    if len(file_ids) == 1:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_ids[0],)).fetchone()
        if row:
            return {
                "format_version": config.WIGLE_FORMAT_VERSION,
                "appRelease": row["app_release"] or profile.get("appRelease", ""),
                "model": row["model"] or profile.get("model", ""),
                "release": row["app_release"] or profile.get("appRelease", ""),
                "device": row["device"] or profile.get("device", ""),
                "display": row["display"] or profile.get("display", ""),
                "board": row["board"] or profile.get("board", ""),
                "brand": row["brand"] or profile.get("brand", ""),
            }
    counts = conn.execute(
        "SELECT brand, model, COUNT(*) c FROM files GROUP BY brand, model ORDER BY c DESC LIMIT 1"
    ).fetchone()
    # Lote com vários arquivos: usa a atribuição do conjunto selecionado (a mais
    # frequente) e só completa com o perfil o que faltar.
    ids = sorted({r["file_id"] for r in rows if r.get("file_id")})
    fields: dict = {"format_version": config.WIGLE_FORMAT_VERSION}
    if ids:
        placeholders = ",".join("?" * len(ids))
        origem = conn.execute(
            f"""SELECT brand, model, device, display, board, app_release, COUNT(*) c
                  FROM files WHERE id IN ({placeholders})
                 GROUP BY brand, model, device, display, board, app_release
                 ORDER BY c DESC LIMIT 1""",
            tuple(ids),
        ).fetchone()
        if origem:
            fields.update({
                "appRelease": origem["app_release"] or "",
                "release": origem["app_release"] or "",
                "model": origem["model"] or "",
                "device": origem["device"] or "",
                "display": origem["display"] or "",
                "board": origem["board"] or "",
                "brand": origem["brand"] or "",
            })
    for chave in ("appRelease", "release", "model", "device", "display", "board", "brand"):
        if not fields.get(chave):
            fields[chave] = profile.get(chave, "")
    if not fields.get("brand") and counts and counts["brand"]:
        fields["brand"] = counts["brand"]
    return fields


def create_export(conn, *, target: str, selection: dict, settings: dict, label: str | None = None) -> dict:
    """Gera os arquivos e registra o export no banco."""
    limits = TARGET_LIMITS[target]
    rows_per_chunk = int(
        (settings["export"].get("rows_per_chunk") if target == "wigle" else settings["export"].get("wdgwars_rows_per_chunk"))
        or settings["export"].get("rows_per_chunk")
        or limits["rows_per_chunk"]
    )
    # WDGoWars: lotes menores evitam o 524 (Cloudflare) do origin síncrono
    if target == "wdgwars":
        rows_per_chunk = min(rows_per_chunk, 10000)
    max_bytes = int(settings["export"].get(f"{target}_max_bytes") or limits["max_bytes"])
    gzip_out = bool(settings["export"].get("gzip_wigle")) and target == "wigle"

    selection = dict(selection)
    selection.setdefault("with_gps_only", not settings["export"].get("include_no_gps", False))
    selection.setdefault("dedupe", settings["export"].get("dedupe", "exact"))

    rows = fetch_rows(conn, selection, exclude_target=target if selection.get("mode") == "new" else None)
    total_selecionado = len(rows)
    # áreas privadas: o dado da casa/trabalho não sai daqui. As linhas omitidas
    # também não entram no mapa de cobertura, então continuam pendentes (se a área
    # for removida depois, elas voltam para a fila em vez de sumirem para sempre).
    rows, resumo_privacidade = privacidade.aplicar(rows, privacidade.listar(conn))
    resumo_privacidade["total_selecionado"] = total_selecionado
    cobertos = fetch_covered_map(conn, selection,
                                 exclude_target=target if selection.get("mode") == "new" else None)
    preheader_fields = pick_preheader(conn, rows, selection, settings, target)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (label or f"{target}-{selection.get('mode', 'all')}")).strip("-")[:60]
    out_dir = config.EXPORT_DIR / f"{stamp}_{target}_{slug or 'export'}"
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks = split_chunks(rows, preheader_fields, rows_per_chunk, max_bytes) if rows else []
    manifest = {
        "app": config.APP_NAME,
        "app_version": config.APP_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "target": target,
        "target_label": limits["label"],
        "label": label,
        "selection": selection,
        "rows": len(rows),
        "privacidade": resumo_privacidade,
        "chunks": [],
        "preheader": build_preheader(preheader_fields),
        "header": ",".join(config.WIGLE_COLUMNS),
        "limits": {"rows_per_chunk": rows_per_chunk, "max_bytes": max_bytes, "gzip": gzip_out},
    }

    cursor = conn.execute(
        "INSERT INTO exports(created_at, target, label, dir, selection_json, params_json, preheader) "
        "VALUES(?,?,?,?,?,?,?)",
        (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            target,
            label,
            str(out_dir),
            json.dumps(selection, ensure_ascii=False),
            json.dumps({"rows_per_chunk": rows_per_chunk, "max_bytes": max_bytes, "gzip": gzip_out}),
            manifest["preheader"],
        ),
    )
    export_id = cursor.lastrowid

    total_bytes = 0
    for idx, chunk_rows in enumerate(chunks, start=1):
        base = f"part{idx:02d}"
        csv_name = f"{base}.csv"
        content = build_csv(chunk_rows, preheader_fields)
        csv_path = out_dir / csv_name
        csv_path.write_bytes(content)

        ids_path = out_dir / f"{base}.ids.jsonl"
        with ids_path.open("w", encoding="utf-8") as fh:
            for row in chunk_rows:
                fh.write(json.dumps([row["id"], row["file_id"]]) + "\n")

        # linhas equivalentes cobertas por esta parte (duplicatas descartadas pelo dedupe):
        # são marcadas como enviadas junto com o representante, senão voltariam à fila
        cover_path = None
        if cobertos:
            ids_cobertos: list[int] = []
            vistos: set[int] = set()
            for row in chunk_rows:
                for obs_id in cobertos.get(group_key(row, selection["dedupe"]), [row["id"]]):
                    if obs_id not in vistos:
                        vistos.add(obs_id)
                        ids_cobertos.append(obs_id)
            if len(ids_cobertos) > len(chunk_rows):
                cover_path = out_dir / f"{base}.cover.txt.gz"
                conteudo_cover = "\n".join(str(i) for i in ids_cobertos).encode("utf-8")
                cover_path.write_bytes(gzip.compress(conteudo_cover, mtime=0))
                manifest.setdefault("cobertura", {})[base] = len(ids_cobertos)

        delivered_name = csv_name
        delivered_path = csv_path
        gzipped = 0
        if gzip_out:
            gz_path = out_dir / f"{base}.csv.gz"
            gz_path.write_bytes(gzip.compress(content, mtime=0))
            delivered_name, delivered_path, gzipped = gz_path.name, gz_path, 1

        digest = hashlib.sha256(content).hexdigest()
        conn.execute(
            "INSERT INTO export_chunks(export_id, idx, filename, path, rows, bytes, sha256, obs_file, "
            "cover_file, gzipped) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                export_id, idx, delivered_name, str(delivered_path), len(chunk_rows),
                content.__len__(), digest, str(ids_path),
                str(cover_path) if cover_path else None, gzipped,
            ),
        )
        total_bytes += len(content)
        manifest["chunks"].append(
            {
                "idx": idx,
                "file": delivered_name,
                "rows": len(chunk_rows),
                "bytes": len(content),
                "sha256": digest,
                "obs_file": ids_path.name,
                "cover_file": cover_path.name if cover_path else None,
            }
        )

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    write_upload_scripts(out_dir, target, manifest, settings)

    conn.execute(
        "UPDATE exports SET chunk_count = ?, row_count = ?, bytes = ? WHERE id = ?",
        (len(chunks), len(rows), total_bytes, export_id),
    )
    return {"export_id": export_id, "dir": str(out_dir), "rows": len(rows), "chunks": len(chunks),
            "bytes": total_bytes, "manifest": manifest, "privacidade": resumo_privacidade}


def write_upload_scripts(out_dir: Path, target: str, manifest: dict, settings: dict) -> None:
    """Gera scripts prontos (curl / PowerShell) para subir o lote sem a interface."""
    name = manifest["chunks"]
    if target == "wigle":
        base = settings["upload"].get("wigle_base") or config.WIGLE_API_BASE
        donate = "true" if settings["upload"].get("wigle_donate") else "false"
        sh = [
            "#!/usr/bin/env bash",
            "# Upload WiGLE — exporte WIGLE_API_NAME e WIGLE_API_TOKEN antes de rodar.",
            "set -euo pipefail",
            ': "${WIGLE_API_NAME:?defina WIGLE_API_NAME (nome da API em wigle.net/account)}"',
            ': "${WIGLE_API_TOKEN:?defina WIGLE_API_TOKEN (token em wigle.net/account)}"',
            f'BASE="{base}"',
            'TOKEN=$(printf "%s:%s" "$WIGLE_API_NAME" "$WIGLE_API_TOKEN" | base64 | tr -d "\\n")',
            'cd "$(dirname "$0")"',
        ]
        ps = [
            "# Upload WiGLE (PowerShell)",
            f'$base = "{base}"',
            '$pair = "$($env:WIGLE_API_NAME):$($env:WIGLE_API_TOKEN)"',
            "$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))",
            f'Set-Location $PSScriptRoot',
        ]
        for chunk in name:
            sh.append(
                f'curl -sS -X POST "$BASE{config.WIGLE_UPLOAD_PATH}" -H "Authorization: Basic $TOKEN" '
                f'-H "Accept: application/json" -F "donate={donate}" -F "file=@{chunk["file"]};type=text/csv"'
            )
            ps.append(
                f'Invoke-RestMethod -Method Post -Uri "$base{config.WIGLE_UPLOAD_PATH}" '
                f'-Headers @{{ Authorization = "Basic $b64" }} -Form @{{ donate = "{donate}"; '
                f'file = Get-Item "{chunk["file"]}" }}'
            )
    else:
        base = settings["upload"].get("wdgwars_base") or config.WDGWARS_API_BASE
        sh = [
            "#!/usr/bin/env bash",
            "# Upload WDGoWars — exporte WDGWARS_API_KEY antes de rodar.",
            "set -euo pipefail",
            ': "${WDGWARS_API_KEY:?defina WDGWARS_API_KEY (chave do seu perfil em wdgwars.pl)}"',
            f'BASE="{base}"',
            'cd "$(dirname "$0")"',
        ]
        ps = [
            "# Upload WDGoWars (PowerShell)",
            f'$base = "{base}"',
            "Set-Location $PSScriptRoot",
        ]
        for chunk in name:
            sh.append(
                f'curl -sS -X POST "$BASE{config.WDGWARS_CSV_PATH}" -H "X-API-Key: $WDGWARS_API_KEY" '
                f'-F "file=@{chunk["file"]};type=text/csv"'
            )
            ps.append(
                f'Invoke-RestMethod -Method Post -Uri "$base{config.WDGWARS_CSV_PATH}" '
                f'-Headers @{{ "X-API-Key" = $env:WDGWARS_API_KEY }} -Form @{{ file = Get-Item "{chunk["file"]}" }}'
            )
    # newline="\n" é essencial: no Windows o Python converte \n em \r\n, e o \r deixa
    # o script bash inutilizável ("set: pipefail\r: invalid option") além de contaminar
    # o nome do arquivo enviado pelo curl.
    (out_dir / "upload.sh").write_text("\n".join(sh) + "\n", encoding="utf-8", newline="\n")
    (out_dir / "upload.ps1").write_text("\n".join(ps) + "\n", encoding="utf-8", newline="\n")


def cover_ids_for(conn, export_id: int, ids: list[int]) -> list[int]:
    """Observações equivalentes às enviadas (para o dedupe não deixar resto na fila).

    Usado quando o chunk não tem arquivo de cobertura — caso das partes geradas
    na divisão automática de um lote grande.
    """
    if not ids:
        return []
    export = conn.execute("SELECT selection_json FROM exports WHERE id = ?", (export_id,)).fetchone()
    selection = json.loads(export["selection_json"] or "{}") if export else {}
    dedupe = selection.get("dedupe") or "exact"
    if dedupe == "none":
        return list(ids)

    placeholders = ",".join("?" * len(ids))
    enviadas = conn.execute(
        f"""SELECT id, file_id, mac, first_seen, lat, lon, type FROM observations
             WHERE id IN ({placeholders})""",
        tuple(ids),
    ).fetchall()
    chaves = {group_key(linha, dedupe) for linha in enviadas}
    if not chaves:
        return list(ids)

    where_sql, params = _filters({**selection, "mode": selection.get("mode", "all")}, exclude_target=None)
    cobertas = []
    for linha in conn.execute(
        f"""SELECT id, file_id, mac, first_seen, lat, lon, type FROM observations o {where_sql}""",
        tuple(params),
    ):
        if group_key(linha, dedupe) in chaves:
            cobertas.append(linha["id"])
    return cobertas or list(ids)


def mark_uploaded(conn, obs_ids: list[int], target: str, upload_id: int) -> int:
    """Marca observações como já enviadas (usado pelo modo incremental)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.executemany(
        "INSERT INTO obs_uploads(obs_id, target, upload_id, ts) VALUES(?,?,?,?) "
        "ON CONFLICT(obs_id, target) DO UPDATE SET upload_id=excluded.upload_id, ts=excluded.ts",
        [(int(o), target, upload_id, now) for o in obs_ids],
    )
    return len(obs_ids)
