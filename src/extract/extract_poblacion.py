"""EXTRACT: fuente 4 (complementaria), población por comuna de Cali.

El archivo no tiene API: se descarga desde datos.cali.gov.co y se guarda en data/bronze
con el nombre de config.yaml (fuentes.poblacion.archivo). Se aceptan tres formatos:

  - ancho: una columna de comuna y una columna por año (2019, 2020, ...)
  - largo: una columna de comuna, una de año y una de población
  - simple: una columna de comuna y una de población (sin año)

Si el archivo no existe o no se puede leer, la función devuelve None y el ETL continúa
sin esta variable (queda registrado en el reporte de calidad).
"""
import re
from datetime import date

import pandas as pd

from .. import config as C
from ..logger import stage_logger

log = stage_logger("EXTRACT")

_COL_COMUNA = ("comuna", "comunas", "nombre_comuna", "nom_comuna", "comnombre", "territorio", "nombre")
_COL_ANIO = ("anio", "año", "ano", "year", "vigencia")
_COL_POBLACION = ("poblacion", "población", "total", "habitantes", "personas")


def _comuna_num(texto):
    """'Comuna 01', 'COMUNA 1', '01' -> 1. Devuelve None si no es una comuna (corregimientos, totales)."""
    m = re.search(r"\d+", str(texto))
    if not m:
        return None
    n = int(m.group())
    return n if 1 <= n <= 22 else None


def _to_int(serie):
    """'80.000', '80,000', '80000' -> 80000. Las proyecciones son enteros."""
    limpio = serie.astype(str).str.replace(r"[\s\.,]", "", regex=True)
    return pd.to_numeric(limpio, errors="coerce")


def _read_raw(path):
    """Lee el archivo sin asumir encabezado en la primera fila (el archivo de la Alcaldía trae títulos)."""
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path, sheet_name=C.POBLACION_SHEET, header=None, dtype=str)
    # No se usa detección automática del separador: los títulos con comas la confunden.
    # Se prueba ';' (Excel en español) y luego ',' (CSV estándar).
    for enc in ("utf-8-sig", "latin-1"):
        for sep in (";", ","):
            try:
                df = pd.read_csv(path, header=None, dtype=str, encoding=enc, sep=sep, engine="python",
                                 skip_blank_lines=False)
            except UnicodeDecodeError:
                break
            if df.shape[1] >= 3:
                return df
    raise ValueError("no se pudo leer el CSV (separador ';' o ',' y codificación utf-8 o latin-1)")


def _es_anio(v):
    return bool(re.fullmatch(r"(19|20)\d{2}", str(v).strip()))


def _con_encabezado(raw):
    """Busca la fila de encabezados: la primera con al menos dos años, o con 'Descripción'/'Comuna'."""
    for i in range(min(20, len(raw))):
        celdas = [str(c).strip() for c in raw.iloc[i].tolist()]
        if sum(_es_anio(c) for c in celdas) >= 2 or any(c.lower().startswith(("descrip", "comuna")) for c in celdas):
            df = raw.iloc[i + 1:].copy()
            df.columns = celdas
            return df.loc[:, [c != "" and c.lower() != "nan" for c in df.columns]]
    return raw  # sin encabezado reconocible: se usa la primera fila, como en un CSV normal


def _anio_objetivo(anios):
    """Año a usar: el configurado, o el más reciente que no sea futuro (2027 es proyección)."""
    if C.POBLACION_ANIO != "auto":
        return int(C.POBLACION_ANIO)
    hasta = date.today().year
    pasados = [a for a in anios if a <= hasta]
    return max(pasados) if pasados else max(anios)


def _col(df, candidatos):
    for c in df.columns:
        if str(c).strip().lower() in candidatos:
            return c
    for c in df.columns:
        if any(k in str(c).strip().lower() for k in candidatos):
            return c
    return None


def parse_poblacion(df):
    """Devuelve DataFrame con columnas comuna_num, comuna, anio, poblacion (una fila por comuna)."""
    col_com = _col(df, _COL_COMUNA) or df.columns[0]
    df = df.rename(columns={col_com: "comuna_texto"})
    df["comuna_num"] = df["comuna_texto"].map(_comuna_num)
    df = df.dropna(subset=["comuna_num"]).copy()
    df["comuna_num"] = df["comuna_num"].astype(int)

    col_anio = _col(df, _COL_ANIO)
    col_pob = _col(df, _COL_POBLACION)
    anios_ancho = [c for c in df.columns if _es_anio(c)]

    if anios_ancho:                                   # formato ancho: un año por columna
        anio = _anio_objetivo([int(str(c).strip()) for c in anios_ancho])
        col = next(c for c in anios_ancho if int(str(c).strip()) == anio)
        out = pd.DataFrame({"comuna_num": df["comuna_num"], "anio": anio, "poblacion": _to_int(df[col])})
    elif col_anio and col_pob:                        # formato largo: año objetivo
        tmp = pd.DataFrame({"comuna_num": df["comuna_num"],
                            "anio": pd.to_numeric(df[col_anio], errors="coerce"),
                            "poblacion": _to_int(df[col_pob])})
        anio = _anio_objetivo(sorted(tmp["anio"].dropna().astype(int).unique().tolist()))
        out = tmp[tmp["anio"] == anio].copy()
    elif col_pob:                                     # formato simple
        out = pd.DataFrame({"comuna_num": df["comuna_num"], "anio": pd.NA,
                            "poblacion": _to_int(df[col_pob])})
    else:
        raise ValueError(f"no se reconocen las columnas (encabezados: {list(df.columns)[:8]})")

    out = out.dropna(subset=["poblacion"]).drop_duplicates("comuna_num")
    out["comuna"] = out["comuna_num"].map(lambda n: f"Comuna {n:02d}")
    out["anio"] = out["anio"].astype("Int64")
    return out[["comuna_num", "comuna", "anio", "poblacion"]].sort_values("comuna_num").reset_index(drop=True)


def extract_poblacion(report):
    """Fuente 4 (opcional). Devuelve DataFrame por comuna o None."""
    if not C.POBLACION_FILE.exists():
        msg = (f"Población no disponible: no existe {C.POBLACION_FILE.name} en data/bronze. "
               "Las columnas de población quedan vacías (ver README).")
        log.warning(msg)
        report.warn(msg)
        return None
    try:
        pob = parse_poblacion(_con_encabezado(_read_raw(C.POBLACION_FILE)))
    except Exception as e:                            # archivo mal formado: el ETL sigue sin población
        msg = f"Población no se pudo leer ({type(e).__name__}: {e}). Se continúa sin esta variable."
        log.error(msg)
        report.warn(msg)
        return None
    anio = pob["anio"].dropna().max()
    log.info("Población: %d comunas leídas (año %s, total %s habitantes)", len(pob),
             "sin año" if pd.isna(anio) else int(anio), f"{int(pob['poblacion'].sum()):,}")
    report.step("extract", "poblacion", len(pob), len(pob), archivo=C.POBLACION_FILE.name)
    return pob
