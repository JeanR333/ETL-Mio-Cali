"""Utilidades y constantes compartidas por las transformaciones."""
import re
import unicodedata

import numpy as np
import pandas as pd

from .. import config as C
from ..logger import stage_logger

log = stage_logger("TRANSFORM")

TIPO_PARADA = {0: "parada", 1: "estacion"}
FUERA_COMUNAS = "Fuera de comunas (rural)"
TIPO_SERVICIO = {"T": "Troncal", "P": "Pretroncal", "A": "Alimentadora", "E": "Expresa"}


# ----------------------------------------------------------------- utilidades
def norm_text(v):
    """minúsculas, sin tildes y sin espacios repetidos (para comparar nombres)."""
    if pd.isna(v):
        return ""
    s = unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.strip().lower())


def to_seconds(s):
    """'HH:MM:SS' -> segundos. GTFS permite horas >= 24 (servicios después de medianoche)."""
    parts = s.str.strip().str.split(":", expand=True)
    if parts.shape[1] != 3:
        return pd.Series(np.nan, index=s.index)
    h, m, sec = (pd.to_numeric(parts[i], errors="coerce") for i in range(3))
    out = h * 3600 + m * 60 + sec
    return out.where((m < 60) & (sec < 60))


def haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6_371_000 * 2 * np.arcsin(np.sqrt(a))


def _strip_all(df):
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_object_dtype(df[c]) or pd.api.types.is_string_dtype(df[c]):
            df[c] = df[c].astype("string").str.strip().replace("", pd.NA)
    return df


def _drop_logged(df, mask, motivo, tabla, report_details):
    n = int(mask.sum())
    if n:
        log.warning("%s: se descartan %d filas por %s", tabla, n, motivo)
    else:
        log.debug("%s: 0 filas con %s", tabla, motivo)
    report_details[motivo] = n
    return df.loc[~mask].copy()


def _valid_coords(lat, lon):
    return lat.between(*C.CALI_LAT) & lon.between(*C.CALI_LON)


