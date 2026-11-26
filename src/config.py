"""Configuración del ETL MIO Cali.

- .env               -> variables de entorno (ruta del config, nivel de log, base de datos)
- config/config.yaml -> fuentes, rutas de las capas y parámetros de validación

El resto del código usa las constantes de este módulo; para cambiar algo se edita
config/config.yaml o .env, no el código.
"""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

CONFIG_FILE = ROOT / os.getenv("CONFIG_PATH", "config/config.yaml")
if not CONFIG_FILE.exists():
    raise FileNotFoundError(f"No se encontró {CONFIG_FILE}. Revise CONFIG_PATH en .env")

with open(CONFIG_FILE, encoding="utf-8") as f:
    CFG = yaml.safe_load(f)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ---------------------------------------------------------------- rutas
_r = CFG["rutas"]
BRONZE, SILVER, GOLD, LOGS = (ROOT / _r[k] for k in ("bronze", "silver", "gold", "logs"))
DATA = BRONZE.parent
for _p in (BRONZE, SILVER, GOLD, LOGS):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- fuente 1: GTFS
_g = CFG["fuentes"]["gtfs"]
GTFS_URL = _g["url"]
GTFS_ZIP = BRONZE / _g["archivo_local"]
GTFS_TIMEOUT = _g["timeout_s"]
STOP_TIMES_CHUNK = int(_g["chunk_stop_times"])

# ---------------------------------------------------------------- fuente 2: API ArcGIS
_a = CFG["fuentes"]["api_arcgis"]
API_BASE = _a["url_base"]
API_LAYER_STOPS = int(_a["capa_paradas"])
API_LAYER_LINES = int(_a["capa_lineas"])
API_PAGE_SIZE = int(_a["registros_por_pagina"])
API_MAX_PAGES = int(_a["max_paginas"])
API_TIMEOUT = _a["timeout_s"]

# ---------------------------------------------------------------- fuente 3: comunas
_c = CFG["fuentes"]["comunas"]
COMUNAS_SHP_ZIP = BRONZE / _c["shapefile_zip"]
COMUNAS_FILE = BRONZE / _c["geojson"]
COMUNAS_CRS_DEFAULT = _c["crs_por_defecto"]
COMUNAS_TIMEOUT = _c["timeout_s"]
COMUNAS_URLS = list(_c.get("urls_descarga") or [])

# ---------------------------------------------------------------- base de datos
_db = CFG.get("base_datos", {})
DB_PATH = ROOT / os.getenv("DB_PATH", _db.get("ruta_por_defecto", "data/gold/mio_cali.db"))
DB_LOAD_SILVER = bool(_db.get("cargar_silver", True))

# ---------------------------------------------------------------- validación
CALI_LAT = tuple(CFG["validacion"]["latitud"])
CALI_LON = tuple(CFG["validacion"]["longitud"])

# ---------------------------------------------------------------- fuente 4: población (opcional)
_p = CFG["fuentes"].get("poblacion", {})
POBLACION_FILE = BRONZE / _p.get("archivo", "poblacion_comunas.csv")
POBLACION_SHEET = _p.get("hoja", 0)
_anio = str(_p.get("anio", "auto")).strip()
POBLACION_ANIO = _anio if _anio == "auto" else int(_anio)  # "auto" = último año que no sea futuro

# ---------------------------------------------------------------- accesibilidad
_acc = CFG.get("accesibilidad", {})
ACC_GRID_M = float(_acc.get("malla_m", 100))
ACC_CRS_M = _acc.get("crs_metrico", "EPSG:6249")
ACC_RADIOS_M = [int(r) for r in _acc.get("radios_m", [200, 300, 500])]
ACC_RADIO_M = int(_acc.get("radio_principal_m", 500))
