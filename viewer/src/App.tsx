import { useState, useCallback, Component, type ReactNode, type ErrorInfo } from "react";
import { useLayers } from "./hooks/useLayers";
import { useMeasure } from "./hooks/useMeasure";
import { useDraw } from "./hooks/useDraw";
import { MapView } from "./components/MapView";
import { LayerPanel } from "./components/LayerPanel";
import { MeasureToolbar } from "./components/MeasureToolbar";
import { DrawToolbar } from "./components/DrawToolbar";
import "./App.css";

class ViewerErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Viewer error:", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, textAlign: "center" }}>
          <h2>Something went wrong</h2>
          <pre style={{ color: "var(--gp-error)", whiteSpace: "pre-wrap", marginTop: 8 }}>
            {this.state.error.message}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            style={{ marginTop: 16, padding: "8px 16px", cursor: "pointer" }}
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function AppInner() {
  const { layers, file, loading, error, toggleLayer } = useLayers();
  const measure = useMeasure();
  const draw = useDraw();
  const [refreshCounter, setRefreshCounter] = useState(0);

  // When switching to draw mode, disable measure and vice versa
  const handleMeasureMode = useCallback(
    (mode: Parameters<typeof measure.setMode>[0]) => {
      draw.setMode("none");
      measure.setMode(mode);
    },
    [draw, measure]
  );

  const handleDrawMode = useCallback(
    (mode: Parameters<typeof draw.setMode>[0]) => {
      measure.setMode("none");
      draw.setMode(mode);
    },
    [draw, measure]
  );

  const handleMapClick = useCallback(
    (lng: number, lat: number) => {
      if (draw.mode !== "none") {
        draw.addPoint(lng, lat);
      } else {
        measure.addPoint(lng, lat);
      }
    },
    [draw, measure]
  );

  const handleDrawFinish = useCallback(() => {
    draw.finish();
    // Trigger a refresh so MapView reloads features
    setRefreshCounter((c) => c + 1);
  }, [draw]);

  const handleFeatureDelete = useCallback(
    (layerName: string, fid: number) => {
      draw.handleDelete(layerName, fid);
      setRefreshCounter((c) => c + 1);
    },
    [draw]
  );

  if (loading) {
    return <div className="loading">Loading layers...</div>;
  }

  if (error) {
    return <div className="error">Error: {error}</div>;
  }

  const layerNames = layers.map((l) => l.name);

  return (
    <div className="app" role="application" aria-label="GISPulse Viewer">
      <LayerPanel file={file} layers={layers} onToggle={toggleLayer} />

      <div className="main">
        <MeasureToolbar
          mode={measure.mode}
          result={measure.result}
          pointCount={measure.points.length}
          onSetMode={handleMeasureMode}
          onClear={measure.clear}
        />

        <DrawToolbar
          mode={draw.mode}
          activeLayer={draw.activeLayer}
          availableLayers={layerNames}
          pointCount={draw.points.length}
          onSetMode={handleDrawMode}
          onSetActiveLayer={draw.setActiveLayer}
          onFinish={handleDrawFinish}
          onClear={draw.clear}
        />

        <MapView
          layers={layers}
          measureMode={measure.mode}
          measurePoints={measure.points}
          drawMode={draw.mode}
          drawPoints={draw.points}
          onMapClick={handleMapClick}
          onFeatureClick={useCallback(() => {}, [])}
          onFeatureDelete={handleFeatureDelete}
          refreshCounter={refreshCounter}
        />
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ViewerErrorBoundary>
      <AppInner />
    </ViewerErrorBoundary>
  );
}
