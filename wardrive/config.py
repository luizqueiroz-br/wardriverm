"""Configuração, caminhos e persistência de preferências do Wardrive Manager.

Nada de segredos hard-coded: chaves de API ficam em data/secrets.json
(ou em variáveis de ambiente WIGLE_API_NAME / WIGLE_API_TOKEN / WDGWARS_API_KEY).
"""
from __future__ import annotations

import json
import os
import secrets as _secrets
import threading
from pathlib import Path

APP_NAME = "Wardrive Manager"
APP_VERSION = "1.2.0"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EXPORT_DIR = DATA_DIR / "exports"
INBOX_DIR = DATA_DIR / "inbox"
DB_PATH = DATA_DIR / "wardrive.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
SECRETS_PATH = DATA_DIR / "secrets.json"
SECRET_KEY_PATH = DATA_DIR / "flask_secret.key"

# WiGLE CSV format (https://api.wigle.net/csvFormat.html)
WIGLE_FORMAT_VERSION = "WigleWifi-1.6"
WIGLE_COLUMNS = [
    "MAC", "SSID", "AuthMode", "FirstSeen", "Channel", "Frequency", "RSSI",
    "CurrentLatitude", "CurrentLongitude", "AltitudeMeters", "AccuracyMeters",
    "RCOIs", "MfgrId", "Type",
]

# Endpoints de destino (podem ser sobrescritos nas configurações / --api-base nos testes)
WIGLE_API_BASE = "https://api.wigle.net"
WIGLE_UPLOAD_PATH = "/api/v2/file/upload"
WIGLE_UPLOADS_PATH = "/api/v2/profile/uploads"
WDGWARS_API_BASE = "https://wdgwars.pl"
WDGWARS_CSV_PATH = "/endpoint/upload-csv"
WDGWARS_ME_PATH = "/api/me"

TARGETS = {
    "wigle": {
        "label": "WiGLE (wigle.net)",
        "max_bytes": 38 * 1024 * 1024,   # o portal aceita ~40 MB por arquivo (gz é medido descomprimido)
        "rows_per_chunk": 10000,
        "gzip": True,                    # WiGLE aceita .csv.gz
    },
    "wdgwars": {
        "label": "WDGoWars (wdgwars.pl)",
        "max_bytes": 14 * 1024 * 1024,   # 413 payload-too-large acima de 15 MB (max_bytes=15728640)
        "rows_per_chunk": 10000,         # Cloudflare 524: lote processado de forma síncrona no origin
        "gzip": False,                   # envie CSV cru para o WDGoWars
    },
}


def _default_ward_dirs() -> list[str]:
    home = Path.home()
    candidates = [
        home / "OneDrive" / "Área de Trabalho" / "ward",
        home / "OneDrive" / "Desktop" / "ward",
        home / "Desktop" / "ward",
        home / "ward",
    ]
    existing = [str(p) for p in candidates if p.is_dir()]
    return existing or [str(candidates[0])]


DEFAULTS: dict = {
    "ward_dirs": _default_ward_dirs(),
    "auto_import_on_start": True,
    "device": {
        "appRelease": f"wardrive-manager-{APP_VERSION}",
        "model": "Marauder v8 C5",
        "release": APP_VERSION,
        "device": "ESP32-C5",
        "display": "ILI9341",
        "board": "ESP32C5",
        "brand": "WDGoWars",
    },
    "export": {
        "rows_per_chunk": 10000,
        "wdgwars_rows_per_chunk": 10000,
        "wigle_max_bytes": TARGETS["wigle"]["max_bytes"],
        "wdgwars_max_bytes": TARGETS["wdgwars"]["max_bytes"],
        "gzip_wigle": False,
        "include_no_gps": False,
        "dedupe": "exact",          # none | exact | network
        "header_mode": "source",    # source (pre-header do arquivo original) | profile
    },
    "upload": {
        "wigle_donate": False,
        "timeout": 300,
        "dry_run_default": True,     # primeiro clique é sempre simulação
        "wigle_base": WIGLE_API_BASE,
        "wdgwars_base": WDGWARS_API_BASE,
    },
    "map": {
        "max_points": 40000,
    },
}

_lock = threading.RLock()


def ensure_dirs() -> None:
    for d in (DATA_DIR, EXPORT_DIR, INBOX_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# settings.json
# --------------------------------------------------------------------------- #
def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings() -> dict:
    ensure_dirs()
    data = {}
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    return _deep_merge(json.loads(json.dumps(DEFAULTS)), data)


def save_settings(new_settings: dict) -> dict:
    with _lock:
        merged = _deep_merge(load_settings(), new_settings)
        SETTINGS_PATH.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return merged


# --------------------------------------------------------------------------- #
# secrets.json (chaves de API)
# --------------------------------------------------------------------------- #
def load_secrets() -> dict:
    ensure_dirs()
    data = {}
    if SECRETS_PATH.exists():
        try:
            data = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    return {
        "wigle_api_name": os.environ.get("WIGLE_API_NAME", data.get("wigle_api_name", "")),
        "wigle_api_token": os.environ.get("WIGLE_API_TOKEN", data.get("wigle_api_token", "")),
        "wdgwars_api_key": os.environ.get("WDGWARS_API_KEY", data.get("wdgwars_api_key", "")),
        "_from_env": bool(os.environ.get("WIGLE_API_TOKEN") or os.environ.get("WDGWARS_API_KEY")),
    }


def save_secrets(patch: dict) -> dict:
    """Grava somente as chaves informadas (string vazia apaga a chave)."""
    with _lock:
        current = {}
        if SECRETS_PATH.exists():
            try:
                current = json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current = {}
        for key in ("wigle_api_name", "wigle_api_token", "wdgwars_api_key"):
            if key in patch and patch[key] is not None:
                value = str(patch[key]).strip()
                if value:
                    current[key] = value
                else:
                    current.pop(key, None)
        SECRETS_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
        try:  # melhor esforço no Windows
            os.chmod(SECRETS_PATH, 0o600)
        except OSError:
            pass
    return load_secrets()


def has_key(target: str) -> bool:
    s = load_secrets()
    if target == "wigle":
        return bool(s.get("wigle_api_name") and s.get("wigle_api_token"))
    return bool(s.get("wdgwars_api_key"))


def mask(value: str) -> str:
    """Mostra só o suficiente para o usuário reconhecer a chave."""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]} ({len(value)} caracteres)"


def flask_secret_key() -> str:
    ensure_dirs()
    if SECRET_KEY_PATH.exists():
        key = SECRET_KEY_PATH.read_text(encoding="utf-8").strip()
        if key:
            return key
    key = _secrets.token_hex(32)
    SECRET_KEY_PATH.write_text(key, encoding="utf-8")
    return key
