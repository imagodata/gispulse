import { useEffect, useRef, useState } from "react";
import { fetchFeatures } from "../api/client";
import type { GeoJSONFeatureCollection } from "../api/types";

/** Debounce delay for viewport changes (ms). */
const DEBOUNCE_MS = 300;

interface UseFeaturesOpts {
  name: string;
  visible: boolean;
  bbox?: [number, number, number, number];
  simplify?: number;
}

export function useFeatures({ name, visible, bbox, simplify }: UseFeaturesOpts) {
  const [data, setData] = useState<GeoJSONFeatureCollection | null>(null);
  const [loading, setLoading] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (!visible) {
      setData(null);
      return;
    }

    // Debounce viewport changes
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      setLoading(true);
      fetchFeatures(name, { bbox, simplify, limit: 10000 })
        .then(setData)
        .catch((e) => console.error(`Failed to load ${name}:`, e))
        .finally(() => setLoading(false));
    }, DEBOUNCE_MS);

    return () => clearTimeout(timerRef.current);
  }, [name, visible, bbox?.join(","), simplify]);

  return { data, loading };
}
