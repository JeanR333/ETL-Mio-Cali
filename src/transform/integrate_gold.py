"""TRANSFORM (Gold): integración de fuentes en tablas de análisis (paradas, rutas, servicios por hora)."""
import numpy as np
import pandas as pd

from .. import config as C
from ..logger import stage_logger
from .common import (TIPO_PARADA, TIPO_SERVICIO, FUERA_COMUNAS, norm_text, to_seconds, haversine_m,
                     _strip_all, _drop_logged, _valid_coords)

log = stage_logger("TRANSFORM")


def build_gold_stops(api, gtfs, route_stops, report):
    """Integra las dos fuentes de paradas por stop_id (= GStopID de la API)."""
    g = gtfs[["stop_id", "stop_name", "stop_lat", "stop_lon", "tipo_parada", "parent_station"]]
    a = api[["stop_id", "api_id", "stop_lat", "stop_lon", "tipo_parada"]]
    m = g.merge(a, on="stop_id", how="outer", suffixes=("_gtfs", "_api"), indicator=True)
    m["presencia"] = m["_merge"].map({"both": "ambas", "left_only": "solo_gtfs", "right_only": "solo_api"})
    m = m.drop(columns="_merge")
    m["distancia_m"] = haversine_m(m["stop_lat_gtfs"], m["stop_lon_gtfs"], m["stop_lat_api"], m["stop_lon_api"]).round(1)
    # Coordenada final: la API es más reciente (2025); si no está, se usa la del GTFS
    m["lat"] = m["stop_lat_api"].fillna(m["stop_lat_gtfs"])
    m["lon"] = m["stop_lon_api"].fillna(m["stop_lon_gtfs"])
    m["fuente_coord"] = np.where(m["stop_lat_api"].notna(), "api", "gtfs")
    m["tipo_parada"] = m["tipo_parada_api"].fillna(m["tipo_parada_gtfs"])
    m = m.drop(columns=["tipo_parada_api", "tipo_parada_gtfs"])
    m["stop_name"] = m["stop_name"].fillna("(sin nombre en GTFS)")

    nr = route_stops.groupby("stop_id")["route_id"].nunique().rename("n_rutas")
    m = m.merge(nr, on="stop_id", how="left")
    m["n_rutas"] = m["n_rutas"].fillna(0).astype(int)

    cnt = m["presencia"].value_counts().to_dict()
    ambas = m.loc[m["presencia"] == "ambas", "distancia_m"]
    log.info("gold_paradas: %d paradas integradas; presencia %s", len(m), cnt)
    log.info("gold_paradas: distancia API vs GTFS (paradas en ambas) mediana %.1f m, p95 %.1f m",
             ambas.median(), ambas.quantile(0.95))
    lejos = int((ambas > 200).sum())
    if lejos:
        log.warning("gold_paradas: %d paradas cambian de ubicación > 200 m entre fuentes", lejos)
    report.step("transform_gold", "gold_paradas", len(gtfs) + len(api), len(m), presencia=cnt,
                distancia_mediana_m=round(float(ambas.median()), 1), paradas_movidas_200m=lejos)
    return m


def build_gold_routes(routes, trips_lvl, route_stops, api_lines, services, report):
    rep = trips_lvl[trips_lvl["service_id"].isin(services.values())]
    hab = rep[rep["tipo_dia"] == "habil"]
    agg = hab.groupby("route_id").agg(
        viajes_dia_habil=("trip_id", "size"),
        duracion_promedio_min=("duracion_min", "mean"),
        primera_salida_h=("t_inicio", "min"), ultima_salida_h=("t_inicio", "max"))
    agg["primera_salida_h"] = (agg["primera_salida_h"] / 3600).round(2)
    agg["ultima_salida_h"] = (agg["ultima_salida_h"] / 3600).round(2)
    span_h = agg["ultima_salida_h"] - agg["primera_salida_h"]
    # Intervalo medio entre salidas (por sentido): horas de operación / viajes por sentido
    agg["intervalo_promedio_min"] = (span_h * 60 / (agg["viajes_dia_habil"] / 2)).round(1)
    agg["duracion_promedio_min"] = agg["duracion_promedio_min"].round(1)
    for td in ("sabado", "domingo_festivo"):
        agg[f"viajes_{td}"] = rep[rep["tipo_dia"] == td].groupby("route_id").size()
    agg["n_paradas"] = route_stops.groupby("route_id")["stop_id"].nunique()

    g = routes.merge(agg.reset_index(), on="route_id", how="left")
    for c in ("viajes_dia_habil", "viajes_sabado", "viajes_domingo_festivo", "n_paradas"):
        g[c] = g[c].fillna(0).astype(int)
    api_ids = set(api_lines["GRouteID"].astype(str).str.strip()) if "GRouteID" in api_lines else set()
    g["en_api_2025"] = g["route_id"].isin(api_ids)
    log.info("gold_rutas: %d rutas; %d también aparecen en la API 2025 (%d rutas en la API)",
             len(g), int(g["en_api_2025"].sum()), len(api_ids))
    sin = int((g["viajes_dia_habil"] == 0).sum())
    if sin:
        log.warning("gold_rutas: %d rutas sin viajes en día hábil", sin)
    report.step("transform_gold", "gold_rutas", len(routes), len(g), rutas_en_api=int(g["en_api_2025"].sum()))
    return g


def build_gold_hourly(trips_lvl, services, report):
    rep = trips_lvl[trips_lvl["service_id"].isin(services.values())]
    h = rep.groupby(["tipo_dia", "hora_inicio"]).size().rename("viajes").reset_index()
    pico = h[h["tipo_dia"] == "habil"].nlargest(1, "viajes")
    if len(pico):
        log.info("gold_servicios_hora: hora pico día hábil %02d:00 con %d salidas",
                 int(pico["hora_inicio"].iloc[0]), int(pico["viajes"].iloc[0]))
    report.step("transform_gold", "gold_servicios_hora", len(rep), len(h))
    return h


def duracion_stats(trips_lvl, services):
    """Tres medidas de duración del recorrido en día hábil (minutos).

    - simple: promedio de las duraciones medias de cada ruta (cada ruta pesa 1).
    - ponderada por viajes: promedio de las rutas con peso = número de viajes de la ruta.
      Es el promedio que representa lo que viven los pasajeros: una ruta con 300 viajes
      pesa más que una con 15.
    - mediana: valor central de todos los viajes, poco sensible a valores extremos.
    """
    rep = trips_lvl[trips_lvl["service_id"].isin(services.values()) & (trips_lvl["tipo_dia"] == "habil")]
    rep = rep.dropna(subset=["duracion_min"])
    por_ruta = rep.groupby("route_id")["duracion_min"].agg(["mean", "size"])
    simple = float(por_ruta["mean"].mean())
    ponderada = float((por_ruta["mean"] * por_ruta["size"]).sum() / por_ruta["size"].sum())
    mediana = float(rep["duracion_min"].median())
    log.info("Duración día hábil: simple %.1f min, ponderada por viajes %.1f min, mediana %.1f min",
             simple, ponderada, mediana)
    return {"simple_por_ruta_min": round(simple, 1), "ponderada_por_viajes_min": round(ponderada, 1),
            "mediana_min": round(mediana, 1), "viajes_considerados": int(len(rep))}


def build_conciliacion_paradas(gold_stops, report):
    """Paradas que no coinciden entre fuentes o que cambiaron de lugar. Se conservan para mostrarlas."""
    m = gold_stops.copy()
    motivo = np.select(
        [(m["presencia"] == "solo_gtfs").to_numpy(bool), (m["presencia"] == "solo_api").to_numpy(bool),
         (m["distancia_m"].fillna(0) > 200).to_numpy(bool)],
        ["solo en GTFS 2017-2021", "solo en API 2025 (nueva)", "reubicada más de 200 m"],
        default="")
    m["motivo"] = motivo
    out = m[m["motivo"] != ""][["stop_id", "stop_name", "presencia", "motivo", "distancia_m",
                                "lat", "lon", "comuna", "n_rutas"]].copy()
    out["distancia_m"] = out["distancia_m"].round(1)
    out = out.sort_values(["motivo", "stop_id"]).reset_index(drop=True)
    conteo = out["motivo"].value_counts().to_dict()
    log.info("Conciliación de paradas: %d registros sin correspondencia exacta %s", len(out), conteo)
    report.step("transform_gold", "gold_conciliacion_paradas", len(gold_stops), len(out), motivos=conteo)
    return out


def build_conciliacion_rutas(routes, gold_routes, api_lines, report):
    """Rutas en GTFS, en la API o en ambas (la API 2025 solo trae el código de ruta, sin nombre)."""
    api_ids = set(api_lines["GRouteID"].astype(str).str.strip()) if "GRouteID" in api_lines else set()
    gtfs_ids = set(routes["route_id"].astype(str))
    base = gold_routes[["route_id", "route_short_name", "route_long_name", "tipo_servicio",
                        "viajes_dia_habil", "en_api_2025"]].copy()
    base["route_id"] = base["route_id"].astype(str)
    base["estado"] = np.where(base["en_api_2025"], "en ambas fuentes", "solo GTFS 2017-2021")
    solo_api = pd.DataFrame({"route_id": sorted(api_ids - gtfs_ids)})
    solo_api["route_short_name"] = pd.NA
    solo_api["route_long_name"] = pd.NA
    solo_api["tipo_servicio"] = pd.NA
    solo_api["viajes_dia_habil"] = 0
    solo_api["en_api_2025"] = True
    solo_api["estado"] = "solo API 2025 (sin horario en GTFS)"
    out = pd.concat([base, solo_api], ignore_index=True)
    conteo = out["estado"].value_counts().to_dict()
    log.info("Conciliación de rutas: %s", conteo)
    report.step("transform_gold", "gold_conciliacion_rutas", len(routes), len(out), estados=conteo)
    return out
