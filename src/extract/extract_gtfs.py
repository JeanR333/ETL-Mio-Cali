"""EXTRACT: fuente 1, feed GTFS público (ZIP). Copia las tablas originales a Bronze."""
import json
import zipfile

import pandas as pd
import requests

from .. import config as C
from ..logger import stage_logger, profile

log = stage_logger("EXTRACT")

GTFS_TABLES = ["agency", "feed_info", "calendar", "routes", "stops", "trips"]


# ----------------------------------------------------------------- GTFS (ZIP)
def ensure_gtfs_zip(report):
    if C.GTFS_ZIP.exists():
        log.info("Feed GTFS ya existe en Bronze (%.1f MB), no se descarga de nuevo",
                 C.GTFS_ZIP.stat().st_size / 1e6)
        return C.GTFS_ZIP
    log.info("Descargando feed GTFS público")
    r = requests.get(C.GTFS_URL, timeout=C.GTFS_TIMEOUT)
    r.raise_for_status()
    C.GTFS_ZIP.write_bytes(r.content)
    log.info("Feed GTFS descargado: %.1f MB", len(r.content) / 1e6)
    return C.GTFS_ZIP


def extract_gtfs(report):
    """Extrae las tablas pequeñas del GTFS a CSV en Bronze. stop_times se lee luego por bloques."""
    zpath = ensure_gtfs_zip(report)
    out = {}
    with zipfile.ZipFile(zpath) as z:
        names = {n.split("/")[-1].lower(): n for n in z.namelist()}
        log.info("Archivos en el ZIP GTFS: %s", sorted(names))
        for t in GTFS_TABLES:
            member = names.get(f"{t}.txt")
            if member is None:
                log.error("No se encontró %s.txt en el feed GTFS", t)
                raise FileNotFoundError(f"{t}.txt no está en el ZIP GTFS")
            with z.open(member) as f:
                df = pd.read_csv(f, dtype=str, encoding="utf-8-sig")
            df.to_csv(C.BRONZE / f"gtfs_{t}_raw.csv", index=False, encoding="utf-8-sig")
            perfil = profile(log, df, f"bronze.gtfs_{t}")
            report.step("extract", f"gtfs_{t}", len(df), len(df), origen="gtfs_zip", perfil=perfil)
            out[t] = df
        if "stop_times.txt" not in names:
            raise FileNotFoundError("stop_times.txt no está en el ZIP GTFS")
        info = z.getinfo(names["stop_times.txt"])
        log.info("stop_times.txt: %.1f MB (se procesa por bloques en TRANSFORM)", info.file_size / 1e6)
    return out


def iter_stop_times():
    """Generador de bloques de stop_times.txt directamente desde el ZIP."""
    with zipfile.ZipFile(C.GTFS_ZIP) as z:
        member = next(n for n in z.namelist() if n.lower().endswith("stop_times.txt"))
        with z.open(member) as f:
            yield from pd.read_csv(f, dtype=str, chunksize=C.STOP_TIMES_CHUNK, encoding="utf-8-sig",
                                   usecols=["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"])
