"""Report builder capabilities — generate PDF/HTML reports from analysis results."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd

from capabilities.base import Capability
from capabilities.registry import register


@register
class ReportBuilderCapability(Capability):
    """Generate an HTML report from a GeoDataFrame analysis result."""

    name = "report_build"
    description = "Generate HTML reports from analysis results with customizable Jinja2 templates."

    def get_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "default": "GISPulse Analysis Report",
                    "description": "Report title",
                },
                "template_path": {
                    "type": "string",
                    "description": "Path to a custom Jinja2 HTML template",
                },
                "output_path": {
                    "type": "string",
                    "description": "Output file path (.html)",
                },
                "include_stats": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include summary statistics table",
                },
                "include_preview": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include data preview (first 20 rows)",
                },
            },
            "additionalProperties": False,
        }

    def execute(
        self,
        gdf: gpd.GeoDataFrame,
        *,
        title: str = "GISPulse Analysis Report",
        template_path: str | None = None,
        output_path: str | None = None,
        include_stats: bool = True,
        include_preview: bool = True,
        **_kw,
    ) -> gpd.GeoDataFrame:
        try:
            from jinja2 import Template
        except ImportError:
            raise ImportError(
                "jinja2 is required for report generation. "
                "Install with: pip install jinja2"
            )

        if template_path and Path(template_path).exists():
            template_str = Path(template_path).read_text(encoding="utf-8")
        else:
            template_str = _DEFAULT_TEMPLATE

        non_geom_cols = [c for c in gdf.columns if c != gdf.geometry.name]

        context = {
            "title": title,
            "feature_count": len(gdf),
            "columns": non_geom_cols,
            "crs": str(gdf.crs) if gdf.crs else "Unknown",
            "geometry_types": gdf.geometry.geom_type.value_counts().to_dict(),
            "bounds": gdf.total_bounds.tolist() if len(gdf) > 0 else [],
        }

        if include_stats and len(gdf) > 0:
            numeric_cols = gdf[non_geom_cols].select_dtypes(include="number")
            if len(numeric_cols.columns) > 0:
                context["stats"] = numeric_cols.describe().to_dict()
            else:
                context["stats"] = {}
        else:
            context["stats"] = {}

        if include_preview:
            preview_df = gdf[non_geom_cols].head(20)
            context["preview_headers"] = list(preview_df.columns)
            context["preview_rows"] = preview_df.values.tolist()
        else:
            context["preview_headers"] = []
            context["preview_rows"] = []

        template = Template(template_str)
        html = template.render(**context)

        if output_path:
            Path(output_path).write_text(html, encoding="utf-8")

        result = gdf.copy()
        result.attrs["report_html"] = html
        return result


_DEFAULT_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{ title }}</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a2e; }
    h1 { color: #16213e; border-bottom: 2px solid #0f3460; padding-bottom: 0.5rem; }
    table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
    th, td { border: 1px solid #ddd; padding: 0.5rem; text-align: left; font-size: 0.875rem; }
    th { background: #f0f0f5; }
    .meta { color: #666; font-size: 0.875rem; }
    .section { margin: 1.5rem 0; }
  </style>
</head>
<body>
  <h1>{{ title }}</h1>
  <div class="meta">
    <p><strong>Features:</strong> {{ feature_count }} | <strong>CRS:</strong> {{ crs }}</p>
    <p><strong>Columns:</strong> {{ columns | join(', ') }}</p>
    <p><strong>Geometry types:</strong>
      {% for gtype, count in geometry_types.items() %}{{ gtype }}: {{ count }}{% if not loop.last %}, {% endif %}{% endfor %}
    </p>
  </div>

  {% if stats %}
  <div class="section">
    <h2>Statistics</h2>
    <table>
      <tr><th>Column</th><th>Count</th><th>Mean</th><th>Std</th><th>Min</th><th>Max</th></tr>
      {% for col, s in stats.items() %}
      <tr>
        <td>{{ col }}</td>
        <td>{{ s.get('count', '') }}</td>
        <td>{{ '%.2f' | format(s.get('mean', 0)) }}</td>
        <td>{{ '%.2f' | format(s.get('std', 0)) }}</td>
        <td>{{ s.get('min', '') }}</td>
        <td>{{ s.get('max', '') }}</td>
      </tr>
      {% endfor %}
    </table>
  </div>
  {% endif %}

  {% if preview_rows %}
  <div class="section">
    <h2>Data Preview (first 20 rows)</h2>
    <table>
      <tr>{% for h in preview_headers %}<th>{{ h }}</th>{% endfor %}</tr>
      {% for row in preview_rows %}
      <tr>{% for cell in row %}<td>{{ cell }}</td>{% endfor %}</tr>
      {% endfor %}
    </table>
  </div>
  {% endif %}
</body>
</html>
"""
