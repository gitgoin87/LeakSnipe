import type { Dashboard } from "../lib/api";

type StatsPanelProps = {
  dashboard: Dashboard | null;
  loading?: boolean;
  warming?: boolean;
  error?: string | null;
  onRetry?: () => void;
};

function alertClass(level: string) {
  if (level === "green") return "alert-green";
  if (level === "yellow") return "alert-yellow";
  return "alert-red";
}

const POSITION_ORDER = ["EP", "MP", "CO", "BTN", "SB", "BB"];

function StatsUnavailable({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="placeholder-card stats-error-card">
      <strong>{title}</strong>
      <p className="stats-error-message">{message}</p>
      {onRetry ? (
        <button type="button" className="ghost-btn small" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function StatsPanel({ dashboard, loading, warming, error, onRetry }: StatsPanelProps) {
  if (loading) {
    return <div className="placeholder-card">Loading leak analysis…</div>;
  }

  if (error && !dashboard) {
    return (
      <StatsUnavailable
        title="Could not load stats"
        message={error}
        onRetry={onRetry}
      />
    );
  }

  if (!dashboard) {
    return (
      <StatsUnavailable
        title="Stats unavailable"
        message="Ensure the Python sidecar is running (port 8765), then click Retry or use Settings → Refresh."
        onRetry={onRetry}
      />
    );
  }

  if (warming) {
    return (
      <div className="placeholder-card">
        Computing leak analysis… ({dashboard.total_hands.toLocaleString()} hands in database)
      </div>
    );
  }

  return (
    <>
      {error ? (
        <div className="error-banner" role="alert">
          Stats refresh failed: {error}
          {onRetry ? (
            <>
              {" "}
              <button type="button" className="ghost-btn small inline" onClick={onRetry}>
                Retry
              </button>
            </>
          ) : null}
        </div>
      ) : null}

      <div className="card-grid">
        <div className="stat-card accent">
          <div className="stat-label">VPIP</div>
          <div className="stat-value">{dashboard.vpip}%</div>
        </div>
        <div className="stat-card accent-gold">
          <div className="stat-label">PFR</div>
          <div className="stat-value">{dashboard.pfr}%</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Aggression</div>
          <div className="stat-value">{dashboard.af}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">WTSD</div>
          <div className="stat-value">{dashboard.wtsd}%</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">W$SD</div>
          <div className="stat-value">{dashboard.wsd}%</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">C-Bet</div>
          <div className="stat-value">{dashboard.cbet}%</div>
        </div>
      </div>

      {dashboard.alerts.length > 0 ? (
        <div className="alerts-section">
          <h3 className="section-title">Leak Alerts</h3>
          <div className="alerts-list">
            {dashboard.alerts.map((a, i) => (
              <div key={i} className={`alert-item ${alertClass(a.level)}`}>
                {a.message}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="two-col">
        <div className="panel-block">
          <h3 className="section-title">By Position</h3>
          <div className="table-wrap compact">
            <table>
              <thead>
                <tr>
                  <th>Pos</th>
                  <th>Hands</th>
                  <th>VPIP</th>
                  <th>PFR</th>
                </tr>
              </thead>
              <tbody>
                {POSITION_ORDER.map((pos) => {
                  const d = dashboard.by_position[pos];
                  if (!d) return null;
                  return (
                    <tr key={pos}>
                      <td>{pos}</td>
                      <td>{d.total}</td>
                      <td>{d.vpip}%</td>
                      <td>{d.pfr}%</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel-block">
          <h3 className="section-title">By Site</h3>
          <div className="table-wrap compact">
            <table>
              <thead>
                <tr>
                  <th>Site</th>
                  <th>Hands</th>
                  <th>VPIP</th>
                  <th>PFR</th>
                  <th>Net</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(dashboard.by_site_stats).map(([site, d]) => (
                  <tr key={site}>
                    <td>{site}</td>
                    <td>{d.total}</td>
                    <td>{d.vpip}%</td>
                    <td>{d.pfr}%</td>
                    <td className={d.net >= 0 ? "positive" : "negative"}>
                      {d.net >= 0 ? "+" : ""}
                      {d.net.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </>
  );
}
