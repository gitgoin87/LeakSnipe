import type { HandDetail } from "./api";

/** Map hand-history seat numbers to clockwise display slots with hero at bottom. */
export function buildSeatDisplayMap(hand: HandDetail): Record<number, number> {
  const seatKeys = Object.keys(hand.players)
    .map((k) => Number(k))
    .filter((n) => !Number.isNaN(n))
    .sort((a, b) => a - b);

  const n = seatKeys.length;
  if (n === 0) return {};

  let heroSeat = seatKeys.find(
    (seat) => hand.players[String(seat)]?.is_hero,
  );
  if (heroSeat === undefined) {
    heroSeat = seatKeys[0];
  }

  const heroIdx = seatKeys.indexOf(heroSeat);
  const map: Record<number, number> = {};
  for (const seat of seatKeys) {
    // ACR seat numbers rise clockwise around the table; with hero at bottom,
    // walk counter-clockwise through slots so the next seat number sits to hero's left.
    map[seat] = (heroIdx - seatKeys.indexOf(seat) + n) % n;
  }
  return map;
}

/** Normalized table coordinates; slot 0 (hero) sits at the bottom. */
export function seatFraction(
  seat: number,
  hand: HandDetail,
  displayMap: Record<number, number>,
): { x: number; y: number } {
  const n = Math.max(Object.keys(hand.players).length, 2);
  const slot = displayMap[seat] ?? 0;

  if (n === 2) {
    return slot === 0 ? { x: 0.5, y: 0.85 } : { x: 0.5, y: 0.18 };
  }

  const cx = 0.5;
  const cy = 0.48;
  const rx = 0.38;
  const ry = 0.34;
  const angle = -Math.PI / 2 + (2 * Math.PI * slot) / n;
  const fx = cx + rx * Math.cos(angle);
  const fy = cy - ry * Math.sin(angle);

  return {
    x: Math.max(0.1, Math.min(0.9, fx)),
    y: Math.max(0.12, Math.min(0.86, fy)),
  };
}
