"""SSID repetido no mesmo quarteirão: indício de clone (evil twin) ou de mesh.

O critério ingênuo — dois BSSIDs com o mesmo nome na mesma célula — devolve mais
de mil casos no acervo, e quase tudo é malha, repetidor ou CPE do vizinho com o
mesmo nome de fábrica. Aqui o filtro é mais duro:

- **OUI diferente**: dois rádios da mesma marca no mesmo SSID são a mesma caixa
  (mesh, 2,4 + 5 GHz). Quem clona usa outro equipamento, ou MAC aleatório.
- **sinal parecido**: os dois alcançáveis, dentro de 15 dB.
- **poucos BSSIDs na célula**: 15 no mesmo nome é hotel, não ataque.
- **mesmo canal** e **segurança diferente** pesam no grau do indício.

Nada aqui afirma ataque: a tela mostra o que foi ouvido, com hora e sinal.
"""
from __future__ import annotations

from . import categorias, seguranca

NIVEIS = {"forte": "indício forte", "medio": "indício médio", "fraco": "indício fraco"}


def _oui(mac: str) -> str:
    return (mac or "").upper()[:8]


def _coletar(conn) -> list[dict]:
    """Uma linha por (SSID, MAC, célula de 0,001°) com o melhor sinal visto."""
    return conn.execute(
        """SELECT COALESCE(ssid, '') AS ssid, mac, round(lat, 3) AS lat, round(lon, 3) AS lon,
                  MAX(rssi) AS rssi, MAX(channel) AS canal, MAX(auth_mode) AS auth,
                  COUNT(*) AS obs, MIN(first_seen) AS primeira, MAX(first_seen) AS ultima
             FROM observations
            WHERE type = 'WIFI' AND has_gps = 1 AND TRIM(COALESCE(ssid, '')) <> ''
              AND rssi IS NOT NULL
            GROUP BY ssid, mac, round(lat, 3), round(lon, 3)"""
    ).fetchall()


def _avaliar(grupo: list[dict]) -> dict | None:
    """Compara o BSSID mais forte com os outros da mesma célula."""
    ordenado = sorted(grupo, key=lambda b: -(b["rssi"] or -999))
    forte = ordenado[0]
    parceiros = []
    motivos = set()
    canal_igual = False
    auth_diferente = False
    for outro in ordenado[1:]:
        if _oui(outro["mac"]) == _oui(forte["mac"]):
            continue                                   # mesmo fabricante: mesh/dual-band
        # MAC aleatório é celular com hotspot pessoal (mesmo nome, endereço novo a
        # cada vez) — não é clone, é randomização
        if categorias.mac_aleatorio(forte["mac"]) or categorias.mac_aleatorio(outro["mac"]):
            continue
        if abs((outro["rssi"] or -999) - (forte["rssi"] or -999)) > 15:
            continue                                   # um está longe demais para ser o mesmo clone
        parceiros.append(outro)
        if outro["canal"] == forte["canal"]:
            canal_igual = True
            motivos.add("mesmo canal")
        if (outro["auth"] or "") != (forte["auth"] or ""):
            auth_diferente = True
            motivos.add("segurança diferente")
    if not parceiros:
        return None

    pontos = (2 if canal_igual else 0) + (1 if auth_diferente else 0)
    if len(grupo) <= 3:
        pontos += 1
        motivos.add("poucos BSSIDs na célula")
    diferenca = abs((parceiros[0]["rssi"] or -999) - (forte["rssi"] or -999))
    if diferenca <= 6:
        pontos += 1
        motivos.add("sinal quase igual")
    motivos.add("fabricante diferente")

    nivel = "forte" if pontos >= 4 else "medio" if pontos >= 2 else "fraco"
    bssids = [forte] + parceiros
    return {
        "ssid": forte["ssid"],
        "lat": forte["lat"], "lon": forte["lon"],
        "n_bssids": len(grupo),
        "nivel": nivel,
        "pontos": pontos,
        "motivos": sorted(motivos),
        "canal": forte["canal"],
        "provedor": seguranca.ssid_de_fabrica(forte["ssid"]),
        "bssids": [{"mac": b["mac"], "rssi": b["rssi"], "canal": b["canal"], "auth": b["auth"],
                    "obs": b["obs"], "primeira": b["primeira"], "ultima": b["ultima"]}
                   for b in bssids],
    }


def detectar(conn, *, limite: int = 200, max_bssids: int = 4) -> dict:
    """Células com SSID repetido por fabricantes diferentes."""
    grupos: dict[tuple, list[dict]] = {}
    for linha in _coletar(conn):
        grupos.setdefault((linha["ssid"], linha["lat"], linha["lon"]), []).append(linha)

    itens = []
    for grupo in grupos.values():
        if not 2 <= len(grupo) <= max_bssids:
            continue
        avaliado = _avaliar(grupo)
        if avaliado:
            itens.append(avaliado)

    ordem = {"forte": 0, "medio": 1, "fraco": 2}
    itens.sort(key=lambda i: (ordem[i["nivel"]], -i["pontos"], -i["n_bssids"], i["ssid"]))
    por_nivel = {nivel: sum(1 for i in itens if i["nivel"] == nivel) for nivel in NIVEIS}
    return {"total": len(itens), "por_nivel": por_nivel, "itens": itens[:limite],
            "max_bssids": max_bssids}


def para_rede(conn, mac: str, limite: int = 5) -> list[dict]:
    """Em quais células este MAC aparece ao lado de um sósia (para a ficha da rede)."""
    alvo = (mac or "").upper()
    achados = detectar(conn, limite=100000)
    saida = []
    for item in achados["itens"]:
        macs = {b["mac"].upper() for b in item["bssids"]}
        if alvo in macs:
            item = dict(item)
            item["outros"] = [b for b in item["bssids"] if b["mac"].upper() != alvo]
            saida.append(item)
    return saida[:limite]
