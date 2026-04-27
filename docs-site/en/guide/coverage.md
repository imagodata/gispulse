# Capability coverage matrix

Auto-generated source of truth produced by `scripts/build_capability_matrix.py`. CI fails any PR that drifts this page from the live registry × tests × docs × templates combination.

**Legend** — `✅` covered · `—` not covered.

**Columns**:
- *Source* — file that defines the capability
- *Tests* — at least one file under `tests/**/test_*.py` references the class
- *Docs* — the capability appears in `guide/capabilities`
- *Playground* — the capability is referenced by a public playground scenario (`docs-site/playground/*.md`)
- *Template* — at least one preset under `templates/*.json` uses it

| Capability | Source | Tests | Docs | Playground | Template |
|---|---|---|---|---|---|
| `add_field` | [schema.py](../../../capabilities/schema.py) | [✅](../../../tests/unit/test_schema_capabilities.py) | ✅ | — | — |
| `add_m` | [transforms.py](../../../capabilities/transforms.py) | [✅](../../../tests/unit/test_beta_fixes_2026_04_24.py) | ✅ | — | — |
| `add_z` | [transforms.py](../../../capabilities/transforms.py) | [✅](../../../tests/unit/test_beta_fixes_2026_04_24.py) | ✅ | — | — |
| `affine_transform` | [transforms.py](../../../capabilities/transforms.py) | [✅](../../../tests/unit/test_transforms_capabilities.py) | ✅ | — | — |
| `alpha_shape` | [shape_ops_advanced.py](../../../capabilities/vector/shape_ops_advanced.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `area_length` | [centroid_area.py](../../../capabilities/vector/centroid_area.py) | [✅](../../../tests/unit/test_edge_cases.py) | ✅ | ✅ | ✅ |
| `assign_projection` | [assign_projection.py](../../../capabilities/vector/assign_projection.py) | [✅](../../../tests/unit/test_layer_transform_capabilities.py) | ✅ | — | — |
| `attribute_join` | [schema.py](../../../capabilities/schema.py) | [✅](../../../tests/unit/test_schema_capabilities.py) | ✅ | — | — |
| `attribute_validation` | [validation.py](../../../capabilities/validation.py) | [✅](../../../tests/unit/test_capabilities_validation.py) | ✅ | — | ✅ |
| `bivariate_choropleth` | [classification.py](../../../capabilities/classification.py) | [✅](../../../tests/unit/test_advanced_viz.py) | ✅ | — | — |
| `boundary` | [boundary.py](../../../capabilities/vector/boundary.py) | [✅](../../../tests/unit/test_layer_transform_capabilities.py) | ✅ | — | — |
| `buffer` | [buffer.py](../../../capabilities/vector/buffer.py) | [✅](../../../tests/unit/test_new_vector_capabilities.py) | — | — | ✅ |
| `calculate` | [calculate.py](../../../capabilities/vector/calculate.py) | [✅](../../../tests/unit/test_calculate_capabilities.py) | ✅ | ✅ | ✅ |
| `case_when` | [schema.py](../../../capabilities/schema.py) | [✅](../../../tests/unit/test_attr_logic_capabilities.py) | ✅ | — | — |
| `cast_field` | [schema.py](../../../capabilities/schema.py) | [✅](../../../tests/unit/test_schema_capabilities.py) | ✅ | — | — |
| `centroid` | [centroid_area.py](../../../capabilities/vector/centroid_area.py) | [✅](../../../tests/unit/test_edge_cases.py) | ✅ | — | ✅ |
| `chaikin_smooth` | [chaikin.py](../../../capabilities/vector/chaikin.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `change_detection` | [raster.py](../../../capabilities/raster.py) | [✅](../../../tests/unit/test_raster_capabilities_s11.py) | ✅ | — | — |
| `choropleth` | [classification.py](../../../capabilities/classification.py) | [✅](../../../tests/unit/test_categorical_and_normalize.py) | ✅ | — | — |
| `classify` | [classification.py](../../../capabilities/classification.py) | [✅](../../../tests/unit/test_classify_capability.py) | ✅ | ✅ | — |
| `classify_by_ring` | [classify.py](../../../capabilities/vector/classify.py) | [✅](../../../tests/unit/test_classify_by_ring_capability.py) | ✅ | ✅ | — |
| `classify_categorical` | [classification.py](../../../capabilities/classification.py) | [✅](../../../tests/unit/test_categorical_and_normalize.py) | ✅ | — | — |
| `clip` | [clip.py](../../../capabilities/vector/clip.py) | [✅](../../../tests/unit/test_edge_cases.py) | — | — | ✅ |
| `cluster_dbscan` | [clustering.py](../../../capabilities/clustering.py) | [✅](../../../tests/unit/test_clustering_capabilities.py) | ✅ | — | — |
| `cluster_hdbscan` | [clustering.py](../../../capabilities/clustering.py) | [✅](../../../tests/unit/test_clustering_capabilities.py) | ✅ | — | ✅ |
| `cluster_kmeans` | [clustering.py](../../../capabilities/clustering.py) | [✅](../../../tests/unit/test_clustering_capabilities.py) | ✅ | — | — |
| `coalesce_fields` | [schema.py](../../../capabilities/schema.py) | [✅](../../../tests/unit/test_attr_logic_capabilities.py) | ✅ | — | — |
| `completeness_check` | [validation.py](../../../capabilities/validation.py) | [✅](../../../tests/unit/test_capabilities_validation.py) | ✅ | — | ✅ |
| `concave_hull` | [concave_hull.py](../../../capabilities/vector/concave_hull.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | ✅ |
| `connectivity_check` | [network.py](../../../capabilities/network.py) | [✅](../../../tests/unit/test_network_capabilities_s11.py) | ✅ | — | ✅ |
| `continuous_ramp` | [classification.py](../../../capabilities/classification.py) | [✅](../../../tests/unit/test_advanced_viz.py) | ✅ | — | — |
| `convex_hull` | [shape_ops_basic.py](../../../capabilities/vector/shape_ops_basic.py) | [✅](../../../tests/unit/test_new_vector_capabilities.py) | ✅ | — | — |
| `deduplicate` | [selection.py](../../../capabilities/selection.py) | [✅](../../../tests/unit/test_selection_capabilities.py) | ✅ | — | — |
| `delaunay_triangulation` | [shape_ops_advanced.py](../../../capabilities/vector/shape_ops_advanced.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | — |
| `densify_vertices` | [extract_ops.py](../../../capabilities/vector/extract_ops.py) | [✅](../../../tests/unit/test_vertex_ops_capabilities.py) | ✅ | — | — |
| `describe` | [schema.py](../../../capabilities/schema.py) | [✅](../../../tests/unit/test_schema_capabilities.py) | ✅ | — | — |
| `dissolve` | [dissolve.py](../../../capabilities/vector/dissolve.py) | [✅](../../../tests/unit/test_edge_cases.py) | ✅ | — | ✅ |
| `drop_field` | [schema.py](../../../capabilities/schema.py) | [✅](../../../tests/unit/test_schema_capabilities.py) | ✅ | — | — |
| `drop_m` | [transforms.py](../../../capabilities/transforms.py) | [✅](../../../tests/unit/test_transforms_capabilities.py) | ✅ | — | — |
| `drop_z` | [transforms.py](../../../capabilities/transforms.py) | [✅](../../../tests/unit/test_transforms_capabilities.py) | ✅ | — | — |
| `duplicate_geometry` | [validation.py](../../../capabilities/validation.py) | [✅](../../../tests/unit/test_capabilities_validation.py) | ✅ | — | — |
| `envelope` | [shape_ops_basic.py](../../../capabilities/vector/shape_ops_basic.py) | [✅](../../../tests/unit/test_new_vector_capabilities.py) | ✅ | — | — |
| `erase` | [overlay.py](../../../capabilities/overlay.py) | [✅](../../../tests/unit/test_overlay_capabilities.py) | ✅ | — | — |
| `extract_holes` | [extract_holes.py](../../../capabilities/vector/extract_holes.py) | [✅](../../../tests/unit/test_geometry_shape_capabilities.py) | ✅ | — | — |
| `extract_segments` | [extract_ops.py](../../../capabilities/vector/extract_ops.py) | [✅](../../../tests/unit/test_vertex_ops_capabilities.py) | ✅ | — | — |
| `extract_vertices` | [extract_ops.py](../../../capabilities/vector/extract_ops.py) | [✅](../../../tests/unit/test_vertex_ops_capabilities.py) | ✅ | — | — |
| `filter` | [filter.py](../../../capabilities/vector/filter.py) | [✅](../../../tests/unit/test_edge_cases.py) | — | ✅ | ✅ |
| `force_geometry_type` | [force_geometry_type.py](../../../capabilities/vector/force_geometry_type.py) | [✅](../../../tests/unit/test_beta_fixes_2026_04_24.py) | ✅ | — | — |
| `getis_ord_g` | [spatial_stats.py](../../../capabilities/spatial_stats.py) | [✅](../../../tests/unit/test_spatial_stats_capabilities.py) | ✅ | — | — |
| `graduated_size` | [classification.py](../../../capabilities/classification.py) | [✅](../../../tests/unit/test_advanced_viz.py) | ✅ | — | — |
| `grid_create` | [density.py](../../../capabilities/density.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | ✅ | — |
| `head_tail_breaks` | [classification.py](../../../capabilities/classification.py) | [✅](../../../tests/unit/test_advanced_viz.py) | ✅ | — | — |
| `hexgrid_create` | [density.py](../../../capabilities/density.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `intersects` | [intersects.py](../../../capabilities/vector/intersects.py) | [✅](../../../tests/unit/test_edge_cases.py) | — | — | ✅ |
| `isochrone` | [network.py](../../../capabilities/network.py) | [✅](../../../tests/unit/test_network_capabilities_s11.py) | ✅ | ✅ | ✅ |
| `kde_heatmap` | [density.py](../../../capabilities/density.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `line_locate_point` | [line_ops.py](../../../capabilities/vector/line_ops.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `line_merge` | [line_merge.py](../../../capabilities/vector/line_merge.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | — |
| `line_substring` | [line_ops.py](../../../capabilities/vector/line_ops.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `lookup_table` | [schema.py](../../../capabilities/schema.py) | [✅](../../../tests/unit/test_attr_logic_capabilities.py) | ✅ | — | — |
| `make_valid` | [shape_ops_basic.py](../../../capabilities/vector/shape_ops_basic.py) | [✅](../../../tests/unit/test_new_vector_capabilities.py) | ✅ | — | ✅ |
| `merge_layers` | [merge.py](../../../capabilities/vector/merge.py) | [✅](../../../tests/unit/test_merge_layers_capability.py) | ✅ | — | — |
| `min_bounding_circle` | [shape_ops_advanced.py](../../../capabilities/vector/shape_ops_advanced.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `morans_i` | [spatial_stats.py](../../../capabilities/spatial_stats.py) | [✅](../../../tests/unit/test_spatial_stats_capabilities.py) | ✅ | — | — |
| `mst` | [network.py](../../../capabilities/network.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `multipart_to_singleparts` | [parts.py](../../../capabilities/vector/parts.py) | [✅](../../../tests/unit/test_layer_transform_capabilities.py) | ✅ | — | — |
| `ndvi` | [raster.py](../../../capabilities/raster.py) | [✅](../../../tests/unit/test_raster_capabilities_s11.py) | ✅ | — | — |
| `nearest_neighbor` | [nearest.py](../../../capabilities/vector/nearest.py) | [✅](../../../tests/unit/test_new_vector_capabilities.py) | — | ✅ | ✅ |
| `network_allocation` | [network.py](../../../capabilities/network.py) | [✅](../../../tests/unit/test_network_capabilities_s11.py) | ✅ | — | — |
| `network_extend_dangles` | [network_topology.py](../../../capabilities/network_topology.py) | [✅](../../../tests/unit/test_network_topology_capabilities.py) | ✅ | — | ✅ |
| `network_node_lines` | [network_topology.py](../../../capabilities/network_topology.py) | [✅](../../../tests/unit/test_network_topology_capabilities.py) | ✅ | — | ✅ |
| `network_remove_duplicates` | [network_topology.py](../../../capabilities/network_topology.py) | [✅](../../../tests/unit/test_network_topology_capabilities.py) | ✅ | — | ✅ |
| `network_remove_pseudo_nodes` | [network_topology.py](../../../capabilities/network_topology.py) | [✅](../../../tests/unit/test_network_topology_capabilities.py) | ✅ | — | ✅ |
| `network_snap_endpoints` | [network_topology.py](../../../capabilities/network_topology.py) | [✅](../../../tests/unit/test_network_topology_capabilities.py) | ✅ | — | ✅ |
| `normalize` | [classification.py](../../../capabilities/classification.py) | [✅](../../../tests/unit/test_categorical_and_normalize.py) | ✅ | — | — |
| `od_matrix` | [network.py](../../../capabilities/network.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `offset_curve` | [offset_curve.py](../../../capabilities/vector/offset_curve.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | — |
| `oriented_bbox` | [shape_ops_advanced.py](../../../capabilities/vector/shape_ops_advanced.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `overlay_intersection` | [overlay.py](../../../capabilities/overlay.py) | [✅](../../../tests/unit/test_overlay_capabilities.py) | ✅ | — | — |
| `overlay_union` | [overlay.py](../../../capabilities/overlay.py) | [✅](../../../tests/unit/test_overlay_capabilities.py) | ✅ | — | — |
| `pivot` | [schema.py](../../../capabilities/schema.py) | — | ✅ | — | — |
| `pointcloud_filter_classification` | [pointcloud.py](../../../capabilities/pointcloud.py) | [✅](../../../tests/unit/test_pointcloud_capabilities.py) | ✅ | — | — |
| `pointcloud_grid_summary` | [pointcloud.py](../../../capabilities/pointcloud.py) | [✅](../../../tests/unit/test_pointcloud_capabilities.py) | ✅ | — | — |
| `pointcloud_load_las` | [pointcloud.py](../../../capabilities/pointcloud.py) | [✅](../../../tests/unit/test_pointcloud_capabilities.py) | ✅ | — | — |
| `pointcloud_zonal_height` | [pointcloud.py](../../../capabilities/pointcloud.py) | [✅](../../../tests/unit/test_pointcloud_capabilities.py) | ✅ | — | — |
| `polygon_fix_gaps` | [polygon_topology.py](../../../capabilities/polygon_topology.py) | [✅](../../../tests/unit/test_polygon_topology_capabilities.py) | ✅ | — | ✅ |
| `polygon_fix_overlaps` | [polygon_topology.py](../../../capabilities/polygon_topology.py) | [✅](../../../tests/unit/test_polygon_topology_capabilities.py) | ✅ | — | ✅ |
| `polygon_remove_slivers` | [polygon_topology.py](../../../capabilities/polygon_topology.py) | [✅](../../../tests/unit/test_polygon_topology_capabilities.py) | ✅ | — | ✅ |
| `polygon_snap_borders` | [polygon_topology.py](../../../capabilities/polygon_topology.py) | [✅](../../../tests/unit/test_polygon_topology_capabilities.py) | ✅ | — | ✅ |
| `polygonize` | [polygonize.py](../../../capabilities/vector/polygonize.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | — |
| `postgis_sql` | [postgis_sql.py](../../../capabilities/postgis_sql.py) | [✅](../../../tests/unit/test_postgis_sql_unit.py) | — | — | — |
| `random_sample` | [selection.py](../../../capabilities/selection.py) | [✅](../../../tests/unit/test_selection_capabilities.py) | ✅ | — | — |
| `raster_clip` | [raster.py](../../../capabilities/raster.py) | [✅](../../../tests/unit/test_raster_capabilities_s11.py) | ✅ | — | — |
| `raster_merge` | [raster.py](../../../capabilities/raster.py) | [✅](../../../tests/unit/test_raster_capabilities_s11.py) | ✅ | — | — |
| `raster_reproject` | [raster.py](../../../capabilities/raster.py) | [✅](../../../tests/unit/test_raster_capabilities_s11.py) | ✅ | — | — |
| `rename_field` | [schema.py](../../../capabilities/schema.py) | [✅](../../../tests/unit/test_schema_capabilities.py) | ✅ | — | — |
| `reproject` | [reproject.py](../../../capabilities/vector/reproject.py) | [✅](../../../tests/unit/test_capabilities.py) | — | — | ✅ |
| `reverse_lines` | [transforms.py](../../../capabilities/transforms.py) | [✅](../../../tests/unit/test_transforms_capabilities.py) | ✅ | — | — |
| `select_columns` | [schema.py](../../../capabilities/schema.py) | [✅](../../../tests/unit/test_schema_capabilities.py) | ✅ | — | — |
| `shortest_path` | [network.py](../../../capabilities/network.py) | [✅](../../../tests/unit/test_network_capabilities_s11.py) | ✅ | — | — |
| `simplify` | [simplify.py](../../../capabilities/vector/simplify.py) | [✅](../../../tests/unit/test_new_vector_capabilities.py) | ✅ | — | — |
| `singleparts_to_multipart` | [parts.py](../../../capabilities/vector/parts.py) | [✅](../../../tests/unit/test_layer_transform_capabilities.py) | ✅ | — | — |
| `snap_to_grid` | [snap_grid.py](../../../capabilities/vector/snap_grid.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | ✅ |
| `sort` | [selection.py](../../../capabilities/selection.py) | [✅](../../../tests/unit/test_selection_capabilities.py) | ✅ | — | — |
| `spatial_aggregate` | [aggregate.py](../../../capabilities/vector/aggregate.py) | [✅](../../../tests/unit/test_calculate_capabilities.py) | — | ✅ | ✅ |
| `spatial_join` | [spatial_join.py](../../../capabilities/vector/spatial_join.py) | [✅](../../../tests/unit/test_edge_cases.py) | — | — | ✅ |
| `spatial_weights` | [spatial_stats.py](../../../capabilities/spatial_stats.py) | [✅](../../../tests/unit/test_spatial_stats_capabilities.py) | ✅ | — | — |
| `swap_xy` | [transforms.py](../../../capabilities/transforms.py) | [✅](../../../tests/unit/test_transforms_capabilities.py) | ✅ | — | — |
| `symmetric_difference` | [diff.py](../../../capabilities/vector/diff.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `temporal_filter` | [temporal.py](../../../capabilities/temporal.py) | [✅](../../../tests/unit/test_temporal_capabilities.py) | ✅ | — | — |
| `temporal_join` | [temporal.py](../../../capabilities/temporal.py) | [✅](../../../tests/unit/test_temporal_capabilities.py) | ✅ | — | — |
| `top_n` | [selection.py](../../../capabilities/selection.py) | [✅](../../../tests/unit/test_beta_fixes_2026_04_24.py) | ✅ | — | — |
| `topology_check` | [validation.py](../../../capabilities/validation.py) | [✅](../../../tests/unit/test_capabilities_validation.py) | ✅ | — | ✅ |
| `union` | [union.py](../../../capabilities/vector/union.py) | [✅](../../../tests/unit/test_edge_cases.py) | ✅ | — | ✅ |
| `unpivot` | [schema.py](../../../capabilities/schema.py) | — | ✅ | — | — |
| `vector_diff` | [diff.py](../../../capabilities/vector/diff.py) | [✅](../../../tests/unit/test_density_and_advanced_vector_capabilities.py) | ✅ | — | — |
| `voronoi_polygons` | [voronoi.py](../../../capabilities/vector/voronoi.py) | [✅](../../../tests/unit/test_advanced_vector_capabilities.py) | ✅ | — | ✅ |
| `zonal_stats` | [raster.py](../../../capabilities/raster.py) | [✅](../../../tests/unit/test_raster_capabilities_s11.py) | ✅ | — | ✅ |
| **Total** | — | 116 / 118 | 109 / 118 | 9 / 118 | 33 / 118 |

*Generated by `scripts/build_capability_matrix.py` (2026-04-27). Run `python scripts/build_capability_matrix.py` after adding / removing a capability.*
