import { useEffect, useMemo, useState } from "react";
import { api, type PlayerHudStats } from "../lib/api";

type OpponentHudPanelProps = {
  names: string[];
  compact?: boolean;
  title?: string;
};

type StatTone = "good" | "warn" | "bad" | "neutral";

function statTone(stat: string, value: number): StatTone {
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

function typeClass(playerType: string): string {
  const key = playerType.toLowerCase().replace(/\s+/g, "-");
  return `hud-type-${key}`;
}

function formatStat(value: number, suffix = ""): string {
  if (!value) return "–";
  return suffix ? `${Math.round(value)}${suffix}` : value.toFixed(1);
}

function PlayerHudBadge({
  stats,
  compact,
}: {
  stats: PlayerHudStats;
  compact?: boolean;
}) {
  const playerType = stats.effective_type || stats.auto_type || "Unknown";
  const [showTooltip, setShowTooltip] = useState(false);
  const positions = useMemo(() => {
    const rows = Object.entries(stats.by_position ?? {})
      .filter(([, d]) => (d.hands ?? 0) > 0)
      .sort((a, b) => (b[1].hands ?? 0) - (a[1].hands ?? 0));
    return rows.slice(0, compact ? 4 : 9);
  }, [stats.by_position, compact]);

  return (
    <div
      className={`hud-badge ${compact ? "compact" : ""}`}
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <div className="hud-badge-name" title={stats.name}>
        {stats.name}
      </div>
      <div className="hud-badge-card">
        <div className="hud-badge-header">
          <span className={`hud-type-pill ${typeClass(playerType)}`}>{playerType}</span>
          <span className="hud-hands-count">H:{stats.hands || "–"}</span>
        </div>
        <div className="hud-stat-grid">
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
          {!compact ? (
            <div className="hud-stat">
              <span className="hud-stat-label">WTSD</span>
              <span className={`hud-stat-value tone-${statTone("wtsd", stats.wtsd)}`}>
                {formatStat(stats.wtsd, "%")}
              </span>
            </div>
          ) : null}
        </div>
        {!compact ? (
          <div className="hud-stat-grid secondary">
            <div className="hud-stat">
              <span className="hud-stat-label">FCBet</span>
              <span className={`hud-stat-value tone-${statTone("fold_cbet", stats.fold_cbet)}`}>
                {formatStat(stats.fold_cbet, "%")}
              </span>
            </div>
          </div>
        ) : null}
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

export function OpponentHudPanel({ names, compact, title = "Opponent HUD" }: OpponentHudPanelProps) {
  const [statsMap, setStatsMap] = useState<Record<string, PlayerHudStats>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const uniqueNames = useMemo(
    () => [...new Set(names.map((n) => n.trim()).filter(Boolean))],
    [names],
  );

  useEffect(() => {
    if (uniqueNames.length === 0) {
      setStatsMap({});
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .playerStatsBatch(uniqueNames)
      .then((res) => {
        if (!cancelled) setStatsMap(res.players);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load HUD stats");
          setStatsMap({});
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [uniqueNames.join("|")]);

  if (uniqueNames.length === 0) return null;

  return (
    <section className="opponent-hud-panel">
      <div className="opponent-hud-header">
        <h3 className="section-title">{title}</h3>
        {loading ? <span className="muted small">Loading stats…</span> : null}
      </div>
      {error ? <div className="error-banner small">{error}</div> : null}
      <div className={`opponent-hud-grid ${compact ? "compact" : ""}`}>
        {uniqueNames.map((name) => {
          const stats = statsMap[name];
          if (!stats) {
            return (
              <div key={name} className="hud-badge skeleton">
                <div className="hud-badge-name">{name}</div>
                <div className="hud-badge-card muted small">No data yet</div>
              </div>
            );
          }
          return <PlayerHudBadge key={name} stats={stats} compact={compact} />;
        })}
      </div>
    </section>
  );
}
