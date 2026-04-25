import type { LayerState } from "../hooks/useLayers";
import { getLayerColor } from "../lib/colors";

interface Props {
  file: string;
  layers: LayerState[];
  onToggle: (name: string) => void;
}

export function LayerPanel({ file, layers, onToggle }: Props) {
  return (
    <div className="layer-panel">
      <h3>Layers</h3>
      <div className="file-name" title={file}>
        {file.split("/").pop()}
      </div>
      <ul>
        {layers.map((layer, i) => {
          const color = getLayerColor(i);
          return (
            <li key={layer.name} className="layer-item">
              <label>
                <input
                  type="checkbox"
                  checked={layer.visible}
                  onChange={() => onToggle(layer.name)}
                />
                <span
                  className="layer-swatch"
                  style={{
                    backgroundColor: `rgba(${color[0]},${color[1]},${color[2]},0.8)`,
                  }}
                />
                <span className="layer-name">{layer.name}</span>
                <span className="layer-count">
                  {layer.feature_count.toLocaleString()}
                  {layer.geometry_type ? ` · ${layer.geometry_type}` : ""}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
