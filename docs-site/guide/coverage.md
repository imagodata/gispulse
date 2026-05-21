# Matrice de couverture des capabilities

Source de vérité auto-générée par `scripts/build_capability_matrix.py`. La CI fait échouer un PR qui désaligne cette page de la combinaison registry × tests × docs × templates.

**Légende** — `✅` couvert · `—` non couvert.

**Colonnes** :
- *Source* — fichier qui définit la capability
- *Tests* — au moins un fichier `tests/**/test_*.py` référence la classe
- *Docs* — la capability apparaît dans `guide/capabilities`
- *Playground* — la capability est référencée dans une scène publique du playground (`docs-site/playground/*.md`)
- *Template* — au moins un preset `templates/*.json` l'utilise

| Capability | Source | Tests | Docs | Playground | Template |
|---|---|---|---|---|---|
| `add_field` | [schema.py](../../../src/gispulse/capabilities/schema.py) | [✅](../../../tests/integration/test_attribute_pushdown.py) | ✅ | — | — |
| `add_m` | [transforms.py](../../../src/gispulse/capabilities/transforms.py) | [✅](../../../tests/unit/test_beta_fixes_2026_04_24.py) | ✅ | — | — |
| `add_z` | [transforms.py](../../../src/gispulse/capabilities/transforms.py) | [✅](../../../tests/unit/test_beta_fixes_2026_04_24.py) | ✅ | — | — |
| `affine_transform` | [transforms.py](../../../src/gispulse/capabilities/transforms.py) | [✅](../../../tests/unit/test_transforms_capabilities.py) | ✅ | — | — |
| `alpha_shape` | [shape_ops_advanced.py](../../../src/gispulse/capabilities/vector/shape_ops_advanced.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `area_length` | [centroid_area.py](../../../src/gispulse/capabilities/vector/centroid_area.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | ✅ | ✅ |
| `assign_projection` | [assign_projection.py](../../../src/gispulse/capabilities/vector/assign_projection.py) | [✅](../../../tests/unit/test_layer_transform_capabilities.py) | ✅ | — | — |
| `attribute_join` | [schema.py](../../../src/gispulse/capabilities/schema.py) | [✅](../../../tests/integration/test_attribute_pushdown.py) | ✅ | — | — |
| `attribute_validation` | [validation.py](../../../src/gispulse/capabilities/validation.py) | [✅](../../../tests/unit/test_capabilities_validation.py) | ✅ | — | ✅ |
| `bivariate_choropleth` | [classification.py](../../../src/gispulse/capabilities/classification.py) | [✅](../../../tests/unit/test_advanced_viz.py) | ✅ | — | — |
| `boundary` | [boundary.py](../../../src/gispulse/capabilities/vector/boundary.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | — |
| `buffer` | [buffer.py](../../../src/gispulse/capabilities/vector/buffer.py) | [✅](../../../tests/unit/test_capabilities.py) | — | — | ✅ |
| `calculate` | [calculate.py](../../../src/gispulse/capabilities/vector/calculate.py) | [✅](../../../tests/integration/test_attribute_pushdown.py) | ✅ | ✅ | ✅ |
| `case_when` | [schema.py](../../../src/gispulse/capabilities/schema.py) | [✅](../../../tests/integration/test_attribute_pushdown.py) | ✅ | — | — |
| `cast_field` | [schema.py](../../../src/gispulse/capabilities/schema.py) | [✅](../../../tests/integration/test_attribute_pushdown.py) | ✅ | — | — |
| `centroid` | [centroid_area.py](../../../src/gispulse/capabilities/vector/centroid_area.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | ✅ |
| `chaikin_smooth` | [chaikin.py](../../../src/gispulse/capabilities/vector/chaikin.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `change_detection` | [raster.py](../../../src/gispulse/capabilities/raster.py) | [✅](../../../tests/unit/test_raster_capabilities.py) | ✅ | — | — |
| `choropleth` | [classification.py](../../../src/gispulse/capabilities/classification.py) | [✅](../../../tests/unit/test_categorical_and_normalize.py) | ✅ | — | — |
| `classify` | [classification.py](../../../src/gispulse/capabilities/classification.py) | [✅](../../../tests/unit/test_choropleth_capability.py) | ✅ | ✅ | — |
| `classify_by_ring` | [classify.py](../../../src/gispulse/capabilities/vector/classify.py) | [✅](../../../tests/unit/test_classify_by_ring_capability.py) | ✅ | ✅ | — |
| `classify_categorical` | [classification.py](../../../src/gispulse/capabilities/classification.py) | [✅](../../../tests/unit/test_categorical_and_normalize.py) | ✅ | — | — |
| `clip` | [clip.py](../../../src/gispulse/capabilities/vector/clip.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | — | — | ✅ |
| `cluster_dbscan` | [clustering.py](../../../src/gispulse/capabilities/clustering.py) | [✅](../../../tests/unit/test_clustering_capabilities.py) | ✅ | — | — |
| `cluster_hdbscan` | [clustering.py](../../../src/gispulse/capabilities/clustering.py) | [✅](../../../tests/unit/test_clustering_capabilities.py) | ✅ | — | ✅ |
| `cluster_kmeans` | [clustering.py](../../../src/gispulse/capabilities/clustering.py) | [✅](../../../tests/unit/test_clustering_capabilities.py) | ✅ | — | — |
| `coalesce_fields` | [schema.py](../../../src/gispulse/capabilities/schema.py) | [✅](../../../tests/integration/test_attribute_pushdown.py) | ✅ | — | — |
| `completeness_check` | [validation.py](../../../src/gispulse/capabilities/validation.py) | [✅](../../../tests/unit/test_capabilities_validation.py) | ✅ | — | ✅ |
| `concave_hull` | [concave_hull.py](../../../src/gispulse/capabilities/vector/concave_hull.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | ✅ |
| `connectivity_check` | [network.py](../../../src/gispulse/capabilities/network.py) | [✅](../../../tests/unit/test_network_capabilities_s11.py) | ✅ | — | ✅ |
| `continuous_ramp` | [classification.py](../../../src/gispulse/capabilities/classification.py) | [✅](../../../tests/unit/test_advanced_viz.py) | ✅ | — | — |
| `convex_hull` | [shape_ops_basic.py](../../../src/gispulse/capabilities/vector/shape_ops_basic.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | — |
| `deduplicate` | [selection.py](../../../src/gispulse/capabilities/selection.py) | [✅](../../../tests/integration/test_attribute_pushdown.py) | ✅ | — | — |
| `delaunay_triangulation` | [shape_ops_advanced.py](../../../src/gispulse/capabilities/vector/shape_ops_advanced.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | — |
| `densify_vertices` | [extract_ops.py](../../../src/gispulse/capabilities/vector/extract_ops.py) | [✅](../../../tests/unit/test_vertex_ops_capabilities.py) | ✅ | — | — |
| `describe` | [schema.py](../../../src/gispulse/capabilities/schema.py) | [✅](../../../tests/unit/test_schema_capabilities.py) | ✅ | — | — |
| `dissolve` | [dissolve.py](../../../src/gispulse/capabilities/vector/dissolve.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | ✅ |
| `drop_field` | [schema.py](../../../src/gispulse/capabilities/schema.py) | [✅](../../../tests/integration/test_attribute_pushdown.py) | ✅ | — | — |
| `drop_m` | [transforms.py](../../../src/gispulse/capabilities/transforms.py) | [✅](../../../tests/unit/test_transforms_capabilities.py) | ✅ | — | — |
| `drop_z` | [transforms.py](../../../src/gispulse/capabilities/transforms.py) | [✅](../../../tests/unit/test_transforms_capabilities.py) | ✅ | — | — |
| `duplicate_geometry` | [validation.py](../../../src/gispulse/capabilities/validation.py) | [✅](../../../tests/unit/test_capabilities_validation.py) | ✅ | — | — |
| `envelope` | [shape_ops_basic.py](../../../src/gispulse/capabilities/vector/shape_ops_basic.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | — |
| `erase` | [overlay.py](../../../src/gispulse/capabilities/overlay.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | — |
| `extract_holes` | [extract_holes.py](../../../src/gispulse/capabilities/vector/extract_holes.py) | [✅](../../../tests/unit/test_geometry_shape_capabilities.py) | ✅ | — | — |
| `extract_segments` | [extract_ops.py](../../../src/gispulse/capabilities/vector/extract_ops.py) | [✅](../../../tests/unit/test_vertex_ops_capabilities.py) | ✅ | — | — |
| `extract_vertices` | [extract_ops.py](../../../src/gispulse/capabilities/vector/extract_ops.py) | [✅](../../../tests/unit/test_vertex_ops_capabilities.py) | ✅ | — | — |
| `filter` | [filter.py](../../../src/gispulse/capabilities/vector/filter.py) | [✅](../../../tests/unit/test_capabilities.py) | — | ✅ | ✅ |
| `force_geometry_type` | [force_geometry_type.py](../../../src/gispulse/capabilities/vector/force_geometry_type.py) | [✅](../../../tests/unit/test_beta_fixes_2026_04_24.py) | ✅ | — | — |
| `getis_ord_g` | [spatial_stats.py](../../../src/gispulse/capabilities/spatial_stats.py) | [✅](../../../tests/unit/test_spatial_stats_capabilities.py) | ✅ | — | — |
| `graduated_size` | [classification.py](../../../src/gispulse/capabilities/classification.py) | [✅](../../../tests/unit/test_advanced_viz.py) | ✅ | — | — |
| `grid_create` | [density.py](../../../src/gispulse/capabilities/density.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | ✅ | — |
| `head_tail_breaks` | [classification.py](../../../src/gispulse/capabilities/classification.py) | [✅](../../../tests/unit/test_advanced_viz.py) | ✅ | — | — |
| `hexgrid_create` | [density.py](../../../src/gispulse/capabilities/density.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `intersects` | [intersects.py](../../../src/gispulse/capabilities/vector/intersects.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | — | — | ✅ |
| `isochrone` | [network.py](../../../src/gispulse/capabilities/network.py) | [✅](../../../tests/unit/test_crs_helpers.py) | ✅ | ✅ | ✅ |
| `kde_heatmap` | [density.py](../../../src/gispulse/capabilities/density.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `line_locate_point` | [line_ops.py](../../../src/gispulse/capabilities/vector/line_ops.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `line_merge` | [line_merge.py](../../../src/gispulse/capabilities/vector/line_merge.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | — |
| `line_substring` | [line_ops.py](../../../src/gispulse/capabilities/vector/line_ops.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `lookup_table` | [schema.py](../../../src/gispulse/capabilities/schema.py) | [✅](../../../tests/unit/test_attr_logic_capabilities.py) | ✅ | — | — |
| `make_valid` | [shape_ops_basic.py](../../../src/gispulse/capabilities/vector/shape_ops_basic.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | ✅ |
| `merge_layers` | [merge.py](../../../src/gispulse/capabilities/vector/merge.py) | [✅](../../../tests/unit/test_merge_layers_capability.py) | ✅ | — | — |
| `min_bounding_circle` | [shape_ops_advanced.py](../../../src/gispulse/capabilities/vector/shape_ops_advanced.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `morans_i` | [spatial_stats.py](../../../src/gispulse/capabilities/spatial_stats.py) | [✅](../../../tests/unit/test_spatial_stats_capabilities.py) | ✅ | — | — |
| `mst` | [network.py](../../../src/gispulse/capabilities/network.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `multipart_to_singleparts` | [parts.py](../../../src/gispulse/capabilities/vector/parts.py) | [✅](../../../tests/unit/test_layer_transform_capabilities.py) | ✅ | — | — |
| `ndvi` | [raster.py](../../../src/gispulse/capabilities/raster.py) | [✅](../../../tests/unit/test_raster_capabilities_s11.py) | ✅ | — | — |
| `nearest_neighbor` | [nearest.py](../../../src/gispulse/capabilities/vector/nearest.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | — | ✅ | ✅ |
| `network_allocation` | [network.py](../../../src/gispulse/capabilities/network.py) | [✅](../../../tests/unit/test_network_capabilities_s11.py) | ✅ | — | — |
| `network_extend_dangles` | [network_topology.py](../../../src/gispulse/capabilities/network_topology.py) | [✅](../../../tests/unit/test_network_topology_capabilities.py) | ✅ | — | ✅ |
| `network_node_lines` | [network_topology.py](../../../src/gispulse/capabilities/network_topology.py) | [✅](../../../tests/unit/test_network_topology_capabilities.py) | ✅ | — | ✅ |
| `network_remove_duplicates` | [network_topology.py](../../../src/gispulse/capabilities/network_topology.py) | [✅](../../../tests/unit/test_network_topology_capabilities.py) | ✅ | — | ✅ |
| `network_remove_pseudo_nodes` | [network_topology.py](../../../src/gispulse/capabilities/network_topology.py) | [✅](../../../tests/unit/test_network_topology_capabilities.py) | ✅ | — | ✅ |
| `network_snap_endpoints` | [network_topology.py](../../../src/gispulse/capabilities/network_topology.py) | [✅](../../../tests/unit/test_network_topology_capabilities.py) | ✅ | — | ✅ |
| `normalize` | [classification.py](../../../src/gispulse/capabilities/classification.py) | [✅](../../../tests/unit/test_categorical_and_normalize.py) | ✅ | — | — |
| `od_matrix` | [network.py](../../../src/gispulse/capabilities/network.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `offset_curve` | [offset_curve.py](../../../src/gispulse/capabilities/vector/offset_curve.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | — |
| `oriented_bbox` | [shape_ops_advanced.py](../../../src/gispulse/capabilities/vector/shape_ops_advanced.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `overlay_intersection` | [overlay.py](../../../src/gispulse/capabilities/overlay.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | — |
| `overlay_union` | [overlay.py](../../../src/gispulse/capabilities/overlay.py) | [✅](../../../tests/unit/test_overlay_capabilities.py) | ✅ | — | — |
| `pivot` | [schema.py](../../../src/gispulse/capabilities/schema.py) | — | ✅ | — | — |
| `pointcloud_filter_classification` | [pointcloud.py](../../../src/gispulse/capabilities/pointcloud.py) | [✅](../../../tests/unit/test_pointcloud_capabilities.py) | ✅ | — | — |
| `pointcloud_grid_summary` | [pointcloud.py](../../../src/gispulse/capabilities/pointcloud.py) | [✅](../../../tests/unit/test_pointcloud_capabilities.py) | ✅ | — | — |
| `pointcloud_load_las` | [pointcloud.py](../../../src/gispulse/capabilities/pointcloud.py) | [✅](../../../tests/unit/test_pointcloud_capabilities.py) | ✅ | — | — |
| `pointcloud_zonal_height` | [pointcloud.py](../../../src/gispulse/capabilities/pointcloud.py) | [✅](../../../tests/unit/test_pointcloud_capabilities.py) | ✅ | — | — |
| `polygon_fix_gaps` | [polygon_topology.py](../../../src/gispulse/capabilities/polygon_topology.py) | [✅](../../../tests/unit/test_polygon_topology_capabilities.py) | ✅ | — | ✅ |
| `polygon_fix_overlaps` | [polygon_topology.py](../../../src/gispulse/capabilities/polygon_topology.py) | [✅](../../../tests/unit/test_polygon_topology_capabilities.py) | ✅ | — | ✅ |
| `polygon_remove_slivers` | [polygon_topology.py](../../../src/gispulse/capabilities/polygon_topology.py) | [✅](../../../tests/unit/test_polygon_topology_capabilities.py) | ✅ | — | ✅ |
| `polygon_snap_borders` | [polygon_topology.py](../../../src/gispulse/capabilities/polygon_topology.py) | [✅](../../../tests/unit/test_polygon_topology_capabilities.py) | ✅ | — | ✅ |
| `polygonize` | [polygonize.py](../../../src/gispulse/capabilities/vector/polygonize.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | — |
| `postgis_sql` | [postgis_sql.py](../../../src/gispulse/capabilities/postgis_sql.py) | [✅](../../../tests/unit/test_postgis_sql_unit.py) | — | — | — |
| `random_sample` | [selection.py](../../../src/gispulse/capabilities/selection.py) | [✅](../../../tests/unit/test_selection_capabilities.py) | ✅ | — | — |
| `raster_clip` | [raster.py](../../../src/gispulse/capabilities/raster.py) | [✅](../../../tests/unit/test_raster_capabilities_s11.py) | ✅ | — | — |
| `raster_merge` | [raster.py](../../../src/gispulse/capabilities/raster.py) | [✅](../../../tests/unit/test_raster_capabilities_s11.py) | ✅ | — | — |
| `raster_reproject` | [raster.py](../../../src/gispulse/capabilities/raster.py) | [✅](../../../tests/unit/test_raster_capabilities_s11.py) | ✅ | — | — |
| `rename_field` | [schema.py](../../../src/gispulse/capabilities/schema.py) | [✅](../../../tests/integration/test_attribute_pushdown.py) | ✅ | — | — |
| `reproject` | [reproject.py](../../../src/gispulse/capabilities/vector/reproject.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | — | — | ✅ |
| `reverse_lines` | [transforms.py](../../../src/gispulse/capabilities/transforms.py) | [✅](../../../tests/unit/test_transforms_capabilities.py) | ✅ | — | — |
| `select_columns` | [schema.py](../../../src/gispulse/capabilities/schema.py) | [✅](../../../tests/integration/test_attribute_pushdown.py) | ✅ | — | — |
| `shortest_path` | [network.py](../../../src/gispulse/capabilities/network.py) | [✅](../../../tests/unit/test_network_capabilities.py) | ✅ | — | — |
| `simplify` | [simplify.py](../../../src/gispulse/capabilities/vector/simplify.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | — |
| `singleparts_to_multipart` | [parts.py](../../../src/gispulse/capabilities/vector/parts.py) | [✅](../../../tests/unit/test_beta_fixes_2026_04_24.py) | ✅ | — | — |
| `snap_to_grid` | [snap_grid.py](../../../src/gispulse/capabilities/vector/snap_grid.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | ✅ |
| `sort` | [selection.py](../../../src/gispulse/capabilities/selection.py) | [✅](../../../tests/integration/test_attribute_pushdown.py) | ✅ | — | — |
| `spatial_aggregate` | [aggregate.py](../../../src/gispulse/capabilities/vector/aggregate.py) | [✅](../../../tests/unit/test_calculate_capabilities.py) | — | ✅ | ✅ |
| `spatial_join` | [spatial_join.py](../../../src/gispulse/capabilities/vector/spatial_join.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | — | — | ✅ |
| `spatial_weights` | [spatial_stats.py](../../../src/gispulse/capabilities/spatial_stats.py) | [✅](../../../tests/unit/test_spatial_stats_capabilities.py) | ✅ | — | — |
| `swap_xy` | [transforms.py](../../../src/gispulse/capabilities/transforms.py) | [✅](../../../tests/unit/test_transforms_capabilities.py) | ✅ | — | — |
| `symmetric_difference` | [diff.py](../../../src/gispulse/capabilities/vector/diff.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | — |
| `temporal_filter` | [temporal.py](../../../src/gispulse/capabilities/temporal.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | — |
| `temporal_join` | [temporal.py](../../../src/gispulse/capabilities/temporal.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | — |
| `top_n` | [selection.py](../../../src/gispulse/capabilities/selection.py) | [✅](../../../tests/integration/test_attribute_pushdown.py) | ✅ | — | — |
| `topology_check` | [validation.py](../../../src/gispulse/capabilities/validation.py) | [✅](../../../tests/unit/test_capabilities_validation.py) | ✅ | — | ✅ |
| `union` | [union.py](../../../src/gispulse/capabilities/vector/union.py) | [✅](../../../tests/integration/test_geometry_pushdown.py) | ✅ | — | ✅ |
| `unpivot` | [schema.py](../../../src/gispulse/capabilities/schema.py) | — | ✅ | — | — |
| `vector_diff` | [diff.py](../../../src/gispulse/capabilities/vector/diff.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `voronoi_polygons` | [voronoi.py](../../../src/gispulse/capabilities/vector/voronoi.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | ✅ |
| `zonal_stats` | [raster.py](../../../src/gispulse/capabilities/raster.py) | [✅](../../../tests/unit/test_raster_capabilities.py) | ✅ | — | ✅ |
| **Total** | — | 116 / 118 | 109 / 118 | 9 / 118 | 33 / 118 |

*Generated by `scripts/build_capability_matrix.py`. Run `python scripts/build_capability_matrix.py` after adding / removing a capability.*
