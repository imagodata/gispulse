import type { DrawMode } from "../hooks/useDraw";

interface Props {
  mode: DrawMode;
  activeLayer: string | null;
  availableLayers: string[];
  pointCount: number;
  onSetMode: (mode: DrawMode) => void;
  onSetActiveLayer: (name: string) => void;
  onFinish: () => void;
  onClear: () => void;
}

const modes: { key: DrawMode; label: string }[] = [
  { key: "point", label: "Point" },
  { key: "line", label: "Line" },
  { key: "polygon", label: "Polygon" },
  { key: "delete", label: "Delete" },
];

export function DrawToolbar({
  mode,
  activeLayer,
  availableLayers,
  pointCount,
  onSetMode,
  onSetActiveLayer,
  onFinish,
  onClear,
}: Props) {
  return (
    <div className="toolbar draw-toolbar">
      <span className="toolbar-label">Draw:</span>

      <select
        value={activeLayer || ""}
        onChange={(e) => onSetActiveLayer(e.target.value)}
        className="toolbar-select"
        title="Target layer"
      >
        <option value="" disabled>
          Layer...
        </option>
        {availableLayers.map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
      </select>

      {modes.map(({ key, label }) => (
        <button
          key={key}
          className={`toolbar-btn${mode === key ? " active" : ""}`}
          onClick={() => onSetMode(mode === key ? "none" : key)}
          disabled={!activeLayer && key !== "none"}
          title={label}
        >
          {label}
        </button>
      ))}

      {mode !== "none" && mode !== "point" && mode !== "delete" && pointCount > 0 && (
        <>
          <button className="toolbar-btn" onClick={onFinish} title="Complete shape">
            Finish ({pointCount} pts)
          </button>
          <button className="toolbar-btn" onClick={onClear} title="Discard current shape">
            Cancel
          </button>
        </>
      )}
    </div>
  );
}
