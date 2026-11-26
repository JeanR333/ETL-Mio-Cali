"""LOAD: escribe las capas Silver y Gold en CSV y los KPIs en JSON."""
import json

from .. import config as C
from ..logger import stage_logger

log = stage_logger("LOAD")


def save_csv(df, capa, nombre, report):
    folder = {"silver": C.SILVER, "gold": C.GOLD}[capa]
    path = folder / f"{nombre}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")
    log.info("%s/%s.csv escrito: %d filas, %.1f KB", capa, nombre, len(df), path.stat().st_size / 1024)
    report.step("load", f"{capa}.{nombre}", len(df), len(df), archivo=str(path.relative_to(C.ROOT)))
    return path


def save_kpis(kpis):
    path = C.GOLD / "kpis.json"
    path.write_text(json.dumps(kpis, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("KPIs escritos en %s", path.relative_to(C.ROOT))
    return path
