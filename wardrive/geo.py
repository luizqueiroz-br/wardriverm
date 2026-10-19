"""Distâncias geográficas usadas no filtro de privacidade, na cobertura e nos rastreadores."""
from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

RAIO_TERRA_KM = 6371.0088
METROS_POR_GRAU_LAT = 111_320.0


def distancia_km(lat1, lon1, lat2, lon2) -> float:
    """Distância em km entre dois pontos (haversine)."""
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * RAIO_TERRA_KM * asin(sqrt(a))


def distancia_m(lat1, lon1, lat2, lon2) -> float:
    return distancia_km(lat1, lon1, lat2, lon2) * 1000.0


def caixa_do_raio(lat: float, lon: float, raio_m: float) -> tuple[float, float, float, float]:
    """Bounding box que envolve o círculo — serve só para pré-filtrar no SQL.

    A decisão final é sempre por haversine: a caixa é quadrada e incluiria cantos
    que estão fora do raio.
    """
    dlat = raio_m / METROS_POR_GRAU_LAT
    # no hemisfério sul a longitude encolhe com o cosseno da latitude
    dlon = raio_m / (METROS_POR_GRAU_LAT * max(cos(radians(lat)), 1e-6))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon
