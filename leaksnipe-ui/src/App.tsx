import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  getApiBase,
  waitForBackend,
  type Dashboard,
  type HandDetail,
  type HandSummary,
  type ImportStatus,
  type ScanDir,
  type Settings,
} from "./lib/api";
import type { TabId } from "./types";
import { AiCoachPanel } from "./components/AiCoachPanel";
import { HandDetailPanel, HandReplayerModal } from "./components/HandDetail";
import { SettingsPanel } from "./components/SettingsPanel";
import { StatsPanel } from "./components/StatsPanel";
import "./App.css";

const TABS: { id: TabId; label: string; hint: string }[] = [
  { id: "hands", label: "Hands", hint: "Click a hand for stats · Replay button opens the table replayer" },
  { id: "stats", label: "Stats", hint: "VPIP, PFR, position breakdown, leak alerts" },
  { id: "coach", label: "AI Coach", hint: "Session analysis & chat (OpenAI / Gemini free tier)" },
  { id: "settings", label: "Settings", hint: "Hero names, watch folders, AI provider" },
];

function formatResult(hand: HandSummary): string {
  const value = hand.hero_won;
  const prefix = value > 0 ? "+" : value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (hand.is_tournament) return `${prefix}${abs.toLocaleString()} chips`;
  return `${prefix}$${abs.toFixed(2)}`;
}

function formatDate(iso: string | null) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function App() {
  const [activeTab, setActiveTab] = useState<TabId>("hands");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [hands, setHands] = useState<HandSummary[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [folders, setFolders] = useState<ScanDir[]>([]);
  const [loading, setLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importStatus, setImportStatus] = useState<ImportStatus | null>(null);
  const [totalHands, setTotalHands] = useState(0);
  const [selectedHandId, setSelectedHandId] = useState<string | null>(null);
  const [selectedHand, setSelectedHand] = useState<HandDetail | null>(null);
  const [replayerHand, setReplayerHand] = useState<HandDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [replayerLoadingId, setReplayerLoadingId] = useState<string | null>(null);

  const tableScrollRef = useRef<HTMLDivElement>(null);
  const handsCountRef = useRef(0);

  const refreshHands = useCallback(async (silent = false) => {
    const scrollTop = silent ? (tableScrollRef.current?.scrollTop ?? 0) : 0;
    if (!silent) setLoading(true);
    setError(null);
    try {
      await waitForBackend();
      const [recent, cfg, watch, status] = await Promise.all([
        api.recentHands(50),
        api.settings(),
        api.watchFolders(),
        api.importStatus().catch(() => null),
      ]);
      setHands(recent.hands);
      handsCountRef.current = recent.total ?? recent.hands.length;
      setTotalHands(recent.total ?? recent.hands.length);
      setSettings(cfg);
      setFolders(watch);
      if (status) {
        setImportStatus(status);
        if (status.total_hands) setTotalHands(status.total_hands);
      } else if (recent.import_status) {
        setImportStatus(recent.import_status);
      }
      if (silent && tableScrollRef.current) {
        requestAnimationFrame(() => {
          if (tableScrollRef.current) tableScrollRef.current.scrollTop = scrollTop;
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  const refreshDashboard = useCallback(async (wait = false) => {
    setStatsLoading(true);
    try {
      const dash = await api.dashboard(wait);
      setDashboard(dash);
      setTotalHands(dash.total_hands);
      if (dash.import_status) setImportStatus(dash.import_status);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStatsLoading(false);
    }
  }, []);

  const refresh = useCallback(
    async (silent = false) => {
      await refreshHands(silent);
      if (activeTab === "stats") {
        await refreshDashboard(true);
      }
    },
    [activeTab, refreshHands, refreshDashboard],
  );

  useEffect(() => {
    void refreshHands();
  }, [refreshHands]);

  useEffect(() => {
    if (activeTab === "stats" && !dashboard && !statsLoading) {
      void refreshDashboard(true);
    }
  }, [activeTab, dashboard, statsLoading, refreshDashboard]);

  useEffect(() => {
    if (!settings?.auto_refresh) return;
    const interval = (settings.refresh_interval ?? 5) * 1000;
    const timer = window.setInterval(() => void refreshHands(true), interval);
    return () => window.clearInterval(timer);
  }, [settings?.auto_refresh, settings?.refresh_interval, refreshHands]);

  useEffect(() => {
    let cancelled = false;
    let source: EventSource | null = null;

    void (async () => {
      try {
        await waitForBackend();
        if (cancelled) return;
        const base = await getApiBase();
        source = new EventSource(`${base}/api/events`);
        source.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data) as { type?: string; count?: number };
            if (data.type === "new_hands" && (data.count ?? 0) > 0) {
              void refreshHands(true);
              if (activeTab === "stats") void refreshDashboard(false);
            }
          } catch {
            // ignore malformed SSE payloads
          }
        };
      } catch {
        // SSE is optional when sidecar is unavailable
      }
    })();

    return () => {
      cancelled = true;
      source?.close();
    };
  }, [activeTab, refreshHands, refreshDashboard]);

  const fetchHand = async (handId: string): Promise<HandDetail> => {
    const res = await api.hand(handId);
    return res.hand;
  };

  const openHand = async (handId: string) => {
    if (selectedHandId === handId && selectedHand) return;
    setSelectedHandId(handId);
    setDetailLoading(true);
    setSelectedHand(null);
    try {
      const hand = await fetchHand(handId);
      setSelectedHand(hand);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load hand");
      setSelectedHandId(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const openReplayer = async (handId: string) => {
    setReplayerLoadingId(handId);
    try {
      const hand = await fetchHand(handId);
      setReplayerHand(hand);
      setSelectedHandId(handId);
      setSelectedHand(hand);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load hand for replayer");
    } finally {
      setReplayerLoadingId(null);
    }
  };

  const closeDetail = () => {
    setSelectedHandId(null);
    setSelectedHand(null);
  };

  const positionStats =
    selectedHand?.hero_position && dashboard?.by_position
      ? dashboard.by_position[selectedHand.hero_position] ?? null
      : null;

  const active = TABS.find((tab) => tab.id === activeTab)!;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand">
          <span className="brand-kicker">LeakSnipe</span>
          <span className="brand-title">Poker Therapist</span>
        </div>
        <div className="header-actions">
          <button type="button" className="ghost-btn small" onClick={() => void refresh()}>
            Refresh
          </button>
          <div className="status-pill" title={importStatus?.watch_folders?.map((f) => f.path).join("\n")}>
            <span className={`status-dot ${importStatus?.watcher_running ? "" : "offline"}`} />
            {loading
              ? "Connecting…"
              : importStatus?.watcher_running
                ? `Watching ${importStatus.existing_folder_count} folder(s)`
                : `${totalHands.toLocaleString()} hands`}
          </div>
        </div>
      </header>

      <div className="app-body">
        <nav className="sidebar">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              className={`tab-button ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
              type="button"
            >
              {tab.label}
            </button>
          ))}
        </nav>

        <main className="content">
          <h1 className="panel-title">{active.label}</h1>
          <p className="panel-subtitle">{active.hint}</p>

          {error ? <div className="error-banner">{error}</div> : null}

          {activeTab === "hands" ? (
            <div className={`hands-layout ${selectedHandId ? "with-drawer" : ""}`}>
              <div className="hands-main">
                <div className="card-grid">
                  <div className="stat-card">
                    <div className="stat-label">Database</div>
                    <div className="stat-value mono small">
                      {dashboard?.db_path?.split(/[/\\]/).pop() ?? "—"}
                    </div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">Showing</div>
                    <div className="stat-value">{hands.length}</div>
                  </div>
                </div>
                <div className="table-wrap table-scroll" ref={tableScrollRef}>
                  <table>
                    <thead>
                      <tr>
                        <th>Date</th>
                        <th>Site</th>
                        <th>Cards</th>
                        <th>Pos</th>
                        <th>Result</th>
                        <th className="col-replay" aria-label="Replay" />
                      </tr>
                    </thead>
                    <tbody>
                      {hands.map((hand) => (
                        <tr
                          key={hand.hand_id}
                          className={selectedHandId === hand.hand_id ? "selected" : ""}
                          onClick={() => void openHand(hand.hand_id)}
                        >
                          <td>{formatDate(hand.date)}</td>
                          <td>{hand.site}</td>
                          <td className="mono">{hand.hero_cards || "—"}</td>
                          <td>{hand.hero_position || "—"}</td>
                          <td
                            className={
                              hand.hero_won > 0
                                ? "positive"
                                : hand.hero_won < 0
                                  ? "negative"
                                  : undefined
                            }
                          >
                            {formatResult(hand)}
                          </td>
                          <td className="col-replay">
                            <button
                              type="button"
                              className="replay-row-btn"
                              title="Replay hand"
                              aria-label={`Replay hand ${hand.hand_id}`}
                              disabled={replayerLoadingId === hand.hand_id}
                              onClick={(e) => {
                                e.stopPropagation();
                                void openReplayer(hand.hand_id);
                              }}
                            >
                              {replayerLoadingId === hand.hand_id ? "…" : "▶"}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <aside
                className={`detail-drawer ${selectedHandId ? "open" : ""}`}
                aria-hidden={!selectedHandId}
              >
                {selectedHandId ? (
                  <HandDetailPanel
                    hand={selectedHand}
                    loading={detailLoading}
                    onClose={closeDetail}
                    onOpenReplayer={() => {
                      if (selectedHand) setReplayerHand(selectedHand);
                    }}
                    positionStats={positionStats}
                    sessionVpip={dashboard?.vpip}
                    sessionPfr={dashboard?.pfr}
                  />
                ) : null}
              </aside>
            </div>
          ) : null}

          {activeTab === "stats" ? (
            <StatsPanel dashboard={dashboard} loading={statsLoading || (!dashboard && loading)} />
          ) : null}

          {activeTab === "coach" ? (
            <AiCoachPanel
              dashboard={dashboard}
              recentHandIds={hands.map((h) => h.hand_id)}
            />
          ) : null}

          {activeTab === "settings" ? (
            <SettingsPanel
              settings={settings}
              folders={folders}
              onSaved={(s) => {
                setSettings(s);
                void refresh(true);
              }}
            />
          ) : null}
        </main>
      </div>

      {replayerHand ? (
        <HandReplayerModal hand={replayerHand} onClose={() => setReplayerHand(null)} />
      ) : null}
    </div>
  );
}

export default App;
