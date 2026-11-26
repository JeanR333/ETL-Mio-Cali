"""LOAD: carga las tablas Silver y Gold en una base de datos SQLite.

La base queda en DB_PATH (.env), por defecto data/gold/mio_cali.db. Cada tabla se
reemplaza en cada ejecución y, al final, se verifica que el número de filas en la base
coincida con el DataFrame cargado (control de calidad de la carga).
"""
import json
import sqlite3
from datetime import datetime

import pandas as pd

from .. import config as C
from ..logger import stage_logger

log = stage_logger("LOAD_DB")


def _prepare(df):
    """SQLite no acepta listas/dicts ni algunos tipos de pandas: se convierten a texto o número."""
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == bool or str(out[c].dtype) == "boolean":
            out[c] = out[c].astype("Int64")
        elif out[c].dtype == object:
            out[c] = out[c].map(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    return out


def load_tables(tables, report, run_id):
    """tables: dict {nombre_tabla: DataFrame}. Devuelve {tabla: filas_en_bd}."""
    C.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    resultado = {}
    with sqlite3.connect(C.DB_PATH) as con:
        for nombre, df in tables.items():
            _prepare(df).to_sql(nombre, con, if_exists="replace", index=False, chunksize=10_000)
            n_db = con.execute(f'SELECT COUNT(*) FROM "{nombre}"').fetchone()[0]
            if n_db != len(df):
                log.error("%s: %d filas en el DataFrame pero %d en la base", nombre, len(df), n_db)
                raise RuntimeError(f"Carga incompleta en la tabla {nombre}")
            log.info("Tabla %s cargada: %d filas", nombre, n_db)
            report.step("load_db", nombre, len(df), n_db)
            resultado[nombre] = n_db

        # Tabla de control: una fila por ejecución del ETL
        con.execute("""CREATE TABLE IF NOT EXISTS etl_ejecuciones (
                         run_id TEXT, fecha TEXT, tabla TEXT, filas INTEGER)""")
        fecha = datetime.now().isoformat(timespec="seconds")
        con.executemany("INSERT INTO etl_ejecuciones VALUES (?, ?, ?, ?)",
                        [(run_id, fecha, t, n) for t, n in resultado.items()])
    log.info("Base de datos actualizada: %s (%d tablas)", C.DB_PATH.relative_to(C.ROOT), len(resultado))
    return resultado


def read_table(nombre):
    """Lee una tabla de la base (útil en el EDA y en las pruebas)."""
    with sqlite3.connect(C.DB_PATH) as con:
        return pd.read_sql(f'SELECT * FROM "{nombre}"', con)
