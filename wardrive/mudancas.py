"""O que mudou na rua entre duas passagens.

Compara duas janelas de tempo numa mesma área e devolve três listas:

- **novas**: MAC que não aparecia antes;
- **sumiram**: aparecia antes e não agora;
- **mudaram**: mesmo MAC, mas o SSID ou a segurança mudou — indício de
  reconfiguração, troca de equipamento ou (em MAC fixo) de clonagem.

`first_seen` é texto `YYYY-MM-DD HH:MM:SS` no horário local do aparelho que
coletou. Comparar como string funciona porque o formato é fixo e ordenável; o
fuso de cada sessão é o do celular/ESP, não há conversão.
"""
from __future__ import annotations

import math

from .geo import distancia_m


def _filtro_area(area: dict | None) -> tuple[str, list]:
    if not area:
        return "", []
    lat, lon, raio = area["lat"], area["lon"], area["raio_m"]
    dlat = raio / 111_320.0
    dlon = raio / (111_320.0 * max(0.15, math.cos(math.radians(lat))))
    return (" AND o.has_gps = 1 AND o.lat BETWEEN ? AND ? AND o.lon BETWEEN ? AND ?",
            [lat - dlat, lat + dlat, lon - dlon, lon + dlon])


def _coletar(conn, de: str, ate: str, area: dict | None, tipo: str) -> dict[str, dict]:
    """{mac: {'ssid', 'auth', 'obs', ...}} — um registro por MAC, pelo par dominante."""
    trecho_area, params_area = _filtro_area(area)
    filtro_tipo = "" if tipo == "all" else " AND o.type = ?"
    params_tipo = [] if tipo == "all" else [tipo]
    linhas = conn.execute(
        f"""SELECT o.mac, COALESCE(o.ssid, '') AS ssid, COALESCE(o.auth_mode, '') AS auth,
                   COUNT(*) AS n, MIN(o.first_seen) AS primeiro, MAX(o.first_seen) AS ultimo,
                   MAX(o.rssi) AS forte, o.type AS tipo,
                   MAX(n2.best_lat) AS lat, MAX(n2.best_lon) AS lon,
                   MAX(n2.categoria) AS categoria
              FROM observations o LEFT JOIN networks n2 ON n2.mac = o.mac
             WHERE o.first_seen >= ? AND o.first_seen <= ?{filtro_tipo}{trecho_area}
             GROUP BY o.mac, o.ssid, o.auth_mode""",
        [de, ate] + params_tipo + params_area,
    ).fetchall()

    por_mac: dict[str, dict] = {}
    for linha in linhas:
        mac = (linha["mac"] or "").upper()
        atual = por_mac.get(mac)
        registro = {
            "mac": mac, "ssid": linha["ssid"], "auth": linha["auth"], "obs": linha["n"],
            "primeiro": linha["primeiro"], "ultimo": linha["ultimo"], "rssi": linha["forte"],
            "lat": linha["lat"], "lon": linha["lon"], "tipo": linha["tipo"],
            "categoria": linha["categoria"],
        }
        if atual is None:
            por_mac[mac] = registro
            continue
        # o "estado" do MAC na janela é o par (ssid, auth) mais observado
        if registro["obs"] > atual["obs"]:
            registro["obs"] += atual["obs"]
            registro["primeiro"] = min(atual["primeiro"], registro["primeiro"])
            registro["ultimo"] = max(atual["ultimo"], registro["ultimo"])
            por_mac[mac] = registro
        else:
            atual["obs"] += registro["obs"]
            atual["primeiro"] = min(atual["primeiro"], registro["primeiro"])
            atual["ultimo"] = max(atual["ultimo"], registro["ultimo"])
    return por_mac


def _no_raio(registro: dict, area: dict | None) -> bool:
    if not area or registro["lat"] is None or registro["lon"] is None:
        return True
    return distancia_m(area["lat"], area["lon"], registro["lat"], registro["lon"]) <= area["raio_m"]


def _fim(texto: str) -> str:
    """Uma data sem hora vale o dia inteiro: `first_seen` é string e
    `'2026-10-18 12:00:00' > '2026-10-18'` — compararia errado."""
    return f"{texto} 23:59:59" if len(texto) == 10 else texto


def comparar(conn, de_a: str, ate_a: str, de_b: str, ate_b: str, *,
             area: dict | None = None, tipo: str = "all", limite: int = 300) -> dict:
    """Duas janelas [de_a..ate_a] (antes) e [de_b..ate_b] (agora)."""
    janela_a = _coletar(conn, de_a, _fim(ate_a), area, tipo)
    janela_b = _coletar(conn, de_b, _fim(ate_b), area, tipo)
    if area:  # a caixa em graus é só pré-filtro: confere o círculo no registro já agregado
        janela_a = {m: r for m, r in janela_a.items() if _no_raio(r, area)}
        janela_b = {m: r for m, r in janela_b.items() if _no_raio(r, area)}

    novas = [r for mac, r in janela_b.items() if mac not in janela_a]
    sumiram = [r for mac, r in janela_a.items() if mac not in janela_b]

    mudaram = []
    for mac in set(janela_a) & set(janela_b):
        antes, agora = janela_a[mac], janela_b[mac]
        mudou_ssid = antes["ssid"] != agora["ssid"]
        mudou_auth = antes["auth"] != agora["auth"]
        if not (mudou_ssid or mudou_auth):
            continue
        mudaram.append({
            "mac": mac,
            "tipo": agora["tipo"],
            "categoria": agora["categoria"],
            "ssid_antes": antes["ssid"], "ssid_agora": agora["ssid"],
            "auth_antes": antes["auth"], "auth_agora": agora["auth"],
            "mudou_ssid": mudou_ssid, "mudou_auth": mudou_auth,
            "obs_antes": antes["obs"], "obs_agora": agora["obs"],
            "primeiro_antes": antes["primeiro"], "ultimo_agora": agora["ultimo"],
            "lat": agora["lat"] if agora["lat"] is not None else antes["lat"],
            "lon": agora["lon"] if agora["lon"] is not None else antes["lon"],
        })

    novas.sort(key=lambda r: -r["obs"])
    sumiram.sort(key=lambda r: -r["obs"])
    mudaram.sort(key=lambda r: (-(r["mudou_auth"] * 2 + r["mudou_ssid"]), -r["obs_agora"]))

    return {
        "janelas": {"antes": {"de": de_a, "ate": ate_a, "redes": len(janela_a)},
                    "agora": {"de": de_b, "ate": ate_b, "redes": len(janela_b)}},
        "area": area,
        "tipo": tipo,
        "novas": novas[:limite], "novas_total": len(novas),
        "sumiram": sumiram[:limite], "sumiram_total": len(sumiram),
        "mudaram": mudaram[:limite], "mudaram_total": len(mudaram),
        "comuns": len(set(janela_a) & set(janela_b)),
    }


def periodo_disponivel(conn) -> tuple[str, str]:
    """Primeira e última data com observação (só a parte YYYY-MM-DD)."""
    linha = conn.execute("SELECT MIN(first_seen) AS a, MAX(first_seen) AS b FROM observations").fetchone()
    if not linha or not linha["a"]:
        return "", ""
    return linha["a"][:10], linha["b"][:10]


def janelas_sugeridas(conn, dias: int = 2) -> dict[str, str]:
    """Divide o acervo em "antes" e "agora", ambos com `dias` de duração."""
    from datetime import date, timedelta

    _, ultimo = periodo_disponivel(conn)
    if not ultimo:
        hoje = date.today()
        ultimo = hoje.isoformat()
    fim = date.fromisoformat(ultimo)
    inicio_b = fim - timedelta(days=dias - 1)
    fim_a = inicio_b - timedelta(days=1)
    inicio_a = fim_a - timedelta(days=dias - 1)
    return {
        "de_a": inicio_a.isoformat(), "ate_a": fim_a.isoformat(),
        "de_b": inicio_b.isoformat(), "ate_b": fim.isoformat(),
    }


#: o que aparece no seletor de período — o usuário escolhe a ideia, não a data
PRESETS: dict[str, tuple[str, int]] = {
    "1d": ("último dia × dia anterior", 1),
    "2d": ("últimos 2 dias × 2 anteriores", 2),
    "7d": ("última semana × semana anterior", 7),
}
PRESET_PADRAO = "2d"


def janelas_por_preset(conn, preset: str, *, de_a: str = "", ate_a: str = "",
                       de_b: str = "", ate_b: str = "") -> dict[str, str]:
    """Resolve o período escolhido por nome. `personalizado` usa as datas do formulário.

    O "último dia" se conta a partir da **última coleta do acervo**, não do relógio:
    quem não saiu hoje não quer comparar hoje (daria zero de um lado).
    """
    if preset == "personalizado" and all((de_a, ate_a, de_b, ate_b)):
        return {"de_a": de_a, "ate_a": ate_a, "de_b": de_b, "ate_b": ate_b}

    if preset == "tudo":
        from datetime import date, timedelta

        inicio, fim = periodo_disponivel(conn)
        if not inicio or not fim:
            return janelas_sugeridas(conn)
        primeiro, ultimo = date.fromisoformat(inicio), date.fromisoformat(fim)
        meio = primeiro + (ultimo - primeiro) / 2
        return {"de_a": primeiro.isoformat(), "ate_a": meio.isoformat(),
                "de_b": (meio + timedelta(days=1)).isoformat(), "ate_b": ultimo.isoformat()}

    dias = PRESETS.get(preset, PRESETS[PRESET_PADRAO])[1]
    return janelas_sugeridas(conn, dias)
