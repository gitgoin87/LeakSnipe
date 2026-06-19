import { useCallback, useEffect, useMemo, useState } from "react";
import type { HandDetail } from "../lib/api";
import { formatChips } from "../lib/chipFormat";
import {
  betChipPosition,
  computeReplayerState,
  dealerButtonPosition,
  nextOpponentCardsMode,
  opponentCardsToggleLabel,
  resolveOpponentCardDisplay,
  type OpponentCardsMode,
} from "../lib/replayerState";
import { buildReplayerSteps, parseShownCards, type ReplayerStep } from "../lib/replayerSteps";
import { buildSeatDisplayMap, seatFraction } from "../lib/seatLayout";
import { parseCardList, PlayingCard } from "./PlayingCard";
import { OpponentHudPanel } from "./OpponentHud";

type HandReplayerProps = {
  hand: HandDetail;
  onClose?: () => void;
};

function ChipStack({ amount, isTournament, compact }: { amount: number; isTournament: boolean; compact?: boolean }) {
  if (amount <= 0) return null;
  const stacks = Math.min(5, Math.max(1, Math.round(Math.log10(amount + 1))));
  return (
    <div className={`chip-stack ${compact ? "compact" : ""}`} aria-hidden>
      {Array.from({ length: stacks }).map((_, i) => (
        <span key={i} className="chip-disc" style={{ bottom: `${i * 3}px` }} />
      ))}
      <span className="chip-amount">{formatChips(amount, isTournament)}</span>
    </div>
  );
}

export function HandReplayer({ hand, onClose }: HandReplayerProps) {
  const steps = useMemo(() => buildReplayerSteps(hand), [hand]);
  const displayMap = useMemo(() => buildSeatDisplayMap(hand), [hand]);
  const shownCards = useMemo(() => parseShownCards(hand.raw_text ?? ""), [hand.raw_text]);

  const [stepIdx, setStepIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [potPulse, setPotPulse] = useState(false);
  const [opponentCardsMode, setOpponentCardsMode] = useState<OpponentCardsMode>("auto");

  const tableState = useMemo(
    () => computeReplayerState(steps, stepIdx, hand),
    [steps, stepIdx, hand],
  );

  const current: ReplayerStep = steps[stepIdx] ?? steps[0];

  useEffect(() => {
    setStepIdx(0);
    setPlaying(false);
    setOpponentCardsMode("auto");
  }, [hand.hand_id]);

  useEffect(() => {
    setPotPulse(true);
    const t = window.setTimeout(() => setPotPulse(false), 350);
    return () => window.clearTimeout(t);
  }, [tableState.potInCenter, stepIdx]);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      setStepIdx((idx) => {
        if (idx >= steps.length - 1) {
          setPlaying(false);
          return idx;
        }
        return idx + 1;
      });
    }, 900);
    return () => window.clearInterval(timer);
  }, [playing, steps.length]);

  const prev = useCallback(() => setStepIdx((i) => Math.max(0, i - 1)), []);
  const next = useCallback(
    () => setStepIdx((i) => Math.min(steps.length - 1, i + 1)),
    [steps.length],
  );

  const heroCards = parseCardList(hand.hero_cards);
  const board = current.board ?? [];
  const isTournament = hand.is_tournament;

  const seatEntries = Object.entries(hand.players)
    .map(([seat, info]) => ({ seat: Number(seat), info }))
    .filter((e) => !Number.isNaN(e.seat))
    .sort((a, b) => a.seat - b.seat);

  const opponentNames = seatEntries
    .filter(({ info }) => !info.is_hero)
    .map(({ info }) => info.name);

  const buttonSeat = hand.button_seat;
  const buttonPos =
    buttonSeat && hand.players[String(buttonSeat)]
      ? dealerButtonPosition(
          ...(() => {
            const f = seatFraction(buttonSeat, hand, displayMap);
            return [f.x, f.y] as const;
          })(),
        )
      : null;

  const potY = 0.62;

  return (
    <div className="replayer-shell">
      {onClose ? (
        <button className="replayer-close" type="button" onClick={onClose} aria-label="Close replayer">
          ✕
        </button>
      ) : null}

      <div className="replayer-meta">
        <span className="mono">{hand.hand_id}</span>
        <span>{hand.site}</span>
        <span>{hand.table_name || hand.game_type}</span>
        {isTournament ? <span className="tourney-badge">Tournament</span> : null}
      </div>

      <OpponentHudPanel names={opponentNames} compact title="Table HUD" />

      <div className="replayer-table-wrap">
        <div className="replayer-table">
          <div className="table-oval" />
          <div className="table-felt-glow" />

          <div className={`pot-display ${potPulse ? "pot-pulse" : ""}`}>
            <ChipStack amount={tableState.potInCenter} isTournament={isTournament} />
            <div className="pot-label">
              POT{" "}
              <strong>{formatChips(tableState.potInCenter, isTournament)}</strong>
            </div>
          </div>

          <div className="board-row">
            {board.length > 0
              ? board.map((c, i) => <PlayingCard key={`${c}-${i}`} card={c} />)
              : Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="board-slot" />
                ))}
          </div>

          {buttonPos ? (
            <div
              className="dealer-button"
              style={{ left: `${buttonPos.x * 100}%`, top: `${buttonPos.y * 100}%` }}
              title={`Dealer — seat ${buttonSeat}`}
            >
              D
            </div>
          ) : null}

          {seatEntries.map(({ seat, info }) => {
            const pos = seatFraction(seat, hand, displayMap);
            const isFolded = tableState.foldedPlayers.has(info.name);
            const isActor = current.actor === info.name;
            const streetBet = tableState.streetBets.get(info.name) ?? 0;
            const stack = tableState.playerStacks.get(info.name) ?? info.stack ?? 0;
            const betPos = betChipPosition(pos.x, pos.y, 0.5, potY);
            const cards =
              info.is_hero
                ? heroCards
                : parseCardList(shownCards[info.name] ?? "");
            const cardDisplay = resolveOpponentCardDisplay({
              isHero: Boolean(info.is_hero),
              isFolded,
              knownCardCount: cards.length,
              stepType: current.type,
              mode: opponentCardsMode,
            });

            return (
              <div key={seat}>
                {streetBet > 0 && !isFolded ? (
                  <div
                    className={`seat-bet-chips ${isActor ? "acting-bet" : ""}`}
                    style={{ left: `${betPos.x * 100}%`, top: `${betPos.y * 100}%` }}
                  >
                    <ChipStack amount={streetBet} isTournament={isTournament} compact />
                  </div>
                ) : null}

                <div
                  className={`seat-node ${info.is_hero ? "hero" : ""} ${isFolded ? "folded" : ""} ${isActor ? "acting" : ""}`}
                  style={{ left: `${pos.x * 100}%`, top: `${pos.y * 100}%` }}
                >
                  <div className="seat-avatar">
                    <span className="seat-num">{seat}</span>
                  </div>
                  <div className="seat-name" title={info.name}>
                    {info.name}
                  </div>
                  <div className="seat-stack">
                    {formatChips(stack, isTournament)}
                  </div>
                  {cardDisplay === "face" ? (
                    <div className="seat-cards">
                      {cards.length > 0
                        ? cards.map((c, i) => <PlayingCard key={i} card={c} small />)
                        : (
                          <>
                            <PlayingCard faceDown small />
                            <PlayingCard faceDown small />
                          </>
                        )}
                    </div>
                  ) : cardDisplay === "backs" ? (
                    <div className="seat-cards">
                      <PlayingCard faceDown small />
                      <PlayingCard faceDown small />
                    </div>
                  ) : isFolded ? (
                    <div className="seat-folded-label">Fold</div>
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div
        className={`action-banner ${current.type === "street" ? "street" : ""} ${current.type === "result" ? "result" : ""}`}
      >
        {current.text}
        {current.type === "action" && tableState.lastAction?.delta ? (
          <span className="action-chip-fly">
            → {formatChips(tableState.lastAction.delta, isTournament)} to pot
          </span>
        ) : null}
      </div>

      <div className="replayer-controls">
        <button type="button" onClick={prev} disabled={stepIdx === 0}>
          ◀ Prev
        </button>
        <span className="step-indicator">
          {stepIdx + 1} / {steps.length}
        </span>
        <button type="button" onClick={next} disabled={stepIdx >= steps.length - 1}>
          Next ▶
        </button>
        <button
          type="button"
          className="play-btn"
          onClick={() => setPlaying((p) => !p)}
        >
          {playing ? "⏸ Pause" : "▶▶ Play"}
        </button>
        <button
          type="button"
          className={`opponent-cards-toggle ${opponentCardsMode}`}
          onClick={() => setOpponentCardsMode((m) => nextOpponentCardsMode(m))}
          title={
            opponentCardsMode === "auto"
              ? "Auto: reveal opponent cards at showdown only"
              : opponentCardsMode === "show"
                ? "Showing all known opponent cards"
                : "Hiding opponent cards"
          }
        >
          <span className="toggle-icon" aria-hidden>
            {opponentCardsMode === "hide" ? "🂠" : "🂡"}
          </span>
          {opponentCardsToggleLabel(opponentCardsMode)}
        </button>
        {opponentCardsMode === "auto" ? (
          <span className="opponent-cards-hint">Showdown only</span>
        ) : null}
      </div>
    </div>
  );
}
