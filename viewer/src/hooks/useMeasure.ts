import { useCallback, useState } from "react";
import { haversineDistance, geodesicArea } from "../lib/geo-math";

export type MeasureMode = "none" | "distance" | "area";

export interface MeasureState {
  mode: MeasureMode;
  points: [number, number][];
  result: string;
}

export function useMeasure() {
  const [state, setState] = useState<MeasureState>({
    mode: "none",
    points: [],
    result: "",
  });

  const setMode = useCallback((mode: MeasureMode) => {
    setState({ mode, points: [], result: "" });
  }, []);

  const addPoint = useCallback(
    (lng: number, lat: number) => {
      setState((prev) => {
        if (prev.mode === "none") return prev;

        const pts: [number, number][] = [...prev.points, [lng, lat]];
        let result = "";

        if (prev.mode === "distance" && pts.length >= 2) {
          let total = 0;
          for (let i = 1; i < pts.length; i++) {
            total += haversineDistance(pts[i - 1], pts[i]);
          }
          if (total >= 1000) {
            result = `${(total / 1000).toFixed(2)} km`;
          } else {
            result = `${total.toFixed(1)} m`;
          }
        }

        if (prev.mode === "area" && pts.length >= 3) {
          const a = geodesicArea(pts);
          if (a >= 1_000_000) {
            result = `${(a / 1_000_000).toFixed(2)} km\u00B2`;
          } else if (a >= 10_000) {
            result = `${(a / 10_000).toFixed(2)} ha`;
          } else {
            result = `${a.toFixed(1)} m\u00B2`;
          }
        }

        return { ...prev, points: pts, result };
      });
    },
    []
  );

  const clear = useCallback(() => {
    setState((prev) => ({ ...prev, points: [], result: "" }));
  }, []);

  return { ...state, setMode, addPoint, clear };
}
