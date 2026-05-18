"""
Vector capabilities for GISPulse.

All capabilities operate on GeoDataFrames and are registered automatically
via the @register decorator so they are discoverable through the registry.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Re-export the same module-level names that the original monolith
# ``capabilities/vector.py`` exposed, so external callers (tests, schemas,
# rule engines) keep working unchanged after the package split.
# ---------------------------------------------------------------------------

import ast as _ast  # noqa: F401  (re-exported for backward compatibility)
import re as _re  # noqa: F401

import geopandas as gpd  # noqa: F401
import numpy as np  # noqa: F401
import pandas as pd  # noqa: F401

from gispulse.capabilities.base import Capability  # noqa: F401
from gispulse.capabilities.registry import register  # noqa: F401
from gispulse.capabilities.strategy import (  # noqa: F401
    ExecutionContext,
    ExecutionStrategy,
    StrategyMode,
)

# Buffer ---------------------------------------------------------------------
from gispulse.capabilities.vector.buffer import (  # noqa: F401
    _BUFFER_CAP_STYLES,
    _BUFFER_JOIN_STYLES,
    _BufferDuckDBStrategy,
    _BufferPostGISStrategy,
    _BufferPythonStrategy,
    BufferCapability,
    _buffer_kwargs,
    _buffer_params_are_default,
    _buffer_style_sql,
)

# Union / Reproject ----------------------------------------------------------
from gispulse.capabilities.vector.union import UnionCapability  # noqa: F401
from gispulse.capabilities.vector.reproject import ReprojectCapability  # noqa: F401

# Filter ---------------------------------------------------------------------
from gispulse.capabilities.vector.filter import (  # noqa: F401
    _FilterDuckDBStrategy,
    _FilterPostGISStrategy,
    _FilterPythonStrategy,
    FilterCapability,
    _apply_predicate_geopandas,
    _buffer_geom,
    _resolve_ref_geom,
)

# Clip / Intersects / Spatial join ------------------------------------------
from gispulse.capabilities.vector.clip import (  # noqa: F401
    _ClipDuckDBStrategy,
    _ClipPostGISStrategy,
    _ClipPythonStrategy,
    ClipCapability,
    _resolve_clip_mask,
)
from gispulse.capabilities.vector.intersects import (  # noqa: F401
    _IntersectsDuckDBStrategy,
    _IntersectsPostGISStrategy,
    _IntersectsPythonStrategy,
    IntersectsCapability,
    _resolve_intersects_ref,
)
from gispulse.capabilities.vector.spatial_join import SpatialJoinCapability  # noqa: F401

# Merge / Classify -----------------------------------------------------------
from gispulse.capabilities.vector.merge import MergeLayersCapability  # noqa: F401
from gispulse.capabilities.vector.classify import ClassifyByRingCapability  # noqa: F401

# Centroid / AreaLength / Dissolve ------------------------------------------
from gispulse.capabilities.vector.centroid_area import (  # noqa: F401
    AreaLengthCapability,
    CentroidCapability,
)
from gispulse.capabilities.vector.dissolve import DissolveCapability  # noqa: F401

# Calculate (incl. shared expression validators) ----------------------------
from gispulse.capabilities.vector.calculate import (  # noqa: F401
    _CALC_ALLOWED,
    _CALC_NP_UFUNCS,
    _CALC_SAFE_NODES,
    _DANGEROUS_EXPR_RE,
    CalculateCapability,
    _SafeNamespace,
    _validate_calc_expression,
    _validate_query_expression,
)

# Aggregate / Simplify ------------------------------------------------------
from gispulse.capabilities.vector.aggregate import (  # noqa: F401
    _AGG_FUNCTIONS,
    _SPATIAL_PREDICATES,
    SpatialAggregateCapability,
)
from gispulse.capabilities.vector.simplify import (  # noqa: F401
    _SIMPLIFY_ALGORITHMS,
    SimplifyCapability,
)

# Shape ops basic / Nearest neighbor ----------------------------------------
from gispulse.capabilities.vector.shape_ops_basic import (  # noqa: F401
    ConvexHullCapability,
    EnvelopeCapability,
    MakeValidCapability,
)
from gispulse.capabilities.vector.nearest import NearestNeighborCapability  # noqa: F401

# Advanced geometry constructions -------------------------------------------
from gispulse.capabilities.vector.concave_hull import ConcaveHullCapability  # noqa: F401
from gispulse.capabilities.vector.offset_curve import OffsetCurveCapability  # noqa: F401
from gispulse.capabilities.vector.snap_grid import SnapToGridCapability  # noqa: F401
from gispulse.capabilities.vector.line_merge import LineMergeCapability  # noqa: F401
from gispulse.capabilities.vector.polygonize import PolygonizeCapability  # noqa: F401
from gispulse.capabilities.vector.voronoi import VoronoiPolygonsCapability  # noqa: F401

# Extraction / densify / advanced shape ops ---------------------------------
from gispulse.capabilities.vector.extract_ops import (  # noqa: F401
    DensifyVerticesCapability,
    ExtractSegmentsCapability,
    ExtractVerticesCapability,
    _iter_coords,
)
from gispulse.capabilities.vector.shape_ops_advanced import (  # noqa: F401
    AlphaShapeCapability,
    DelaunayTriangulationCapability,
    MinBoundingCircleCapability,
    OrientedBBoxCapability,
)
from gispulse.capabilities.vector.chaikin import ChaikinSmoothCapability  # noqa: F401

# Line ops / Diff -----------------------------------------------------------
from gispulse.capabilities.vector.line_ops import (  # noqa: F401
    LineLocatePointCapability,
    LineSubstringCapability,
)
from gispulse.capabilities.vector.diff import (  # noqa: F401
    SymmetricDifferenceCapability,
    VectorDiffCapability,
)

# Multipart / boundary / projection / holes / force type --------------------
from gispulse.capabilities.vector.parts import (  # noqa: F401
    MultipartToSinglepartsCapability,
    SinglepartsToMultipartCapability,
)
from gispulse.capabilities.vector.boundary import BoundaryCapability  # noqa: F401
from gispulse.capabilities.vector.assign_projection import (  # noqa: F401
    AssignProjectionCapability,
)
from gispulse.capabilities.vector.extract_holes import ExtractHolesCapability  # noqa: F401
from gispulse.capabilities.vector.force_geometry_type import (  # noqa: F401
    _SINGLE_TO_MULTI,
    _VALID_TARGETS,
    ForceGeometryTypeCapability,
)
