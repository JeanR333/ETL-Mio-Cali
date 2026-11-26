"""Logging del ETL.

- logs/etl.log        -> histórico de todas las ejecuciones (DEBUG en adelante)
- logs/run_<id>.log   -> log solo de la ejecución actual
- consola                  -> nivel definido en .env (LOG_LEVEL)
- logs/quality_report_<id>.json -> métricas por etapa (filas, nulos, descartes, tiempos)
"""
import json
import logging
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler

from .config import LOGS, LOG_LEVEL

RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")
_FMT = "%(asctime)s | %(levelname)-8s | %(etapa)-14s | %(message)s"


class _EtapaFilter(logging.Filter):
    """Agrega el campo 'etapa' a cada registro aunque no se pase explícitamente."""
    def filter(self, record):
        if not hasattr(record, "etapa"):
            record.etapa = "general"
        return True


def get_logger(name="etl_mio"):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(_FMT)
    flt = _EtapaFilter()

    hist = RotatingFileHandler(LOGS / "etl.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    hist.setLevel(logging.DEBUG)
    run = logging.FileHandler(LOGS / f"run_{RUN_ID}.log", encoding="utf-8")
    run.setLevel(logging.DEBUG)
    cons = logging.StreamHandler()
    cons.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    handlers = (cons,) if "pytest" in sys.modules else (hist, run, cons)   # las pruebas no ensucian los logs
    if "pytest" in sys.modules:
        hist.close(); run.close(); (LOGS / f"run_{RUN_ID}.log").unlink(missing_ok=True)
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(flt)
        logger.addHandler(h)
    logger.propagate = False
    return logger


def stage_logger(etapa):
    """Logger que marca cada línea con la etapa (EXTRACT, TRANSFORM, LOAD...)."""
    return logging.LoggerAdapter(get_logger(), {"etapa": etapa})


class QualityReport:
    """Acumula métricas de calidad y trazabilidad de cada paso del pipeline."""

    def __init__(self):
        self.data = {"run_id": RUN_ID, "inicio": datetime.now().isoformat(timespec="seconds"),
                     "pasos": [], "advertencias": [], "estado": "EN_CURSO"}

    def step(self, etapa, tabla, filas_entrada, filas_salida, **extra):
        item = {"etapa": etapa, "tabla": tabla, "filas_entrada": int(filas_entrada),
                "filas_salida": int(filas_salida),
                "filas_descartadas": int(filas_entrada - filas_salida), **extra}
        self.data["pasos"].append(item)
        return item

    def warn(self, msg):
        self.data["advertencias"].append(msg)

    def close(self, estado, **extra):
        self.data.update(estado=estado, fin=datetime.now().isoformat(timespec="seconds"), **extra)
        out = LOGS / f"quality_report_{RUN_ID}.json"
        out.write_text(json.dumps(self.data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (LOGS / "quality_report_latest.json").write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        return out


@contextmanager
def timed(log, descripcion):
    """Mide y registra la duración de un bloque."""
    t0 = time.perf_counter()
    log.info("Inicio: %s", descripcion)
    try:
        yield
    except Exception:
        log.exception("Falló: %s (%.2fs)", descripcion, time.perf_counter() - t0)
        raise
    log.info("Fin: %s (%.2fs)", descripcion, time.perf_counter() - t0)


def profile(log, df, nombre):
    """Registra un perfil rápido de un DataFrame: tamaño, nulos y duplicados."""
    nulos = {c: int(n) for c, n in df.isna().sum().items() if n}
    log.info("%s -> %d filas x %d columnas", nombre, len(df), df.shape[1])
    log.debug("%s columnas: %s", nombre, list(df.columns))
    if nulos:
        log.debug("%s nulos por columna: %s", nombre, nulos)
    dups = int(df.duplicated().sum())
    if dups:
        log.warning("%s tiene %d filas duplicadas completas", nombre, dups)
    return {"filas": len(df), "columnas": df.shape[1], "nulos": nulos, "duplicados_completos": dups}
