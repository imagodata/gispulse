import { useCallback, useState } from "react";
import { createFeature, deleteFeature } from "../api/client";

export type DrawMode = "none" | "point" | "line" | "polygon" | "delete";

export interface DrawState {
  mode: DrawMode;
  points: [number, number][];
  activeLayer: string | null;
}

export function useDraw() {
  const [state, setState] = useState<DrawState>({
    mode: "none",
    points: [],
    activeLayer: null,
  });

  const setMode = useCallback((mode: DrawMode) => {
    setState((prev) => ({ ...prev, mode, points: [] }));
  }, []);

  const setActiveLayer = useCallback((name: string | null) => {
    setState((prev) => ({ ...prev, activeLayer: name }));
  }, []);

  const addPoint = useCallback(
    (lng: number, lat: number) => {
      setState((prev) => {
        if (prev.mode === "none" || prev.mode === "delete") return prev;

        const pts: [number, number][] = [...prev.points, [lng, lat]];

        // Point mode: create immediately
        if (prev.mode === "point" && prev.activeLayer) {
          createFeature(prev.activeLayer, {
            type: "Point",
            coordinates: [lng, lat],
          }).catch(console.error);
          return { ...prev, points: [] };
        }

        return { ...prev, points: pts };
      });
    },
    []
  );

  const finish = useCallback(() => {
    setState((prev) => {
      if (!prev.activeLayer || prev.points.length === 0) {
        return { ...prev, points: [] };
      }

      if (prev.mode === "line" && prev.points.length >= 2) {
        createFeature(prev.activeLayer, {
          type: "LineString",
          coordinates: prev.points,
        }).catch(console.error);
      }

      if (prev.mode === "polygon" && prev.points.length >= 3) {
        const closed = [...prev.points, prev.points[0]];
        createFeature(prev.activeLayer, {
          type: "Polygon",
          coordinates: [closed],
        }).catch(console.error);
      }

      return { ...prev, points: [] };
    });
  }, []);

  const handleDelete = useCallback(
    (layerName: string, fid: number) => {
      if (state.mode !== "delete") return;
      deleteFeature(layerName, fid).catch(console.error);
    },
    [state.mode]
  );

  const clear = useCallback(() => {
    setState((prev) => ({ ...prev, points: [] }));
  }, []);

  return {
    ...state,
    setMode,
    setActiveLayer,
    addPoint,
    finish,
    handleDelete,
    clear,
  };
}
