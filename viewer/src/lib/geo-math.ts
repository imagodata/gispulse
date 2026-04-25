/**
 * Lightweight geodesic math — replaces @turf/distance, @turf/area, @turf/helpers.
 *
 * Haversine distance and spherical excess area on WGS84 ellipsoid.
 */

const EARTH_RADIUS = 6371008.8; // mean earth radius in metres (same as Turf)

function toRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

/**
 * Haversine distance between two [lng, lat] points in metres.
 */
export function haversineDistance(
  a: [number, number],
  b: [number, number],
): number {
  const dLat = toRad(b[1] - a[1]);
  const dLon = toRad(b[0] - a[0]);
  const lat1 = toRad(a[1]);
  const lat2 = toRad(b[1]);

  const sinDLat = Math.sin(dLat / 2);
  const sinDLon = Math.sin(dLon / 2);
  const h = sinDLat * sinDLat + Math.cos(lat1) * Math.cos(lat2) * sinDLon * sinDLon;

  return 2 * EARTH_RADIUS * Math.asin(Math.sqrt(h));
}

/**
 * Geodesic area of a polygon ring (array of [lng, lat]) in square metres.
 * Uses the spherical excess formula (same approach as Turf/MapBox).
 */
export function geodesicArea(ring: [number, number][]): number {
  const len = ring.length;
  if (len < 3) return 0;

  let total = 0;
  for (let i = 0; i < len; i++) {
    const [lng1, lat1] = ring[i];
    const [lng2, lat2] = ring[(i + 1) % len];
    total += toRad(lng2 - lng1) * (2 + Math.sin(toRad(lat1)) + Math.sin(toRad(lat2)));
  }
  return Math.abs((total * EARTH_RADIUS * EARTH_RADIUS) / 2);
}
