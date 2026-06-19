import { useState } from "react";
import type { AiAnalysis } from "../lib/api";
import { formatAiProviderLabel } from "../lib/api";

type HandAnalysisViewProps = {
  analysis: AiAnalysis;
  compact?: boolean;
};

function gradeClass(grade?: string): string {
  const g = (grade || "").toUpperCase();
  if (g === "A") return "grade-a";
  if (g === "B") return "grade-b";
  if (g === "C") return "grade-c";
  if (g === "D") return "grade-d";
  return "grade-na";
}

function outcomeClass(outcome?: string): string {
  const o = (outcome || "").toLowerCase();
  if (o === "won") return "outcome-won";
  if (o === "lost") return "outcome-lost";
  return "outcome-neutral";
}

function formatActionLabel(action: {
  action_type?: string;
  amount?: number;
  player?: string;
}): string {
  const type = action.action_type || "action";
  const amt = action.amount;
  if (amt && amt > 0) {
    return `${type} ${amt}`;
  }
  return type;
}

export function HandAnalysisView({ analysis, compact = false }: HandAnalysisViewProps) {
  const [expandedStreet, setExpandedStreet] = useState<string | null>(null);
  const streets = analysis.streets ?? [];
  const heroActions = analysis.hero_actions ?? [];
  const hasTimeline = streets.length > 0 || heroActions.length > 0;

  return (
    <div className={`hand-analysis-view${compact ? " compact" : ""}`}>
      {analysis.outcome ? (
        <div className={`analysis-outcome-badge ${outcomeClass(analysis.outcome)}`}>
          {analysis.outcome.toUpperCase()}
        </div>
      ) : null}

      {analysis.summary ? <p className="analysis-summary">{analysis.summary}</p> : null}

      {analysis.biggest_leak ? (
        <p className="analysis-leak">
          <span className="analysis-leak-label">Biggest leak:</span> {analysis.biggest_leak}
        </p>
      ) : null}

      {!compact && analysis.play_style ? (
        <p className="muted analysis-meta">
          Style: {analysis.play_style}
          {analysis.ev_estimate ? ` · EV: ${analysis.ev_estimate}` : ""}
          {analysis.mistakes_found != null ? ` · Flags: ${analysis.mistakes_found}` : ""}
        </p>
      ) : null}

      {hasTimeline ? (
        <div className="analysis-timeline">
          <div className="detail-label">Street review</div>
          {streets.map((street, idx) => {
            const key = `${street.street}-${idx}`;
            const open = expandedStreet === key;
            const streetActions = heroActions.filter(
              (a) => (a.street || "").toLowerCase() === (street.street || "").toLowerCase(),
            );
            return (
              <div key={key} className="analysis-street-card">
                <button
                  type="button"
                  className="analysis-street-header"
                  onClick={() => setExpandedStreet(open ? null : key)}
                >
                  <span className="analysis-street-name">
                    {(street.street || "street").toUpperCase()}
                    {street.board ? ` · ${street.board}` : ""}
                  </span>
                  {street.grade ? (
                    <span className={`grade-badge ${gradeClass(street.grade)}`}>{street.grade}</span>
                  ) : null}
                  <span className="analysis-expand-icon">{open ? "▾" : "▸"}</span>
                </button>
                {street.hero_action || street.facing ? (
                  <div className="analysis-street-meta muted">
                    {street.hero_action ? <span>Hero: {street.hero_action}</span> : null}
                    {street.facing ? <span>Facing: {street.facing}</span> : null}
                  </div>
                ) : null}
                {street.comment ? <p className="analysis-street-comment">{street.comment}</p> : null}
                {open && streetActions.length > 0 ? (
                  <ul className="analysis-action-list">
                    {streetActions.map((act, actIdx) => (
                      <li key={`${key}-act-${actIdx}`} className="analysis-action-row">
                        <div className="analysis-action-head">
                          <span className="mono">{formatActionLabel(act)}</span>
                          {act.grade ? (
                            <span className={`grade-badge ${gradeClass(act.grade)}`}>{act.grade}</span>
                          ) : null}
                        </div>
                        {act.comment ? <p className="analysis-action-comment">{act.comment}</p> : null}
                        {act.multiway && act.pot_odds != null ? (
                          <p className="field-hint analysis-pot-odds">
                            Multi-way pot odds: {(act.pot_odds * 100).toFixed(1)}%
                            {act.num_players_in_pot ? ` (${act.num_players_in_pot} players)` : ""}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            );
          })}

          {heroActions.length > 0 && streets.length === 0 ? (
            <ul className="analysis-action-list flat">
              {heroActions.map((act, idx) => (
                <li key={`flat-${idx}`} className="analysis-action-row">
                  <div className="analysis-action-head">
                    <span className="mono">
                      {(act.street || "?").toUpperCase()} · {formatActionLabel(act)}
                    </span>
                    {act.grade ? (
                      <span className={`grade-badge ${gradeClass(act.grade)}`}>{act.grade}</span>
                    ) : null}
                  </div>
                  {act.comment ? <p className="analysis-action-comment">{act.comment}</p> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {analysis.provider ? (
        <p className="muted mono analysis-provider">
          via {formatAiProviderLabel(analysis.provider, analysis.model)}
        </p>
      ) : null}
    </div>
  );
}
