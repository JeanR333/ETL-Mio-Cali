"""Pruebas de CALIDAD sobre los resultados del pipeline (Gold y base de datos).

Se ejecutan después de `python main.py`. Si todavía no hay resultados, se omiten.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config as C    # noqa: E402

GOLD_PARADAS = C.GOLD / "gold_final.csv"
pytestmark = pytest.mark.skipif(not GOLD_PARADAS.exists(), reason="Primero ejecute python main.py")


@pytest.fixture(scope="module")
def paradas():
    return pd.read_csv(GOLD_PARADAS, dtype={"stop_id": str})


@pytest.fixture(scope="module")
def rutas():
    return pd.read_csv(C.GOLD / "gold_rutas.csv", dtype={"route_id": str})


# ----------------------------------------------------------------- paradas
def test_paradas_llave_unica_y_sin_nulos(paradas):
    assert paradas["stop_id"].notna().all()
    assert paradas["stop_id"].is_unique


def test_paradas_coordenadas_dentro_de_cali(paradas):
    assert paradas["lat"].between(*C.CALI_LAT).all()
    assert paradas["lon"].between(*C.CALI_LON).all()


def test_paradas_presencia_valida(paradas):
    assert set(paradas["presencia"]) <= {"ambas", "solo_api", "solo_gtfs"}
    # la integración por ID debe encontrar la mayoría de paradas en ambas fuentes
    assert (paradas["presencia"] == "ambas").mean() > 0.5


def test_paradas_misma_ubicacion_entre_fuentes(paradas):
    d = paradas.loc[paradas["presencia"] == "ambas", "distancia_m"]
    assert d.median() < 50      # la llave GStopID = stop_id une la misma parada física


# ----------------------------------------------------------------- rutas
def test_rutas_llave_unica(rutas):
    assert rutas["route_id"].is_unique


def test_rutas_valores_coherentes(rutas):
    con_viajes = rutas[rutas["viajes_dia_habil"] > 0]
    assert (con_viajes["duracion_promedio_min"] > 0).all()
    assert (rutas["n_paradas"] >= 0).all()
    assert set(rutas["tipo_servicio"]) <= {"Troncal", "Pretroncal", "Alimentadora", "Expresa", "Otro"}


# ----------------------------------------------------------------- KPIs
def test_kpis_completos():
    k = json.loads((C.GOLD / "kpis.json").read_text(encoding="utf-8"))
    for clave in ("KPI1_numero_rutas", "KPI2_numero_paradas_integradas", "KPI3_cobertura_por_zona",
                  "KPI4_viajes_programados_dia_habil", "KPI5_tiempo_promedio_recorrido_min"):
        assert clave in k
    assert k["KPI1_numero_rutas"] > 0 and k["KPI5_tiempo_promedio_recorrido_min"] > 0


# ----------------------------------------------------------------- base de datos
def test_base_de_datos_coincide_con_csv(paradas, rutas):
    if not C.DB_PATH.exists():
        pytest.skip("No existe la base de datos")
    with sqlite3.connect(C.DB_PATH) as con:
        assert con.execute("SELECT COUNT(*) FROM gold_paradas").fetchone()[0] == len(paradas)
        assert con.execute("SELECT COUNT(*) FROM gold_rutas").fetchone()[0] == len(rutas)
        assert con.execute("SELECT COUNT(*) FROM etl_ejecuciones").fetchone()[0] > 0


# ----------------------------------------------------------------- resultados de las mejoras
def test_tablas_nuevas_estan_en_la_base():
    with sqlite3.connect(C.DB_PATH) as con:
        tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("gold_accesibilidad", "gold_conciliacion_paradas", "gold_conciliacion_rutas"):
        assert t in tablas, f"falta la tabla {t}"


def test_kpi5_tiene_tres_medidas_de_duracion():
    kpis = json.loads((C.GOLD / "kpis.json").read_text(encoding="utf-8"))
    dur = kpis["KPI5_tiempo_recorrido_min"]
    assert {"simple_por_ruta_min", "ponderada_por_viajes_min", "mediana_min"} <= set(dur)


def test_kpi4_declara_la_vigencia_de_la_programacion():
    kpis = json.loads((C.GOLD / "kpis.json").read_text(encoding="utf-8"))
    assert "2017-2021" in kpis["KPI4_vigencia"]
    suma = kpis["KPI4_viajes_rutas_que_siguen_en_api_2025"] + kpis["KPI4_viajes_rutas_sin_correspondencia_api_2025"]
    assert suma == kpis["KPI4_viajes_programados_dia_habil"]


def test_conciliacion_no_pierde_registros(rutas):
    conc = pd.read_csv(C.GOLD / "gold_conciliacion_paradas.csv", dtype={"stop_id": str})
    paradas = pd.read_csv(GOLD_PARADAS, dtype={"stop_id": str})
    # Cada parada sin correspondencia exacta aparece una vez (no se borran las diferencias)
    assert conc["stop_id"].is_unique
    assert len(conc) == int((paradas["presencia"] != "ambas").sum()) + int(
        ((paradas["presencia"] == "ambas") & (paradas["distancia_m"] > 200)).sum())
