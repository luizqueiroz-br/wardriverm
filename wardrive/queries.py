"""Consultas e agregações usadas pelas telas (dashboard, listas, mapa)."""
from __future__ import annotations

from . import config


def overview(conn) -> dict:
    files = conn.execute(
        """SELECT COUNT(*) AS arquivos,
                  COALESCE(SUM(obs_count), 0) AS observacoes,
                  COALESCE(SUM(no_gps_count), 0) AS sem_gps,
                  COALESCE(SUM(bad_count), 0) AS invalidas,
                  COALESCE(SUM(wifi_count), 0) AS wifi,
                  COALESCE(SUM(ble_count), 0) AS ble,
                  COALESCE(SUM(cell_count), 0) AS celular,
                  MIN(first_seen) AS inicio,
                  MAX(last_seen) AS fim
             FROM files"""
    ).fetchone()
    networks = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN type = 'WIFI' THEN 1 ELSE 0 END) AS wifi,
                  SUM(CASE WHEN type IN ('BLE','BT') THEN 1 ELSE 0 END) AS ble,
                  SUM(CASE WHEN type NOT IN ('WIFI','BLE','BT') THEN 1 ELSE 0 END) AS celular,
                  SUM(CASE WHEN ssid IS NOT NULL AND ssid <> '' THEN 1 ELSE 0 END) AS com_ssid
             FROM networks"""
    ).fetchone()
    exports = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(row_count),0) AS linhas FROM exports"
    ).fetchone()
    uploads = conn.execute(
        """SELECT target, status, COUNT(*) AS n,
                  MAX(created_at) AS ultimo
             FROM uploads WHERE dry_run = 0 GROUP BY target, status"""
    ).fetchall()
    pending = {
        "wigle": conn.execute(
            """SELECT COUNT(*) FROM observations o
                WHERE o.has_gps = 1 AND NOT EXISTS (
                    SELECT 1 FROM obs_uploads u WHERE u.obs_id = o.id AND u.target = 'wigle')"""
        ).fetchone()[0],
        "wdgwars": conn.execute(
            """SELECT COUNT(*) FROM observations o
                WHERE o.has_gps = 1 AND NOT EXISTS (
                    SELECT 1 FROM obs_uploads u WHERE u.obs_id = o.id AND u.target = 'wdgwars')"""
        ).fetchone()[0],
    }
    return {
        "arquivos": files["arquivos"],
        "observacoes": files["observacoes"],
        "sem_gps": files["sem_gps"],
        "invalidas": files["invalidas"],
        "wifi": files["wifi"],
        "ble": files["ble"],
        "celular": files["celular"],
        "inicio": files["inicio"],
        "fim": files["fim"],
        "redes": networks["total"] or 0,
        "redes_wifi": networks["wifi"] or 0,
        "redes_ble": networks["ble"] or 0,
        "redes_celular": networks["celular"] or 0,
        "redes_com_ssid": networks["com_ssid"] or 0,
        "exports": exports["n"] or 0,
        "exports_linhas": exports["linhas"] or 0,
        "uploads": [dict(u) for u in uploads],
        "pendentes": pending,
    }


def daily_series(conn, days: int = 30) -> list[dict]:
    rows = conn.execute(
        """SELECT substr(first_seen, 1, 10) AS dia,
                  COUNT(*) AS obs,
                  COUNT(DISTINCT mac) AS redes,
                  SUM(CASE WHEN type = 'WIFI' THEN 1 ELSE 0 END) AS wifi,
                  SUM(CASE WHEN type IN ('BLE','BT') THEN 1 ELSE 0 END) AS ble
             FROM observations
            WHERE first_seen IS NOT NULL AND first_seen >= date('now', ?)
            GROUP BY dia ORDER BY dia""",
        (f"-{int(days)} day",),
    ).fetchall()
    return [dict(r) for r in rows]


def top_ssids(conn, limit: int = 12) -> list[dict]:
    rows = conn.execute(
        """SELECT ssid, COUNT(DISTINCT mac) AS redes, COUNT(*) AS obs
             FROM observations
            WHERE ssid IS NOT NULL AND ssid <> ''
            GROUP BY ssid ORDER BY redes DESC, obs DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def top_auth_modes(conn, limit: int = 12) -> list[dict]:
    rows = conn.execute(
        """SELECT auth_mode, COUNT(*) AS obs
             FROM observations WHERE auth_mode <> '' GROUP BY auth_mode
            ORDER BY obs DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def channel_histogram(conn, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """SELECT type, channel, COUNT(*) AS obs
             FROM observations WHERE channel IS NOT NULL AND channel > 0
            GROUP BY type, channel ORDER BY obs DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def sessions(conn, *, search: str = "", limit: int = 100, offset: int = 0) -> list[dict]:
    where, params = "", ()
    if search:
        where = "WHERE (f.name LIKE ? OR f.model LIKE ? OR f.brand LIKE ?)"
        like = f"%{search}%"
        params = (like, like, like)
    rows = conn.execute(
        f"""SELECT f.*,
                   (SELECT COUNT(*) FROM observations o
                      JOIN obs_uploads u ON u.obs_id = o.id AND u.target = 'wigle'
                     WHERE o.file_id = f.id) AS up_wigle,
                   (SELECT COUNT(*) FROM observations o
                      JOIN obs_uploads u ON u.obs_id = o.id AND u.target = 'wdgwars'
                     WHERE o.file_id = f.id) AS up_wdgwars
              FROM files f {where} ORDER BY f.first_seen DESC, f.id DESC LIMIT ? OFFSET ?""",
        params + (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


def session_count(conn, search: str = "") -> int:
    if search:
        like = f"%{search}%"
        return conn.execute(
            "SELECT COUNT(*) FROM files WHERE name LIKE ? OR model LIKE ? OR brand LIKE ?",
            (like, like, like)).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]


def session(conn, file_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    return dict(row) if row else None


NETWORK_SORTS = {
    "obs": "obs_count DESC",
    "ssid": "ssid COLLATE NOCASE ASC",
    "rssi": "best_rssi DESC",
    "recente": "last_seen DESC",
    "primeiro": "first_seen ASC",
    "mac": "mac ASC",
    "sessoes": "file_count DESC",
}


def networks(conn, *, search: str = "", type_: str = "", auth: str = "", date_from: str = "",
             date_to: str = "", only_no_ssid: bool = False, sort: str = "obs",
             limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    where, params = [], []
    if search:
        like = f"%{search}%"
        where.append("(n.ssid LIKE ? OR n.mac LIKE ?)")
        params += [like, like]
    if type_:
        types = [t for t in type_.split(",") if t]
        where.append(f"n.type IN ({','.join('?' * len(types))})")
        params += types
    if auth:
        where.append("n.auth_mode LIKE ?")
        params.append(f"%{auth}%")
    if date_from:
        where.append("n.last_seen >= ?")
        params.append(date_from)
    if date_to:
        where.append("n.first_seen <= ?")
        params.append(date_to + " 23:59:59" if len(date_to) == 10 else date_to)
    if only_no_ssid:
        where.append("(n.ssid IS NULL OR n.ssid = '')")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    order = NETWORK_SORTS.get(sort, NETWORK_SORTS["obs"])
    total = conn.execute(f"SELECT COUNT(*) FROM networks n {where_sql}", tuple(params)).fetchone()[0]
    rows = conn.execute(
        f"SELECT n.* FROM networks n {where_sql} ORDER BY {order} LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows], total


def network_detail(conn, mac: str) -> dict | None:
    net = conn.execute("SELECT * FROM networks WHERE mac = ?", (mac,)).fetchone()
    if not net:
        return None
    obs = conn.execute(
        """SELECT o.*, f.name AS file_name FROM observations o
             JOIN files f ON f.id = o.file_id
            WHERE o.mac = ? ORDER BY o.first_seen DESC LIMIT 500""",
        (mac,),
    ).fetchall()
    ssids = conn.execute(
        "SELECT ssid, COUNT(*) c FROM observations WHERE mac = ? GROUP BY ssid ORDER BY c DESC",
        (mac,),
    ).fetchall()
    return {
        "network": dict(net),
        "observations": [dict(r) for r in obs],
        "ssids": [dict(r) for r in ssids],
    }


def map_points(conn, *, file_ids=None, types=None, date_from: str = "", date_to: str = "",
               limit: int | None = None, network_mode: bool = False) -> list[dict]:
    where = ["o.has_gps = 1"]
    params: list = []
    if file_ids:
        file_ids = [int(i) for i in file_ids]
        where.append(f"o.file_id IN ({','.join('?' * len(file_ids))})")
        params += file_ids
    if types:
        where.append(f"o.type IN ({','.join('?' * len(types))})")
        params += list(types)
    if date_from:
        where.append("o.first_seen >= ?")
        params.append(date_from)
    if date_to:
        where.append("o.first_seen <= ?")
        params.append(date_to + " 23:59:59" if len(date_to) == 10 else date_to)

    where_sql = " AND ".join(where)
    limit = int(limit or config.load_settings()["map"]["max_points"])
    if network_mode:
        rows = conn.execute(
            f"""SELECT n.mac, n.ssid, n.type, n.best_rssi AS rssi, n.best_lat AS lat,
                       n.best_lon AS lon, n.obs_count, n.first_seen, n.last_seen,
                       '' AS auth_mode
                  FROM networks n
                 WHERE n.best_lat IS NOT NULL AND n.best_lon IS NOT NULL
                   {'AND n.type IN (' + ','.join('?' * len(types)) + ')' if types else ''}
                 ORDER BY n.obs_count DESC LIMIT ?""",
            tuple(types or []) + (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT o.mac, o.ssid, o.type, o.rssi, o.lat, o.lon, 1 AS obs_count,
                       o.first_seen, o.first_seen AS last_seen, o.auth_mode, o.file_id
                  FROM observations o WHERE {where_sql}
                 ORDER BY o.first_seen DESC LIMIT ?""",
            tuple(params) + (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_uploads(conn, limit: int = 15) -> list[dict]:
    rows = conn.execute(
        """SELECT u.*, e.label AS export_label FROM uploads u
             LEFT JOIN exports e ON e.id = u.export_id
            ORDER BY u.id DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def uploads(conn, *, target: str = "", status: str = "", limit: int = 200, offset: int = 0):
    where, params = [], []
    if target:
        where.append("u.target = ?")
        params.append(target)
    if status:
        where.append("u.status = ?")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM uploads u {where_sql}", tuple(params)).fetchone()[0]
    rows = conn.execute(
        f"""SELECT u.*, e.label AS export_label, c.filename AS chunk_file
              FROM uploads u LEFT JOIN exports e ON e.id = u.export_id
              LEFT JOIN export_chunks c ON c.id = u.chunk_id
              {where_sql} ORDER BY u.id DESC LIMIT ? OFFSET ?""",
        tuple(params) + (limit, offset),
    ).fetchall()
    return [dict(r) for r in rows], total


def exports(conn, limit: int = 60) -> list[dict]:
    rows = conn.execute(
        """SELECT e.*,
                  (SELECT COUNT(*) FROM export_chunks c WHERE c.export_id = e.id) AS arquivos,
                  (SELECT COUNT(*) FROM uploads u WHERE u.export_id = e.id AND u.dry_run = 0
                    AND u.status IN ('ok','duplicado')) AS enviados,
                  (SELECT COUNT(*) FROM uploads u WHERE u.export_id = e.id AND u.dry_run = 0
                    AND u.status NOT IN ('ok','duplicado')) AS falhas
             FROM exports e ORDER BY e.id DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def export_detail(conn, export_id: int) -> dict:
    export = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    chunks = conn.execute(
        """SELECT c.*,
                  (SELECT COUNT(*) FROM uploads u WHERE u.chunk_id = c.id AND u.dry_run = 0
                    AND u.status IN ('ok','duplicado')) AS enviados,
                  (SELECT GROUP_CONCAT(DISTINCT u.target) FROM uploads u
                    WHERE u.chunk_id = c.id AND u.dry_run = 0 AND u.status IN ('ok','duplicado')) AS destinos
             FROM export_chunks c WHERE c.export_id = ? ORDER BY c.idx""",
        (export_id,),
    ).fetchall()
    uploads_rows = conn.execute(
        "SELECT * FROM uploads WHERE export_id = ? ORDER BY id DESC LIMIT 100", (export_id,)
    ).fetchall()
    return {
        "export": dict(export) if export else None,
        "chunks": [dict(c) for c in chunks],
        "uploads": [dict(u) for u in uploads_rows],
    }


def events(conn, limit: int = 80) -> list[dict]:
    rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def quality_report(conn) -> dict:
    problemas = conn.execute(
        """SELECT id, name, obs_count, no_gps_count, bad_count, network_count,
                  format_version, has_frequency, first_seen, model, brand
             FROM files
            WHERE no_gps_count > 0 OR bad_count > 0 OR obs_count = 0
            ORDER BY no_gps_count DESC, id DESC LIMIT 100"""
    ).fetchall()
    duplicados = conn.execute(
        """SELECT sha256, COUNT(*) AS copias, GROUP_CONCAT(name, ' | ') AS nomes,
                  SUM(obs_count) AS obs
             FROM files GROUP BY sha256 HAVING copias > 1 ORDER BY copias DESC LIMIT 50"""
    ).fetchall()
    return {"problemas": [dict(r) for r in problemas], "duplicados": [dict(r) for r in duplicados]}


def filters_meta(conn) -> dict:
    types = conn.execute("SELECT type, COUNT(*) c FROM networks GROUP BY type ORDER BY c DESC").fetchall()
    brands = conn.execute(
        "SELECT brand, model, COUNT(*) c FROM files GROUP BY brand, model ORDER BY c DESC").fetchall()
    return {"types": [dict(t) for t in types], "brands": [dict(b) for b in brands]}
