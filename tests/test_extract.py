"""Pruebas de EXTRACT (sin internet): paginación de la API y lectura del shapefile de comunas."""
import io
import sys
import zipfile
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importlib                                   # noqa: E402

# Se importan los módulos (no las funciones del mismo nombre que expone src.extract)
EA = importlib.import_module("src.extract.extract_api")
EC = importlib.import_module("src.extract.extract_comunas")
from src.logger import QualityReport               # noqa: E402

TOTAL = 4500


def _fake_get(url, params, timeout):
    """Simula la API ArcGIS: 4500 registros en páginas de 2000."""
    off, n = params["resultOffset"], params["resultRecordCount"]
    feats = [{"attributes": {"OBJECTID": i, "GStopID": str(500000 + i)}, "geometry": {"x": -76.5, "y": 3.4}}
             for i in range(off, min(off + n, TOTAL))]
    r = mock.Mock()
    r.raise_for_status = lambda: None
    r.json = lambda: {"features": feats, "exceededTransferLimit": off + n < TOTAL}
    return r


def test_api_descarga_todas_las_paginas(tmp_path, monkeypatch):
    monkeypatch.setattr(EA.C, "BRONZE", tmp_path)
    with mock.patch.object(EA.requests, "get", side_effect=_fake_get):
        df = EA.extract_api(0, "stops", QualityReport())
    assert len(df) == TOTAL
    assert df["OBJECTID"].is_unique
    assert {"longitude", "latitude"} <= set(df.columns)
    assert (tmp_path / "api_stops_raw.json").exists()      # la respuesta original queda en Bronze


def test_api_sin_conexion_usa_copia_bronze(tmp_path, monkeypatch):
    monkeypatch.setattr(EA.C, "BRONZE", tmp_path)
    with mock.patch.object(EA.requests, "get", side_effect=_fake_get):
        EA.extract_api(0, "stops", QualityReport())
    with mock.patch.object(EA.requests, "get", side_effect=EA.requests.ConnectionError("sin red")):
        rep = QualityReport()
        df = EA.extract_api(0, "stops", rep)
    assert len(df) == TOTAL
    assert any("copia Bronze" in a for a in rep.data["advertencias"])


def _shapefile_zip(path):
    """Crea un shapefile mínimo (2 comunas cuadradas) comprimido en ZIP."""
    import shapefile
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
    w.field("comcodigo", "C")
    w.field("comnombre", "C")
    w.poly([[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]]); w.record("01", "Comuna 01")
    w.poly([[[10, 0], [10, 10], [20, 10], [20, 0], [10, 0]]]); w.record("02", "Comuna 02")
    w.close()
    prj = 'PROJCS["MAGNA_Cali",GEOGCS["GCS_MAGNA"],AUTHORITY["EPSG","6249"]]'
    with zipfile.ZipFile(path, "w") as z:
        for name, buf in (("c.shp", shp), ("c.shx", shx), ("c.dbf", dbf)):
            z.writestr(name, buf.getvalue())
        z.writestr("c.prj", prj)
    return path


def test_shapefile_zip_a_geojson(tmp_path):
    gj = EC._shp_zip_to_geojson(_shapefile_zip(tmp_path / "comunas.zip"))
    assert len(gj["features"]) == 2
    assert gj["features"][1]["properties"]["comnombre"] == "Comuna 02"
    assert gj["crs"]["properties"]["name"] == "EPSG:6249"
    assert gj["features"][0]["geometry"]["type"] == "Polygon"


# ----------------------------------------------------------------- población (fuente 4)
EP = importlib.import_module("src.extract.extract_poblacion")


def test_poblacion_formato_ancho_toma_el_anio_mas_reciente():
    df = pd.DataFrame({"Comuna": ["Comuna 01", "Comuna 02", "Corregimiento Los Andes", "Total"],
                       "2019": ["70.000", "80.000", "5.000", "1.000"],
                       "2020": ["71.500", "81.200", "5.100", "1.100"]})
    out = EP.parse_poblacion(df)
    assert list(out["comuna_num"]) == [1, 2]                  # corregimientos y totales se descartan
    assert set(out["anio"]) == {2020}
    assert out.loc[out["comuna_num"] == 2, "poblacion"].item() == 81200  # "81.200" -> 81200


def test_poblacion_formato_largo_y_simple():
    largo = pd.DataFrame({"comuna": ["COMUNA 1", "COMUNA 1", "COMUNA 2", "COMUNA 2"],
                          "año": ["2018", "2019", "2018", "2019"],
                          "población": ["60000", "61000", "80000", "81000"]})
    out = EP.parse_poblacion(largo)
    assert out.sort_values("comuna_num")["poblacion"].tolist() == [61000, 81000]
    simple = pd.DataFrame({"Comuna": ["01", "02"], "Poblacion": ["70.000", "80.000"]})
    assert EP.parse_poblacion(simple)["poblacion"].sum() == 150000


def test_poblacion_sin_archivo_no_detiene_el_etl(tmp_path, monkeypatch):
    monkeypatch.setattr(EP.C, "POBLACION_FILE", tmp_path / "no_existe.csv")
    assert EP.extract_poblacion(QualityReport()) is None
