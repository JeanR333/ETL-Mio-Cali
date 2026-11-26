"""Diagnóstico: verifica si Python puede llegar a cada fuente de datos."""
import requests

FUENTES = {
    "API ArcGIS (fuente 2)": "https://services9.arcgis.com/8rJ42n9yWry0I4K4/arcgis/rest/services/GTFS/FeatureServer?f=json",
    "GTFS GitHub (fuente 1)": "https://github.com/ColombiaInfo/ColombiaGTFS",
    "Comunas datos.gov.co (fuente 3)": "https://www.datos.gov.co/api/geospatial/dx2g-2mhm?method=export&format=GeoJSON",
    "Comunas IDESC (fuente 3, alterna)": "http://ws-idesc.cali.gov.co:8081/geoserver/idesc/ows?service=WFS&version=1.0.0&request=GetCapabilities",
}
for nombre, url in FUENTES.items():
    try:
        r = requests.get(url, timeout=30)
        print(f"OK    {nombre}: HTTP {r.status_code}")
    except Exception as e:
        print(f"FALLA {nombre}: {type(e).__name__} -> {str(e)[:300]}")
