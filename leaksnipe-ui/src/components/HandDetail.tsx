import { useState } from "react";
import { api, type AiAnalysis, type HandDetail } from "../lib/api";
import { HandReplayer } from "./HandReplayer";
import { parseCardList, PlayingCard } from "./PlayingCard";

export type PositionContext = {
  vpip: number;
  pfr: number;
  total: number;
};

type HandDetailPanelProps = {
  hand?: HandDetail | null;
  onClose: () => void;
  onOpenReplayer: () => void;
  positionStats?: PositionContext | null;
  sessionVpip?: number;
  sessionPfr?: number;
  loading?: boolean;
};

function formatWon(amount: number, isTournament: boolean) {
  if (isTournament) return `${amount >= 0 ? "+" : ""}${amount.toLocaleString()} chips`;
  return `${amount >= 0 ? "+" : ""}$${amount.toFixed(2)}`;
}

export function HandDetailPanel({
  hand,
  onClose,
  onOpenReplayer,
  positionStats,
  sessionVpip,
  sessionPfr,
  loading,
}: HandDetailPanelProps) {
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<AiAnalysis | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);

  if (loading || !hand) {
    return (
      <div className="detail-panel detail-panel-loading">
        <div className="detail-skeleton" />
        <p className="muted">Loading hand…</p>
      </div>
    );
  }

  const runAi = async () => {
    setAnalyzing(true);
    setAiError(null);
    try {
      const res = await api.analyzeHand(hand.hand_id);
      setAnalysis(res.analysis);
    } catch (err) {
      setAiError(err instanceof Error ? err.message : "AI analysis failed");
    } finally {
      setAnalyzing(false);
    }
  };

  const heroCards = parseCardList(hand.hero_cards);

  return (
    <div className="detail-panel">
      <div className="detail-header">
        <div>
          <h2 className="detail-title">Hand Stats</h2>
          <p className="detail-sub mono">{hand.hand_id}</p>
        </div>
        <button type="button" className="ghost-btn" onClick={onClose} aria-label="Close panel">
          ✕
        </button>
      </div>

      <button type="button" className="replay-hero-btn" onClick={onOpenReplayer}>
        <span className="replay-hero-icon" aria-hidden>▶</span>
        Replay Hand
      </button>

      <div className="detail-grid">
        <div className="detail-card">
          <div className="detail-label">Site / Table</div>
          <div>
            {hand.site} · {hand.table_name || "—"}
          </div>
        </div>
        <div className="detail-card">
          <div className="detail-label">Position</div>
          <div className="detail-value-emphasis">{hand.hero_position || "—"}</div>
          {positionStats ? (
            <div className="detail-context muted">
              {hand.hero_position} stats: VPIP {positionStats.vpip}% · PFR {positionStats.pfr}%
              <span className="detail-context-sub"> ({positionStats.total} hands)</span>
            </div>
          ) : null}
        </div>
        <div className="detail-card">
          <div className="detail-label">Result</div>
          <div className={hand.hero_won >= 0 ? "positive detail-value-emphasis" : "negative detail-value-emphasis"}>
            {formatWon(hand.hero_won, hand.is_tournament)}
          </div>
        </div>
        <div className="detail-card">
          <div className="detail-label">Pot</div>
          <div>
            {hand.is_tournament
              ? `${hand.pot.toLocaleString()} chips`
              : `$${hand.pot.toFixed(2)}`}
          </div>
        </div>
      </div>

      {sessionVpip != null ? (
        <div className="session-context-bar">
          Session: VPIP <strong>{sessionVpip}%</strong>
          {sessionPfr != null ? (
            <>
              {" "}
              · PFR <strong>{sessionPfr}%</strong>
            </>
          ) : null}
        </div>
      ) : null}

      <div className="detail-cards-row">
        <span className="detail-label">Hero</span>
        <div className="card-row">
          {heroCards.length > 0
            ? heroCards.map((c, i) => <PlayingCard key={i} card={c} />)
            : <span className="muted">—</span>}
        </div>
      </div>

      {hand.board_cards?.length > 0 ? (
        <div className="detail-cards-row">
          <span className="detail-label">Board</span>
          <div className="card-row">
            {hand.board_cards.map((c, i) => (
              <PlayingCard key={i} card={c} />
            ))}
          </div>
        </div>
      ) : null}

      <div className="detail-actions">
        <button type="button" className="secondary-btn" onClick={runAi} disabled={analyzing}>
          {analyzing ? "Analyzing with Ollama…" : "AI Coach"}
        </button>
      </div>

      {analyzing ? (
        <p className="muted ai-analyzing-hint">Local Ollama analysis can take 1–2 minutes on large models.</p>
      ) : null}

      {aiError ? <div className="error-banner">{aiError}</div> : null}
      {analysis ? (
        <div className="ai-result">
          <div className="detail-label">AI Coach</div>
          <p>{analysis.summary || (analysis as { analysis?: string }).analysis || "Analysis complete."}</p>
          {analysis.play_style ? (
            <p className="muted">Style: {analysis.play_style}</p>
          ) : null}
          {analysis.mistakes_found != null ? (
            <p className="muted">Mistakes flagged: {analysis.mistakes_found}</p>
          ) : null}
        </div>
      ) : null}

      <div className="streets-log">
        <div className="detail-label">Action Log</div>
        {hand.streets?.map((street) => (
          <div key={street.name} className="street-block">
            <div className="street-name">
              {street.name}
              {street.cards?.length ? ` · ${street.cards.join(" ")}` : ""}
            </div>
            {street.actions?.map((act, i) => (
              <div key={i} className="action-line mono">
                {act.player}: {act.action}
                {act.amount > 0 ? ` $${act.amount.toFixed(2)}` : ""}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

type HandReplayerModalProps = {
  hand: HandDetail;
  onClose: () => void;
};

export function HandReplayerModal({ hand, onClose }: HandReplayerModalProps) {
  return (
    <div className="modal-overlay" onClick={onClose} role="presentation">
      <div
        className="modal-content replayer-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label={`Replay hand ${hand.hand_id}`}
      >
        <HandReplayer hand={hand} onClose={onClose} />
      </div>
    </div>
  );
}
