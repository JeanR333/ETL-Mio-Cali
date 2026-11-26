"""TRANSFORM (Gold): accesibilidad peatonal a las paradas por comuna.

Método:
1. Los polígonos de comunas se llevan a metros (EPSG:6249) para medir distancias en metros.
2. Dentro de cada comuna se genera una malla de puntos cada `malla_m` metros (por defecto 100 m).
   Cada punto representa un área de 100 m x 100 m = 0,01 km².
3. Para cada punto se calcula la distancia en línea recta a la parada VIGENTE más cercana
   (paradas de la API 2025; las que solo existen en el GTFS 2017-2021 no se usan).
4. Se resume por comuna: porcentaje de área a 200, 300 y 500 m, distancia media y p90,
   y, si hay población, personas que quedan a 500 m o menos (supuesto: población uniforme
   dentro de la comuna).

Movilidad reducida: el campo GWheelchairBoarding de la API llega vacío en todas las paradas,
así que no se puede medir la accesibilidad física de cada parada. Como aproximación se usa
la cobertura a 200 m (distancia corta que una persona con movilidad reducida puede recorrer).
"""
import numpy as np
import pandas as pd

from .. import config as C
from ..logger import stage_logger

log = stage_logger("TRANSFORM")


def _to_metric_polygons(geojson):
    """Devuelve [(comuna_num, anillo exterior en metros)] usando el CRS declarado en el GeoJSON."""
    from pyproj import Transformer

    feats = geojson.get("features", [])
    crs_src = ((geojson.get("crs") or {}).get("properties") or {}).get("name") or C.COMUNAS_CRS_DEFAULT
    tr = None
    if crs_src.upper() not in (C.ACC_CRS_M.upper(), "EPSG:6249"):
        tr = Transformer.from_crs(crs_src, C.ACC_CRS_M, always_xy=True)
    polys = []
    for f in feats:
        props = f.get("properties") or {}
        num = None
        for k, v in props.items():
            if k.lower() in ("comnombre", "nombre", "comuna", "nom_comuna", "name"):
                num = _num(v)
                break
        if num is None:
            num = _num(props.get("comcodigo"))
        geom = f.get("geometry") or {}
        rings = []
        if geom.get("type") == "Polygon":
            rings = [geom["coordinates"][0]]
        elif geom.get("type") == "MultiPolygon":
            rings = [p[0] for p in geom["coordinates"]]
        for ring in rings:
            arr = np.asarray(ring, dtype=float)[:, :2]
            if tr is not None:
                arr = np.column_stack(tr.transform(arr[:, 0], arr[:, 1]))
            polys.append((num, arr))
    return polys


def _num(v):
    import re
    m = re.search(r"\d+", str(v)) if v is not None else None
    return int(m.group()) if m else None


def _grid_in_polygon(ring, step):
    """Centros de celdas de `step` metros dentro de un anillo (metros)."""
    from matplotlib.path import Path as MPath
    x0, y0 = ring.min(axis=0)
    x1, y1 = ring.max(axis=0)
    xs = np.arange(x0 + step / 2, x1, step)
    ys = np.arange(y0 + step / 2, y1, step)
    if not len(xs) or not len(ys):
        return np.empty((0, 2))
    gx, gy = np.meshgrid(xs, ys)
    pts = np.column_stack([gx.ravel(), gy.ravel()])
    return pts[MPath(ring).contains_points(pts)]


def _distancias_min(puntos, paradas, chunk=2000):
    """Distancia (m) de cada punto a la parada más cercana. Cálculo por bloques para no agotar la memoria."""
    out = np.empty(len(puntos))
    for i in range(0, len(puntos), chunk):
        bloque = puntos[i:i + chunk]
        d2 = ((bloque[:, None, :] - paradas[None, :, :]) ** 2).sum(axis=2)
        out[i:i + chunk] = np.sqrt(d2.min(axis=1))
    return out


def build_accesibilidad(comunas_gj, gold_stops, poblacion, report):
    """Tabla gold_accesibilidad: una fila por comuna y una fila total de Cali."""
    polys = _to_metric_polygons(comunas_gj)
    if not polys:
        log.warning("Accesibilidad: no hay polígonos de comunas")
        return None

    # Paradas vigentes (2025): las que tienen coordenada de la API; las solo_gtfs son de 2017-2021
    vig = gold_stops[gold_stops["presencia"].isin(["ambas", "solo_api"])].dropna(subset=["lat", "lon"])
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", C.ACC_CRS_M, always_xy=True)
    x, y = tr.transform(vig["lon"].to_numpy(), vig["lat"].to_numpy())
    paradas = np.column_stack([x, y])
    log.info("Accesibilidad: %d paradas vigentes (2025) usadas como destino", len(paradas))
    if len(paradas) == 0:
        log.warning("Accesibilidad: no hay paradas vigentes con coordenadas; no se calcula cobertura")
        report.warn("Accesibilidad sin paradas vigentes: no se calculó cobertura")
        return None

    paso = C.ACC_GRID_M
    celda_km2 = (paso * paso) / 1e6
    radios = sorted(set(C.ACC_RADIOS_M + [C.ACC_RADIO_M]))
    pob_por_comuna = dict(zip(poblacion["comuna_num"], poblacion["poblacion"])) if poblacion is not None else {}

    # Agrupa los puntos de la malla por comuna (una comuna puede tener varios polígonos)
    por_comuna = {}
    for num, ring in polys:
        pts = _grid_in_polygon(ring, paso)
        if len(pts):
            por_comuna.setdefault(num, []).append(pts)

    filas = []
    todas_dist = []
    for num in sorted(k for k in por_comuna if k is not None):
        puntos = np.vstack(por_comuna[num])
        d = _distancias_min(puntos, paradas)
        todas_dist.append(d)
        n_par = int(((vig["comuna"] == f"Comuna {num:02d}")).sum()) if "comuna" in vig else None
        fila = {"comuna_num": num, "comuna": f"Comuna {num:02d}", "area_km2": round(len(d) * celda_km2, 2),
                "paradas_vigentes": n_par, "distancia_media_m": round(float(d.mean()), 0),
                "distancia_p90_m": round(float(np.quantile(d, 0.9)), 0)}
        for r in radios:
            fila[f"pct_area_{r}m"] = round(100 * float((d <= r).mean()), 1)
        pob = pob_por_comuna.get(num)
        fila["poblacion"] = pob
        if pob is not None:
            fila["personas_cubiertas_500m"] = int(round(pob * (d <= C.ACC_RADIO_M).mean()))
            fila["personas_sin_cobertura_500m"] = int(pob) - fila["personas_cubiertas_500m"]
            fila["habitantes_por_parada"] = round(pob / n_par, 0) if n_par else None
        filas.append(fila)

    g = pd.DataFrame(filas)
    if not len(g):
        return None

    # Fila total de la ciudad (áreas sumadas; porcentajes ponderados por área)
    dist = np.concatenate(todas_dist)
    total = {"comuna_num": 0, "comuna": "Total Cali (comunas)", "area_km2": round(len(dist) * celda_km2, 2),
             "paradas_vigentes": int(g["paradas_vigentes"].sum()),
             "distancia_media_m": round(float(dist.mean()), 0),
             "distancia_p90_m": round(float(np.quantile(dist, 0.9)), 0)}
    for r in radios:
        total[f"pct_area_{r}m"] = round(100 * float((dist <= r).mean()), 1)
    if pob_por_comuna:
        total["poblacion"] = int(g["poblacion"].sum())
        total["personas_cubiertas_500m"] = int(g["personas_cubiertas_500m"].sum())
        total["personas_sin_cobertura_500m"] = int(g["personas_sin_cobertura_500m"].sum())
        total["habitantes_por_parada"] = round(total["poblacion"] / total["paradas_vigentes"], 0) \
            if total["paradas_vigentes"] else None
    g = pd.concat([g, pd.DataFrame([total])], ignore_index=True)

    r0 = C.ACC_RADIO_M
    log.info("Accesibilidad: %.1f%% del área de comunas a %d m o menos de una parada vigente",
             total[f"pct_area_{r0}m"], r0)
    peores = g.iloc[:-1].nsmallest(2, f"pct_area_{r0}m")
    for r in peores.itertuples():
        log.info("Accesibilidad: %s con %.1f%% del área a %d m o menos", r.comuna, getattr(r, f"pct_area_{r0}m"), r0)
    report.step("transform_gold", "gold_accesibilidad", len(polys), len(g),
                puntos_malla=int(len(dist)), paso_m=paso, radio_principal_m=r0,
                con_poblacion=bool(pob_por_comuna))
    return g

