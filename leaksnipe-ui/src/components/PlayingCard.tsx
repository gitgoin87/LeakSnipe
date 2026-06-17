const SUITS: Record<string, { sym: string; color: string }> = {
  s: { sym: "♠", color: "#1a1a2e" },
  h: { sym: "♥", color: "#cc2244" },
  d: { sym: "♦", color: "#cc2244" },
  c: { sym: "♣", color: "#1a1a2e" },
};

type PlayingCardProps = {
  card?: string;
  faceDown?: boolean;
  small?: boolean;
};

export function PlayingCard({ card, faceDown = false, small = false }: PlayingCardProps) {
  const w = small ? 36 : 50;
  const h = small ? 50 : 70;

  if (faceDown || !card) {
    return (
      <div
        className="playing-card face-down"
        style={{ width: w, height: h }}
        aria-hidden={faceDown}
      />
    );
  }

  const suitChar = card.slice(-1).toLowerCase();
  const rank = card.slice(0, -1);
  const suit = SUITS[suitChar] ?? { sym: "?", color: "#333" };

  return (
    <div className="playing-card" style={{ width: w, height: h }}>
      <span className="card-rank" style={{ color: suit.color }}>
        {rank}
      </span>
      <span className="card-suit-sm" style={{ color: suit.color }}>
        {suit.sym}
      </span>
      <span className="card-suit-lg" style={{ color: suit.color }}>
        {suit.sym}
      </span>
    </div>
  );
}

export function parseCardList(cards: string): string[] {
  return cards
    .trim()
    .split(/\s+/)
    .filter((c) => c.length >= 2);
}
