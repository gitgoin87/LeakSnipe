export type StatTone = "good" | "warn" | "bad" | "neutral";

export function statTone(stat: string, value: number): StatTone {
  if (!value) return "neutral";
  if (stat === "vpip") {
    if (value >= 15 && value <= 28) return "good";
    if ((value >= 10 && value < 15) || (value > 28 && value <= 35)) return "warn";
    return "bad";
  }
  if (stat === "pfr") {
    if (value >= 10 && value <= 22) return "good";
    if ((value >= 8 && value < 10) || (value > 22 && value <= 30)) return "warn";
    return "bad";
  }
  if (stat === "af") {
    if (value >= 1.5 && value <= 4) return "good";
    if ((value >= 1 && value < 1.5) || (value > 4 && value <= 6)) return "warn";
    return "bad";
  }
  if (stat === "three_bet") {
    if (value >= 6 && value <= 12) return "good";
    if ((value >= 4 && value < 6) || (value > 12 && value <= 18)) return "warn";
    return value > 0 ? "bad" : "neutral";
  }
  if (stat === "wtsd") {
    if (value >= 25 && value <= 35) return "good";
    if ((value >= 20 && value < 25) || (value > 35 && value <= 45)) return "warn";
    return "bad";
  }
  if (stat === "fold_cbet") {
    if (value >= 55) return "good";
    if (value >= 30) return "warn";
    return "bad";
  }
  return "neutral";
}

export function typeClass(playerType: string): string {
  const key = playerType.toLowerCase().replace(/\s+/g, "-");
  return `hud-type-${key}`;
}

export function formatStat(value: number, suffix = ""): string {
  if (!value) return "–";
  return suffix ? `${Math.round(value)}${suffix}` : value.toFixed(1);
}
