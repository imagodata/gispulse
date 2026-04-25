/**
 * Shared formatters for feature popups across map engines (MapLibre & Leaflet).
 * Produces a consistent look in both clients and keeps formatting logic in one
 * place.
 */

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return '\u2014'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(3)
  return String(v)
}

function rowsHtml(props: Record<string, unknown>, limit: number): string {
  return Object.entries(props)
    .filter(([k]) => !k.startsWith('_') && k !== 'id')
    .slice(0, limit)
    .map(([k, v]) =>
      `<tr><td style="font-weight:600;padding:3px 10px 3px 0;color:#666;white-space:nowrap;font-size:12px">${k}</td>` +
      `<td style="padding:3px 0;font-size:12px">${formatValue(v)}</td></tr>`,
    )
    .join('')
}

/** Rich popup for MapLibre — branded header + full attribute table (up to 14 rows). */
export function featurePopupHtml(layerName: string, props: Record<string, unknown>, limit = 14): string {
  const body = rowsHtml(props, limit)
  return (
    `<div style="font-family:system-ui,-apple-system,sans-serif">` +
      `<div style="font-weight:700;margin-bottom:8px;font-size:13px;border-bottom:2px solid #3451b2;` +
      `padding-bottom:5px;color:#3451b2;text-transform:capitalize;letter-spacing:.02em">${layerName}</div>` +
      `<table style="border-collapse:collapse;width:100%">${body}</table>` +
    `</div>`
  )
}
