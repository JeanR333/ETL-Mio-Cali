"""EXTRACT: fuente 2, API REST ArcGIS (paradas y líneas), con paginación. Guarda la respuesta original en Bronze."""
import json
import zipfile

import pandas as pd
import requests

from .. import config as C
from ..logger import stage_logger, profile

log = stage_logger("EXTRACT")


# ----------------------------------------------------------------- API ArcGIS
def _api_page(layer_id, offset):
    params = {
        "where": "1=1", "outFields": "*", "f": "json", "outSR": "4326",
        "returnGeometry": "true" if layer_id == C.API_LAYER_STOPS else "false",
        "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": C.API_PAGE_SIZE,
    }
    r = requests.get(f"{C.API_BASE}/{layer_id}/query", params=params, timeout=C.API_TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    if payload.get("error"):
        raise RuntimeError(f"Error de la API: {payload['error']}")
    return payload


def _features_to_df(features):
    rows = []
    for feat in features:
        row = dict(feat.get("attributes", {}))
        geom = feat.get("geometry") or {}
        if geom:
            row["longitude"], row["latitude"] = geom.get("x"), geom.get("y")
        rows.append(row)
    return pd.DataFrame(rows)


def extract_api(layer_id, label, report):
    """Descarga todas las páginas de una capa. Si la API no responde, usa la última copia Bronze."""
    raw_json = C.BRONZE / f"api_{label}_raw.json"
    try:
        features, fields, offset = [], None, 0
        for page in range(1, C.API_MAX_PAGES + 1):
            payload = _api_page(layer_id, offset)
            batch = payload.get("features", [])
            fields = fields or payload.get("fields")
            features.extend(batch)
            log.info("API %s: página %d, offset %d, %d registros", label, page, offset, len(batch))
            if not batch or not payload.get("exceededTransferLimit"):
                break
            offset += len(batch)
        else:
            log.warning("API %s: se alcanzó el máximo de %d páginas", label, C.API_MAX_PAGES)
        combined = {"fields": fields, "features": features, "paginas": page, "origen": "api"}
        raw_json.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
        origen = "api"
    except (requests.RequestException, RuntimeError) as e:
        if not raw_json.exists():
            log.error("API %s no disponible y no hay copia Bronze: %s", label, e)
            raise
        log.warning("API %s no disponible (%s). Se usa la copia Bronze existente.", label, type(e).__name__)
        log.warning("Detalle del error: %s", str(e)[:400])
        report.warn(f"API {label}: se usó copia Bronze por error de conexión ({type(e).__name__})")
        combined = json.loads(raw_json.read_text(encoding="utf-8"))
        features, origen = combined.get("features", []), "cache_bronze"
        if combined.get("exceededTransferLimit"):
            msg = f"La copia Bronze de {label} viene de una descarga sin paginar (puede estar incompleta)"
            log.warning(msg)
            report.warn(msg)

    df = _features_to_df(features)
    df.to_csv(C.BRONZE / f"api_{label}_raw.csv", index=False, encoding="utf-8-sig")
    perfil = profile(log, df, f"bronze.api_{label}")
    report.step("extract", f"api_{label}", len(df), len(df), origen=origen, perfil=perfil)
    return df
