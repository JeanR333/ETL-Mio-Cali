"""TRANSFORM (Gold): asignación de paradas a comunas (fuente 3) mediante spatial join."""
import numpy as np
import pandas as pd

from .. import config as C
from ..logger import stage_logger
from .common import (TIPO_PARADA, TIPO_SERVICIO, FUERA_COMUNAS, norm_text, to_seconds, haversine_m,
                     _strip_all, _drop_logged, _valid_coords)

log = stage_logger("TRANSFORM")


def _comuna_name_key(props):
    keys = list(props)
    for cand in ("comnombre", "nombre", "comuna", "nom_comuna", "id_comuna", "cod_comuna", "name"):
        for k in keys:
            if k.lower() == cand:
                return k
    for k in keys:
        if "comuna" in k.lower() or "nombre" in k.lower():
            return k
    return keys[0] if keys else None


def assign_comunas(gold_stops, geojson, report):
    """Spatial join punto-en-polígono: asigna a cada parada la comuna donde cae."""
    from matplotlib.path import Path as MPath

    feats = geojson.get("features", [])
    key = _comuna_name_key(feats[0].get("properties", {}) or {})
    polys = []
    for f in feats:
        geom = f.get("geometry") or {}
        rings = []
        if geom.get("type") == "Polygon":
            rings = [geom["coordinates"][0]]
        elif geom.get("type") == "MultiPolygon":
            rings = [poly[0] for poly in geom["coordinates"]]
        name = str((f.get("properties") or {}).get(key, "sin_nombre")).strip()
        for ring in rings:
            arr = np.asarray(ring, dtype=float)[:, :2]
            polys.append((name, arr))
    if not polys:
        log.warning("Comunas: el GeoJSON no tiene polígonos válidos")
        return gold_stops.assign(comuna=pd.NA)

    sample = polys[0][1][0]
    if abs(sample[0]) > 180 or abs(sample[1]) > 180:
        # Coordenadas planas (metros): se convierten a lon/lat con pyproj
        crs = ((geojson.get("crs") or {}).get("properties") or {}).get("name") or C.COMUNAS_CRS_DEFAULT
        try:
            from pyproj import Transformer
        except ImportError:
            msg = "Comunas: coordenadas proyectadas y pyproj no está instalado (pip install pyproj)"
            log.error(msg); report.warn(msg)
            return gold_stops.assign(comuna=pd.NA)
        tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        polys = [(n, np.column_stack(tr.transform(a[:, 0], a[:, 1]))) for n, a in polys]
        log.info("Comunas: coordenadas convertidas de %s a EPSG:4326", crs)
        lon, lat = polys[0][1][0]
        if not (C.CALI_LON[0] - 0.2 < lon < C.CALI_LON[1] + 0.2 and C.CALI_LAT[0] - 0.2 < lat < C.CALI_LAT[1] + 0.2):
            msg = f"Comunas: tras convertir desde {crs} los polígonos no caen en Cali; revise el sistema de coordenadas"
            log.error(msg); report.warn(msg)
            return gold_stops.assign(comuna=pd.NA)
    if abs(sample[0]) < 10 < abs(sample[1]):     # viene como (lat, lon) -> se invierte
        log.info("Comunas: coordenadas en orden lat/lon, se invierten a lon/lat")
        polys = [(n, a[:, ::-1]) for n, a in polys]

    pts = gold_stops[["lon", "lat"]].to_numpy()
    comuna = np.full(len(pts), None, dtype=object)
    for name, arr in polys:
        inside = MPath(arr).contains_points(pts) & (comuna == None)  # noqa: E711
        comuna[inside] = name
    sin = int(sum(c is None for c in comuna))
    out = gold_stops.assign(comuna=[c if c is not None else FUERA_COMUNAS for c in comuna])
    log.info("Comunas: campo de nombre '%s'; %d paradas asignadas, %d fuera de las comunas (zona rural o borde)",
             key, len(out) - sin, sin)
    report.step("transform_gold", "asignacion_comunas", len(out), len(out) - sin, campo_nombre=key,
                paradas_sin_comuna=sin)
    return out


def build_gold_comunas(gold_stops, route_stops, report):
    d = gold_stops.dropna(subset=["comuna"])
    rs = route_stops.merge(d[["stop_id", "comuna"]], on="stop_id")
    g = d.groupby("comuna").agg(n_paradas=("stop_id", "nunique"),
                                n_estaciones=("tipo_parada", lambda s: int((s == "estacion").sum())))
    g["n_rutas"] = rs.groupby("comuna")["route_id"].nunique()
    g = g.fillna(0).astype(int).reset_index().sort_values("n_paradas", ascending=False)
    log.info("gold_comunas: %d comunas con paradas", len(g))
    report.step("transform_gold", "gold_comunas", len(d), len(g))
    return g
