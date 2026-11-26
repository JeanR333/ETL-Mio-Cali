"""TRANSFORM (Silver): limpieza del feed GTFS (fuente 1): paradas, rutas, calendario, viajes y horarios."""
import numpy as np
import pandas as pd

from .. import config as C
from ..logger import stage_logger
from .common import (TIPO_PARADA, TIPO_SERVICIO, FUERA_COMUNAS, norm_text, to_seconds, haversine_m,
                     _strip_all, _drop_logged, _valid_coords)

log = stage_logger("TRANSFORM")


def clean_gtfs_stops(df, report):
    n0, det = len(df), {}
    df = _strip_all(df)
    df.columns = [c.strip().lower() for c in df.columns]
    faltan = {"stop_id", "stop_name", "stop_lat", "stop_lon"} - set(df.columns)
    if faltan:
        raise ValueError(f"gtfs_stops: faltan columnas obligatorias {faltan}")
    for c in ("stop_lat", "stop_lon"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["location_type"] = pd.to_numeric(df.get("location_type"), errors="coerce").fillna(0).astype(int)
    df["tipo_parada"] = df["location_type"].map(TIPO_PARADA).fillna("otro")
    df["nombre_norm"] = df["stop_name"].map(norm_text)

    df = _drop_logged(df, df["stop_id"].isna() | df["stop_name"].isna(), "id_o_nombre_nulo", "gtfs_stops", det)
    df = _drop_logged(df, df["stop_id"].duplicated(), "stop_id_duplicado", "gtfs_stops", det)
    df = _drop_logged(df, ~_valid_coords(df["stop_lat"], df["stop_lon"]), "coordenadas_fuera_de_cali", "gtfs_stops", det)

    rep = int(df["nombre_norm"].duplicated(keep=False).sum())
    det["paradas_con_nombre_repetido"] = rep
    log.info("gtfs_stops: %d paradas comparten nombre con otra (se conservan: son paradas distintas)", rep)
    report.step("transform_silver", "gtfs_stops", n0, len(df), detalle=det)
    log.info("gtfs_stops silver: %d -> %d filas", n0, len(df))
    return df


def clean_routes(df, report):
    n0, det = len(df), {}
    df = _strip_all(df)
    df["route_short_name"] = df["route_short_name"].str.upper()
    df["tipo_servicio"] = df["route_short_name"].str[0].map(TIPO_SERVICIO).fillna("Otro")
    df["route_type"] = pd.to_numeric(df["route_type"], errors="coerce").astype("Int64")
    df = _drop_logged(df, df["route_id"].isna(), "route_id_nulo", "routes", det)
    df = _drop_logged(df, df["route_id"].duplicated(), "route_id_duplicado", "routes", det)
    log.info("routes: tipo de servicio derivado de la letra inicial: %s",
             df["tipo_servicio"].value_counts().to_dict())
    report.step("transform_silver", "routes", n0, len(df), detalle=det)
    return df


def clean_calendar(df, report):
    df = _strip_all(df)
    dias = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for d in dias:
        df[d] = pd.to_numeric(df[d], errors="coerce").fillna(0).astype(int)
    for c in ("start_date", "end_date"):
        df[c] = pd.to_datetime(df[c], format="%Y%m%d", errors="coerce")

    def tipo(r):
        if r[dias[:5]].sum() > 0:
            return "habil"
        if r["saturday"]:
            return "sabado"
        return "domingo_festivo"
    df["tipo_dia"] = df.apply(tipo, axis=1)
    log.info("calendar: vigencia %s a %s", df["start_date"].min().date(), df["end_date"].max().date())
    report.step("transform_silver", "calendar", len(df), len(df))
    return df


def clean_trips(df, routes, calendar, report):
    n0, det = len(df), {}
    df = _strip_all(df)
    df["direction_id"] = pd.to_numeric(df["direction_id"], errors="coerce").astype("Int64")
    df = _drop_logged(df, df["trip_id"].duplicated(), "trip_id_duplicado", "trips", det)
    df = _drop_logged(df, ~df["route_id"].isin(routes["route_id"]), "ruta_inexistente", "trips", det)
    df = _drop_logged(df, ~df["service_id"].isin(calendar["service_id"]), "servicio_inexistente", "trips", det)
    df = df.merge(calendar[["service_id", "tipo_dia"]], on="service_id", how="left")
    report.step("transform_silver", "trips", n0, len(df), detalle=det)
    return df


def representative_services(trips, report):
    """El feed trae varios service_id que se solapan para el mismo tipo de día (versiones
    alternativas de la programación). Sumarlos duplicaría la frecuencia, así que por cada
    tipo de día se toma el service_id con más viajes."""
    cnt = trips.groupby(["tipo_dia", "service_id"]).size().rename("viajes").reset_index()
    rep = cnt.sort_values(["tipo_dia", "viajes", "service_id"], ascending=[True, False, True]) \
             .drop_duplicates("tipo_dia")
    sel = dict(zip(rep["tipo_dia"], rep["service_id"]))
    log.info("Servicios representativos por tipo de día: %s", sel)
    log.debug("Viajes por service_id: %s", cnt.to_dict("records"))
    report.data["servicios_representativos"] = sel
    return sel


def process_stop_times(chunks, trips, stops_ids, report):
    """Agrega stop_times por bloques -> tabla a nivel de viaje + pares ruta-parada."""
    trip_route = trips.set_index("trip_id")["route_id"]
    valid_trips, valid_stops = set(trips["trip_id"]), set(stops_ids)
    parts, pairs = [], set()
    tot = {"filas_leidas": 0, "hora_invalida": 0, "trip_inexistente": 0, "parada_inexistente": 0}

    for i, ch in enumerate(chunks, 1):
        tot["filas_leidas"] += len(ch)
        ch = _strip_all(ch)
        ch["arr_s"] = to_seconds(ch["arrival_time"].fillna(""))
        ch["dep_s"] = to_seconds(ch["departure_time"].fillna(""))
        bad_t = ch["arr_s"].isna() | ch["dep_s"].isna()
        bad_trip = ~ch["trip_id"].isin(valid_trips)
        bad_stop = ~ch["stop_id"].isin(valid_stops)
        tot["hora_invalida"] += int(bad_t.sum())
        tot["trip_inexistente"] += int(bad_trip.sum())
        tot["parada_inexistente"] += int(bad_stop.sum())
        ch = ch.loc[~(bad_t | bad_trip | bad_stop)]
        ch["seq"] = pd.to_numeric(ch["stop_sequence"], errors="coerce")

        parts.append(ch.groupby("trip_id").agg(t_inicio=("dep_s", "min"), t_fin=("arr_s", "max"),
                                                n_paradas=("stop_id", "size")))
        rs = pd.DataFrame({"route_id": ch["trip_id"].map(trip_route), "stop_id": ch["stop_id"]}).drop_duplicates()
        pairs.update(map(tuple, rs.to_numpy()))
        log.debug("stop_times bloque %d: %d filas válidas", i, len(ch))

    agg = pd.concat(parts).groupby(level=0).agg(t_inicio=("t_inicio", "min"), t_fin=("t_fin", "max"),
                                                 n_paradas=("n_paradas", "sum")).reset_index()
    agg["duracion_min"] = (agg["t_fin"] - agg["t_inicio"]) / 60
    agg["hora_inicio"] = (agg["t_inicio"] // 3600).astype(int)
    validas = tot["filas_leidas"] - tot["hora_invalida"] - tot["trip_inexistente"] - tot["parada_inexistente"]
    for k in ("hora_invalida", "trip_inexistente", "parada_inexistente"):
        (log.warning if tot[k] else log.info)("stop_times: %d filas con %s", tot[k], k)
    log.info("stop_times: %d filas leídas, %d viajes agregados, %d pares ruta-parada",
             tot["filas_leidas"], len(agg), len(pairs))

    malos = agg["duracion_min"] <= 0
    if malos.any():
        log.warning("stop_times: %d viajes con duración <= 0 se descartan", int(malos.sum()))
    agg = agg.loc[~malos]
    report.step("transform_silver", "stop_times", tot["filas_leidas"], validas, detalle=tot,
                viajes_agregados=len(agg), viajes_duracion_invalida=int(malos.sum()))

    trip_level = trips.merge(agg, on="trip_id", how="inner")
    sin_horario = len(trips) - len(trip_level)
    if sin_horario:
        log.warning("trips: %d viajes sin horarios en stop_times", sin_horario)
    route_stops = pd.DataFrame(sorted(pairs), columns=["route_id", "stop_id"])
    return trip_level, route_stops
