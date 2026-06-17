import type { HandDetail } from "./api";
import { formatChips, formatChipsSigned } from "./chipFormat";

export type ReplayerStep = {
  type: "deal" | "street" | "action" | "result";
  text: string;
  board: string[];
  actor?: string;
  action?: string;
  amount?: number;
};

function formatActionText(
  hand: HandDetail,
  player: string,
  action: string,
  amount: number,
): string {
  const chipLabel = formatChips(amount, hand.is_tournament);
  if (amount > 0) {
    return `${player}: ${action}s ${chipLabel}`;
  }
  return `${player}: ${action}s`;
}

function formatResult(hand: HandDetail, won: number): string {
  return formatChipsSigned(won, hand.is_tournament);
}

export function buildReplayerSteps(hand: HandDetail): ReplayerStep[] {
  const steps: ReplayerStep[] = [];

  steps.push({
    type: "deal",
    text: `Deal — Hero: ${hand.hero_cards || "??"} (${hand.hero_position || "?"})`,
    board: [],
  });

  let boardSoFar: string[] = [];

  for (const street of hand.streets ?? []) {
    const streetName = street.name ?? "";
    const newCards = street.cards ?? [];
    boardSoFar = [...boardSoFar, ...newCards];

    if (newCards.length > 0) {
      steps.push({
        type: "street",
        text: `── ${streetName}: ${newCards.join(" ")} ──`,
        board: [...boardSoFar],
      });
    } else {
      steps.push({
        type: "street",
        text: `── ${streetName} ──`,
        board: [...boardSoFar],
      });
    }

    for (const act of street.actions ?? []) {
      const player = act.player ?? "?";
      const action = act.action ?? "?";
      const amount = act.amount ?? 0;
      const text = formatActionText(hand, player, action, amount);
      steps.push({
        type: "action",
        text,
        board: [...boardSoFar],
        actor: player,
        action,
        amount,
      });
    }
  }

  const won = hand.hero_won ?? 0;
  const pot = hand.pot ?? 0;
  const potLabel = hand.is_tournament
    ? `${formatChips(pot, true)} chips`
    : formatChips(pot, false);

  let resultText: string;
  if (won > 0) {
    resultText = `Result: Won ${formatResult(hand, won)} | Pot: ${potLabel}`;
  } else if (won < 0) {
    resultText = `Result: Lost ${formatResult(hand, Math.abs(won))} | Pot: ${potLabel}`;
  } else {
    resultText = `Result: Break even | Pot: ${potLabel}`;
  }

  steps.push({
    type: "result",
    text: resultText,
    board: [...boardSoFar],
  });

  return steps;
}

export function parseShownCards(rawText: string): Record<string, string> {
  const shown: Record<string, string> = {};
  const re = /([\w ]+?)\s+shows?\s+\[([^\]]+)\]/gi;
  let match: RegExpExecArray | null;
  while ((match = re.exec(rawText)) !== null) {
    shown[match[1].trim()] = match[2].trim();
  }
  return shown;
}
