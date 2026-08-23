import type { Coordinates } from "@/lib/providers/types";

export type Turn = { pathIndex: number; direction: "left" | "right"; angleDeg: number };

// A real path has vertices at every geometry bend the graph recorded, not
// just at actual street corners — most vertex-to-vertex angle changes are
// noise (a slight road curve, not a turn a walker needs telling about).
// Only a bearing change past this threshold gets surfaced as an instruction.
const TURN_THRESHOLD_DEG = 30;

function bearingDeg(a: Coordinates, b: Coordinates): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const toDeg = (rad: number) => (rad * 180) / Math.PI;
  const y = Math.sin(toRad(b.lon - a.lon)) * Math.cos(toRad(b.lat));
  const x =
    Math.cos(toRad(a.lat)) * Math.sin(toRad(b.lat)) -
    Math.sin(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.cos(toRad(b.lon - a.lon));
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

function angleDiffDeg(from: number, to: number): number {
  // Signed difference in (-180, 180]: positive = turning right (clockwise).
  let diff = to - from;
  while (diff > 180) diff -= 360;
  while (diff <= -180) diff += 360;
  return diff;
}

// No street names exist in the routed path (see route-map.tsx's segment-tone
// comment — V1 has no per-segment geometry beyond raw coordinates), so this
// only ever reports "turn left/right in Xm", never a street name — an
// honest, real derivation from the actual path, not fabricated guidance.
export function computeTurns(path: Coordinates[]): Turn[] {
  if (path.length < 3) return [];
  const turns: Turn[] = [];
  for (let i = 1; i < path.length - 1; i++) {
    const inBearing = bearingDeg(path[i - 1], path[i]);
    const outBearing = bearingDeg(path[i], path[i + 1]);
    const diff = angleDiffDeg(inBearing, outBearing);
    if (Math.abs(diff) >= TURN_THRESHOLD_DEG) {
      turns.push({ pathIndex: i, direction: diff > 0 ? "right" : "left", angleDeg: Math.abs(diff) });
    }
  }
  return turns;
}
