"""Leitura de arquivos CSV no formato WiGLE (WigleWifi-1.0 … 1.6).

Aceita o que existe na prática:
  * ESP32 Marauder (JustCallMeKoko) — WigleWifi-1.4, 11 colunas (sem Frequency/RCOIs/MfgrId)
  * WDGoWars / projectZero (warrior-0.2) — WigleWifi-1.6, 12 colunas (sem RCOIs/MfgrId)
  * app WiGLE Android, Bruce, Kismet, pwnagotchi — 14 colunas completas

O mapeamento de colunas é feito por NOME (não por posição), então versões com
colunas faltando ou em ordem diferente são lidas corretamente. A frequência,
quando ausente, é derivada do canal (convenção WiGLE).
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime

MAX_REASONABLE_ROWS = 5_000_000

TYPE_ALIASES = {
    "wifi": "WIFI", "wi-fi": "WIFI", "wlan": "WIFI",
    "ble": "BLE", "btle": "BLE", "bluetoothle": "BLE",
    "bt": "BT", "bluetooth": "BT", "br/edr": "BT",
    "gsm": "GSM", "cdma": "CDMA", "wcdma": "WCDMA", "umts": "WCDMA",
    "lte": "LTE", "nr": "NR", "5g": "NR",
}
WIFI_TYPES = {"WIFI"}
BLUETOOTH_TYPES = {"BLE", "BT"}
CELL_TYPES = {"GSM", "CDMA", "WCDMA", "LTE", "NR"}

MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
CELL_KEY_RE = re.compile(r"^\d+_\d+_\d+$")

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "mac": ("mac", "bssid", "bdaddr", "bdaddress", "macaddress", "cellkey", "cell"),
    "ssid": ("ssid", "networkname", "devicename", "essid", "name"),
    "auth_mode": ("authmode", "capabilities", "capability", "encryption", "security"),
    "first_seen": ("firstseen", "firsttimestampseen", "firstseentsince", "timestamp", "seen", "time"),
    "channel": ("channel", "chan"),
    "frequency": ("frequency", "freq", "frequenc ymhz", "frequencymhz", "mhz"),
    "rssi": ("rssi", "signal", "signalstrength", "level"),
    "lat": ("currentlatitude", "latitude", "lat"),
    "lon": ("currentlongitude", "longitude", "lon", "lng", "long"),
    "altitude": ("altitudemeters", "altitude", "alt", "elevation"),
    "accuracy": ("accuracymeters", "accuracy", "acc", "hdop"),
    "rcois": ("rcois", "roamingconsortium", "roamingconsortiumorganizationidentifiers"),
    "mfgr_id": ("mfgrid", "manufacturerid", "mfgr", "manufacturer"),
    "type": ("type", "devicetype", "networktype"),
}

TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y/%m/%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M",
)


# --------------------------------------------------------------------------- #
# utilidades de conversão
# --------------------------------------------------------------------------- #
def _norm_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def to_int(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def to_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_time(value) -> str | None:
    text = ("" if value is None else str(value)).strip()
    if not text:
        return None
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    if text.isdigit() and len(text) >= 9:  # epoch
        try:
            return datetime.fromtimestamp(int(text)).strftime("%Y-%m-%d %H:%M:%S")
        except (OverflowError, OSError, ValueError):
            return None
    return None


def normalize_type(value, auth_mode: str = "") -> str:
    raw = ("" if value is None else str(value)).strip()
    key = raw.lower().replace(" ", "").replace("_", "")
    if key in TYPE_ALIASES:
        return TYPE_ALIASES[key]
    if raw.upper() in {"WIFI", "BLE", "BT", "GSM", "CDMA", "WCDMA", "LTE", "NR"}:
        return raw.upper()
    auth = (auth_mode or "").upper()
    if "[BLE]" in auth or "[LE]" in auth:
        return "BLE"
    if "[BT]" in auth:
        return "BT"
    return "WIFI" if raw == "" else "WIFI"


def freq_from_channel(channel, type_: str = "WIFI"):
    """Converte canal em frequência (MHz) segundo a convenção WiGLE."""
    ch = to_int(channel)
    if not ch or ch <= 0:
        return None
    if 1 <= ch <= 13:
        return 2407 + 5 * ch
    if ch == 14:
        return 2484
    if 32 <= ch <= 177:
        return 5000 + 5 * ch
    if 178 <= ch <= 233:
        return 5950 + 5 * ch
    return None


def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def parse_preheader(line: str) -> dict:
    fields = next(csv.reader([line]), []) if line else []
    out: dict = {"raw": line.strip(), "format_version": ""}
    if not fields:
        return out
    out["format_version"] = fields[0].strip()
    for field in fields[1:]:
        if "=" in field:
            key, value = field.split("=", 1)
            out[key.strip()] = value.strip().strip('"')
    return out


def build_column_map(header: list[str]) -> dict[str, int]:
    """Mapeia nome canônico -> índice usando os apelidos conhecidos."""
    normalized = [_norm_key(h) for h in header]
    mapping: dict[str, int] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            alias_key = _norm_key(alias)
            if alias_key in normalized:
                mapping[canonical] = normalized.index(alias_key)
                break
    return mapping


def _cell(row: list[str], idx) -> str:
    if idx is None or idx >= len(row):
        return ""
    return (row[idx] or "").strip()


def has_preheader(data: bytes) -> bool:
    text = decode_bytes(data[:400])
    first = text.splitlines()[0] if text.splitlines() else ""
    return first.startswith("WigleWifi-") or first.startswith("Wigle")


def parse_bytes(data: bytes, filename: str = "") -> dict:
    """Converte um CSV WiGLE em estruturas prontas para o banco.

    Retorna dict com: preheader, columns, rows, stats, warnings, bad_rows.
    """
    warnings: list[str] = []
    bad_rows: list[tuple[int, str]] = []
    text = decode_bytes(data)
    if not text.strip():
        return {
            "preheader": {"format_version": "", "raw": ""},
            "columns": [],
            "rows": [],
            "stats": {},
            "warnings": [f"{filename}: arquivo vazio"],
            "bad_rows": [],
        }

    # o pre-header precisa vir da linha crua do arquivo: reconstruir a linha a
    # partir dos campos já fatiados destrói o escape RFC 4180 (ex.: "brand=Moog, LLC").
    primeira_linha, _, resto = text.partition("\n")
    primeira_linha = primeira_linha.rstrip("\r")
    candidato = parse_preheader(primeira_linha)
    if candidato["format_version"].lower().startswith("wiglewifi"):
        preheader = candidato
        linha_cabecalho, _, resto = resto.partition("\n")
        header = next(csv.reader([linha_cabecalho.rstrip("\r")]), [])
    else:
        preheader = {"format_version": "", "raw": ""}
        header = next(csv.reader([primeira_linha]), [])
        warnings.append("arquivo sem linha de pre-header WiGleWifi — cabeçalho lido da primeira linha")
    reader = csv.reader(io.StringIO(resto))

    header = [(h or "").strip() for h in header]
    cmap = build_column_map(header)
    if "mac" not in cmap:
        raise ValueError(
            f"{filename}: cabeçalho inválido (coluna MAC/BSSID não encontrada): {','.join(header)[:200]}"
        )
    if "first_seen" not in cmap:
        warnings.append("coluna FirstSeen ausente no cabeçalho")
    if "type" not in cmap:
        warnings.append("coluna Type ausente: o tipo será inferido pelo AuthMode")
    if "frequency" not in cmap:
        warnings.append("coluna Frequency ausente (formato 1.4): frequência derivada do canal")

    rows: list[dict] = []
    counts = {"wifi": 0, "ble": 0, "bt": 0, "cell": 0, "no_gps": 0, "bad": 0}
    min_lat = max_lat = min_lon = max_lon = None
    first_seen = last_seen = None
    macs: set[str] = set()

    for lineno, raw_row in enumerate(reader, start=3):
        if not raw_row or all(not (c or "").strip() for c in raw_row):
            continue
        if len(raw_row) > len(header):
            raw_row = raw_row[: len(header)]
        row = list(raw_row)

        mac = _cell(row, cmap.get("mac")).upper()
        if not mac:
            counts["bad"] += 1
            if len(bad_rows) < 50:
                bad_rows.append((lineno, "linha sem MAC/BSSID"))
            continue
        if not (MAC_RE.match(mac) or CELL_KEY_RE.match(mac)):
            counts["bad"] += 1
            if len(bad_rows) < 50:
                bad_rows.append((lineno, f"MAC inválido: {mac[:40]}"))
            continue

        auth_mode = _cell(row, cmap.get("auth_mode"))
        type_ = normalize_type(_cell(row, cmap.get("type")), auth_mode)
        ssid = _cell(row, cmap.get("ssid"))
        seen = normalize_time(_cell(row, cmap.get("first_seen")))
        if not seen:
            counts["bad"] += 1
            if len(bad_rows) < 50:
                bad_rows.append(map_bad_time(lineno, _cell(row, cmap.get("first_seen"))))
            continue

        channel = to_int(_cell(row, cmap.get("channel")))
        frequency = to_int(_cell(row, cmap.get("frequency")))
        if not frequency and type_ in WIFI_TYPES:
            frequency = freq_from_channel(channel, type_)

        lat = to_float(_cell(row, cmap.get("lat")))
        lon = to_float(_cell(row, cmap.get("lon")))
        gps_ok = (
            lat is not None and lon is not None
            and abs(lat) <= 90 and abs(lon) <= 180
            and not (lat == 0 and lon == 0)
        )
        if not gps_ok:
            counts["no_gps"] += 1
        else:
            min_lat = lat if min_lat is None else min(min_lat, lat)
            max_lat = lat if max_lat is None else max(max_lat, lat)
            min_lon = lon if min_lon is None else min(min_lon, lon)
            max_lon = lon if max_lon is None else max(max_lon, lon)

        if type_ in WIFI_TYPES:
            counts["wifi"] += 1
        elif type_ in BLUETOOTH_TYPES:
            counts["ble" if type_ == "BLE" else "bt"] += 1
        else:
            counts["cell"] += 1

        first_seen = seen if first_seen is None or seen < first_seen else first_seen
        last_seen = seen if last_seen is None or seen > last_seen else last_seen
        macs.add(mac)

        rows.append(
            {
                "mac": mac,
                "ssid": ssid,
                "auth_mode": auth_mode,
                "first_seen": seen,
                "channel": channel,
                "frequency": frequency,
                "rssi": to_int(_cell(row, cmap.get("rssi"))),
                "lat": lat,
                "lon": lon,
                "altitude": to_float(_cell(row, cmap.get("altitude"))),
                "accuracy": to_float(_cell(row, cmap.get("accuracy"))),
                "rcois": _cell(row, cmap.get("rcois")),
                "mfgr_id": _cell(row, cmap.get("mfgr_id")),
                "type": type_,
                "has_gps": 1 if gps_ok else 0,
            }
        )
        if len(rows) > MAX_REASONABLE_ROWS:
            warnings.append(f"limite de {MAX_REASONABLE_ROWS} linhas atingido — resto ignorado")
            break

    stats = {
        "obs_count": len(rows),
        "wifi_count": counts["wifi"],
        "ble_count": counts["ble"] + counts["bt"],
        "cell_count": counts["cell"],
        "no_gps_count": counts["no_gps"],
        "bad_count": counts["bad"],
        "network_count": len(macs),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "min_lat": min_lat,
        "max_lat": max_lat,
        "min_lon": min_lon,
        "max_lon": max_lon,
        "has_frequency": 1 if ("frequency" in cmap and cmap.get("frequency") is not None) else 0,
    }
    if stats["obs_count"] and stats["no_gps_count"] == stats["obs_count"]:
        warnings.append("nenhuma linha tem GPS válido — verifique o fix do receptor")

    return {
        "preheader": preheader,
        "columns": header,
        "rows": rows,
        "stats": stats,
        "warnings": warnings,
        "bad_rows": bad_rows,
    }


def map_bad_time(lineno: int, value: str) -> tuple[int, str]:
    return (lineno, f"data/hora inválida: {value[:40] or '(vazio)'}")


def parse_file(path) -> dict:
    from pathlib import Path

    p = Path(path)
    data = p.read_bytes()
    if p.suffix.lower() == ".gz":
        import gzip

        data = gzip.decompress(data)
    return parse_bytes(data, p.name)
