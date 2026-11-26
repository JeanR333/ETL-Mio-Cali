# ETL MIO Cali: operación y accesibilidad del sistema de transporte masivo

**Universidad Autónoma de Occidente** · Facultad de Ingeniería y Ciencias Básicas
**Curso:** ETL · **Entrega:** Parcial 2, etapa de extracción, transformación, análisis e interfaz

**Integrante:** Jean Paul Tapia Ruiz

**Tema:** Análisis de la operación y la accesibilidad del Sistema Integrado de Transporte Masivo MIO de Cali.

---

## Explicación para el profesor

### Problema
La información del MIO está repartida en fuentes de distintos formatos y años: archivos GTFS (paradas, rutas, viajes y horarios), un servicio web con las paradas actualizadas, capas geográficas de la ciudad y datos de población. Por separado no se pueden cruzar, así que es difícil responder preguntas sobre la cobertura y la operación del sistema. Este es el problema central del árbol de problema de la ficha (`docs/Avance_1_Proyecto_ETL_2026.docx`).

### Objetivo (OKR de la ficha)
Consolidar e integrar la información del MIO en un pipeline ETL para analizar su operación y cobertura.

- **KR1:** integrar las fuentes en una estructura centralizada: capas Bronze, Silver y Gold, más la base de datos SQLite.
- **KR2:** estandarizar y transformar los datos de rutas, paradas y horarios.
- **KR3:** reducir inconsistencias como duplicados, nulos, llaves distintas y programación repetida.
- **KR4:** generar una base consolidada que responda las preguntas estratégicas, y presentarla en una interfaz.

### Cómo funciona el pipeline
1. **Extracción:** baja el feed GTFS, consulta la API ArcGIS página por página, lee el shapefile de comunas y, si existe, el archivo de población. Guarda todo sin modificar en `data/bronze`.
2. **Transformación a Silver:** renombra las columnas al estándar GTFS, convierte los tipos, valida que las coordenadas estén dentro de Cali y elimina llaves nulas o duplicadas y columnas vacías. Además convierte los horarios a segundos y resume 3 millones de registros en 139.277 viajes con su duración.
3. **Integración a Gold:** une las paradas de las dos fuentes, calcula métricas por ruta y por hora, asigna cada parada a su comuna y mide la cobertura peatonal de cada comuna.
4. **Conciliación:** no elimina las diferencias entre fuentes; las guarda en tablas propias para verlas.
5. **Carga:** escribe los CSV y `kpis.json`, y carga 13 tablas de datos en `data/gold/mio_cali.db`. Para cada tabla verifica que las filas en la base coincidan con las procesadas.

Cada paso queda registrado en `logs/` con filas de entrada, filas de salida, descartes, advertencias y tiempos.

### Ajustes por la retroalimentación del segundo avance

El profesor pidió cuatro mejoras. Cada una tiene una respuesta concreta en el código y en el EDA (sección 10 del notebook):

| Observación del profesor | Qué se hizo | Dónde se ve |
|---|---|---|
| Diferencias temporales entre GTFS y API: no presentar frecuencias históricas como operación actual | Los KPI de frecuencia y viajes declaran su vigencia (`KPI4_vigencia`: GTFS 2017-2021). Cada ruta se marca según aparezca o no en la API 2025: 68 % de los viajes están en rutas que siguen en la API y 32 % en rutas que no aparecen | `kpis.json`, tabla `gold_rutas` (columna `en_api_2025`), EDA 10.1 |
| Ampliar la accesibilidad: población atendida, movilidad reducida y distancias de acceso | Módulo nuevo de accesibilidad: malla de puntos cada 100 m dentro de cada comuna y distancia a la parada vigente más cercana. Resultado: 95,3 % del área urbana queda a 500 m o menos de una parada; la distancia media es de 188 m y el percentil 90 de 346 m. Con la población (fuente 4) se calculan las personas cubiertas por comuna. Para movilidad reducida se mide la cobertura a 200 m, porque el campo de silla de ruedas de la API está vacío en todas las paradas | `gold_accesibilidad`, `src/transform/accesibilidad.py`, EDA 10.2 |
| Duraciones con promedios ponderados por número de viajes | Tres medidas: 28,7 min promedio simple por ruta, 29,8 min ponderado por viajes y 24,5 min de mediana por viaje. La ponderada es la que vive un pasajero promedio | `KPI5_tiempo_recorrido_min`, EDA 10.3 |
| Mantener visibles las diferencias y los registros sin correspondencia | 945 registros con diferencia: 495 paradas solo en la API 2025, 436 solo en el GTFS 2017-2021 y 14 reubicadas más de 200 m. En rutas: 63 en ambas fuentes, 34 solo en el GTFS y 31 solo en la API 2025 | `gold_conciliacion_paradas`, `gold_conciliacion_rutas`, pestaña *Diferencias entre fuentes* de la interfaz |

### EDA (`notebooks/EDA_transformacion.ipynb`)
El análisis exploratorio justifica cada transformación con evidencia de los datos:

| Hallazgo del EDA | Decisión en el ETL |
|---|---|
| La API no publica el nombre de la parada, así que unir por nombre daba 0 coincidencias | Se une por `GStopID` = `stop_id`, que cruza el 76 % de las paradas. Se validó con la distancia entre fuentes: mediana de 9,7 m |
| La API entrega máximo 2.000 registros por consulta | Paginación: se bajan las 2.107 paradas |
| `wheelchair_boarding` está 100 % vacía | Se elimina y se reporta. Para movilidad reducida se usa la cobertura a 200 m como aproximación |
| 94 paradas del GTFS comparten nombre | El nombre no se usa como llave |
| El calendario trae 3 versiones de la programación de día hábil | Se usa un servicio representativo por tipo de día, porque sumarlas triplicaría los viajes |
| Viajes atípicos de más de 80 min (≈1 %), casi todos de la ruta P12A | Se conservan porque son reales (la ruta es larga); solo se descartan duraciones de 0 o negativas |
| El shapefile de comunas viene en metros (MAGNA-SIRGAS Cali) | Se convierte a latitud y longitud antes del cruce con las paradas, y la accesibilidad se mide en metros |

El notebook tiene 12 gráficas y 12 secciones (0 a 11): calidad de los datos, horarios, rutas, comunas, accesibilidad, diferencias entre fuentes y conclusiones.

### Resultados (KPIs de la ficha)
| KPI | Resultado |
|---|---|
| 1. Número de rutas | 97 (6 troncales, 28 pretroncales, 56 alimentadoras, 7 expresas) |
| 2. Número de paradas | 2.543 integradas (1.612 en ambas fuentes, 495 solo en la API 2025, 436 solo en el GTFS 2017-2021) |
| 3. Cobertura por zona | 22 comunas. La 17 tiene la mayor oferta (239 paradas, 34 rutas). 263 paradas están en zona rural |
| 4. Frecuencia programada (GTFS 2017-2021) | 20.112 viajes en día hábil, con picos a las 6:00 (1.457 salidas) y las 17:00 (1.366) |
| 5. Tiempo de recorrido | 28,7 min simple por ruta · 29,8 min ponderado por viajes · 24,5 min mediana |
| 6. Accesibilidad peatonal | 95,3 % del área de las comunas a 500 m o menos de una parada vigente. Las comunas con menor cobertura son la 22 (70,2 %) y la 1 (72,4 %) |
| Población cubierta | Depende del archivo de población (ver *Fuente 4*). Sin el archivo, el KPI queda marcado como pendiente |

Respuestas a las preguntas de la ficha: las rutas con más paradas son pretroncales (P12A, 149 paradas), la de mayor frecuencia es la A47 (585 viajes en día hábil) y el centro (comuna 3) tiene muchas rutas en pocas paradas.

### Interfaz (`app.py`)
La interfaz de Streamlit lee la base `data/gold/mio_cali.db` y tiene cinco secciones:
- **Resumen:** KPIs principales, con la advertencia de vigencia del GTFS.
- **Rutas y servicios:** búsqueda y filtro por tipo de servicio, viajes y duración por ruta, y salidas por hora.
- **Accesibilidad:** cobertura por comuna con selector de radio (200, 300 o 500 m), y mapa de paradas vigentes.
- **Diferencias entre fuentes:** paradas y rutas sin correspondencia, con mapa y filtro por motivo.
- **Calidad:** estado y advertencias de la última ejecución.

Para abrirla, primero ejecute `ejecutar.bat` (crea la base) y luego `interfaz.bat`.


## Estructura
```
etl_mio_parcial2/
├── main.py                      punto de entrada del ETL
├── app.py                       interfaz (Streamlit) sobre la base de datos
├── .env                         variables de entorno (CONFIG_PATH, LOG_LEVEL, DB_PATH)
├── .gitignore
├── requirements.txt
├── README.md
├── ejecutar.bat                 crea el entorno, instala, prueba conexión, corre el ETL y las pruebas
├── interfaz.bat                 abre la interfaz en el navegador
├── probar_conexion.py           diagnóstico de acceso a las fuentes
├── config/
│   └── config.yaml              fuentes, rutas de capas, paginación, accesibilidad, validación
├── src/
│   ├── __init__.py
│   ├── config.py                lee .env y config/config.yaml
│   ├── logger.py                logs por etapa + reporte de calidad JSON
│   ├── pipeline.py              orquestador Bronze → Silver → Gold → BD
│   ├── extract/
│   │   ├── extract_gtfs.py      fuente 1: feed GTFS (ZIP)
│   │   ├── extract_api.py       fuente 2: API ArcGIS con paginación
│   │   ├── extract_comunas.py   fuente 3: shapefile/GeoJSON de comunas
│   │   └── extract_poblacion.py fuente 4: población por comuna (opcional)
│   ├── transform/
│   │   ├── common.py            utilidades (horas a segundos, distancias, normalización)
│   │   ├── clean_gtfs.py        limpieza GTFS: paradas, rutas, calendario, viajes, horarios
│   │   ├── clean_api.py         limpieza de las paradas de la API
│   │   ├── clean_comunas.py     cruce parada → comuna (spatial join)
│   │   ├── integrate_gold.py    tablas Gold, duración ponderada y conciliación
│   │   └── accesibilidad.py     cobertura peatonal por comuna (malla de 100 m)
│   └── load/
│       ├── load_files.py        CSV (Silver/Gold) y kpis.json
│       └── load_database.py     carga a SQLite con verificación de filas
├── data/
│   ├── bronze/                  datos originales de las fuentes (incluye poblacion_comunas.csv si se descarga)
│   ├── silver/                  datos limpios
│   └── gold/                    tablas de análisis, kpis.json y mio_cali.db
├── logs/                        etl.log, run_<id>.log, quality_report_<id>.json
├── notebooks/
│   └── EDA_transformacion.ipynb EDA que justifica cada transformación y los ajustes
├── tests/
│   ├── test_extract.py          paginación, caché sin conexión, shapefile y población
│   ├── test_transform.py        limpieza, horarios, servicios, comunas, duración, accesibilidad, conciliación
│   └── test_quality.py          calidad de Gold, KPIs, conciliación y base de datos
├── docs/                        ficha del proyecto (Avance 1)
└── evidencias/                  capturas de la ejecución
```

## Fuentes
1. **Feed GTFS público del MIO** (ColombiaGTFS, vigencia 2017-2021): `stops`, `routes`, `trips`, `stop_times`, `calendar`, `agency` y `feed_info`.
2. **API REST ArcGIS** del GTFS de Cali (vigencia 2025): capa 0 *Stops* y capa 4 *Lines*. Se descarga con paginación porque el servicio entrega como máximo 2.000 registros por petición.
3. **Comunas de Cali**: shapefile oficial `pdt_dpa_comunas` de Datos Abiertos Cali / IDESC, con 22 comunas en MAGNA-SIRGAS Cali (EPSG:6249). Está guardado en `data/bronze/comunas_cali_shp.zip`.
4. **Población por comuna** (complementaria): proyecciones de la Alcaldía de Cali, en `datos.cali.gov.co`. No tiene API, así que se descarga a mano y se guarda en `data/bronze/poblacion_comunas.csv` (o `.xlsx`). Acepta formato ancho (un año por columna), largo (año y población en filas) o simple (comuna y población). Si el archivo no existe, el ETL sigue sin esta variable y lo registra como advertencia.

Las URL y los parámetros de las cuatro fuentes están en `config/config.yaml`.


## Limitaciones
- El GTFS es histórico (2017-2021) y la API es de 2025, así que las frecuencias describen la programación de ese periodo y no necesariamente la operación actual. Las paradas "solo_gtfs" o "solo_api" reflejan cambios del sistema entre esos años.
- Las rutas que solo aparecen en el GTFS pueden haber cambiado de código, no desaparecido. La API 2025 no trae nombres de ruta que lo confirmen.
- **Movilidad reducida:** la API no publica si las paradas son accesibles (el campo está vacío). La cobertura a 200 m es una aproximación, no una medición de accesibilidad física.
- **Población:** se supone población uniforme dentro de cada comuna, así que las personas cubiertas son una estimación. El archivo de proyecciones puede llegar solo hasta 2020; si hay una versión más reciente, conviene usarla.
- La medición de distancias es en línea recta, no por la red peatonal. Las distancias reales a pie son mayores.
- Los servidores de la IDESC y de datos.gov.co no permitieron descargar las comunas automáticamente; se usa el shapefile oficial descargado de Datos Abiertos Cali, guardado en Bronze.
