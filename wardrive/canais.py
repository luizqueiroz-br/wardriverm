"""Ocupação de canais Wi-Fi num ponto: qual canal configurar no roteador.

A pergunta "qual canal eu uso?" tem resposta no próprio acervo: basta olhar quem
está no ar em volta do ponto e somar a **energia** que cada vizinho joga no canal
candidato. Não é contagem de APs: um vizinho a -40 dBm atrapalha mil vezes mais que
um a -70 dBm, e canal vizinho (4, 5, 8…) também invade — por isso a sobreposição.
"""
from __future__ import annotations

import math

from .geo import distancia_m

#: 2,4 GHz só tem três canais que não se sobrepõem (no Brasil, 1 a 13)
CANAIS_24 = (1, 6, 11)

#: 5 GHz sem DFS — os de radar (52–144) podem sumir por 60 s quando detectam radar
CANAIS_5_SEM_DFS = (36, 40, 44, 48, 149, 153, 157, 161, 165)

FAIXAS = {"2.4": "2,4 GHz", "5": "5 GHz"}


def potencia_mw(rssi: float) -> float:
    """Escala relativa de energia: -40 dBm → 1000, -60 → 100, -100 → 1."""
    return 10 ** ((float(rssi) + 100) / 20)


def faixa_de(canal: int, freq: float | None = None) -> str:
    """2,4 GHz ou 5 GHz — a frequência manda quando existe; o número é o palpite."""
    if freq:
        if freq >= 5000:
            return "5"
        if 2400 <= freq < 2500:
            return "2.4"
    return "5" if canal and canal >= 36 else "2.4"


def fator_sobreposicao(diferenca: int, faixa: str) -> float:
    """Quanto um AP a `diferenca` canais do candidato ainda atrapalha (0 a 1).

    2,4 GHz: canais espaçados de 5 MHz, canal de 20 MHz — quem está a 1 canal ainda
    entra com 80%, a 4 canais com 20%, a 5 já não entra.
    5 GHz: espaçamento de 20 MHz, então só o mesmo canal (a ±4 quando o vizinho usa 40 MHz).
    """
    if diferenca == 0:
        return 1.0
    if faixa == "2.4":
        if diferenca >= 5:
            return 0.0
        return round(1 - diferenca / 5, 2)
    if diferenca == 4:
        return 0.25
    return 0.0


def _aps_no_raio(conn, lat: float, lon: float, raio_m: float, *,
                 tipo: str = "WIFI", excluir_mac: str | None = None) -> list[dict]:
    """Uma linha por MAC, com o canal e o sinal **da observação mais forte**."""
    dlat = raio_m / 111_320.0
    dlon = raio_m / (111_320.0 * max(0.15, math.cos(math.radians(lat))))
    linhas = conn.execute(
        """SELECT mac, ssid, channel, frequency, rssi, lat, lon
             FROM observations
            WHERE type = ? AND has_gps = 1 AND channel IS NOT NULL AND rssi IS NOT NULL
              AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?""",
        (tipo, lat - dlat, lat + dlat, lon - dlon, lon + dlon),
    ).fetchall()

    melhores: dict[str, dict] = {}
    for linha in linhas:
        if distancia_m(lat, lon, linha["lat"], linha["lon"]) > raio_m:
            continue                                   # a caixa é quadrada; o raio é círculo
        mac = (linha["mac"] or "").upper()
        if not mac or (excluir_mac and mac == excluir_mac.upper()):
            continue
        if mac not in melhores or linha["rssi"] > melhores[mac]["rssi"]:
            canal = int(linha["channel"])
            melhores[mac] = {
                "mac": mac,
                "ssid": linha["ssid"] or "",
                "canal": canal,
                "faixa": faixa_de(canal, linha["frequency"]),
                "rssi": linha["rssi"],
            }
    return list(melhores.values())


def sugestao(aps: list[dict], faixa: str) -> list[dict]:
    """Candidatos da faixa, do menos poluído para o mais poluído."""
    candidatos = CANAIS_24 if faixa == "2.4" else CANAIS_5_SEM_DFS
    itens = []
    for canal in candidatos:
        energia = 0.0
        no_canal = 0
        for ap in aps:
            fator = fator_sobreposicao(abs(ap["canal"] - canal), faixa)
            if fator <= 0:
                continue
            energia += potencia_mw(ap["rssi"]) * fator
            if ap["canal"] == canal:
                no_canal += 1
        itens.append({"canal": canal, "energia": round(energia, 1), "aps_mesmo_canal": no_canal})
    itens.sort(key=lambda item: (item["energia"], item["canal"]))
    melhor = itens[0]["energia"] if itens else 0.0
    for posicao, item in enumerate(itens, start=1):
        item["posicao"] = posicao
        item["excesso_pct"] = (round(100 * (item["energia"] - melhor) / melhor)
                               if melhor > 0 else 0)
    return itens


def relatorio(conn, lat: float, lon: float, raio_m: float = 100.0, *,
              tipo: str = "WIFI", excluir_mac: str | None = None) -> dict:
    aps = _aps_no_raio(conn, lat, lon, raio_m, tipo=tipo, excluir_mac=excluir_mac)
    saida = {"lat": lat, "lon": lon, "raio_m": raio_m, "aps": len(aps), "faixas": {}}
    for faixa in ("2.4", "5"):
        meus = [ap for ap in aps if ap["faixa"] == faixa]
        por_canal: dict[int, list[dict]] = {}
        for ap in meus:
            por_canal.setdefault(ap["canal"], []).append(ap)
        canais = []
        for canal in sorted(por_canal):
            grupo = por_canal[canal]
            canais.append({
                "canal": canal,
                "aps": len(grupo),
                "rssi_medio": round(sum(a["rssi"] for a in grupo) / len(grupo), 1),
                "rssi_forte": max(a["rssi"] for a in grupo),
            })
        canais.sort(key=lambda c: -c["aps"])
        saida["faixas"][faixa] = {
            "rotulo": FAIXAS[faixa],
            "aps": len(meus),
            "canais": canais,
            "sugestao": sugestao(meus, faixa),
        }
    return saida
