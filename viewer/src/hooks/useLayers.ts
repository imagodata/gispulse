import { useCallback, useEffect, useState } from "react";
import { fetchLayers } from "../api/client";
import type { LayerSummary } from "../api/types";

export interface LayerState extends LayerSummary {
  visible: boolean;
}

export function useLayers() {
  const [layers, setLayers] = useState<LayerState[]>([]);
  const [file, setFile] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchLayers()
      .then((data) => {
        setFile(data.file);
        setLayers(data.layers.map((l) => ({ ...l, visible: true })));
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const toggleLayer = useCallback((name: string) => {
    setLayers((prev) =>
      prev.map((l) => (l.name === name ? { ...l, visible: !l.visible } : l))
    );
  }, []);

  return { layers, file, loading, error, toggleLayer };
}
