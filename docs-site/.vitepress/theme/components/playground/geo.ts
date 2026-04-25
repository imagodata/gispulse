/**
 * Tiny dependency-free geometry helpers for the playground client-side DML
 * evaluator. Goal: answer "is the drawn polygon inside this zone?" well
 * enough for a demo, without pulling Turf (~200 kB min).
 *
 * Assumptions:
 *   - Coordinates are GeoJSON-style [lng, lat] in EPSG:4326.
 *   - Zones are small enough (city-scale) that planar ray-casting on lon/lat
 *     is visually indistinguishable from a proper geodesic test.
 *   - We compare the drawn polygon by its centroid, not full polygon-in-
 *     polygon intersection. This matches the "does the building sit inside
 *     the setback strip?" intent and stays O(V) on the zone instead of O(V*W).
 */

type Point = [number, number]
type Ring = Point[]

/** Live measurement label surfaced by the draw UI (area while polygon has
 *  3+ vertices, length while it is still an open polyline). */
export interface DrawMeasure {
  type: 'area' | 'length'
  text: string
}

/**
 * Shoelace centroid of a Polygon or first ring of a MultiPolygon — fine for
 * the convex-ish shapes users draw in the playground. Falls back to the
 * bounding-box center when the polygon is degenerate (zero area).
 */
export function polygonCentroid(geom: any): Point | null {
  if (!geom) return null
  let ring: Ring | null = null
  if (geom.type === 'Polygon' && Array.isArray(geom.coordinates?.[0])) {
    ring = geom.coordinates[0] as Ring
  } else if (geom.type === 'MultiPolygon' && Array.isArray(geom.coordinates?.[0]?.[0])) {
    ring = geom.coordinates[0][0] as Ring
  }
  if (!ring || ring.length < 3) return null

  let twiceArea = 0
  let cx = 0
  let cy = 0
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    const f = xi * yj - xj * yi
    twiceArea += f
    cx += (xi + xj) * f
    cy += (yi + yj) * f
  }
  if (twiceArea === 0) {
    // Degenerate ring — fall back to bbox center so we still return something.
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    for (const [x, y] of ring) {
      if (x < minX) minX = x; if (x > maxX) maxX = x
      if (y < minY) minY = y; if (y > maxY) maxY = y
    }
    return [(minX + maxX) / 2, (minY + maxY) / 2]
  }
  const k = 1 / (3 * twiceArea)
  return [cx * k, cy * k]
}

/**
 * Ray-casting point-in-ring test (standard even-odd rule). Handles the
 * horizontal-edge case by checking for strict y-straddle.
 */
function pointInRing(pt: Point, ring: Ring): boolean {
  const [x, y] = pt
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    const straddles = yi > y !== yj > y
    if (straddles) {
      const xIntersect = ((xj - xi) * (y - yi)) / (yj - yi) + xi
      if (x < xIntersect) inside = !inside
    }
  }
  return inside
}

/** Polygon = outer ring minus holes. */
function pointInPolygon(pt: Point, rings: Ring[]): boolean {
  if (!rings.length) return false
  if (!pointInRing(pt, rings[0])) return false
  // Any hole hit disqualifies the match.
  for (let i = 1; i < rings.length; i++) {
    if (pointInRing(pt, rings[i])) return false
  }
  return true
}

/**
 * Spherical polygon area in m^2 for a ring in [lng, lat] (EPSG:4326).
 * Uses the WGS84 equatorial radius; accurate enough for city-scale draws
 * (sub-percent error) and avoids pulling `@turf/area` (~40 kB) for a one-
 * shot live readout under the cursor.
 */
export function polygonAreaM2(ring: Point[]): number {
  if (ring.length < 3) return 0
  const R = 6378137 // WGS84 equatorial radius
  const toRad = (d: number) => (d * Math.PI) / 180
  let sum = 0
  for (let i = 0; i < ring.length; i++) {
    const [lng1, lat1] = ring[i]
    const [lng2, lat2] = ring[(i + 1) % ring.length]
    sum += toRad(lng2 - lng1) * (2 + Math.sin(toRad(lat1)) + Math.sin(toRad(lat2)))
  }
  return Math.abs((sum * R * R) / 2)
}

/** Haversine distance in meters between two [lng, lat] points. */
export function haversineM(a: Point, b: Point): number {
  const R = 6378137
  const toRad = (d: number) => (d * Math.PI) / 180
  const dLat = toRad(b[1] - a[1])
  const dLng = toRad(b[0] - a[0])
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(s))
}

/** Total length in meters of an open polyline [lng, lat][]. */
export function polylineLengthM(pts: Point[]): number {
  let total = 0
  for (let i = 1; i < pts.length; i++) total += haversineM(pts[i - 1], pts[i])
  return total
}

/** Human-friendly area string. Under 1 ha → m²; under 1 km² → ha; else km². */
export function formatArea(m2: number): string {
  if (!isFinite(m2) || m2 <= 0) return ''
  if (m2 < 10_000) return `${m2.toFixed(0)} m${String.fromCharCode(178)}`
  if (m2 < 1_000_000) return `${(m2 / 10_000).toFixed(2)} ha`
  return `${(m2 / 1_000_000).toFixed(2)} km${String.fromCharCode(178)}`
}

/** Human-friendly length string. Under 1 km → m; else km. */
export function formatLength(m: number): string {
  if (!isFinite(m) || m <= 0) return ''
  if (m < 1000) return `${m.toFixed(0)} m`
  return `${(m / 1000).toFixed(2)} km`
}

/**
 * Returns true iff `pt` sits inside any Polygon / MultiPolygon feature of
 * `geojson`. No spatial index — OK for the dissolved single-MultiPolygon
 * zones the build script produces (one feature per scenario).
 */
export function pointInGeoJSON(pt: Point, geojson: any): boolean {
  const features = geojson?.features ?? []
  for (const f of features) {
    const g = f?.geometry
    if (!g) continue
    if (g.type === 'Polygon') {
      if (pointInPolygon(pt, g.coordinates as Ring[])) return true
    } else if (g.type === 'MultiPolygon') {
      for (const poly of g.coordinates as Ring[][]) {
        if (pointInPolygon(pt, poly)) return true
      }
    }
  }
  return false
}

/**
 * Proper segment-segment intersection (a1→a2 vs b1→b2) using the standard
 * parametric orientation test. Returns true when the two open segments
 * cross; co-linear / touching-at-endpoint cases are treated as "no cross"
 * to avoid false positives where a building just kisses the zone edge.
 */
function segmentsIntersect(a1: Point, a2: Point, b1: Point, b2: Point): boolean {
  const d1x = a2[0] - a1[0], d1y = a2[1] - a1[1]
  const d2x = b2[0] - b1[0], d2y = b2[1] - b1[1]
  const denom = d1x * d2y - d1y * d2x
  if (denom === 0) return false // parallel / co-linear
  const dx = b1[0] - a1[0], dy = b1[1] - a1[1]
  const t = (dx * d2y - dy * d2x) / denom
  const u = (dx * d1y - dy * d1x) / denom
  return t > 0 && t < 1 && u > 0 && u < 1
}

/** True iff any edge of `ringA` crosses any edge of `ringB`. */
function ringsCross(ringA: Ring, ringB: Ring): boolean {
  for (let i = 0, ni = ringA.length; i < ni; i++) {
    const a1 = ringA[i]
    const a2 = ringA[(i + 1) % ni]
    for (let j = 0, nj = ringB.length; j < nj; j++) {
      const b1 = ringB[j]
      const b2 = ringB[(j + 1) % nj]
      if (segmentsIntersect(a1, a2, b1, b2)) return true
    }
  }
  return false
}

/**
 * Returns true iff the drawn geometry (`geom`) shares any area with, or sits
 * inside, any Polygon/MultiPolygon in `geojson`.
 *
 * - Point: classic point-in-polygon test against every zone polygon.
 * - Polygon/MultiPolygon: covers the three overlap cases relevant to the
 *   road-setback demo:
 *     1. Any draw vertex inside a zone polygon
 *     2. Any zone vertex inside the draw
 *     3. Any draw edge crossing any zone edge
 *   Case 1 catches "fully inside" and typical "overlap"; case 2 catches the
 *   symmetric "zone fully inside draw" (rare — zones are wider than
 *   hand-drawn buildings); case 3 catches edge-clip scenarios where no
 *   vertex of one is inside the other.
 *
 * Planar (lon/lat) — fine at city zoom.
 */
export function polygonIntersectsGeoJSON(geom: any, geojson: any): boolean {
  if (!geom || !geojson) return false

  const features = geojson.features ?? []

  if (geom.type === 'Point') {
    const pt = geom.coordinates as Point
    for (const f of features) {
      const g = f?.geometry
      if (!g) continue
      if (g.type === 'Polygon') {
        if (pointInPolygon(pt, g.coordinates as Ring[])) return true
      } else if (g.type === 'MultiPolygon') {
        for (const poly of g.coordinates as Ring[][][]) {
          if (pointInPolygon(pt, poly as Ring[])) return true
        }
      }
    }
    return false
  }

  const polyRings: Ring[] = []
  if (geom.type === 'Polygon') {
    polyRings.push(geom.coordinates[0] as Ring)
  } else if (geom.type === 'MultiPolygon') {
    for (const poly of geom.coordinates as Ring[][]) polyRings.push(poly[0])
  } else {
    return false
  }

  for (const f of features) {
    const g = f?.geometry
    if (!g) continue
    const zoneRings: Ring[][] =
      g.type === 'Polygon'
        ? [g.coordinates as Ring[]]
        : g.type === 'MultiPolygon'
          ? (g.coordinates as Ring[][][]).map((p) => p as Ring[])
          : []

    for (const zonePoly of zoneRings) {
      const zoneOuter = zonePoly[0]
      for (const drawRing of polyRings) {
        // 1. any draw vertex inside zone
        for (const pt of drawRing) {
          if (pointInPolygon(pt, zonePoly)) return true
        }
        // 2. any zone-outer vertex inside draw
        for (const pt of zoneOuter) {
          if (pointInRing(pt, drawRing)) return true
        }
        // 3. edges cross (draw outer ring vs zone outer ring)
        if (ringsCross(drawRing, zoneOuter)) return true
      }
    }
  }
  return false
}
