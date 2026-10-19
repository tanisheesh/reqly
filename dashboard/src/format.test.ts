import { describe, expect, it } from "vitest";

import { formatMs, formatPercent, formatUsd, spansMultipleDays } from "./format";
import { placeReleaseMarkers } from "./releaseMarkers";

describe("formatters", () => {
  it("show a dash for missing values", () => {
    expect(formatPercent(null)).toBe("—");
    expect(formatMs(undefined)).toBe("—");
    expect(formatUsd(null)).toBe("—");
  });

  it("format rates, latencies and costs", () => {
    expect(formatPercent(0.0912)).toBe("9.1%");
    expect(formatMs(41.6)).toBe("42ms");
    expect(formatUsd(0)).toBe("$0");
    expect(formatUsd(0.00042)).toBe("$0.0004");
    expect(formatUsd(12.345)).toBe("$12.35");
    expect(formatUsd(1234.5)).toBe(`$${(1235).toLocaleString()}`);
  });

  it("know when an axis spans more than one day", () => {
    expect(spansMultipleDays([])).toBe(false);
    expect(spansMultipleDays(["2026-10-10T10:00:00", "2026-10-10T11:00:00"])).toBe(false);
    expect(spansMultipleDays(["2026-10-10T10:00:00", "2026-10-11T10:00:00"])).toBe(true);
  });
});

describe("placeReleaseMarkers", () => {
  const points = [
    { bucket: "2026-10-10T10:00:00Z", label: "10:00" },
    { bucket: "2026-10-10T10:05:00Z", label: "10:05" },
    { bucket: "2026-10-10T10:10:00Z", label: "10:10" },
  ];

  it("draws a deploy at the first bucket at or after it", () => {
    const placed = placeReleaseMarkers(points, [
      { release: "v1", first_seen_at: "2026-10-10T10:00:00Z" },
      { release: "v2", first_seen_at: "2026-10-10T10:03:00Z" },
    ]);
    expect(placed).toEqual([
      { x: "10:00", release: "v1" },
      { x: "10:05", release: "v2" },
    ]);
  });

  it("leaves out deploys after the last bucket", () => {
    expect(placeReleaseMarkers(points, [{ release: "v3", first_seen_at: "2026-10-10T11:00:00Z" }])).toEqual([]);
  });
});
