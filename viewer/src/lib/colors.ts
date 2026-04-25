/** Color palette for layers. */
const PALETTE: [number, number, number, number][] = [
  [31, 119, 180, 180],   // blue
  [255, 127, 14, 180],   // orange
  [44, 160, 44, 180],    // green
  [214, 39, 40, 180],    // red
  [148, 103, 189, 180],  // purple
  [140, 86, 75, 180],    // brown
  [227, 119, 194, 180],  // pink
  [127, 127, 127, 180],  // gray
  [188, 189, 34, 180],   // olive
  [23, 190, 207, 180],   // cyan
];

export function getLayerColor(index: number): [number, number, number, number] {
  return PALETTE[index % PALETTE.length];
}
