"""Risco das redes Wi-Fi: o que está aberto, fraco ou com senha de fábrica.

Duas leituras que o acervo permite e que não são a mesma coisa:

- **força da criptografia** (`auth_mode`): aberta, WEP, WPA, misto, WPA2, WPA3, 802.1X;
- **SSID de fábrica**: nome que sai do roteador do jeito que veio — forte indício de
  credencial padrão (`admin/admin`), o que derruba até uma rede WPA2.

Nada aqui acusa ninguém: é diagnóstico do que está no ar na região.
"""
from __future__ import annotations

import csv
import io
import re

#: ordem de gravidade (0 = pior). Usada para pintar a célula pelo pior caso.
RISCO: dict[str, dict] = {
    "aberta": {"rotulo": "aberta (sem senha)", "cor": "#ff5252", "gravidade": 0},
    "wep": {"rotulo": "WEP", "cor": "#ff9f1c", "gravidade": 1},
    "wpa": {"rotulo": "WPA", "cor": "#ffd166", "gravidade": 2},
    "mista": {"rotulo": "misto (WPA+WPA2/WPA3)", "cor": "#c9d96b", "gravidade": 3},
    "wpa2": {"rotulo": "WPA2", "cor": "#5aa9e6", "gravidade": 4},
    "wpa3": {"rotulo": "WPA3", "cor": "#48d295", "gravidade": 5},
    "empresa": {"rotulo": "802.1X / empresarial", "cor": "#7bd3c6", "gravidade": 6},
    "desconhecido": {"rotulo": "não informado", "cor": "#6b7688", "gravidade": 7},
}

#: "rede fraca" para os contadores = sem senha ou com criptografia quebrada
RISCOS_FRACOS = ("aberta", "wep")

#: SSIDs que saem de fábrica (o aparelho aceita a senha padrão do manual)
PADROES_FABRICA: list[tuple[str, str]] = [
    (r"^vivo", "Vivo Fibra"),
    (r"^claro", "Claro"),
    (r"^net[_\-]|^net ?(virtua|combo)", "NET/Claro"),
    (r"^(oi|tim)[ _\-]", "Oi/TIM"),
    (r"^algar|^sercomtel|^brisanet|^unifique|^desktop-", "provedor regional"),
    (r"^geek", "GEEK NETWORKS"),
    (r"^starlink", "Starlink"),
    (r"^tp-?link", "TP-Link"),
    (r"^d-?link|^dir-\d+", "D-Link"),
    (r"^netgear", "Netgear"),
    (r"^mercusys", "Mercusys"),
    (r"^intelbras", "Intelbras"),
    (r"^tenda", "Tenda"),
    (r"^zyxel", "Zyxel"),
    (r"^asustek|^asus", "Asus"),
    (r"^huawei", "Huawei"),
    (r"^zt[ef][ _\-]", "ZTE"),
    (r"^fiberhome", "Fiberhome"),
    (r"^sagemcom|^technicolor|^vantiva", "Sagemcom/Technicolor"),
    (r"^mitrastar", "MitraStar"),
    (r"^multilaser|^mult[ _\-]", "Multilaser"),
    (r"^cisco|^linksys", "Cisco/Linksys"),
    (r"^ubiquiti|^unifi", "Ubiquiti/UniFi"),
    (r"^wifi[ _\-]?[0-9a-f]{4}", "padrão genérico (WiFi-XXXX)"),
]


def risco_de(auth_mode: str | None) -> str:
    """Classifica o `auth_mode` do WiGLE em uma faixa de risco.

    Cobre as grafias que aparecem nos logs: `[WPA2_PSK]`, `[WPA2-PSK-CCMP+TKIP][ESS]`,
    `[WPA_WPA2_PSK]`, `[OPEN]`, `[ESS]` (aberta), `[WPA3_SAE]`, `[WPA2-EAP-CCMP]`.
    """
    texto = (auth_mode or "").upper()
    if not texto or "UNDEFINED" in texto:
        return "desconhecido"
    if "OPEN" in texto:
        return "aberta"
    if "WEP" in texto:
        return "wep"
    if "EAP" in texto or "802.1X" in texto:
        return "empresa"
    tem3 = "WPA3" in texto
    tem2 = "WPA2" in texto
    # "WPA2" contém "WPA": o WPA "puro" é o que não tem dígito depois
    tem1 = re.search(r"WPA(?![\d3])", texto) is not None
    if tem3 and tem2:
        return "mista"
    if tem3:
        return "wpa3"
    if tem2 and tem1:
        return "mista"
    if tem2:
        return "wpa2"
    if tem1:
        return "wpa"
    if "ESS" in texto:
        return "aberta"      # ESS sozinho = rede aberta no WiGLE
    return "desconhecido"


def pior_risco(categorias) -> str:
    """A faixa mais grave de uma lista (para colorir a célula pelo pior caso)."""
    pior = None
    for categoria in categorias:
        if pior is None or RISCO[categoria]["gravidade"] < RISCO[pior]["gravidade"]:
            pior = categoria
    return pior or "desconhecido"


def ssid_de_fabrica(ssid: str | None) -> str | None:
    """Devolve o nome do padrão de fábrica que casou, ou None."""
    nome = (ssid or "").strip().lower()
    if not nome:
        return None
    for padrao, rotulo in PADROES_FABRICA:
        if re.search(padrao, nome):
            return rotulo
    return None


# --------------------------------------------------------------------------- #
# Consultas
# --------------------------------------------------------------------------- #
def _filtros(file_ids, de: str, ate: str) -> tuple[str, list]:
    where = ["o.has_gps = 1", "o.type = 'WIFI'"]
    params: list = []
    if file_ids:
        ids = [int(i) for i in file_ids]
        where.append(f"o.file_id IN ({','.join('?' * len(ids))})")
        params += ids
    if de:
        where.append("o.first_seen >= ?")
        params.append(de)
    if ate:
        where.append("o.first_seen <= ?")
        params.append(ate + " 23:59:59" if len(ate) == 10 else ate)
    return " AND ".join(where), params


def resumo(conn) -> dict:
    """Contagem por faixa de risco das redes Wi-Fi (não das observações)."""
    contagem = {chave: 0 for chave in RISCO}
    for linha in conn.execute("SELECT mac, MAX(auth_mode) AS auth FROM networks "
                              "WHERE type = 'WIFI' GROUP BY mac"):
        contagem[risco_de(linha["auth"])] += 1
    total = sum(contagem.values())
    fracas = sum(contagem[c] for c in RISCOS_FRACOS)
    return {"por_risco": contagem, "total": total, "fracas": fracas,
            "pct_fracas": round(100 * fracas / total, 1) if total else 0.0}


def por_celula(conn, *, file_ids=None, de: str = "", ate: str = "",
               grau: float = 0.001, limite: int = 2000) -> list[dict]:
    """Ranking de células (~100 m) por redes fracas.

    Cada célula devolve quantas redes Wi-Fi diferentes foram vistas, quantas são
    fracas e o pior risco encontrado — é o que a camada do mapa pinta.
    """
    where_sql, params = _filtros(file_ids, de, ate)
    linhas = conn.execute(
        f"""SELECT round(o.lat, 3) AS lat, round(o.lon, 3) AS lon, o.mac,
                   MAX(n.auth_mode) AS auth, COUNT(*) AS obs
              FROM observations o JOIN networks n ON n.mac = o.mac
             WHERE {where_sql}
             GROUP BY 1, 2, o.mac""",
        tuple(params),
    ).fetchall()
    celulas: dict[tuple, dict] = {}
    for linha in linhas:
        chave = (linha["lat"], linha["lon"])
        celula = celulas.setdefault(chave, {"lat": linha["lat"], "lon": linha["lon"],
                                            "redes": 0, "fracas": 0, "obs": 0,
                                            "abertas": 0, "wep": 0, "riscos": set()})
        risco = risco_de(linha["auth"])
        celula["redes"] += 1
        celula["obs"] += linha["obs"]
        celula["riscos"].add(risco)
        if risco in RISCOS_FRACOS:
            celula["fracas"] += 1
        if risco == "aberta":
            celula["abertas"] += 1
        elif risco == "wep":
            celula["wep"] += 1
    saida = []
    for celula in celulas.values():
        pior = pior_risco(celula["riscos"])
        saida.append({
            "lat": celula["lat"], "lon": celula["lon"], "redes": celula["redes"],
            "fracas": celula["fracas"], "abertas": celula["abertas"], "wep": celula["wep"],
            "obs": celula["obs"], "pior": pior,
            "pct_fracas": round(100 * celula["fracas"] / celula["redes"], 1),
        })
    saida.sort(key=lambda c: (-c["fracas"], -c["pct_fracas"], -c["redes"]))
    return saida[:limite]


def fabrica(conn, *, limite: int = 200) -> list[dict]:
    """Redes com SSID de fábrica, agrupadas pelo padrão que casou."""
    grupos: dict[tuple, dict] = {}
    for linha in conn.execute(
            """SELECT ssid, mac, auth_mode, obs_count, best_rssi, best_lat, best_lon
                 FROM networks WHERE type = 'WIFI' AND COALESCE(ssid,'') <> ''"""):
        padrao = ssid_de_fabrica(linha["ssid"])
        if not padrao:
            continue
        chave = (padrao, linha["ssid"])
        grupo = grupos.setdefault(chave, {"padrao": padrao, "ssid": linha["ssid"],
                                          "redes": 0, "obs": 0, "abertas": 0,
                                          "lat": linha["best_lat"], "lon": linha["best_lon"]})
        grupo["redes"] += 1
        grupo["obs"] += linha["obs_count"] or 0
        if risco_de(linha["auth_mode"]) in RISCOS_FRACOS:
            grupo["abertas"] += 1
    saida = list(grupos.values())
    saida.sort(key=lambda g: (-g["redes"], -g["obs"]))
    return saida[:limite]


def mascarar_ssid(ssid: str | None) -> str:
    """Mostra só o começo do nome: dá para reconhecer o padrão sem expor o vizinho."""
    nome = (ssid or "").strip()
    if len(nome) <= 4:
        return "***"
    return nome[:4] + "***"


def mascarar_mac(mac: str | None) -> str:
    limpo = (mac or "").upper()
    return limpo[:8] + ":**:**" if len(limpo) >= 8 else "**"


def csv_ranking(celulas: list[dict], mascarar: bool = False) -> bytes:
    buffer = io.StringIO()
    escritor = csv.writer(buffer, lineterminator="\r\n")
    escritor.writerow(["latitude", "longitude", "redes_wifi", "abertas", "wep",
                       "redes_fracas", "pct_fracas", "pior_risco", "observacoes"])
    for c in celulas:
        escritor.writerow([c["lat"], c["lon"], c["redes"], c["abertas"], c["wep"],
                           c["fracas"], c["pct_fracas"],
                           RISCO[c["pior"]]["rotulo"], c["obs"]])
    return buffer.getvalue().encode("utf-8")


def csv_fabrica(itens: list[dict], mascarar: bool = False) -> bytes:
    buffer = io.StringIO()
    escritor = csv.writer(buffer, lineterminator="\r\n")
    escritor.writerow(["ssid", "padrao", "redes", "observacoes", "abertas_ou_wep"])
    for item in itens:
        escritor.writerow([mascarar_ssid(item["ssid"]) if mascarar else item["ssid"],
                           item["padrao"], item["redes"], item["obs"], item["abertas"]])
    return buffer.getvalue().encode("utf-8")
