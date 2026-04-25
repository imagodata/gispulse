"""Generate sample datasets for GISPulse examples."""
import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point, box

np.random.seed(42)

# Example 1: Urban parcels
parcels = gpd.GeoDataFrame(
    {
        "name": [f"Parcel_{i}" for i in range(1, 21)],
        "zone": np.random.choice(["residential", "commercial", "industrial", "green"], 20),
        "area_m2": np.random.randint(100, 5000, 20),
        "owner": [f"Owner_{i}" for i in range(1, 21)],
    },
    geometry=[
        box(x * 0.01, y * 0.01, x * 0.01 + 0.008, y * 0.01 + 0.008)
        for x, y in zip(np.random.randint(0, 50, 20), np.random.randint(0, 50, 20))
    ],
    crs="EPSG:4326",
)

roads = gpd.GeoDataFrame(
    {
        "name": [f"Road_{i}" for i in range(1, 11)],
        "type": np.random.choice(["primary", "secondary", "tertiary"], 10),
        "width_m": np.random.choice([6, 8, 12, 15], 10),
    },
    geometry=[LineString([(x * 0.01, 0), (x * 0.01, 0.5)]) for x in range(10)],
    crs="EPSG:4326",
)

# Write multi-layer GPKG
path = "examples/urban_planning.gpkg"
parcels.to_file(path, layer="parcels", driver="GPKG")
roads.to_file(path, layer="roads", driver="GPKG", mode="a")
print(f"Created {path} with layers: parcels (20 features), roads (10 features)")

# Example 2: Points of interest
pois = gpd.GeoDataFrame(
    {
        "name": [f"POI_{i}" for i in range(1, 31)],
        "category": np.random.choice(["school", "hospital", "park", "shop", "restaurant"], 30),
        "rating": np.round(np.random.uniform(1, 5, 30), 1),
        "capacity": np.random.randint(10, 500, 30),
    },
    geometry=[
        Point(np.random.uniform(2.2, 2.5), np.random.uniform(48.8, 48.95)) for _ in range(30)
    ],
    crs="EPSG:4326",
)
poi_path = "examples/points_of_interest.gpkg"
pois.to_file(poi_path, layer="pois", driver="GPKG")
print(f"Created {poi_path} with layer: pois (30 features)")

print("\nDone! Run examples with:")
print(
    "  gispulse run examples/urban_planning.gpkg --rules examples/filter_zones.json"
    " -o result.gpkg --layer parcels"
)
print(
    "  gispulse run examples/urban_planning.gpkg --rules examples/buffer_roads.json"
    " -o result.gpkg --layer roads"
)
print(
    "  gispulse run examples/points_of_interest.gpkg --rules examples/filter_pois.json"
    " -o result.gpkg --layer pois"
)
