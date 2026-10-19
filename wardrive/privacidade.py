"""Áreas privadas: não publicar (nem enviar) o que foi coletado num raio.

A regra existe porque exportar é publicar: mascarar MAC no print não resolve se a
coordenada da sua casa vai junto no CSV. Cada área tem um modo:

- ``excluir`` — a linha sai do CSV (é a falta dela que protege);
- ``zerar`` — a linha fica, mas sem coordenada (mantém a contagem de redes).

A decisão é por haversine, nunca por bounding box: a caixa é quadrada e deixaria
passar (ou barrar) o que não devia.
"""
from __future__ import annotations

from . import geo

MODOS = {"excluir": "excluir a linha do CSV", "zerar": "manter a linha sem coordenada"}


def listar(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM areas_privadas ORDER BY id")]


def salvar(conn, *, lat: float, lon: float, raio_m: int, rotulo: str = "",
           modo: str = "excluir") -> int:
    if modo not in MODOS:
        raise ValueError(f"modo inválido: {modo}")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("coordenada fora do intervalo")
    if not (50 <= raio_m <= 50_000):
        raise ValueError("o raio precisa ficar entre 50 m e 50 km")
    cur = conn.execute(
        """INSERT INTO areas_privadas (lat, lon, raio_m, rotulo, modo)
           VALUES (?,?,?,?,?)""",
        (round(lat, 7), round(lon, 7), int(raio_m), (rotulo or "").strip() or None, modo),
    )
    conn.commit()
    return int(cur.lastrowid)


def remover(conn, area_id: int) -> None:
    conn.execute("DELETE FROM areas_privadas WHERE id = ?", (int(area_id),))
    conn.commit()


def _area_do_ponto(lat, lon, areas: list[dict]) -> dict | None:
    """Primeira área (na ordem em que foram criadas) que contém o ponto."""
    if lat is None or lon is None:
        return None
    for area in areas:
        if geo.distancia_m(lat, lon, area["lat"], area["lon"]) <= area["raio_m"]:
            return area
    return None


def aplicar(rows: list[dict], areas: list[dict]) -> tuple[list[dict], dict]:
    """Filtra as linhas do lote. Devolve (linhas, resumo) — o resumo vai no manifesto."""
    resumo = {"areas": len(areas), "omitidas": 0, "zeradas": 0, "por_area": {}}
    if not areas:
        return rows, resumo
    saida: list[dict] = []
    for row in rows:
        area = _area_do_ponto(row.get("lat"), row.get("lon"), areas)
        if area is None:
            saida.append(row)
            continue
        chave = area["rotulo"] or f"área {area['id']}"
        resumo["por_area"][chave] = resumo["por_area"].get(chave, 0) + 1
        if area["modo"] == "zerar":
            # cópia: a linha original segue intacta na memória de quem chamou
            saida.append({**row, "lat": None, "lon": None, "altitude": None, "accuracy": None})
            resumo["zeradas"] += 1
        else:
            resumo["omitidas"] += 1
    return saida, resumo


def previa(conn, selection: dict, areas: list[dict] | None = None) -> dict:
    """Contagem do que será omitido/zerado, sem montar arquivo (usada no aviso)."""
    from . import exporter  # import tardio: exporter importa este módulo

    areas = listar(conn) if areas is None else areas
    if not areas:
        return {"areas": 0, "omitidas": 0, "zeradas": 0, "por_area": {}, "total": 0}
    rows = exporter.fetch_rows(conn, selection)
    _, resumo = aplicar(rows, areas)
    resumo["total"] = len(rows)
    return resumo
