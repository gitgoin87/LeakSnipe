import type { HandDetail } from "./api";
import type { ReplayerStep } from "./replayerSteps";

export type ReplayerTableState = {
  foldedPlayers: Set<string>;
  streetBets: Map<string, number>;
  potInCenter: number;
  playerStacks: Map<string, number>;
  lastAction?: {
    actor: string;
    action: string;
    amount: number;
    delta: number;
  };
};

export type OpponentCardsMode = "auto" | "show" | "hide";

/** How to render hole cards for a seat (hero always faces up). */
export type HoleCardDisplay = "face" | "backs" | "none";

/**
 * Resolve opponent hole-card visibility.
 * - auto (default): backs during the hand, face-up at showdown when known
 * - show: face-up whenever showdown data exists, else backs
 * - hide: card backs for active players, nothing when folded
 */
export function resolveOpponentCardDisplay(opts: {
  isHero: boolean;
  isFolded: boolean;
  knownCardCount: number;
  stepType: ReplayerStep["type"];
  mode: OpponentCardsMode;
}): HoleCardDisplay {
  if (opts.isHero) return "face";

  if (opts.isFolded && opts.mode !== "show") return "none";

  const hasKnown = opts.knownCardCount > 0;

  if (opts.mode === "hide") return opts.isFolded ? "none" : "backs";

  if (opts.mode === "show") {
    return hasKnown ? "face" : "backs";
  }

  // auto — reveal only on the result / showdown step
  if (opts.stepType === "result" && hasKnown) return "face";
  return opts.isFolded ? "none" : "backs";
}

export function opponentCardsToggleLabel(mode: OpponentCardsMode): string {
  switch (mode) {
    case "show":
      return "Hide opponent cards";
    case "hide":
      return "Show opponent cards";
    case "auto":
      return "Always show opponent cards";
    default: {
      const _exhaustive: never = mode;
      return _exhaustive;
    }
  }
}

export function nextOpponentCardsMode(mode: OpponentCardsMode): OpponentCardsMode {
  switch (mode) {
    case "auto":
      return "show";
    case "show":
      return "hide";
    case "hide":
      return "auto";
    default: {
      const _exhaustive: never = mode;
      return _exhaustive;
    }
  }
}

/** Compute street bet after an action (BetACR: blinds set level, calls/bets add, raises set total). */
function nextStreetBet(prev: number, action: string, amount: number): number {
  const act = action.toLowerCase();
  if (amount <= 0 || act === "fold" || act === "check") return prev;
  if (act === "raise") return Math.max(prev, amount);
  if (act === "post") {
    // Antes add; blind posts (larger) set the street level.
    if (amount >= 400) return Math.max(prev, amount);
    return prev + amount;
  }
  if (act === "call" || act === "bet") return prev + amount;
  return prev;
}

/** Replay steps up to index and derive pot, per-street bets, stacks, folds. */
export function computeReplayerState(
  steps: ReplayerStep[],
  stepIdx: number,
  hand: HandDetail,
): ReplayerTableState {
  const foldedPlayers = new Set<string>();
  const streetBets = new Map<string, number>();
  const totalInvested = new Map<string, number>();
  let potCommitted = 0;
  let lastAction: ReplayerTableState["lastAction"];

  const startingStacks = new Map<string, number>();
  for (const info of Object.values(hand.players)) {
    startingStacks.set(info.name, info.stack ?? 0);
    totalInvested.set(info.name, 0);
  }

  const cappedIdx = Math.max(0, Math.min(stepIdx, steps.length - 1));

  for (let i = 0; i <= cappedIdx; i++) {
    const step = steps[i];
    if (!step) continue;

    if (step.type === "street") {
      for (const [, bet] of streetBets) {
        potCommitted += bet;
      }
      streetBets.clear();
      continue;
    }

    if (step.type !== "action" || !step.actor) continue;

    const actor = step.actor;
    const action = (step.action ?? "").toLowerCase();
    const amount = step.amount ?? 0;

    if (action === "fold") {
      foldedPlayers.add(actor);
      continue;
    }

    if (amount > 0 && action !== "check") {
      const prevBet = streetBets.get(actor) ?? 0;
      const newBet = nextStreetBet(prevBet, action, amount);
      const delta = newBet - prevBet;
      if (delta > 0) {
        streetBets.set(actor, newBet);
        totalInvested.set(actor, (totalInvested.get(actor) ?? 0) + delta);
        lastAction = { actor, action, amount, delta };
      }
    }
  }

  let potInStreet = 0;
  for (const bet of streetBets.values()) {
    potInStreet += bet;
  }

  const playerStacks = new Map<string, number>();
  for (const [name, start] of startingStacks) {
    playerStacks.set(name, Math.max(0, start - (totalInvested.get(name) ?? 0)));
  }

  return {
    foldedPlayers,
    streetBets,
    potInCenter: potCommitted + potInStreet,
    playerStacks,
    lastAction,
  };
}

/** Position bet chips midway between seat and table center (pot). */
export function betChipPosition(
  seatX: number,
  seatY: number,
  potX = 0.5,
  potY = 0.62,
  t = 0.42,
): { x: number; y: number } {
  return {
    x: seatX + (potX - seatX) * t,
    y: seatY + (potY - seatY) * t,
  };
}

/** Dealer button offset from seat — away from table center. */
export function dealerButtonPosition(
  seatX: number,
  seatY: number,
  centerX = 0.5,
  centerY = 0.48,
): { x: number; y: number } {
  const dx = seatX - centerX;
  const dy = seatY - centerY;
  const len = Math.hypot(dx, dy) || 1;
  const push = 0.08;
  return {
    x: Math.max(0.05, Math.min(0.95, seatX + (dx / len) * push)),
    y: Math.max(0.05, Math.min(0.92, seatY + (dy / len) * push - 0.06)),
  };
}
