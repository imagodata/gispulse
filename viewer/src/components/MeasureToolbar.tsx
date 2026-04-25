import type { MeasureMode } from "../hooks/useMeasure";

interface Props {
  mode: MeasureMode;
  result: string;
  pointCount: number;
  onSetMode: (mode: MeasureMode) => void;
  onClear: () => void;
}

export function MeasureToolbar({ mode, result, pointCount, onSetMode, onClear }: Props) {
  return (
    <div className="measure-toolbar">
      <button
        className={mode === "distance" ? "active" : ""}
        onClick={() => onSetMode(mode === "distance" ? "none" : "distance")}
        title="Measure distance"
      >
        Dist
      </button>
      <button
        className={mode === "area" ? "active" : ""}
        onClick={() => onSetMode(mode === "area" ? "none" : "area")}
        title="Measure area"
      >
        Area
      </button>
      {pointCount > 0 && (
        <button onClick={onClear} title="Clear measurement">
          Clear
        </button>
      )}
      {result && <span className="measure-result">{result}</span>}
      {mode !== "none" && !result && (
        <span className="measure-hint">Click on map to add points</span>
      )}
    </div>
  );
}
