import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  checkBackendHealth,
  getApiBase,
  getSidecarStatus,
  HEALTH_POLL_INTERVAL_MS,
  launchSidecarWindow,
  restartSidecar,
  waitForBackend,
  type Dashboard,
  type HandDetail,
  type HandSummary,
  type ImportStatus,
  type ScanDir,
  type Settings,
  type SidecarStatus,
  isDashboardStatsWarming,
} from "./lib/api";
import type { TabId } from "./types";
import { AiCoachPanel } from "./components/AiCoachPanel";
import { EquityCalculator } from "./components/EquityCalculator";
import { TheoryPanel } from "./components/TheoryPanel";
import { HandDetailPanel, HandReplayerModal } from "./components/HandDetail";
import { SettingsPanel } from "./components/SettingsPanel";
import { StatsPanel } from "./components/StatsPanel";
import { resolveHudBackend, syncLiveHud } from "./lib/hudManager";
import "./App.css";

const TABS: { id: TabId; label: string; hint: string }[] = [
  { id: "hands", label: "Hands", hint: "Click a hand for stats · Replay button opens the table replayer" },
  { id: "stats", label: "Stats", hint: "VPIP, PFR, position breakdown, leak alerts" },
  { id: "coach", label: "AI Coach", hint: "Session analysis & chat (OpenAI / Gemini free tier)" },
  { id: "equity", label: "Equity", hint: "Monte Carlo equity — NLHE, Omaha Hi/Lo, 7-Card Stud & Stud Hi/Lo" },
  { id: "theory", label: "Theory", hint: "CFR+ · stack charts · neural value (unified MTT theory)" },
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
  const [handsLoading, setHandsLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [importStatus, setImportStatus] = useState<ImportStatus | null>(null);
  const [totalHands, setTotalHands] = useState(0);
  const [dbPath, setDbPath] = useState<string | null>(null);
  const [selectedHandId, setSelectedHandId] = useState<string | null>(null);
  const [selectedHand, setSelectedHand] = useState<HandDetail | null>(null);
  const [replayerHand, setReplayerHand] = useState<HandDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [replayerLoadingId, setReplayerLoadingId] = useState<string | null>(null);
  const [hudError, setHudError] = useState<string | null>(null);
  const [sidecarOnline, setSidecarOnline] = useState<boolean | null>(null);
  const [sidecarStarting, setSidecarStarting] = useState(false);
  const [sidecarStatus, setSidecarStatus] = useState<SidecarStatus | null>(null);

  const tableScrollRef = useRef<HTMLDivElement>(null);
  const handsCountRef = useRef(0);
  const handsAbortRef = useRef<AbortController | null>(null);
  const statsPollRef = useRef<number | null>(null);

  const refreshHands = useCallback(async (silent = false) => {
    handsAbortRef.current?.abort();
    const controller = new AbortController();
    handsAbortRef.current = controller;

    const scrollTop = silent ? (tableScrollRef.current?.scrollTop ?? 0) : 0;
    if (!silent) setHandsLoading(true);
    setError(null);
    try {
      const signal = controller.signal;
      const [recent, cfg, watch, status] = await Promise.all([
        api.recentHands(50, signal),
        api.settings(signal),
        api.watchFolders(signal),
        api.importStatus(signal).catch(() => null),
      ]);
      if (signal.aborted) return;
      setSidecarOnline(true);
      setHands(recent.hands);
      handsCountRef.current = recent.total ?? recent.hands.length;
      setTotalHands(recent.total ?? recent.hands.length);
      if (recent.db_path) setDbPath(recent.db_path);
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
      if (controller.signal.aborted) return;
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
      setSidecarOnline(false);
    } finally {
      if (!controller.signal.aborted && !silent) setHandsLoading(false);
      if (handsAbortRef.current === controller) {
        handsAbortRef.current = null;
      }
    }
  }, []);

  const refreshDashboard = useCallback(async (wait = false) => {
    setStatsLoading(true);
    setStatsError(null);
    try {
      const dash = await api.dashboard(wait);
      setDashboard(dash);
      setTotalHands(dash.total_hands);
      if (dash.import_status) setImportStatus(dash.import_status);
      return dash;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setStatsError(message);
      setError(message);
      return null;
    } finally {
      setStatsLoading(false);
    }
  }, []);

  const scheduleStatsWarmPoll = useCallback(() => {
    if (statsPollRef.current != null) return;
    statsPollRef.current = window.setInterval(() => {
      void refreshDashboard(false).then((dash) => {
        if (dash && !isDashboardStatsWarming(dash) && statsPollRef.current != null) {
          window.clearInterval(statsPollRef.current);
          statsPollRef.current = null;
        }
      });
    }, 4000);
  }, [refreshDashboard]);

  const refresh = useCallback(
    async (silent = false) => {
      await refreshHands(silent);
      if (activeTab === "stats") {
        const dash = await refreshDashboard(false);
        if (dash && isDashboardStatsWarming(dash)) {
          scheduleStatsWarmPoll();
        }
      }
    },
    [activeTab, refreshHands, refreshDashboard, scheduleStatsWarmPoll],
  );

  useEffect(() => {
    void refreshHands();
    return () => {
      handsAbortRef.current?.abort();
      if (statsPollRef.current != null) {
        window.clearInterval(statsPollRef.current);
        statsPollRef.current = null;
      }
    };
  }, [refreshHands]);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      const ok = await checkBackendHealth({ includeSidecarStatus: false });
      if (cancelled) return;
      if (ok) {
        setSidecarOnline(true);
        return;
      }
      const status = await getSidecarStatus();
      if (!cancelled) {
        setSidecarOnline(status?.healthy ?? false);
        if (status) setSidecarStatus(status);
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), HEALTH_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [sidecarStarting]);

  const startSidecar = async () => {
    setSidecarStarting(true);
    setError(null);
    try {
      try {
        await launchSidecarWindow();
      } catch {
        await restartSidecar();
      }
      await waitForBackend(40, 250);
      setSidecarOnline(true);
      await refresh(true);
    } catch (err) {
      setSidecarOnline(false);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSidecarStarting(false);
    }
  };

  useEffect(() => {
    if (activeTab !== "stats") return;
    if (dashboard && !isDashboardStatsWarming(dashboard)) return;
    void refreshDashboard(false).then((dash) => {
      if (dash && isDashboardStatsWarming(dash)) {
        scheduleStatsWarmPoll();
      }
    });
  }, [activeTab, dashboard, refreshDashboard, scheduleStatsWarmPoll]);

  useEffect(() => {
    if (!settings?.auto_refresh) return;
    const interval = (settings.refresh_interval ?? 5) * 1000;
    const timer = window.setInterval(() => void refreshHands(true), interval);
    return () => window.clearInterval(timer);
  }, [settings?.auto_refresh, settings?.refresh_interval, refreshHands]);

  useEffect(() => {
    if (!settings) return;
    const backend = resolveHudBackend(settings);
    void syncLiveHud(!!settings.live_hud_enabled, backend)
      .then(() => setHudError(null))
      .catch((err) => {
        setHudError(err instanceof Error ? err.message : String(err));
      });
  }, [settings?.live_hud_enabled, settings?.live_hud_backend]);

  useEffect(() => {
    let cancelled = false;
    let source: EventSource | null = null;

    void (async () => {
      try {
        for (let i = 0; i < 40 && !cancelled; i++) {
          if (await checkBackendHealth({ includeSidecarStatus: false })) break;
          await new Promise((r) => setTimeout(r, 250));
        }
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
            <span
              className={`status-dot ${sidecarOnline === false ? "offline" : importStatus?.watcher_running ? "" : "offline"}`}
            />
            {handsLoading || sidecarStarting
              ? "Connecting…"
              : sidecarOnline === false
                ? "Sidecar offline"
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

          {sidecarOnline === false ? (
            <div className="sidecar-offline-banner" role="alert">
              <div>
                <strong>Python sidecar not running</strong> — port 8765 is offline; your hand
                database is safe.{" "}
                {sidecarStatus?.deps_installed === false ? (
                  <>
                    Run <code className="mono">Install-Sidecar.bat</code> once from the repo folder,
                    then <code className="mono">Start-Sidecar.bat</code> or click Start Sidecar.
                  </>
                ) : (
                  <>
                    Run <code className="mono">Start-Sidecar.bat</code> or click Start Sidecar below.
                  </>
                )}{" "}
                Log: <code className="mono">%TEMP%\leaksnipe_sidecar.log</code>
                {sidecarStatus?.last_error ? (
                  <>
                    {" "}
                    — <span className="mono">{sidecarStatus.last_error}</span>
                  </>
                ) : null}
              </div>
              <button
                type="button"
                className="primary-btn"
                disabled={sidecarStarting}
                onClick={() => void startSidecar()}
              >
                {sidecarStarting ? "Starting…" : "Start Sidecar"}
              </button>
            </div>
          ) : null}

          {error ? <div className="error-banner">{error}</div> : null}
          {hudError ? (
            <div className="error-banner" role="alert">
              Live HUD: {hudError}
            </div>
          ) : null}
          {settings?.live_hud_enabled && !hudError ? (
            resolveHudBackend(settings) === "tauri" ? (
              <div className="success-banner">Tauri Live HUD overlay is active (experimental)</div>
            ) : (
              <div className="success-banner">
                Live HUD enabled — open Settings and click Launch Python Live HUD
              </div>
            )
          ) : null}

          {activeTab === "hands" ? (
            <div className={`hands-layout ${selectedHandId ? "with-drawer" : ""}`}>
              <div className="hands-main">
                <div className="card-grid">
                  <div className="stat-card">
                    <div className="stat-label">Database</div>
                    <div className="stat-value mono small">
                      {(dbPath ?? dashboard?.db_path)?.split(/[/\\]/).pop() ?? "—"}
                    </div>
                  </div>
                  <div className="stat-card">
                    <div className="stat-label">In database</div>
                    <div className="stat-value">{totalHands.toLocaleString()}</div>
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
                      {handsLoading && hands.length === 0 ? (
                        <tr>
                          <td colSpan={6} className="table-loading-hint">
                            Loading hands…
                          </td>
                        </tr>
                      ) : null}
                      {!handsLoading && hands.length === 0 ? (
                        <tr>
                          <td colSpan={6} className="table-loading-hint">
                            {sidecarOnline === false
                              ? "Sidecar offline — start it with the banner above, then click Refresh."
                              : "No hands yet — add watch folders in Settings and run Scan Now."}
                          </td>
                        </tr>
                      ) : null}
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
            <StatsPanel
              dashboard={dashboard}
              loading={statsLoading}
              warming={Boolean(dashboard && isDashboardStatsWarming(dashboard) && !statsError)}
              error={statsError}
              onRetry={() => {
                void refreshDashboard(false).then((dash) => {
                  if (dash && isDashboardStatsWarming(dash)) scheduleStatsWarmPoll();
                });
              }}
            />
          ) : null}

          {activeTab === "coach" ? (
            <AiCoachPanel
              dashboard={dashboard}
              recentHandIds={hands.map((h) => h.hand_id)}
            />
          ) : null}

          {activeTab === "equity" ? <EquityCalculator /> : null}

          {activeTab === "theory" ? <TheoryPanel /> : null}

          {activeTab === "settings" ? (
            <SettingsPanel
              settings={settings}
              folders={folders}
              onSaved={(s) => {
                setSettings(s);
                void syncLiveHud(!!s.live_hud_enabled, resolveHudBackend(s))
                  .then(() => setHudError(null))
                  .catch((err) => {
                    setHudError(err instanceof Error ? err.message : String(err));
                  });
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
