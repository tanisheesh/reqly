import { ReleaseMarker } from "./api/client";

export interface PlacedMarker {
  /** The x-axis category (bucket label) the marker is drawn at. */
  x: string;
  release: string;
}

/**
 * Charts use a categorical x-axis (one label per bucket), so a deploy can only
 * be drawn at a bucket that exists: the first bucket at or after the time the
 * release was first seen. Deploys after the last bucket aren't drawn.
 */
export function placeReleaseMarkers(
  points: { bucket: string; label: string }[],
  releases: ReleaseMarker[]
): PlacedMarker[] {
  const placed: PlacedMarker[] = [];
  for (const r of releases) {
    const firstSeen = new Date(r.first_seen_at).getTime();
    const point = points.find((p) => new Date(p.bucket).getTime() >= firstSeen);
    if (point) placed.push({ x: point.label, release: r.release });
  }
  return placed;
}

export const RELEASE_MARKER_COLOR = "#a78bfa";
