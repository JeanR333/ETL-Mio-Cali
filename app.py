"""Interfaz del proyecto ETL MIO Cali (Streamlit).

Lee la base SQLite que genera `python main.py` (data/gold/mio_cali.db) y el reporte de
calidad de la última ejecución (logs/quality_report_latest.json).

Ejecución (con el entorno virtual activo):
    streamlit run app.py
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "gold" / "mio_cali.db"
KPIS = ROOT / "data" / "gold" / "kpis.json"
QREPORT = ROOT / "logs" / "quality_report_latest.json"

st.set_page_config(page_title="MIO Cali: operación y accesibilidad", page_icon="🚌", layout="wide")


# ----------------------------------------------------------------- datos
@st.cache_data(show_spinner=False)
def tabla(nombre):
    """Lee una tabla de la base. Si la base no existe, muestra un aviso y se detiene."""
    if not DB.exists():
        st.error("No hay base de datos. Primero ejecute `python main.py` (o ejecutar.bat).")
        st.stop()
    with sqlite3.connect(DB) as con:
        existe = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nombre,)).fetchone()
        if not existe:
            return None
        return pd.read_sql(f'SELECT * FROM "{nombre}"', con)


@st.cache_data(show_spinner=False)
def kpis():
    return json.loads(KPIS.read_text(encoding="utf-8")) if KPIS.exists() else {}


def aviso_si_falta(df, mensaje):
    if df is None:
        st.info(mensaje)
        return True
    return False


# ----------------------------------------------------------------- páginas
def pagina_resumen():
    k = kpis()
    st.title("Operación y accesibilidad del MIO, Cali")
    st.caption("Integra GTFS (2017-2021), API ArcGIS (2025) y comunas de Cali. Todas las cifras salen de la base de datos.")
    if not k:
        st.info("Aún no hay KPIs. Ejecute el ETL.")
        return

    dur = k.get("KPI5_tiempo_recorrido_min", {})
    acc = k.get("KPI6_accesibilidad", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rutas", k["KPI1_numero_rutas"])
    c2.metric("Paradas integradas", f'{k["KPI2_numero_paradas_integradas"]:,}')
    c3.metric("Viajes día hábil", f'{k["KPI4_viajes_programados_dia_habil"]:,}')
    c4.metric("Duración ponderada", f'{dur.get("ponderada_por_viajes_min", "–")} min')

    c5, c6, c7 = st.columns(3)
    c5.metric("Área a 500 m o menos de una parada", f'{acc.get("pct_area_cubierta_radio_principal", "–")} %')
    c6.metric("Distancia media a una parada", f'{acc.get("distancia_media_m", "–")} m')
    if "poblacion_total" in acc:
        c7.metric("Personas a 500 m o menos", f'{acc["personas_cubiertas_radio_principal"]:,}')
    else:
        c7.metric("Personas a 500 m o menos", "Sin datos", help="Falta data/bronze/poblacion_comunas.csv")

    st.warning(k.get("KPI4_vigencia", "") + ". Los viajes y la duración describen esa programación, no la operación de 2025.")

    st.subheader("Duración del recorrido en día hábil")
    st.bar_chart(pd.Series({"Simple por ruta": dur.get("simple_por_ruta_min"),
                            "Ponderada por viajes": dur.get("ponderada_por_viajes_min"),
                            "Mediana por viaje": dur.get("mediana_min")}), horizontal=True)

    st.subheader("Viajes según correspondencia con la API 2025")
    st.write(f'{k["KPI4_viajes_rutas_que_siguen_en_api_2025"]:,} viajes en rutas que aparecen en la API 2025 y '
             f'{k["KPI4_viajes_rutas_sin_correspondencia_api_2025"]:,} en rutas que no aparecen. Las rutas sin '
             "correspondencia pueden haber cambiado de código; la API no trae nombres para confirmarlo.")


def pagina_rutas():
    st.title("Rutas y servicios")
    rutas = tabla("gold_rutas")
    if aviso_si_falta(rutas, "No existe la tabla gold_rutas."):
        return
    serv = tabla("gold_servicios_hora")

    c1, c2 = st.columns([1, 2])
    tipos = ["Todos"] + sorted(rutas["tipo_servicio"].dropna().unique().tolist())
    tipo = c1.selectbox("Tipo de servicio", tipos)
    busq = c2.text_input("Buscar ruta (código o nombre)", "")
    df = rutas if tipo == "Todos" else rutas[rutas["tipo_servicio"] == tipo]
    if busq:
        m = df["route_short_name"].astype(str).str.contains(busq, case=False, na=False) | \
            df["route_long_name"].astype(str).str.contains(busq, case=False, na=False)
        df = df[m]

    st.write(f"{len(df)} rutas")
    cols = ["route_short_name", "route_long_name", "tipo_servicio", "viajes_dia_habil", "duracion_promedio_min",
            "intervalo_promedio_min", "n_paradas", "en_api_2025"]
    st.dataframe(df[cols].sort_values("viajes_dia_habil", ascending=False), width="stretch", hide_index=True)

    st.subheader("Top 10 rutas por viajes en día hábil")
    top = df.nlargest(10, "viajes_dia_habil").set_index("route_short_name")["viajes_dia_habil"]
    st.bar_chart(top)

    if serv is not None:
        st.subheader("Salidas programadas por hora (día hábil)")
        h = serv[serv["tipo_dia"] == "habil"].set_index("hora_inicio")["viajes"]
        st.bar_chart(h)


def pagina_accesibilidad():
    st.title("Accesibilidad peatonal por comuna")
    st.caption("Distancia en línea recta desde puntos de una malla de 100 m hasta la parada vigente más cercana (API 2025).")
    acc = tabla("gold_accesibilidad")
    if aviso_si_falta(acc, "No existe gold_accesibilidad (falta la capa de comunas en data/bronze)."):
        return

    radio = st.select_slider("Radio de referencia (m)", options=[200, 300, 500], value=500,
                             help="500 m es el radio peatonal estándar. 200 m aproxima la movilidad reducida.")
    total = acc[acc["comuna_num"] == 0].iloc[0]
    zonas = acc[acc["comuna_num"] > 0].copy()
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Área de Cali a {radio} m o menos", f'{total[f"pct_area_{radio}m"]} %')
    c2.metric("Distancia media", f'{total["distancia_media_m"]:.0f} m')
    c3.metric("Distancia p90", f'{total["distancia_p90_m"]:.0f} m',
              help="El 90 % de los puntos está a esta distancia o menos de una parada.")

    st.subheader(f"Comunas ordenadas por cobertura a {radio} m")
    st.bar_chart(zonas.set_index("comuna")[f"pct_area_{radio}m"].sort_values(), horizontal=True)

    cols = ["comuna", "area_km2", "paradas_vigentes", f"pct_area_{radio}m", "distancia_media_m", "distancia_p90_m"]
    if "poblacion" in acc.columns and acc["poblacion"].notna().any():
        cols += ["poblacion", "personas_sin_cobertura_500m", "habitantes_por_parada"]
        st.caption("Población: proyecciones de la Alcaldía de Cali. Se supone población uniforme dentro de cada comuna.")
    else:
        st.info("Sin población: agregue data/bronze/poblacion_comunas.csv para ver personas cubiertas.")
    st.dataframe(zonas[cols].sort_values(f"pct_area_{radio}m"), width="stretch", hide_index=True)

    paradas = tabla("gold_paradas")
    if paradas is not None:
        vig = paradas[paradas["presencia"].isin(["ambas", "solo_api"])].dropna(subset=["lat", "lon"])
        st.subheader("Paradas vigentes (2025)")
        st.map(vig[["lat", "lon"]], size=6)


def pagina_conciliacion():
    st.title("Diferencias entre fuentes")
    st.caption("Registros sin correspondencia exacta entre GTFS (2017-2021) y API (2025). Se conservan a propósito.")
    cp = tabla("gold_conciliacion_paradas")
    cr = tabla("gold_conciliacion_rutas")
    if aviso_si_falta(cp, "No existe gold_conciliacion_paradas. Ejecute de nuevo el ETL."):
        return

    st.subheader("Paradas")
    conteo = cp["motivo"].value_counts()
    cols = st.columns(len(conteo))
    for col, (motivo, n) in zip(cols, conteo.items()):
        col.metric(motivo, f"{n:,}")
    motivo = st.selectbox("Filtrar por motivo", ["Todos"] + conteo.index.tolist())
    df = cp if motivo == "Todos" else cp[cp["motivo"] == motivo]
    st.map(df.dropna(subset=["lat", "lon"])[["lat", "lon"]], size=6)
    st.dataframe(df.sort_values("distancia_m", ascending=False), width="stretch", hide_index=True)

    if cr is not None:
        st.subheader("Rutas")
        st.bar_chart(cr["estado"].value_counts())
        st.dataframe(cr[cr["estado"] != "en ambas fuentes"], width="stretch", hide_index=True)


def pagina_calidad():
    st.title("Calidad de la última ejecución")
    if not QREPORT.exists():
        st.info("No hay reporte de calidad. Ejecute el ETL.")
        return
    q = json.loads(QREPORT.read_text(encoding="utf-8"))
    c1, c2, c3 = st.columns(3)
    c1.metric("Estado", q.get("estado", "–"))
    c2.metric("Duración", f'{q.get("duracion_s", "–")} s')
    c3.metric("Advertencias", len(q.get("advertencias", [])))
    if q.get("advertencias"):
        st.subheader("Advertencias")
        for a in q["advertencias"]:
            st.warning(a)
    pasos = pd.DataFrame(q.get("pasos", []))
    if len(pasos):
        st.subheader("Pasos del pipeline")
        st.dataframe(pasos, width="stretch", hide_index=True)


# ----------------------------------------------------------------- navegación
PAGINAS = {
    "Resumen": pagina_resumen,
    "Rutas y servicios": pagina_rutas,
    "Accesibilidad": pagina_accesibilidad,
    "Diferencias entre fuentes": pagina_conciliacion,
    "Calidad": pagina_calidad,
}
with st.sidebar:
    st.header("MIO Cali")
    eleccion = st.radio("Sección", list(PAGINAS))
    st.caption(f"Base: {DB.relative_to(ROOT)}")
PAGINAS[eleccion]()
