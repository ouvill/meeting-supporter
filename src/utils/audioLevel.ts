/**
 * Convert a raw audio peak amplitude in [0, 1] to a percentage
 * for visual level bars. The scale is intentionally generous
 * (×350 of the raw value) to make low-level signals clearly visible.
 */
export function levelToPercent(level: number): number {
  return Math.min(level * 350, 100);
}
