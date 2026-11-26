"""TRANSFORM (Silver): limpieza de las paradas de la API ArcGIS (fuente 2)."""
import numpy as np
import pandas as pd

from .. import config as C
from ..logger import stage_logger
from .common import (TIPO_PARADA, TIPO_SERVICIO, FUERA_COMUNAS, norm_text, to_seconds, haversine_m,
                     _strip_all, _drop_logged, _valid_coords)

log = stage_logger("TRANSFORM")


def clean_api_stops(df, report):
    n0, det = len(df), {}
    df = _strip_all(df).rename(columns={
        "OBJECTID": "api_objectid", "ID": "api_id", "GStopID": "stop_id", "GStopType": "location_type",
        "GStopParen": "parent_station", "ParentID": "platform_code",
        "GWheelchairBoarding": "wheelchair_boarding", "latitude": "stop_lat", "longitude": "stop_lon"})
    log.info("api_stops: columnas renombradas al estándar GTFS (GStopID -> stop_id, etc.)")

    vacias = [c for c in df.columns if df[c].isna().all()]
    if vacias:
        log.warning("api_stops: columnas 100%% vacías eliminadas: %s", vacias)
        df = df.drop(columns=vacias)
    det["columnas_vacias_eliminadas"] = vacias

    for c in ("stop_lat", "stop_lon"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["location_type"] = pd.to_numeric(df["location_type"], errors="coerce").fillna(0).astype(int)
    df["tipo_parada"] = df["location_type"].map(TIPO_PARADA).fillna("otro")

    df = _drop_logged(df, df["stop_id"].isna(), "stop_id_nulo", "api_stops", det)
    df = _drop_logged(df, df["stop_id"].duplicated(), "stop_id_duplicado", "api_stops", det)
    df = _drop_logged(df, ~_valid_coords(df["stop_lat"], df["stop_lon"]), "coordenadas_fuera_de_cali", "api_stops", det)

    report.step("transform_silver", "api_stops", n0, len(df), detalle=det)
    log.info("api_stops silver: %d -> %d filas", n0, len(df))
    return df
