"""Camada de banco (SQLite puro, sem ORM).

Tabelas principais:
  files            arquivos de wardriving importados (dedup por sha256+nome)
  observations     cada linha observada (rede/AP/BLE em um instante e local)
  networks         cache agregado por MAC (usado nas listas e no mapa)
  exports          conjuntos exportados (compatíveis com WiGLE / WDGoWars)
  export_chunks    partes (.csv) de um export
  uploads          cada POST enviado a um destino
  obs_uploads      quais observações já subiram para cada destino (upload incremental)
  jobs/events      log de atividades
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterable

from flask import g

from . import config

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    path           TEXT,
    sha256         TEXT NOT NULL,
    size           INTEGER NOT NULL DEFAULT 0,
    format_version TEXT,
    app_release    TEXT,
    model          TEXT,
    device         TEXT,
    display        TEXT,
    board          TEXT,
    brand          TEXT,
    preheader      TEXT,
    columns        TEXT,
    has_frequency  INTEGER NOT NULL DEFAULT 0,
    obs_count      INTEGER NOT NULL DEFAULT 0,
    wifi_count     INTEGER NOT NULL DEFAULT 0,
    ble_count      INTEGER NOT NULL DEFAULT 0,
    cell_count     INTEGER NOT NULL DEFAULT 0,
    network_count  INTEGER NOT NULL DEFAULT 0,
    no_gps_count   INTEGER NOT NULL DEFAULT 0,
    bad_count      INTEGER NOT NULL DEFAULT 0,
    first_seen     TEXT,
    last_seen      TEXT,
    min_lat REAL, max_lat REAL, min_lon REAL, max_lon REAL,
    source         TEXT NOT NULL DEFAULT 'pasta',
    notes          TEXT,
    imported_at    TEXT NOT NULL,
    UNIQUE (sha256, name)
);
CREATE INDEX IF NOT EXISTS idx_files_imported ON files(imported_at DESC);

CREATE TABLE IF NOT EXISTS observations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    mac        TEXT NOT NULL,
    ssid       TEXT,
    auth_mode  TEXT,
    first_seen TEXT,
    channel    INTEGER,
    frequency  INTEGER,
    rssi       INTEGER,
    lat        REAL,
    lon        REAL,
    altitude   REAL,
    accuracy   REAL,
    rcois      TEXT,
    mfgr_id    TEXT,
    type       TEXT NOT NULL DEFAULT 'WIFI',
    has_gps    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_obs_file ON observations(file_id);
CREATE INDEX IF NOT EXISTS idx_obs_mac ON observations(mac);
CREATE INDEX IF NOT EXISTS idx_obs_seen ON observations(first_seen);
CREATE INDEX IF NOT EXISTS idx_obs_type ON observations(type);
CREATE INDEX IF NOT EXISTS idx_obs_dedupe ON observations(mac, first_seen, lat, lon, type);

CREATE TABLE IF NOT EXISTS networks (
    mac        TEXT PRIMARY KEY,
    type       TEXT,
    ssid       TEXT,
    auth_mode  TEXT,
    first_seen TEXT,
    last_seen  TEXT,
    best_rssi  INTEGER,
    obs_count  INTEGER NOT NULL DEFAULT 0,
    gps_count  INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    best_lat   REAL,
    best_lon   REAL,
    vendor          TEXT,
    categoria       TEXT,
    categoria_conf  TEXT,
    categoria_origem TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_net_ssid ON networks(ssid);
CREATE INDEX IF NOT EXISTS idx_net_type ON networks(type);

-- base oficial de fabricantes por prefixo de MAC (OUI do IEEE)
CREATE TABLE IF NOT EXISTS oui (
    prefix TEXT PRIMARY KEY,
    vendor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL,
    target         TEXT NOT NULL,
    label          TEXT,
    dir            TEXT NOT NULL,
    chunk_count    INTEGER NOT NULL DEFAULT 0,
    row_count      INTEGER NOT NULL DEFAULT 0,
    bytes          INTEGER NOT NULL DEFAULT 0,
    selection_json TEXT,
    params_json    TEXT,
    preheader      TEXT,
    status         TEXT NOT NULL DEFAULT 'pronto'
);

CREATE TABLE IF NOT EXISTS export_chunks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    export_id INTEGER NOT NULL REFERENCES exports(id) ON DELETE CASCADE,
    idx       INTEGER NOT NULL,
    filename  TEXT NOT NULL,
    path      TEXT NOT NULL,
    rows INTEGER NOT NULL DEFAULT 0,
    bytes     INTEGER NOT NULL DEFAULT 0,
    sha256    TEXT,
    obs_file  TEXT,
    cover_file TEXT,
    gzipped   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chunks_export ON export_chunks(export_id, idx);

CREATE TABLE IF NOT EXISTS uploads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    target        TEXT NOT NULL,
    export_id     INTEGER,
    chunk_id      INTEGER,
    filename      TEXT,
    sha256        TEXT,
    rows          INTEGER DEFAULT 0,
    dry_run       INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,
    http_status   INTEGER,
    target_ref    TEXT,
    message       TEXT,
    response_json TEXT,
    duration_ms   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_uploads_created ON uploads(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uploads_chunk ON uploads(chunk_id);

CREATE TABLE IF NOT EXISTS obs_uploads (
    obs_id    INTEGER NOT NULL,
    target    TEXT NOT NULL,
    upload_id INTEGER,
    ts        TEXT,
    PRIMARY KEY (obs_id, target)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    level    TEXT NOT NULL DEFAULT 'info',
    source   TEXT,
    message  TEXT NOT NULL,
    detail   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
"""


def connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or config.DB_PATH), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    own = conn is None
    conn = conn or connect()
    conn.executescript(SCHEMA)
    migrar(conn)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    if own:
        conn.close()
    else:
        return conn
    return connect()


def migrar(conn: sqlite3.Connection) -> None:
    """Ajustes em bancos criados por versões anteriores (aditivos e idempotentes)."""
    colunas = {linha["name"] for linha in conn.execute("PRAGMA table_info(export_chunks)")}
    if "cover_file" not in colunas:
        conn.execute("ALTER TABLE export_chunks ADD COLUMN cover_file TEXT")
    colunas_arquivos = {linha["name"] for linha in conn.execute("PRAGMA table_info(files)")}
    if "notes" not in colunas_arquivos:
        conn.execute("ALTER TABLE files ADD COLUMN notes TEXT")
    # classificação em categorias de objeto (roteador, veículo, smart tag…)
    colunas_redes = {linha["name"] for linha in conn.execute("PRAGMA table_info(networks)")}
    for coluna, tipo in (("vendor", "TEXT"), ("categoria", "TEXT"),
                         ("categoria_conf", "TEXT"), ("categoria_origem", "TEXT")):
        if coluna not in colunas_redes:
            conn.execute(f"ALTER TABLE networks ADD COLUMN {coluna} {tipo}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_net_categoria ON networks(categoria)")
    conn.commit()


# --------------------------------------------------------------------------- #
# Flask helpers
# --------------------------------------------------------------------------- #
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def session():
    """Conexão própria (uso fora do request: jobs, CLI)."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def query(conn: sqlite3.Connection, sql: str, params: Iterable = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, tuple(params)).fetchall()


def one(conn: sqlite3.Connection, sql: str, params: Iterable = ()):
    row = conn.execute(sql, tuple(params)).fetchone()
    return row


def scalar(conn: sqlite3.Connection, sql: str, params: Iterable = (), default=0):
    row = conn.execute(sql, tuple(params)).fetchone()
    if not row or row[0] is None:
        return default
    return row[0]


def log_event(conn: sqlite3.Connection, level: str, source: str, message: str, detail=None) -> None:
    import json
    from datetime import datetime, timezone

    conn.execute(
        "INSERT INTO events(ts, level, source, message, detail) VALUES(?,?,?,?,?)",
        (
            datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            level,
            source,
            message,
            json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None,
        ),
    )


def rebuild_networks(conn: sqlite3.Connection, macs: Iterable[str] | None = None) -> int:
    """Recalcula o cache agregado de redes.

    Com ``macs`` informado, refaz só aqueles MACs (import incremental);
    com ``None``, refaz tudo.
    """
    if macs is None:
        conn.execute("DELETE FROM networks")
        where = "1"
    else:
        macs = list({m for m in macs if m})
        if not macs:
            return conn.execute("SELECT COUNT(*) FROM networks").fetchone()[0]
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _pending_macs(mac TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM _pending_macs")
        conn.executemany("INSERT OR IGNORE INTO _pending_macs(mac) VALUES(?)", [(m,) for m in macs])
        conn.execute("DELETE FROM networks WHERE mac IN (SELECT mac FROM _pending_macs)")
        where = "o.mac IN (SELECT mac FROM _pending_macs)"

    conn.execute(
        f"""
        INSERT INTO networks(mac, type, ssid, auth_mode, first_seen, last_seen,
                              best_rssi, obs_count, gps_count, file_count, best_lat, best_lon, updated_at)
        SELECT o.mac,
               (SELECT o2.type FROM observations o2 WHERE o2.mac = o.mac
                 GROUP BY o2.type ORDER BY COUNT(*) DESC LIMIT 1),
               (SELECT o3.ssid FROM observations o3 WHERE o3.mac = o.mac AND o3.ssid <> ''
                 GROUP BY o3.ssid ORDER BY COUNT(*) DESC LIMIT 1),
               (SELECT o4.auth_mode FROM observations o4 WHERE o4.mac = o.mac AND o4.auth_mode <> ''
                 GROUP BY o4.auth_mode ORDER BY COUNT(*) DESC LIMIT 1),
               MIN(o.first_seen), MAX(o.first_seen),
               MAX(o.rssi),
               COUNT(*), SUM(o.has_gps), COUNT(DISTINCT o.file_id),
               (SELECT o5.lat FROM observations o5 WHERE o5.mac = o.mac AND o5.has_gps = 1
                 ORDER BY o5.rssi DESC LIMIT 1),
               (SELECT o6.lon FROM observations o6 WHERE o6.mac = o.mac AND o6.has_gps = 1
                 ORDER BY o6.rssi DESC LIMIT 1),
               datetime('now')
          FROM observations o
         WHERE {where}
         GROUP BY o.mac
        """,
    )
    return conn.execute("SELECT COUNT(*) FROM networks").fetchone()[0]
