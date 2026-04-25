import { useCallback, useEffect, useMemo, useState } from "react";
import { DeckGL } from "@deck.gl/react";
import { GeoJsonLayer, ScatterplotLayer, PathLayer, PolygonLayer, BitmapLayer } from "@deck.gl/layers";
import { TileLayer } from "@deck.gl/geo-layers";
import type { LayerState } from "../hooks/useLayers";
import type { GeoJSONFeatureCollection } from "../api/types";
import type { MeasureMode } from "../hooks/useMeasure";
import type { DrawMode } from "../hooks/useDraw";
import { getLayerColor } from "../lib/colors";
import { fetchFeatures } from "../api/client";

const INITIAL_VIEW = {
  longitude: 0,
  latitude: 0,
  zoom: 2,
  pitch: 0,
  bearing: 0,
};

interface Props {
  layers: LayerState[];
  measureMode: MeasureMode;
  measurePoints: [number, number][];
  drawMode: DrawMode;
  drawPoints: [number, number][];
  onMapClick: (lng: number, lat: number) => void;
  onFeatureClick: (properties: Record<string, unknown>) => void;
  onFeatureDelete?: (layerName: string, fid: number) => void;
  refreshCounter?: number;
}

export function MapView({
  layers,
  measureMode,
  measurePoints,
  drawMode,
  drawPoints,
  onMapClick,
  onFeatureClick,
  onFeatureDelete,
  refreshCounter = 0,
}: Props) {
  const [viewState, setViewState] = useState(INITIAL_VIEW);
  const [featureData, setFeatureData] = useState<
    Record<string, GeoJSONFeatureCollection>
  >({});
  const [hasInternet] = useState(() => navigator.onLine);

  // Fly to data extent on first load
  useEffect(() => {
    if (layers.length === 0) return;
    // Use first visible layer's bbox
    const visible = layers.find((l) => l.visible) || layers[0];
    const [minx, miny, maxx, maxy] = visible.bbox;
    if (minx === maxx && miny === maxy) return;
    setViewState({
      ...INITIAL_VIEW,
      longitude: (minx + maxx) / 2,
      latitude: (miny + maxy) / 2,
      zoom: 10,
    });
  }, [layers.length > 0]);

  // Load features for visible layers (re-fetches on refreshCounter change)
  useEffect(() => {
    const visibleLayers = layers.filter((l) => l.visible);
    for (const layer of visibleLayers) {
      fetchFeatures(layer.name, { limit: 10000 }).then((data) => {
        setFeatureData((prev) => ({ ...prev, [layer.name]: data }));
      });
    }
  }, [layers.map((l) => `${l.name}:${l.visible}`).join(","), refreshCounter]);

  // Build deck.gl layers
  const deckLayers = useMemo(() => {
    const result: any[] = [];

    // Basemap tile layer
    if (hasInternet) {
      result.push(
        new TileLayer({
          id: "basemap",
          data: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
          minZoom: 0,
          maxZoom: 19,
          tileSize: 256,
          renderSubLayers: (props: any) => {
            const { boundingBox } = props.tile;
            return new BitmapLayer(props, {
              data: undefined,
              image: props.data,
              bounds: [
                boundingBox[0][0],
                boundingBox[0][1],
                boundingBox[1][0],
                boundingBox[1][1],
              ],
            });
          },
        })
      );
    }

    // Data layers
    layers.forEach((layer, i) => {
      if (!layer.visible || !featureData[layer.name]) return;
      const color = getLayerColor(i);
      result.push(
        new GeoJsonLayer({
          id: `data-${layer.name}`,
          data: featureData[layer.name] as any,
          pickable: true,
          stroked: true,
          filled: true,
          getFillColor: color,
          getLineColor: [color[0], color[1], color[2], 220],
          getLineWidth: 2,
          getPointRadius: 6,
          pointRadiusUnits: "pixels",
          lineWidthUnits: "pixels",
          onClick: (info: any) => {
            if (info.object?.properties) {
              onFeatureClick(info.object.properties);
            }
          },
        })
      );
    });

    // Draw overlay
    if (drawPoints.length > 0) {
      result.push(
        new ScatterplotLayer({
          id: "draw-points",
          data: drawPoints.map((p) => ({ position: p })),
          getPosition: (d: any) => d.position,
          getRadius: 5,
          radiusUnits: "pixels",
          getFillColor: [0, 120, 255, 220],
        })
      );

      if (drawPoints.length >= 2) {
        result.push(
          new PathLayer({
            id: "draw-line",
            data: [{ path: drawPoints }],
            getPath: (d: any) => d.path,
            getColor: [0, 120, 255, 180],
            getWidth: 2,
            widthUnits: "pixels",
          })
        );
      }

      if (drawMode === "polygon" && drawPoints.length >= 3) {
        result.push(
          new PolygonLayer({
            id: "draw-fill",
            data: [{ polygon: [...drawPoints, drawPoints[0]] }],
            getPolygon: (d: any) => d.polygon,
            getFillColor: [0, 120, 255, 40],
            getLineColor: [0, 120, 255, 0],
          })
        );
      }
    }

    // Measure overlay
    if (measurePoints.length > 0) {
      // Points
      result.push(
        new ScatterplotLayer({
          id: "measure-points",
          data: measurePoints.map((p) => ({ position: p })),
          getPosition: (d: any) => d.position,
          getRadius: 5,
          radiusUnits: "pixels",
          getFillColor: [255, 0, 0, 200],
        })
      );

      // Lines
      if (measurePoints.length >= 2) {
        const path =
          measureMode === "area" && measurePoints.length >= 3
            ? [...measurePoints, measurePoints[0]]
            : measurePoints;
        result.push(
          new PathLayer({
            id: "measure-line",
            data: [{ path }],
            getPath: (d: any) => d.path,
            getColor: [255, 0, 0, 180],
            getWidth: 2,
            widthUnits: "pixels",
          })
        );
      }

      // Area fill
      if (measureMode === "area" && measurePoints.length >= 3) {
        result.push(
          new PolygonLayer({
            id: "measure-fill",
            data: [{ polygon: [...measurePoints, measurePoints[0]] }],
            getPolygon: (d: any) => d.polygon,
            getFillColor: [255, 0, 0, 40],
            getLineColor: [255, 0, 0, 0],
          })
        );
      }
    }

    return result;
  }, [layers, featureData, hasInternet, measurePoints, measureMode, drawPoints, drawMode]);

  const handleClick = useCallback(
    (info: any) => {
      // Draw mode: delete on feature click, or add point on map click
      if (drawMode === "delete" && info.object?.properties && onFeatureDelete) {
        // Find the feature index from the picked info
        const fid = info.index ?? 0;
        const layerId = info.layer?.id ?? "";
        const layerName = layerId.replace("data-", "");
        onFeatureDelete(layerName, fid);
        return;
      }
      if (drawMode !== "none" && drawMode !== "delete" && info.coordinate) {
        onMapClick(info.coordinate[0], info.coordinate[1]);
        return;
      }
      if (measureMode !== "none" && info.coordinate) {
        onMapClick(info.coordinate[0], info.coordinate[1]);
      }
    },
    [measureMode, drawMode, onMapClick, onFeatureDelete]
  );

  return (
    <div className="map-container">
      <DeckGL
        viewState={viewState}
        onViewStateChange={({ viewState }: any) => setViewState(viewState)}
        controller={true}
        layers={deckLayers}
        onClick={handleClick}
        getCursor={({ isDragging }: any) =>
          drawMode !== "none"
            ? drawMode === "delete"
              ? "not-allowed"
              : "crosshair"
            : measureMode !== "none"
              ? "crosshair"
              : isDragging
                ? "grabbing"
                : "grab"
        }
        style={{ background: hasInternet ? "#fff" : "#e8e8e8" }}
      />
    </div>
  );
}
