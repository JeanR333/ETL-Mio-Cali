"""Orquestador del ETL MIO Cali (arquitectura Medallion: Bronze -> Silver -> Gold)."""
import json
import time

import pandas as pd

from . import config as C
from . import extract as E
from . import load as L
from . import transform as T
from .logger import QualityReport, RUN_ID, stage_logger, timed

log = stage_logger("PIPELINE")


def _resumen_acc(acc):
    """Resumen de accesibilidad para kpis.json (fila total de Cali)."""
    if acc is None or not len(acc):
        return "PENDIENTE: falta la capa de comunas en data/bronze"
    t = acc.iloc[-1]
    r = C.ACC_RADIO_M
    out = {"radio_principal_m": r, "pct_area_cubierta_radio_principal": float(t[f"pct_area_{r}m"]),
           "distancia_media_m": float(t["distancia_media_m"]), "distancia_p90_m": float(t["distancia_p90_m"]),
           "pct_area_por_radio_m": {str(x): float(t[f"pct_area_{x}m"]) for x in C.ACC_RADIOS_M}}
    if "poblacion" in t and pd.notna(t.get("poblacion")):
        out.update({"poblacion_total": int(t["poblacion"]),
                    "personas_cubiertas_radio_principal": int(t["personas_cubiertas_500m"]),
                    "personas_sin_cobertura_radio_principal": int(t["personas_sin_cobertura_500m"])})
    else:
        out["poblacion"] = "PENDIENTE: falta data/bronze/poblacion_comunas.csv"
    return out


def compute_kpis(gold_stops, gold_routes, hourly, api_stops, gtfs_stops, services, gold_comunas=None,
                 duracion=None, gold_accesibilidad=None, conc_paradas=None, conc_rutas=None):
    hab = gold_routes[gold_routes["viajes_dia_habil"] > 0]
    top_paradas = gold_routes.nlargest(5, "n_paradas")[["route_short_name", "n_paradas"]]
    top_freq = gold_routes.nlargest(5, "viajes_dia_habil")[["route_short_name", "viajes_dia_habil"]]
    h = hourly[hourly["tipo_dia"] == "habil"].nlargest(3, "viajes")
    ambas = int((gold_stops["presencia"] == "ambas").sum())
    return {
        "proyecto": "ETL MIO Cali", "run_id": RUN_ID,
        "KPI1_numero_rutas": int(len(gold_routes)),
        "KPI1_rutas_por_tipo_servicio": gold_routes["tipo_servicio"].value_counts().to_dict(),
        "KPI2_numero_paradas_integradas": int(len(gold_stops)),
        "KPI2_paradas_por_fuente": gold_stops["presencia"].value_counts().to_dict(),
        "KPI3_cobertura_por_zona": (
            {r.comuna: {"paradas": int(r.n_paradas), "rutas": int(r.n_rutas)} for r in gold_comunas.itertuples()}
            if gold_comunas is not None and len(gold_comunas) else
            "PENDIENTE: falta la capa de comunas en data/bronze"),
        "KPI1_rutas_en_api_2025": int(gold_routes["en_api_2025"].sum()),
        "KPI4_vigencia": "Programación GTFS 2017-2021 (la API 2025 no trae horarios)",
        "KPI4_viajes_programados_dia_habil": int(hab["viajes_dia_habil"].sum()),
        "KPI4_viajes_rutas_que_siguen_en_api_2025": int(hab.loc[hab["en_api_2025"], "viajes_dia_habil"].sum()),
        "KPI4_viajes_rutas_sin_correspondencia_api_2025": int(hab.loc[~hab["en_api_2025"], "viajes_dia_habil"].sum()),
        "KPI4_horas_pico_habil": {f"{int(r.hora_inicio):02d}:00": int(r.viajes) for r in h.itertuples()},
        "KPI4_rutas_mayor_frecuencia": dict(zip(top_freq["route_short_name"], top_freq["viajes_dia_habil"].astype(int))),
        "KPI5_tiempo_recorrido_min": duracion or {},
        "KPI5_tiempo_promedio_recorrido_min": round(float(hab["duracion_promedio_min"].mean()), 1),
        "KPI6_accesibilidad": _resumen_acc(gold_accesibilidad),
        "KPI7_conciliacion": {
            "paradas_sin_correspondencia": (conc_paradas["motivo"].value_counts().to_dict()
                                            if conc_paradas is not None else {}),
            "rutas_por_estado": (conc_rutas["estado"].value_counts().to_dict()
                                 if conc_rutas is not None else {}),
        },
        "rutas_con_mas_paradas": dict(zip(top_paradas["route_short_name"], top_paradas["n_paradas"].astype(int))),
        "calidad": {
            "paradas_api_silver": int(len(api_stops)), "paradas_gtfs_silver": int(len(gtfs_stops)),
            "paradas_en_ambas_fuentes": ambas,
            "porcentaje_paradas_api_encontradas_en_gtfs": round(100 * ambas / len(api_stops), 2) if len(api_stops) else 0,
            "metodo_union": "stop_id (GTFS) = GStopID (API)",
            "servicios_representativos": services,
        },
        "nota": "GTFS con vigencia 2017-2021 y API con vigencia 2025: las diferencias entre fuentes "
                "reflejan cambios del sistema, no necesariamente errores.",
    }


def run_pipeline():
    report = QualityReport()
    t0 = time.perf_counter()
    log.info("========== Inicio ETL MIO Cali | run_id=%s ==========", RUN_ID)
    log.info("Configuración cargada desde %s", C.CONFIG_FILE.relative_to(C.ROOT))
    try:
        # ---------------- EXTRACT -> Bronze
        with timed(log, "Extracción (Bronze)"):
            api_stops_raw = E.extract_api(C.API_LAYER_STOPS, "stops", report)
            api_lines_raw = E.extract_api(C.API_LAYER_LINES, "lines", report)
            gtfs = E.extract_gtfs(report)
            comunas_gj = E.extract_comunas(report)
            poblacion = E.extract_poblacion(report)

        # ---------------- TRANSFORM -> Silver
        with timed(log, "Transformación (Silver)"):
            api_stops = T.clean_api_stops(api_stops_raw, report)
            gtfs_stops = T.clean_gtfs_stops(gtfs["stops"], report)
            routes = T.clean_routes(gtfs["routes"], report)
            calendar = T.clean_calendar(gtfs["calendar"], report)
            trips = T.clean_trips(gtfs["trips"], routes, calendar, report)
            services = T.representative_services(trips, report)
            trips_lvl, route_stops = T.process_stop_times(E.iter_stop_times(), trips, gtfs_stops["stop_id"], report)

        with timed(log, "Carga Silver"):
            L.save_csv(api_stops, "silver", "api_stops_silver", report)
            L.save_csv(gtfs_stops, "silver", "gtfs_stops_silver", report)
            L.save_csv(routes, "silver", "routes_silver", report)
            L.save_csv(calendar, "silver", "calendar_silver", report)
            L.save_csv(trips_lvl, "silver", "trips_silver", report)
            L.save_csv(route_stops, "silver", "route_stops_silver", report)

        # ---------------- TRANSFORM -> Gold
        with timed(log, "Integración (Gold)"):
            gold_stops = T.build_gold_stops(api_stops, gtfs_stops, route_stops, report)
            gold_comunas = None
            if comunas_gj:
                gold_stops = T.assign_comunas(gold_stops, comunas_gj, report)
                gold_comunas = T.build_gold_comunas(gold_stops, route_stops, report)
                L.save_csv(gold_comunas, "gold", "gold_comunas", report)
            gold_acc = T.build_accesibilidad(comunas_gj, gold_stops, poblacion, report) if comunas_gj else None
            if gold_acc is not None:
                L.save_csv(gold_acc, "gold", "gold_accesibilidad", report)
            conc_paradas = T.build_conciliacion_paradas(gold_stops, report)
            gold_routes = T.build_gold_routes(routes, trips_lvl, route_stops, api_lines_raw, services, report)
            hourly = T.build_gold_hourly(trips_lvl, services, report)
            L.save_csv(gold_stops, "gold", "gold_final", report)          # tabla integrada de paradas
            L.save_csv(gold_routes, "gold", "gold_rutas", report)
            conc_rutas = T.build_conciliacion_rutas(routes, gold_routes, api_lines_raw, report)
            L.save_csv(conc_paradas, "gold", "gold_conciliacion_paradas", report)
            L.save_csv(conc_rutas, "gold", "gold_conciliacion_rutas", report)
            duracion = T.duracion_stats(trips_lvl, services)
            L.save_csv(hourly, "gold", "gold_servicios_hora", report)
            kpis = compute_kpis(gold_stops, gold_routes, hourly, api_stops, gtfs_stops, services, gold_comunas,
                                duracion=duracion, gold_accesibilidad=gold_acc,
                                conc_paradas=conc_paradas, conc_rutas=conc_rutas)
            L.save_kpis(kpis)

        # ---------------- LOAD -> Base de datos
        with timed(log, "Carga a base de datos (SQLite)"):
            tablas = {"gold_paradas": gold_stops, "gold_rutas": gold_routes, "gold_servicios_hora": hourly}
            if gold_comunas is not None:
                tablas["gold_comunas"] = gold_comunas
            if gold_acc is not None:
                tablas["gold_accesibilidad"] = gold_acc
            tablas["gold_conciliacion_paradas"] = conc_paradas
            tablas["gold_conciliacion_rutas"] = conc_rutas
            if C.DB_LOAD_SILVER:
                tablas.update({"silver_api_stops": api_stops, "silver_gtfs_stops": gtfs_stops,
                               "silver_routes": routes, "silver_calendar": calendar,
                               "silver_trips": trips_lvl, "silver_route_stops": route_stops})
            L.load_tables(tablas, report, RUN_ID)

        dur = time.perf_counter() - t0
        out = report.close("OK", duracion_s=round(dur, 2))
        log.info("Reporte de calidad: %s", out.name)
        log.info("========== ETL finalizado OK en %.1fs ==========", dur)
        print(json.dumps(kpis, ensure_ascii=False, indent=2))
        return kpis
    except Exception as e:
        report.close("ERROR", error=f"{type(e).__name__}: {e}", duracion_s=round(time.perf_counter() - t0, 2))
        log.critical("ETL abortado: %s", e)
        raise
