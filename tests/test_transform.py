"""Pruebas de TRANSFORM: utilidades, limpieza de la API, servicios representativos y comunas."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import transform as T          # noqa: E402
from src.logger import QualityReport    # noqa: E402


# ----------------------------------------------------------------- utilidades
def test_to_seconds_admite_horas_mayores_a_24():
    s = pd.Series(["05:15:00", "24:30:10", "25:00:00", "aa:bb:cc", "10:75:00"])
    out = T.to_seconds(s)
    assert out.iloc[0] == 5 * 3600 + 15 * 60
    assert out.iloc[1] == 24 * 3600 + 30 * 60 + 10
    assert out.iloc[2] == 25 * 3600
    assert pd.isna(out.iloc[3]) and pd.isna(out.iloc[4])     # texto o minutos inválidos


def test_norm_text_quita_tildes_y_espacios():
    assert T.norm_text("  Estación   Menga ") == "estacion menga"
    assert T.norm_text(None) == ""


def test_haversine_distancia_conocida():
    # 0.001° de latitud ≈ 111 m
    d = T.haversine_m(3.400, -76.5, 3.401, -76.5)
    assert 105 < d < 117


# ----------------------------------------------------------------- limpieza API
def test_clean_api_stops_renombra_y_filtra():
    raw = pd.DataFrame({
        "OBJECTID": ["1", "2", "3", "4"], "ID": ["10", "11", "12", "13"],
        "GStopID": ["500", "501", "501", "502"],              # 501 duplicado
        "GStopType": ["0", "1", "0", "0"], "GStopParen": [None] * 4, "ParentID": [None] * 4,
        "GWheelchairBoarding": [None] * 4,                    # columna 100 % vacía
        "longitude": ["-76.52", "-76.50", "-76.50", "-75.00"],  # la última está fuera de Cali
        "latitude": ["3.42", "3.44", "3.44", "3.42"],
    })
    out = T.clean_api_stops(raw, QualityReport())
    assert "stop_id" in out and "GStopID" not in out
    assert "wheelchair_boarding" not in out
    assert out["stop_id"].tolist() == ["500", "501"]
    assert out.loc[out.stop_id == "501", "tipo_parada"].item() == "estacion"


# ----------------------------------------------------------------- servicios
def test_representative_services_un_servicio_por_tipo_de_dia():
    trips = pd.DataFrame({
        "trip_id": [f"t{i}" for i in range(7)],
        "service_id": ["A", "A", "A", "B", "B", "S", "S"],
        "tipo_dia": ["habil"] * 5 + ["sabado"] * 2,
    })
    rep = T.representative_services(trips, QualityReport())
    assert rep == {"habil": "A", "sabado": "S"}


# ----------------------------------------------------------------- comunas
def _gj(swap=False):
    def sq(x0, y0, x1, y1):
        ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
        return [[p[1], p[0]] for p in ring] if swap else ring
    cuad = {"Norte-Oeste": (-76.60, 3.42, -76.52, 3.50), "Norte-Este": (-76.52, 3.42, -76.44, 3.50),
            "Sur-Oeste": (-76.60, 3.30, -76.52, 3.42), "Sur-Este": (-76.52, 3.30, -76.44, 3.42)}
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"nombre": k}, "geometry": {"type": "Polygon", "coordinates": [sq(*v)]}}
        for k, v in cuad.items()]}


STOPS = pd.DataFrame({"stop_id": ["1", "2", "3", "4"], "lon": [-76.55, -76.48, -76.55, -76.90],
                      "lat": [3.45, 3.45, 3.35, 3.45], "tipo_parada": ["parada"] * 4})


def test_asigna_comunas():
    out = T.assign_comunas(STOPS, _gj(), QualityReport())
    assert out["comuna"].tolist() == ["Norte-Oeste", "Norte-Este", "Sur-Oeste", T.FUERA_COMUNAS]


def test_comunas_con_coordenadas_invertidas():
    out = T.assign_comunas(STOPS, _gj(swap=True), QualityReport())
    assert out["comuna"].iloc[0] == "Norte-Oeste"


def test_comunas_en_magna_sirgas_cali():
    """Polígonos en metros (EPSG:6249) se convierten a lon/lat antes del cruce."""
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", "EPSG:6249", always_xy=True)
    gj = _gj()
    for f in gj["features"]:
        f["geometry"]["coordinates"] = [[list(tr.transform(x, y)) for x, y in f["geometry"]["coordinates"][0]]]
    out = T.assign_comunas(STOPS, gj, QualityReport())
    assert out["comuna"].tolist()[:3] == ["Norte-Oeste", "Norte-Este", "Sur-Oeste"]


def test_gold_comunas_cuenta_rutas():
    out = T.assign_comunas(STOPS, _gj(), QualityReport())
    rs = pd.DataFrame({"route_id": ["A", "A", "B"], "stop_id": ["1", "2", "2"]})
    g = T.build_gold_comunas(out, rs, QualityReport()).set_index("comuna")
    assert g.loc["Norte-Este", "n_rutas"] == 2
    assert g.loc["Sur-Oeste", "n_rutas"] == 0


# ----------------------------------------------------------------- duración ponderada
def test_duracion_ponderada_pesa_las_rutas_por_viajes():
    from src.transform.integrate_gold import duracion_stats
    # Ruta A: 300 viajes de 20 min. Ruta B: 10 viajes de 60 min.
    trips = pd.DataFrame({
        "route_id": ["A"] * 300 + ["B"] * 10,
        "service_id": "s1", "tipo_dia": "habil",
        "duracion_min": [20.0] * 300 + [60.0] * 10})
    st = duracion_stats(trips, {"habil": "s1"})
    assert st["simple_por_ruta_min"] == 40.0            # (20 + 60) / 2: cada ruta pesa igual
    assert round(st["ponderada_por_viajes_min"], 1) == 21.3   # (20*300 + 60*10) / 310 ≈ 21,3
    assert st["mediana_min"] == 20.0


# ----------------------------------------------------------------- accesibilidad
def _cuadrado_gj(lado=1000.0):
    """Comuna cuadrada de 1 km x 1 km en metros (EPSG:6249), con una parada en el centro."""
    x0, y0 = 1_054_000.0, 874_000.0
    ring = [[x0, y0], [x0 + lado, y0], [x0 + lado, y0 + lado], [x0, y0 + lado], [x0, y0]]
    return {"type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:6249"}},
            "features": [{"type": "Feature", "properties": {"comnombre": "Comuna 01"},
                          "geometry": {"type": "Polygon", "coordinates": [ring]}}]}, (x0 + lado / 2, y0 + lado / 2)


def test_accesibilidad_cuadrado_con_parada_al_centro():
    from pyproj import Transformer
    gj, (xc, yc) = _cuadrado_gj()
    lon, lat = Transformer.from_crs("EPSG:6249", "EPSG:4326", always_xy=True).transform(xc, yc)
    stops = pd.DataFrame({"stop_id": ["1"], "presencia": ["solo_api"], "lat": [lat], "lon": [lon],
                          "comuna": ["Comuna 01"]})
    pob = pd.DataFrame({"comuna_num": [1], "comuna": ["Comuna 01"], "anio": [2020], "poblacion": [10_000]})
    acc = T.build_accesibilidad(gj, stops, pob, QualityReport())
    fila = acc[acc["comuna_num"] == 1].iloc[0]
    assert abs(fila["area_km2"] - 1.0) < 0.01
    # Círculo de 500 m dentro de un cuadrado de 1 km: π·500² / 1000² ≈ 78,5 %
    assert abs(fila["pct_area_500m"] - 78.5) < 2.0
    assert fila["pct_area_200m"] < fila["pct_area_300m"] < fila["pct_area_500m"]
    assert fila["personas_cubiertas_500m"] == round(10_000 * fila["pct_area_500m"] / 100, 0) or \
        abs(fila["personas_cubiertas_500m"] - 7850) < 300
    # La fila total usa todos los puntos de la malla
    assert acc.iloc[-1]["comuna"].startswith("Total")


def test_accesibilidad_ignora_paradas_solo_gtfs():
    from pyproj import Transformer
    gj, (xc, yc) = _cuadrado_gj()
    lon, lat = Transformer.from_crs("EPSG:6249", "EPSG:4326", always_xy=True).transform(xc, yc)
    stops = pd.DataFrame({"stop_id": ["1"], "presencia": ["solo_gtfs"], "lat": [lat], "lon": [lon],
                          "comuna": ["Comuna 01"]})
    acc = T.build_accesibilidad(gj, stops, None, QualityReport())
    # Sin paradas vigentes no hay destinos: la función no debe calcular cobertura con paradas de 2017
    assert acc is None or acc.empty or acc.iloc[0]["paradas_vigentes"] == 0


# ----------------------------------------------------------------- conciliación
def test_conciliacion_clasifica_cada_diferencia():
    from src.transform.integrate_gold import build_conciliacion_paradas
    gold = pd.DataFrame({
        "stop_id": ["1", "2", "3", "4"],
        "stop_name": ["a", "b", "c", "d"],
        "presencia": ["ambas", "solo_gtfs", "solo_api", "ambas"],
        "distancia_m": [10.0, None, None, 250.0],
        "lat": [3.4] * 4, "lon": [-76.5] * 4, "comuna": ["Comuna 01"] * 4, "n_rutas": [1, 0, 2, 3]})
    out = build_conciliacion_paradas(gold, QualityReport())
    assert set(out["stop_id"]) == {"2", "3", "4"}          # la parada 1 coincide, no entra
    motivos = dict(zip(out["stop_id"], out["motivo"]))
    assert motivos["2"] == "solo en GTFS 2017-2021"
    assert motivos["3"] == "solo en API 2025 (nueva)"
    assert motivos["4"] == "reubicada más de 200 m"
