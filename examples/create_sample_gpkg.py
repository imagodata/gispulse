"""Generate a sample multi-layer GPKG for cross-layer examples.

Run: python examples/create_sample_gpkg.py
Creates: examples/city.gpkg (3 layers: parcels, flood_zones, schools)
"""

import geopandas as gpd
from shapely.geometry import box, Point

# Layer 1: Parcels
parcels = gpd.GeoDataFrame(
    {
        "name": [
            "Parc Central", "Zone Industrielle", "Lotissement Sud",
            "Ferme Bio", "Ecole Maternelle", "Foret Communale",
            "Centre Commercial", "Stade Municipal",
        ],
        "zone": [
            "green", "industrial", "residential",
            "agricultural", "public", "green",
            "commercial", "public",
        ],
        "owner": [
            "Commune", "Prive", "Commune",
            "Prive", "Commune", "Commune",
            "Prive", "Commune",
        ],
        "area_ha": [12.5, 45.0, 3.2, 28.7, 0.8, 120.0, 5.5, 2.1],
    },
    geometry=[
        box(2.3488, 48.8534, 2.3520, 48.8560),
        box(2.3550, 48.8600, 2.3620, 48.8650),
        box(2.3400, 48.8480, 2.3450, 48.8510),
        box(2.3300, 48.8550, 2.3380, 48.8610),
        box(2.3500, 48.8520, 2.3515, 48.8530),
        box(2.3100, 48.8400, 2.3300, 48.8500),
        box(2.3460, 48.8540, 2.3490, 48.8560),
        box(2.3530, 48.8500, 2.3560, 48.8520),
    ],
    crs="EPSG:4326",
)

# Layer 2: Flood zones (overlaps some parcels)
flood_zones = gpd.GeoDataFrame(
    {
        "risk_level": ["high", "medium", "low"],
        "name": ["Zone inondable A", "Zone inondable B", "Zone inondable C"],
    },
    geometry=[
        box(2.3450, 48.8500, 2.3550, 48.8570),  # overlaps Parc Central, Ecole, Centre Commercial
        box(2.3280, 48.8540, 2.3400, 48.8620),   # overlaps Ferme Bio
        box(2.3080, 48.8380, 2.3320, 48.8520),   # overlaps Foret Communale
    ],
    crs="EPSG:4326",
)

# Layer 3: Schools (points)
schools = gpd.GeoDataFrame(
    {
        "name": ["Ecole Maternelle Voltaire", "College Jean Moulin", "Lycee Victor Hugo"],
        "capacity": [120, 450, 800],
        "type": ["maternelle", "college", "lycee"],
    },
    geometry=[
        Point(2.3507, 48.8525),
        Point(2.3490, 48.8550),
        Point(2.3400, 48.8495),
    ],
    crs="EPSG:4326",
)

# Write multi-layer GPKG
out = "examples/city.gpkg"
parcels.to_file(out, layer="parcels", driver="GPKG")
flood_zones.to_file(out, layer="flood_zones", driver="GPKG", mode="a")
schools.to_file(out, layer="schools", driver="GPKG", mode="a")

print(f"Created {out} with 3 layers:")
print(f"  - parcels: {len(parcels)} features")
print(f"  - flood_zones: {len(flood_zones)} features")
print(f"  - schools: {len(schools)} features")
