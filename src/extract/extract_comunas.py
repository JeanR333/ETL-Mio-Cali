"""EXTRACT: fuente 3, capa de comunas de Cali (shapefile/GeoJSON de la IDESC - Datos Abiertos Cali)."""
import json
import zipfile

import pandas as pd
import requests

from .. import config as C
from ..logger import stage_logger, profile

log = stage_logger("EXTRACT")


# ----------------------------------------------------------------- Comunas (IDESC)
def _shp_zip_to_geojson(zpath):
    """Convierte un shapefile comprimido (ZIP) a GeoJSON en memoria, conservando su sistema de coordenadas."""
    import io
    import re
    import shapefile  # pyshp
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        base = next(n[:-4] for n in names if n.lower().endswith(".shp"))
        data = {ext: io.BytesIO(z.read(next(n for n in names if n.lower() == (base + ext).lower())))
                for ext in (".shp", ".dbf", ".shx")}
        cst = next((n for n in names if n.lower().endswith(".cst")), None)
        enc = z.read(cst).decode().strip() if cst else "latin-1"
        prj = z.read(base + ".prj").decode(errors="ignore") if (base + ".prj") in names else ""
    r = shapefile.Reader(shp=data[".shp"], dbf=data[".dbf"], shx=data[".shx"], encoding=enc)
    fields = [f[0] for f in r.fields[1:]]
    feats = [{"type": "Feature", "properties": dict(zip(fields, rec.record)), "geometry": rec.shape.__geo_interface__}
             for rec in r.iterShapeRecords()]
    m = re.search(r'AUTHORITY\["EPSG",\s*"(\d+)"\]\]\s*$', prj.strip())
    gj = {"type": "FeatureCollection", "features": feats}
    if m:
        gj["crs"] = {"type": "name", "properties": {"name": f"EPSG:{m.group(1)}"}}
    log.info("Comunas: shapefile %s.shp convertido a GeoJSON (%d polígonos, %s)", base, len(feats),
             gj.get("crs", {}).get("properties", {}).get("name", "CRS no declarado"))
    return gj


def extract_comunas(report):
    """Fuente 3 (opcional): polígonos de comunas en GeoJSON. Prueba un archivo local y luego
    cada URL configurada. Devuelve el dict GeoJSON o None."""
    if C.COMUNAS_SHP_ZIP.exists():
        gj, origen = _shp_zip_to_geojson(C.COMUNAS_SHP_ZIP), "shapefile_datos_abiertos_cali"
        C.COMUNAS_FILE.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    elif C.COMUNAS_FILE.exists():
        log.info("Comunas: se usa el archivo local %s", C.COMUNAS_FILE.name)
        gj, origen = json.loads(C.COMUNAS_FILE.read_text(encoding="utf-8")), "archivo_local"
    else:
        gj = origen = None
        for url in C.COMUNAS_URLS:
            host = url.split("/")[2]
            try:
                log.info("Comunas: intentando descarga desde %s", host)
                r = requests.get(url, timeout=C.COMUNAS_TIMEOUT)
                r.raise_for_status()
                cand = r.json()
                if not cand.get("features"):
                    raise ValueError("la respuesta no es un GeoJSON con 'features'")
                gj, origen = cand, host
                C.COMUNAS_FILE.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
                log.info("Comunas: descargadas desde %s y guardadas en Bronze", host)
                break
            except (requests.RequestException, ValueError) as e:
                log.warning("Comunas: falló %s (%s)", host, type(e).__name__)
                log.warning("Detalle del error: %s", str(e)[:400])
        if gj is None:
            msg = ("Comunas no disponibles en ninguna fuente. KPI 3 queda pendiente. "
                   f"Descargue el GeoJSON y guárdelo en data/bronze/{C.COMUNAS_FILE.name}")
            log.warning(msg)
            report.warn(msg)
            return None
    n = len(gj.get("features", []))
    log.info("Comunas: %d polígonos leídos", n)
    report.step("extract", "comunas", n, n, origen=origen)
    return gj if n else None
