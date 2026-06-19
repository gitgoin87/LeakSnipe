import { useMemo, useState } from "react";
import type { PlayerHudStats } from "../lib/api";
import { formatStat, statTone, typeClass } from "../lib/hudStats";

type SeatHudBadgeProps = {
  stats: PlayerHudStats | null;
  name: string;
  layoutMode?: boolean;
  onDragEnd?: (dx: number, dy: number) => void;
};

export function SeatHudBadge({ stats, name, layoutMode }: SeatHudBadgeProps) {
  const [showTooltip, setShowTooltip] = useState(false);
  const playerType = stats?.effective_type || stats?.auto_type || "Unknown";

  const positions = useMemo(() => {
    if (!stats?.by_position) return [];
    return Object.entries(stats.by_position)
      .filter(([, d]) => (d.hands ?? 0) > 0)
      .sort((a, b) => (b[1].hands ?? 0) - (a[1].hands ?? 0))
      .slice(0, 9);
  }, [stats?.by_position]);

  return (
    <div
      className={`live-seat-badge ${layoutMode ? "layout-mode" : ""}`}
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <div className="hud-badge-name" title={name}>
        {name}
      </div>
      <div className="hud-badge-card">
        <div className="hud-badge-header">
          <span className={`hud-type-pill ${typeClass(playerType)}`}>{playerType}</span>
          <span className="hud-hands-count">H:{stats?.hands || "–"}</span>
        </div>
        {stats ? (
          <>
            <div className="hud-stat-grid live">
              <div className="hud-stat">
                <span className="hud-stat-label">VPIP</span>
                <span className={`hud-stat-value tone-${statTone("vpip", stats.vpip)}`}>
                  {formatStat(stats.vpip, "%")}
                </span>
              </div>
              <div className="hud-stat">
                <span className="hud-stat-label">PFR</span>
                <span className={`hud-stat-value tone-${statTone("pfr", stats.pfr)}`}>
                  {formatStat(stats.pfr, "%")}
                </span>
              </div>
              <div className="hud-stat">
                <span className="hud-stat-label">AF</span>
                <span className={`hud-stat-value tone-${statTone("af", stats.af)}`}>
                  {formatStat(stats.af)}
                </span>
              </div>
              <div className="hud-stat">
                <span className="hud-stat-label">3B</span>
                <span
                  className={`hud-stat-value tone-${statTone("three_bet", stats.three_bet ?? 0)}`}
                >
                  {formatStat(stats.three_bet ?? 0, "%")}
                </span>
              </div>
            </div>
            <div className="hud-stat-grid secondary live">
              <div className="hud-stat">
                <span className="hud-stat-label">WTSD</span>
                <span className={`hud-stat-value tone-${statTone("wtsd", stats.wtsd)}`}>
                  {formatStat(stats.wtsd, "%")}
                </span>
              </div>
              <div className="hud-stat">
                <span className="hud-stat-label">FCBet</span>
                <span
                  className={`hud-stat-value tone-${statTone("fold_cbet", stats.fold_cbet)}`}
                >
                  {formatStat(stats.fold_cbet, "%")}
                </span>
              </div>
            </div>
          </>
        ) : (
          <div className="hud-badge-card muted small live-loading">Loading…</div>
        )}
      </div>

      {showTooltip && positions.length > 0 ? (
        <div className="hud-tooltip" role="tooltip">
          <div className="hud-tooltip-title">Position stats</div>
          <table>
            <thead>
              <tr>
                <th>Pos</th>
                <th>H</th>
                <th>VPIP</th>
                <th>PFR</th>
              </tr>
            </thead>
            <tbody>
              {positions.map(([pos, d]) => (
                <tr key={pos}>
                  <td>{pos}</td>
                  <td>{d.hands}</td>
                  <td>{d.vpip}%</td>
                  <td>{d.pfr}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
