"""Formatos que não são CSV: Kismet (.netxml) e o banco do app WiGLE Android.

A conversão é para o **formato WiGLE** e depois segue o mesmo `parse_bytes` dos
outros arquivos. Assim as estatísticas, a validação e a compatibilidade de
exportação saem de graça — e o CSV que você manda para o WiGLE continua válido,
independente de onde a coleta nasceu.
"""
from __future__ import annotations

import csv
import io
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

#: cabeçalho completo (14 colunas) — o mesmo que o export usa
CABECALHO = ["MAC", "SSID", "AuthMode", "FirstSeen", "Channel", "Frequency", "RSSI",
             "CurrentLatitude", "CurrentLongitude", "AltitudeMeters", "AccuracyMeters",
             "RCOIs", "MfgrId", "Type"]

#: como o Kismet escreve a criptografia → como o WiGLE escreve
CRIPTO = {
    "": "[OPEN]",
    "none": "[OPEN]",
    "open": "[OPEN]",
    "wep": "[WEP]",
    "wpa": "[WPA_PSK]",
    "wpa-psk": "[WPA_PSK]",
    "wpa+wpa2": "[WPA_WPA2_PSK]",
    "wpa2": "[WPA2_PSK]",
    "wpa2-psk": "[WPA2_PSK]",
    "wpa2-eap": "[WPA2_EAP]",
    "wpa3": "[WPA3_SAE]",
    "wpa3-sae": "[WPA3_SAE]",
    "wpa3+sae": "[WPA3_SAE]",
}

EXTENSOES = {".netxml", ".sqlite", ".db"}


def _preheader(origem: str) -> str:
    return (f"WigleWifi-1.6,appRelease={origem},model={origem},release=1.0,"
            f"device={origem},display=none,board=local,brand={origem}")


def _csv(linhas: list[list], origem: str) -> bytes:
    buffer = io.StringIO()
    buffer.write(_preheader(origem) + "\n")
    escritor = csv.writer(buffer, lineterminator="\n")
    escritor.writerow(CABECALHO)
    for linha in linhas:
        escritor.writerow(linha)
    return buffer.getvalue().encode("utf-8")


def _data_kismet(valor: str | None) -> str:
    """`Wed Oct 12 10:00:00 2022`, ISO ou epoch → `2022-10-12 10:00:00` (hora local)."""
    if not valor:
        return ""
    texto = str(valor).strip()
    if texto.isdigit():
        numero = int(texto)
        if numero > 10**11:            # milissegundos
            numero //= 1000
        return datetime.fromtimestamp(numero).strftime("%Y-%m-%d %H:%M:%S")
    try:
        data = parsedate_to_datetime(texto)
        if data.tzinfo:
            data = data.astimezone(timezone.utc).replace(tzinfo=None)
        return data.strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        pass
    for formato in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(texto[:len(formato) + 6], formato).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return ""


def _cripto(valor: str | None) -> str:
    chave = (valor or "").strip().lower()
    return CRIPTO.get(chave, f"[{chave.upper()}]" if chave else "[OPEN]")


def _freq_do_canal(canal: int | None) -> int | None:
    if not canal:
        return None
    if canal <= 14:
        return 2407 if canal == 14 else 2407 + 5 * canal
    return 5000 + 5 * canal


def netxml_para_csv(data: bytes) -> bytes:
    """Kismet `.netxml`: um `<wireless-network>` vira uma observação por snapshot com GPS."""
    raiz = ET.fromstring(data)
    linhas: list[list] = []
    for rede in raiz.iter("wireless-network"):
        tipo = (rede.get("type") or "").lower()
        if tipo not in ("infrastructure", "adhoc", "mesh"):
            continue                       # "client" é estação, não ponto de acesso
        bssid = (rede.findtext("BSSID") or "").strip().upper()
        if not bssid:
            continue
        ssid_el = rede.find(".//SSID/ssid")
        ssid = (ssid_el.text or "") if ssid_el is not None else ""
        if ssid_el is not None and (ssid_el.get("cloaked") or "").lower() == "true":
            ssid = ""
        cripto = _cripto(rede.findtext(".//SSID/encryption") or rede.findtext("encryption"))
        canal_txt = (rede.findtext("channel") or "").strip()
        canal = int(canal_txt) if canal_txt.isdigit() else None
        freq_txt = (rede.findtext("freqmhz") or "").strip().split()[0] if rede.findtext("freqmhz") else ""
        freq = int(freq_txt) if freq_txt.isdigit() else _freq_do_canal(canal)
        manuf = (rede.findtext("manuf") or rede.findtext("manuf") or "").strip()
        quando_rede = _data_kismet(rede.get("first-time"))

        snapshots = list(rede.iter("snapshot"))
        GPS = []
        for snap in snapshots:
            gps = snap.find("gps-info")
            if gps is None:
                continue
            lat = gps.findtext("gps-lat") or gps.findtext("gps-min-lat")
            lon = gps.findtext("gps-lon") or gps.findtext("gps-min-lon")
            if not (lat and lon):
                continue
            GPS.append({
                "lat": lat, "lon": lon,
                "alt": gps.findtext("gps-alt") or "",
                "quando": _data_kismet(snap.get("time") or gps.findtext("gps-time")),
                "rssi": (snap.findtext("signal/rssi") or "").strip(),
            })
        if not GPS:                        # rede ouvida sem fix de GPS: entra como "sem GPS"
            GPS = [{"lat": "", "lon": "", "alt": "", "quando": quando_rede, "rssi": ""}]

        for ponto in GPS:
            linhas.append([bssid, ssid, cripto, ponto["quando"] or quando_rede,
                           canal if canal is not None else "", freq if freq else "",
                           ponto["rssi"], ponto["lat"], ponto["lon"], ponto["alt"], "",
                           "", manuf, "WIFI"])
    return _csv(linhas, "kismet")


# --------------------------------------------------------------------------- #
# banco do app WiGLE Android (wiglewifi.sqlite)
# --------------------------------------------------------------------------- #
_ALIASES = {
    "mac": ("bssid", "mac", "bdaddr"),
    "ssid": ("ssid", "networkname", "name"),
    "lat": ("latitude", "lat", "gps_lat"),
    "lon": ("longitude", "lon", "gps_lon", "lng"),
    "rssi": ("level", "rssi", "signal"),
    "freq": ("frequency", "freq", "freqmhz"),
    "canal": ("channel", "chan"),
    "tempo": ("time", "timestamp", "firstseen", "seen"),
    "alt": ("altitude", "alt"),
    "acuracia": ("accuracy", "acc"),
    "tipo": ("type", "nettype"),
    "cripto": ("capabilities", "authmode", "encryption", "security"),
}


def _colunas_sqlite(conn) -> tuple[str, dict[str, str]]:
    tabelas = [linha[0] for linha in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    for tabela in tabelas:
        nomes = [linha[1].lower() for linha in conn.execute(f"PRAGMA table_info('{tabela}')")]
        mapa = {}
        for campo, apelidos in _ALIASES.items():
            for apelido in apelidos:
                if apelido in nomes:
                    mapa[campo] = apelido
                    break
        if "mac" in mapa and "ssid" in mapa:
            return tabela, mapa
    raise ValueError("banco sem tabela com BSSID/MAC e SSID — não é o wiglewifi.sqlite do app")


def sqlite_para_csv(caminho: Path | str) -> bytes:
    """Lê o SQLite **em memória e somente leitura** (o app pode estar aberto)."""
    caminho = Path(caminho)
    uri = f"file:{caminho.as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        tabela, mapa = _colunas_sqlite(conn)
        campos = ["mac", "ssid", "cripto", "tempo", "canal", "freq", "rssi",
                  "lat", "lon", "alt", "acuracia", "tipo"]
        select = ", ".join(f'"{mapa[campo]}"' if campo in mapa else "NULL" for campo in campos)
        linhas = []
        for registro in conn.execute(f'SELECT {select} FROM "{tabela}"'):
            (mac, ssid, cripto, tempo, canal, freq, rssi, lat, lon, alt, acuracia, tipo) = registro
            mac = (mac or "").strip().upper()
            if not mac:
                continue
            quando = _data_kismet(str(tempo)) if tempo is not None else ""
            if not quando:
                continue
            canal_num = int(canal) if str(canal or "").strip().isdigit() else None
            freq_num = int(freq) if str(freq or "").strip().isdigit() else _freq_do_canal(canal_num)
            tipo_wigle = "WIFI" if str(tipo or "").lower() in ("", "wifi", "wlan", "0") else "BLE"
            linhas.append([mac, ssid or "", _cripto(cripto), quando,
                           canal_num if canal_num is not None else "",
                           freq_num if freq_num else "", rssi or "",
                           lat if lat is not None else "", lon if lon is not None else "",
                           alt if alt is not None else "", acuracia if acuracia is not None else "",
                           "", "", tipo_wigle])
    finally:
        conn.close()
    return _csv(linhas, "wigle-android")


# --------------------------------------------------------------------------- #
# despacho
# --------------------------------------------------------------------------- #
def detectar(nome: str, data: bytes) -> str | None:
    """Reconhece o formato pelo **conteúdo**, não só pela extensão."""
    sufixo = Path(nome).suffix.lower()
    inicio = data[:400].lstrip()
    if sufixo == ".netxml" or inicio.startswith(b"<?xml") or b"<detection-run" in data[:2000]:
        return "netxml"
    if data[:16].startswith(b"SQLite format 3"):
        return "sqlite"
    return None


def para_csv_wigle(nome: str, data: bytes) -> bytes | None:
    """Devolve o equivalente em CSV WiGLE, ou None quando já é CSV."""
    formato = detectar(nome, data)
    if formato == "netxml":
        return netxml_para_csv(data)
    if formato == "sqlite":
        raise ValueError(f"{nome}: SQLite precisa ser lido do disco, não dos bytes")
    return None
