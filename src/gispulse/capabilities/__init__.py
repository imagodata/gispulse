"""GISPulse capabilities — spatial operations registry."""

# Auto-import capability modules so they register themselves.
# vector et postgis_sql n'ont aucune dépendance optionnelle.
import gispulse.capabilities.vector  # noqa: F401
import gispulse.capabilities.postgis_sql  # noqa: F401

# validation : dépend uniquement de geopandas/shapely (déjà obligatoires)
import gispulse.capabilities.validation  # noqa: F401

# Network & polygon topology repair — pure shapely, no optional deps.
import gispulse.capabilities.network_topology  # noqa: F401
import gispulse.capabilities.polygon_topology  # noqa: F401

# Spatial statistics + density — require numpy/scipy/sklearn (always installed).
import gispulse.capabilities.spatial_stats  # noqa: F401
import gispulse.capabilities.density  # noqa: F401

# Classification / choropleth — pure numpy core; mapclassify optional.
import gispulse.capabilities.classification  # noqa: F401

# Schema / attribute manipulation — pure pandas, no optional deps.
import gispulse.capabilities.schema  # noqa: F401

# Selection / row-level ops — pure pandas, no optional deps.
import gispulse.capabilities.selection  # noqa: F401

# Overlay (intersection / union / erase) — pure geopandas.
import gispulse.capabilities.overlay  # noqa: F401

# Geometry transforms — pure shapely.
import gispulse.capabilities.transforms  # noqa: F401

# Temporal — pure pandas.
import gispulse.capabilities.temporal  # noqa: F401

# Pointcloud — three pure-pandas capabilities + one optional laspy loader.
# The module imports laspy lazily inside execute(), so this top-level import is
# always safe; pointcloud_load_las raises RuntimeError if laspy is missing.
import gispulse.capabilities.pointcloud  # noqa: F401

from gispulse.capabilities.base import Capability
from gispulse.capabilities.registry import REGISTRY, get, list_all, register
from gispulse.capabilities.postgis_sql import PostGISSQLCapability
from gispulse.capabilities.classification import (
    BivariateChoroplethCapability,
    ChoroplethCapability,
    ClassifyCapability,
    ClassifyCategoricalCapability,
    ContinuousRampCapability,
    GraduatedSizeCapability,
    HeadTailBreaksCapability,
    NormalizeCapability,
    build_graduated_style_def,
    build_legend,
)
from gispulse.capabilities.vector import (
    AlphaShapeCapability,
    AreaLengthCapability,
    AssignProjectionCapability,
    BoundaryCapability,
    BufferCapability,
    CalculateCapability,
    CentroidCapability,
    ChaikinSmoothCapability,
    ClassifyByRingCapability,
    ClipCapability,
    ConcaveHullCapability,
    ConvexHullCapability,
    DelaunayTriangulationCapability,
    DensifyVerticesCapability,
    DissolveCapability,
    EnvelopeCapability,
    ExtractHolesCapability,
    ExtractSegmentsCapability,
    ExtractVerticesCapability,
    FilterCapability,
    ForceGeometryTypeCapability,
    IntersectsCapability,
    LineLocatePointCapability,
    LineMergeCapability,
    LineSubstringCapability,
    MakeValidCapability,
    MergeLayersCapability,
    MinBoundingCircleCapability,
    MultipartToSinglepartsCapability,
    NearestNeighborCapability,
    OffsetCurveCapability,
    OrientedBBoxCapability,
    PolygonizeCapability,
    ReprojectCapability,
    SimplifyCapability,
    SinglepartsToMultipartCapability,
    SnapToGridCapability,
    SpatialAggregateCapability,
    SpatialJoinCapability,
    SymmetricDifferenceCapability,
    UnionCapability,
    VectorDiffCapability,
    VoronoiPolygonsCapability,
)
from gispulse.capabilities.schema import (
    AddFieldCapability,
    AttributeJoinCapability,
    CaseWhenCapability,
    CastFieldCapability,
    CoalesceFieldsCapability,
    DescribeCapability,
    DropFieldCapability,
    LookupTableCapability,
    PivotCapability,
    RenameFieldCapability,
    SelectColumnsCapability,
    UnpivotCapability,
)
from gispulse.capabilities.selection import (
    DeduplicateCapability,
    RandomSampleCapability,
    SortCapability,
    TopNCapability,
)
from gispulse.capabilities.overlay import (
    EraseCapability,
    OverlayIntersectionCapability,
    OverlayUnionCapability,
)
from gispulse.capabilities.transforms import (
    AddMCapability,
    AddZCapability,
    AffineTransformCapability,
    DropMCapability,
    DropZCapability,
    ReverseLinesCapability,
    SwapXYCapability,
)
from gispulse.capabilities.temporal import (
    TemporalFilterCapability,
    TemporalJoinCapability,
)
from gispulse.capabilities.pointcloud import (
    PointcloudFilterClassificationCapability,
    PointcloudGridSummaryCapability,
    PointcloudLoadLasCapability,
    PointcloudZonalHeightCapability,
)
from gispulse.capabilities.spatial_stats import (
    GetisOrdGStarCapability,
    MoransICapability,
    SpatialWeightsCapability,
)
from gispulse.capabilities.density import (
    GridCreateCapability,
    HexGridCreateCapability,
    KDEHeatmapCapability,
)
from gispulse.capabilities.network_topology import (
    ExtendDanglesCapability,
    NodeLinesCapability,
    RemoveDuplicateEdgesCapability,
    RemovePseudoNodesCapability,
    SnapEndpointsCapability,
)
from gispulse.capabilities.polygon_topology import (
    FixGapsCapability,
    FixOverlapsCapability,
    RemoveSliversCapability,
    SnapBordersCapability,
)

# Clustering depends on scikit-learn (optional).
try:
    import gispulse.capabilities.clustering  # noqa: F401
    from gispulse.capabilities.clustering import (
        DBSCANClusterCapability,
        HDBSCANClusterCapability,
        KMeansClusterCapability,
    )

    _CLUSTERING_AVAILABLE = True
except ImportError:
    _CLUSTERING_AVAILABLE = False
    DBSCANClusterCapability = None  # type: ignore[assignment, misc]
    HDBSCANClusterCapability = None  # type: ignore[assignment, misc]
    KMeansClusterCapability = None  # type: ignore[assignment, misc]
from gispulse.capabilities.validation import (
    AttributeValidationCapability,
    CompletenessCheckCapability,
    DuplicateGeometryCapability,
    TopologyCheckCapability,
)

# raster : dépend de rasterio / rasterstats (optionnels)
try:
    import gispulse.capabilities.raster  # noqa: F401
    from gispulse.capabilities.raster import (
        ChangeDetectionCapability,
        NdviCapability,
        RasterClipCapability,
        RasterMergeCapability,
        RasterReprojectCapability,
        ZonalStatsCapability,
    )

    _RASTER_AVAILABLE = True
except ImportError:
    _RASTER_AVAILABLE = False
    ChangeDetectionCapability = None  # type: ignore[assignment, misc]
    ZonalStatsCapability = None  # type: ignore[assignment, misc]
    RasterClipCapability = None  # type: ignore[assignment, misc]
    NdviCapability = None  # type: ignore[assignment, misc]
    RasterReprojectCapability = None  # type: ignore[assignment, misc]
    RasterMergeCapability = None  # type: ignore[assignment, misc]

# network : dépend de networkx (optionnel)
try:
    import gispulse.capabilities.network  # noqa: F401
    from gispulse.capabilities.network import (
        ConnectivityCheckCapability,
        IsochroneCapability,
        MinimumSpanningTreeCapability,
        NetworkAllocationCapability,
        ODMatrixCapability,
        ShortestPathCapability,
    )

    _NETWORK_AVAILABLE = True
except ImportError:
    _NETWORK_AVAILABLE = False
    IsochroneCapability = None  # type: ignore[assignment, misc]
    ShortestPathCapability = None  # type: ignore[assignment, misc]
    NetworkAllocationCapability = None  # type: ignore[assignment, misc]
    ConnectivityCheckCapability = None  # type: ignore[assignment, misc]
    ODMatrixCapability = None  # type: ignore[assignment, misc]
    MinimumSpanningTreeCapability = None  # type: ignore[assignment, misc]

__all__ = [
    "Capability",
    "REGISTRY",
    "get",
    "list_all",
    "register",
    # classification / choropleth
    "BivariateChoroplethCapability",
    "ChoroplethCapability",
    "ClassifyCapability",
    "ClassifyCategoricalCapability",
    "ContinuousRampCapability",
    "GraduatedSizeCapability",
    "HeadTailBreaksCapability",
    "NormalizeCapability",
    "build_graduated_style_def",
    "build_legend",
    # vector
    "AlphaShapeCapability",
    "AreaLengthCapability",
    "AssignProjectionCapability",
    "BoundaryCapability",
    "BufferCapability",
    "CalculateCapability",
    "CentroidCapability",
    "ChaikinSmoothCapability",
    "ClassifyByRingCapability",
    "ClipCapability",
    "ConcaveHullCapability",
    "ConvexHullCapability",
    "DelaunayTriangulationCapability",
    "DensifyVerticesCapability",
    "DissolveCapability",
    "EnvelopeCapability",
    "ExtractHolesCapability",
    "ExtractSegmentsCapability",
    "ExtractVerticesCapability",
    "FilterCapability",
    "ForceGeometryTypeCapability",
    "IntersectsCapability",
    "LineLocatePointCapability",
    "LineMergeCapability",
    "LineSubstringCapability",
    "MakeValidCapability",
    "MergeLayersCapability",
    "MinBoundingCircleCapability",
    "MultipartToSinglepartsCapability",
    "NearestNeighborCapability",
    "OffsetCurveCapability",
    "OrientedBBoxCapability",
    "PolygonizeCapability",
    "ReprojectCapability",
    "SimplifyCapability",
    "SinglepartsToMultipartCapability",
    "SnapToGridCapability",
    "SpatialAggregateCapability",
    "SpatialJoinCapability",
    "SymmetricDifferenceCapability",
    "UnionCapability",
    "VectorDiffCapability",
    "VoronoiPolygonsCapability",
    # schema / attribute manipulation
    "AddFieldCapability",
    "AttributeJoinCapability",
    "CaseWhenCapability",
    "CastFieldCapability",
    "CoalesceFieldsCapability",
    "DescribeCapability",
    "DropFieldCapability",
    "LookupTableCapability",
    "PivotCapability",
    "RenameFieldCapability",
    "SelectColumnsCapability",
    "UnpivotCapability",
    # selection / row-level
    "DeduplicateCapability",
    "RandomSampleCapability",
    "SortCapability",
    "TopNCapability",
    # overlay
    "EraseCapability",
    "OverlayIntersectionCapability",
    "OverlayUnionCapability",
    # geometry transforms
    "AddMCapability",
    "AddZCapability",
    "AffineTransformCapability",
    "DropMCapability",
    "DropZCapability",
    "ReverseLinesCapability",
    "SwapXYCapability",
    # temporal
    "TemporalFilterCapability",
    "TemporalJoinCapability",
    # pointcloud (load_las requires laspy; others are pure pandas/shapely)
    "PointcloudFilterClassificationCapability",
    "PointcloudGridSummaryCapability",
    "PointcloudLoadLasCapability",
    "PointcloudZonalHeightCapability",
    # spatial statistics
    "GetisOrdGStarCapability",
    "MoransICapability",
    "SpatialWeightsCapability",
    # density & tessellation
    "GridCreateCapability",
    "HexGridCreateCapability",
    "KDEHeatmapCapability",
    # network topology
    "ExtendDanglesCapability",
    "NodeLinesCapability",
    "RemoveDuplicateEdgesCapability",
    "RemovePseudoNodesCapability",
    "SnapEndpointsCapability",
    # polygon topology
    "FixGapsCapability",
    "FixOverlapsCapability",
    "RemoveSliversCapability",
    "SnapBordersCapability",
    # clustering (None si non disponible)
    "DBSCANClusterCapability",
    "HDBSCANClusterCapability",
    "KMeansClusterCapability",
    "_CLUSTERING_AVAILABLE",
    # validation (Community — toujours disponible)
    "TopologyCheckCapability",
    "DuplicateGeometryCapability",
    "AttributeValidationCapability",
    "CompletenessCheckCapability",
    # raster (None si non disponible)
    "ZonalStatsCapability",
    "ChangeDetectionCapability",
    "RasterClipCapability",
    "NdviCapability",
    "RasterReprojectCapability",
    "RasterMergeCapability",
    "_RASTER_AVAILABLE",
    # network (None si non disponible)
    "ShortestPathCapability",
    "IsochroneCapability",
    "NetworkAllocationCapability",
    "ConnectivityCheckCapability",
    "ODMatrixCapability",
    "MinimumSpanningTreeCapability",
    "_NETWORK_AVAILABLE",
    # postgis
    "PostGISSQLCapability",
]
