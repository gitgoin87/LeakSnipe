import { useCallback, useEffect, useState } from "react";
import {
  api,
  waitForBackend,
  type AiProviderTestResult,
  type AiStatus,
  type ScanDir,
  type Settings,
} from "../lib/api";
import {
  diagnoseLiveHud,
  isPythonHudRunning,
  launchPythonLiveHud,
  resolveHudBackend,
  stopPythonLiveHud,
  testLiveHud,
  type HudDiagnostics,
} from "../lib/hudManager";

const SITE_OPTIONS = ["BetACR", "ACR", "CoinPoker", "GGPoker", "ReplayPoker"];
const OLLAMA_RECOMMENDED_MODELS = ["deepseek-r1:8b", "qwen2.5:7b"] as const;

const AI_PROVIDERS = [
  { id: "asi1", label: "ASI:One (cloud — recommended)", envVar: "ASI_ONE_API_KEY" },
  { id: "openai", label: "OpenAI", envVar: "OPENAI_API_KEY" },
  { id: "deepseek", label: "DeepSeek (cloud API)", envVar: "DEEPSEEK_API_KEY" },
  { id: "gemini", label: "Google Gemini", envVar: "GEMINI_API_KEY or GOOGLE_API_KEY" },
  { id: "anthropic", label: "Anthropic Claude", envVar: "ANTHROPIC_API_KEY" },
  { id: "ollama", label: "Ollama (local fallback)", envVar: "— no key required" },
] as const;

type SettingsPanelProps = {
  settings: Settings | null;
  folders: ScanDir[];
  onSaved: (settings: Settings) => void;
};

export function SettingsPanel({ settings, folders, onSaved }: SettingsPanelProps) {
  const [draft, setDraft] = useState<Settings | null>(settings);
  const [saving, setSaving] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newPath, setNewPath] = useState("");
  const [newSite, setNewSite] = useState("BetACR");
  const [aiStatus, setAiStatus] = useState<AiStatus | null>(null);
  const [testResults, setTestResults] = useState<Record<string, AiProviderTestResult>>({});
  const [testingProvider, setTestingProvider] = useState<string | null>(null);
  const [testingAll, setTestingAll] = useState(false);
  const [hudTesting, setHudTesting] = useState(false);
  const [hudDiag, setHudDiag] = useState<HudDiagnostics | null>(null);
  const [hudDiagError, setHudDiagError] = useState<string | null>(null);
  const [sidecarOk, setSidecarOk] = useState<boolean | null>(null);
  const [pythonHudLaunching, setPythonHudLaunching] = useState(false);
  const [pythonHudStopping, setPythonHudStopping] = useState(false);
  const [pythonHudRunning, setPythonHudRunning] = useState(false);
  const [refreshingAi, setRefreshingAi] = useState(false);

  const loadAiStatus = useCallback(async (reloadKeys = false) => {
    setRefreshingAi(true);
    try {
      await waitForBackend();
      setAiStatus(reloadKeys ? await api.aiReload() : await api.aiStatus());
      setError(null);
    } catch (err) {
      setAiStatus({ ok: false, llm_available: false, llm_provider: "none", ollama_ready: false });
      setError(err instanceof Error ? err.message : "Could not reach LeakSnipe backend");
    } finally {
      setRefreshingAi(false);
    }
  }, []);

  useEffect(() => {
    void loadAiStatus();
  }, [loadAiStatus]);

  useEffect(() => {
    setDraft(settings);
  }, [settings]);

  useEffect(() => {
    void (async () => {
      try {
        await waitForBackend();
        setSidecarOk(true);
      } catch {
        setSidecarOk(false);
      }
    })();
  }, []);

  const runHudTest = async () => {
    setHudTesting(true);
    setHudDiagError(null);
    try {
      await waitForBackend();
      setSidecarOk(true);
      const diag = await testLiveHud();
      setHudDiag(diag);
    } catch (err) {
      setHudDiagError(err instanceof Error ? err.message : String(err));
      try {
        setHudDiag(await diagnoseLiveHud());
      } catch {
        setHudDiag(null);
      }
    } finally {
      setHudTesting(false);
    }
  };

  useEffect(() => {
    void (async () => {
      try {
        setPythonHudRunning(await isPythonHudRunning());
      } catch {
        setPythonHudRunning(false);
      }
    })();
  }, [pythonHudLaunching, pythonHudStopping, draft?.live_hud_enabled]);

  const runPythonHudFallback = async () => {
    setPythonHudLaunching(true);
    setHudDiagError(null);
    try {
      await launchPythonLiveHud();
      setMessage(
        "Python Live HUD launched — locked by default (click-through). Unlock on toolbar or Ctrl+Shift+H to drag seat badges.",
      );
      setPythonHudRunning(true);
      window.setTimeout(() => setMessage(null), 8000);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setHudDiagError(
        `${msg}\n\nIf pywin32 is missing: pip install pywin32\nLog: %TEMP%\\leaksnipe_python_hud.log`,
      );
    } finally {
      setPythonHudLaunching(false);
    }
  };

  const stopPythonHud = async () => {
    setPythonHudStopping(true);
    setHudDiagError(null);
    try {
      await stopPythonLiveHud();
      setPythonHudRunning(false);
      setMessage("Python Live HUD stopped.");
      window.setTimeout(() => setMessage(null), 4000);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setHudDiagError(`Could not stop Python HUD: ${msg}`);
    } finally {
      setPythonHudStopping(false);
    }
  };

  const resetHudSeatPositions = async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const rawProfiles = (draft.hud_site_profiles ?? {}) as Record<
        string,
        Record<string, unknown>
      >;
      const clearedProfiles: Record<string, Record<string, unknown>> = {};
      for (const [site, prof] of Object.entries(rawProfiles)) {
        clearedProfiles[site] = { ...prof, badge_offsets: {} };
      }
      const next = {
        ...draft,
        hud_slot_positions: {},
        hud_site_profiles: clearedProfiles,
      };
      setDraft(next);
      const saved = await api.updateSettings(next);
      onSaved(saved);
      setMessage("HUD seat positions reset to defaults.");
      window.setTimeout(() => setMessage(null), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset failed");
    } finally {
      setSaving(false);
    }
  };

  const hudBackend = resolveHudBackend(draft);
  const useTauriHud = hudBackend === "tauri";

  if (!draft) {
    return <div className="placeholder-card">Loading settings…</div>;
  }

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const saved = await api.updateSettings(draft);
      onSaved(saved);
      void loadAiStatus();
      setMessage("Settings saved");
      window.setTimeout(() => setMessage(null), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const runScan = async () => {
    setScanning(true);
    setError(null);
    try {
      const res = await api.scanImport();
      setMessage(`Scan complete — ${res.saved} new hands from ${res.files_scanned} files`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scan failed");
    } finally {
      setScanning(false);
    }
  };

  const updateHero = (site: string, value: string) => {
    setDraft({
      ...draft,
      hero_names: { ...draft.hero_names, [site]: value },
    });
  };

  const addFolder = () => {
    const path = newPath.trim();
    if (!path) return;
    const scan_dirs = [...(draft.scan_dirs ?? []), { path, site: newSite }];
    setDraft({ ...draft, scan_dirs });
    setNewPath("");
  };

  const removeFolder = (idx: number) => {
    const scan_dirs = [...(draft.scan_dirs ?? [])];
    scan_dirs.splice(idx, 1);
    setDraft({ ...draft, scan_dirs });
  };

  const aiConnected = Boolean(
    aiStatus?.llm_available ||
      (aiStatus?.ollama_ready && (aiStatus?.ollama_models_installed?.length ?? 0) > 0),
  );
  const showOllamaModelPicker =
    ((draft.ai_provider as string) ?? "asi1") === "ollama" ||
    (draft.ai_provider as string) === "auto";
  const installedModels = aiStatus?.ollama_models_installed ?? [];
  const selectedOllamaModel = (draft.ollama_model as string) ?? "";
  const selectedNotInstalled =
    Boolean(selectedOllamaModel) &&
    !installedModels.some(
      (m) => m === selectedOllamaModel || m.split(":")[0] === selectedOllamaModel.split(":")[0],
    );

  const testProvider = async (providerId: string) => {
    setTestingProvider(providerId);
    setError(null);
    try {
      await waitForBackend();
      const result = await api.aiTestProvider(providerId);
      setTestResults((prev) => ({ ...prev, [providerId]: result }));
      void loadAiStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Connection test failed");
    } finally {
      setTestingProvider(null);
    }
  };

  const testAllProviders = async () => {
    setTestingAll(true);
    setError(null);
    try {
      await waitForBackend();
      const res = await api.aiTestAll();
      setTestResults(res.results ?? {});
      void loadAiStatus();
      setMessage("Finished testing all AI providers");
      window.setTimeout(() => setMessage(null), 3500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Test-all failed");
    } finally {
      setTestingAll(false);
    }
  };

  const providerStatus = (id: string) => aiStatus?.providers?.[id];
  const providerDotClass = (id: string) => {
    const live = testResults[id];
    if (live) return live.ok ? "live" : "off";
    const st = providerStatus(id);
    return st?.ready ? "live" : "off";
  };

  const cloudKeyReady = Boolean(
    aiStatus?.keys_detected?.asi1 ||
      aiStatus?.keys_detected?.openai ||
      aiStatus?.keys_detected?.deepseek ||
      aiStatus?.keys_detected?.gemini ||
      aiStatus?.keys_detected?.anthropic,
  );
  const usingLocalOnly =
    ((draft.ai_provider as string) ?? "asi1") === "ollama" ||
    (((draft.ai_provider as string) ?? "asi1") === "auto" && !cloudKeyReady);

  return (
    <div className="settings-panel">
      {message ? <div className="success-banner">{message}</div> : null}
      <div className="ai-status-bar settings-ai-status">
        <span className={`ai-dot ${aiConnected ? "live" : "off"}`} />
        <span>
          {aiConnected
            ? aiStatus?.llm_provider?.startsWith("asi1") || aiStatus?.asi1_ready
              ? `ASI:One cloud · ${aiStatus?.asi1_model ?? "asi1"}`
              : aiStatus?.keys_detected?.asi1
                ? "ASI:One key detected — restart LeakSnipe if not connected"
              : aiStatus?.cloud_recommended && !aiStatus?.llm_provider?.startsWith("ollama")
                ? `Cloud AI · ${aiStatus?.llm_provider ?? "ready"}`
                : aiStatus?.ollama_ready &&
                    ((draft.ai_provider as string) ?? "asi1") === "ollama"
                  ? `Ollama local · ${aiStatus?.ollama_model ?? "model ready"}`
                  : `AI ready · ${aiStatus?.llm_provider ?? "cloud"}`
            : aiStatus?.keys_detected?.asi1
              ? "Add ASI_ONE_API_KEY to .env and restart"
              : aiStatus?.ollama_ready
                ? "Ollama running — cloud keys recommended for better coaching"
                : "AI not connected — add ASI_ONE_API_KEY or start Ollama"}
        </span>
        <button
          type="button"
          className="secondary-btn ai-status-refresh"
          disabled={refreshingAi}
          onClick={() => void loadAiStatus(true)}
        >
          {refreshingAi ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {error ? <div className="error-banner">{error}</div> : null}
      {aiStatus?.asi1_routing_mode === "split" ? (
        <div className="success-banner">
          Dual ASI1 keys: hand analysis and coach chat run in parallel on separate keys.
        </div>
      ) : null}
      {cloudKeyReady && usingLocalOnly ? (
        <div className="success-banner">
          Cloud API key detected — for faster, stronger coaching, set <strong>AI provider</strong> to{" "}
          <strong>ASI:One</strong> or <strong>Auto (cloud first)</strong> and save.
        </div>
      ) : null}
      {!cloudKeyReady ? (
        <div className="ai-setup-card settings-ai-status">
          <p className="section-hint">
            <strong>Tip:</strong> Local Ollama models are slow and weaker for hand review. Get an{" "}
            <a href="https://asi1.ai" target="_blank" rel="noreferrer">
              ASI:One
            </a>{" "}
            API key, add <code className="mono">ASI_ONE_API_KEY</code> to <code className="mono">.env</code>
            , restart LeakSnipe. Sidecar deps:{" "}
            <code className="mono">pip install -r sidecar\requirements.txt</code> (not{" "}
            <code className="mono">request</code> — use <code className="mono">requests</code> only if
            you need it elsewhere; ASI:One uses the <code className="mono">openai</code> package).
          </p>
        </div>
      ) : null}

      <section className="settings-section">
        <h3 className="section-title">Hero Names</h3>
        <p className="section-hint">Comma-separated aliases per site (e.g. GBOSS101,JohnDaWalka)</p>
        <div className="form-grid">
          {SITE_OPTIONS.map((site) => (
            <label key={site} className="form-field">
              <span>{site}</span>
              <input
                type="text"
                value={draft.hero_names?.[site] ?? ""}
                onChange={(e) => updateHero(site, e.target.value)}
                placeholder="Hero screen name(s)"
              />
            </label>
          ))}
        </div>
      </section>

      <section className="settings-section">
        <h3 className="section-title">Watch Folders</h3>
        <p className="section-hint">
          Hand history directories polled every {draft.refresh_interval ?? 5}s when auto-refresh is on
        </p>
        <ul className="folder-list">
          {(draft.scan_dirs ?? folders).map((f, i) => (
            <li key={`${f.path}-${i}`}>
              <div>
                <span className="folder-site">{f.site}</span>
                <span className="mono folder-path">{f.path}</span>
              </div>
              <button type="button" className="ghost-btn small" onClick={() => removeFolder(i)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
        <div className="add-folder-row">
          <select value={newSite} onChange={(e) => setNewSite(e.target.value)}>
            {SITE_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <input
            type="text"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            placeholder="C:\path\to\handHistory"
            className="flex-grow"
          />
          <button type="button" className="secondary-btn" onClick={addFolder}>
            Add
          </button>
        </div>
      </section>

      <section className="settings-section">
        <h3 className="section-title">Live Table HUD</h3>
        <p className="section-hint">
          <strong>Recommended: Python Live HUD</strong> — the original transparent pywin32 overlay
          from poker_gui.py, proven on BetACR/ACR Poker. Per-seat stats refresh as hands import from
          your watch folders. The Tauri webview overlay is experimental (may show a black background
          on Windows).
        </p>
        <div className="form-grid prefs">
          <label className="form-field">
            <span>HUD engine</span>
            <select
              value={hudBackend}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  live_hud_backend: e.target.value as "python" | "tauri",
                })
              }
            >
              <option value="python">Python Live HUD (recommended)</option>
              <option value="tauri">Tauri overlay (experimental)</option>
            </select>
          </label>
          <label className="form-field checkbox">
            <input
              type="checkbox"
              checked={!!draft.live_hud_enabled}
              onChange={(e) => setDraft({ ...draft, live_hud_enabled: e.target.checked })}
            />
            <span>
              {useTauriHud
                ? "Enable Tauri overlay on save"
                : "Enable Live HUD (launch Python overlay below)"}
            </span>
          </label>
          <label className="form-field">
            <span>HUD opacity</span>
            <input
              type="range"
              min={0.3}
              max={1}
              step={0.05}
              value={Number(draft.hud_opacity ?? 0.85)}
              onChange={(e) =>
                setDraft({ ...draft, hud_opacity: Number(e.target.value) })
              }
            />
            <span className="field-hint">{Math.round(Number(draft.hud_opacity ?? 0.85) * 100)}%</span>
          </label>
          <label className="form-field">
            <span>Seat layout</span>
            <select
              value={(draft.hud_seat_layout as string) ?? "auto"}
              onChange={(e) => setDraft({ ...draft, hud_seat_layout: e.target.value })}
            >
              <option value="auto">Auto (from hand max seats)</option>
              <option value="6max">6-max</option>
              <option value="9max">9-max</option>
              <option value="2max">Heads-up</option>
            </select>
          </label>
          <label className="form-field">
            <span>Badge scale</span>
            <input
              type="number"
              min={0.8}
              max={2.5}
              step={0.1}
              value={Number(draft.hud_badge_scale ?? 1.5)}
              onChange={(e) =>
                setDraft({ ...draft, hud_badge_scale: Number(e.target.value) })
              }
            />
            <span className="field-hint">1.5 = 50% larger</span>
          </label>
          <label className="form-field">
            <span>Edge margin %</span>
            <input
              type="number"
              min={5}
              max={25}
              step={1}
              value={Math.round(Number(draft.hud_edge_margin_pct ?? 0.12) * 100)}
              onChange={(e) =>
                setDraft({ ...draft, hud_edge_margin_pct: Number(e.target.value) / 100 })
              }
            />
            <span className="field-hint">Badges avoid left/right edges for ACR action buttons</span>
          </label>
        </div>
        <div className="hud-diagnostics">
          <div className="hud-diagnostics-actions">
            <button
              type="button"
              className="primary-btn"
              onClick={() => void runPythonHudFallback()}
              disabled={pythonHudLaunching || pythonHudRunning}
            >
              {pythonHudLaunching ? "Launching…" : "Launch Python Live HUD"}
            </button>
            {!useTauriHud ? (
              <button
                type="button"
                className="secondary-btn"
                onClick={() => void stopPythonHud()}
                disabled={pythonHudStopping || !pythonHudRunning}
              >
                {pythonHudStopping ? "Stopping…" : "Stop Python Live HUD"}
              </button>
            ) : null}
            {useTauriHud ? (
              <button
                type="button"
                className="secondary-btn"
                onClick={() => void runHudTest()}
                disabled={hudTesting}
              >
                {hudTesting ? "Testing HUD…" : "Test Tauri overlay"}
              </button>
            ) : null}
            {!useTauriHud ? (
              <button
                type="button"
                className="secondary-btn"
                onClick={() => void resetHudSeatPositions()}
                disabled={saving}
              >
                Reset HUD seat positions
              </button>
            ) : null}
          </div>
          <p className="field-hint">
            CLI: <span className="mono">python poker_gui.py --live-hud</span> or{" "}
            <span className="mono">scripts\start-python-hud.ps1</span>. On the table toolbar:{" "}
            <strong>Unlock HUD</strong> to drag each seat badge, <strong>Lock HUD</strong> for
            click-through play, <strong>↺ Reset seats</strong> for defaults. Hotkey: Ctrl+Shift+H.
            Close HUD: toolbar ✕, Escape, or Ctrl+Shift+Q.
          </p>
          <ul className="hud-diagnostics-list">
            <li>
              Sidecar (8765):{" "}
              {sidecarOk === null ? "checking…" : sidecarOk ? "OK" : "not reachable"}
            </li>
            <li>HUD engine: {useTauriHud ? "Tauri (experimental)" : "Python (recommended)"}</li>
            <li>Live HUD enabled in settings: {draft.live_hud_enabled ? "yes" : "no"}</li>
            {!useTauriHud ? (
              <li>Python HUD process: {pythonHudRunning ? "running" : "stopped"}</li>
            ) : null}
            {useTauriHud && hudDiag ? (
              <>
                <li>Overlay window: {hudDiag.overlay_exists ? "created" : "missing"}</li>
                <li>HUD thread: {hudDiag.hud_running ? "running" : "stopped"}</li>
                <li>Build mode: {hudDiag.is_dev ? "dev (tauri dev)" : "production"}</li>
                <li>HUD URL: {hudDiag.webview_url}</li>
                <li>
                  ACR tables detected: {hudDiag.table_count}
                  {hudDiag.table_titles.length > 0
                    ? ` — ${hudDiag.table_titles[0]}`
                    : " — open an ACR table window"}
                </li>
              </>
            ) : null}
          </ul>
          {hudDiagError ? <div className="error-banner">{hudDiagError}</div> : null}
          {useTauriHud && !hudDiagError && hudDiag?.overlay_exists ? (
            <p className="field-hint">
              Tauri overlay should appear at top-left (120, 80) then snap to your ACR table. If the
              background is black, switch back to Python Live HUD.
            </p>
          ) : null}
        </div>
      </section>

      <section className="settings-section">
        <h3 className="section-title">Preferences</h3>
        <div className="form-grid prefs">
          <label className="form-field checkbox">
            <input
              type="checkbox"
              checked={!!draft.auto_refresh}
              onChange={(e) => setDraft({ ...draft, auto_refresh: e.target.checked })}
            />
            <span>Auto-refresh (watch folders)</span>
          </label>
          <label className="form-field">
            <span>Refresh interval (seconds)</span>
            <input
              type="number"
              min={2}
              max={120}
              value={draft.refresh_interval ?? 5}
              onChange={(e) =>
                setDraft({ ...draft, refresh_interval: Number(e.target.value) || 5 })
              }
            />
          </label>
          <label className="form-field">
            <span>Theme</span>
            <input
              type="text"
              value={draft.theme ?? "Slate Blue"}
              onChange={(e) => setDraft({ ...draft, theme: e.target.value })}
            />
          </label>
          <label className="form-field">
            <span>AI provider</span>
            <select
              value={(draft.ai_provider as string) ?? "asi1"}
              onChange={(e) => setDraft({ ...draft, ai_provider: e.target.value })}
            >
              <option value="auto">Auto (cloud first → Ollama fallback)</option>
              <option value="asi1">ASI:One (cloud — recommended)</option>
              <option value="openai">OpenAI</option>
              <option value="deepseek">DeepSeek (cloud)</option>
              <option value="gemini">Gemini (free tier)</option>
              <option value="anthropic">Anthropic Claude</option>
              <option value="ollama">Ollama (local only)</option>
            </select>
            <span className="field-hint">
              Cloud providers use keys in <code className="mono">LeakSnipe/.env</code>. ASI:One uses the
              OpenAI-compatible API at api.asi1.ai — no uAgents install required.
              {((draft.ai_provider as string) ?? "asi1") === "auto" &&
              aiStatus?.recommended_provider &&
              aiStatus.recommended_provider !== "auto" ? (
                <> Active: {aiStatus.recommended_provider} (auto picks cloud first).</>
              ) : null}
            </span>
          </label>
          <label className="form-field checkbox-field">
            <span>Include full database context in AI analysis</span>
            <input
              type="checkbox"
              checked={draft.ai_include_dataset_context !== false}
              onChange={(e) =>
                setDraft({ ...draft, ai_include_dataset_context: e.target.checked })
              }
            />
            <span className="field-hint">
              Injects career stats, positional tendencies, leak alerts, and notable spots into
              ASI:One and other AI coaching prompts. Recommended ON for personalized analysis.
            </span>
          </label>
          <label className="form-field">
            <span>Live web search</span>
            <select
              value={
                (draft.ai_web_search_mode as string) ??
                (draft.ai_include_web_context === false ? "off" : "on_demand")
              }
              onChange={(e) => {
                const mode = e.target.value as "off" | "on_demand" | "always";
                setDraft({
                  ...draft,
                  ai_web_search_mode: mode,
                  ai_include_web_context: mode !== "off",
                });
              }}
            >
              <option value="off">Off — database only</option>
              <option value="on_demand">On-demand (default) — research &amp; tool calls only</option>
              <option value="always">Always — inject web on every message (slow)</option>
            </select>
            <span className="field-hint">
              On-demand uses live web only when you ask for research, sources, or online strategy,
              or when the coach calls the <code className="mono">web_search</code> tool. Hand
              analysis and simple stats questions never hit the web. Always mode enables
              native <code className="mono">web_search</code> on your selected ASI:One model.
            </span>
          </label>
          <label className="form-field checkbox-field">
            <span>Personalization — remember my sessions</span>
            <input
              type="checkbox"
              checked={draft.ai_personalization !== false}
              onChange={(e) =>
                setDraft({ ...draft, ai_personalization: e.target.checked })
              }
            />
            <span className="field-hint">
              Builds a durable per-hero memory of past coaching (stored locally in{" "}
              <code className="mono">coach_memory.db</code>) and feeds key takeaways back into
              future analysis so the coach tracks your progress.
              {aiStatus?.coach_memory_count != null && aiStatus.coach_memory_count > 0 ? (
                <> Currently remembering {aiStatus.coach_memory_count} entr
                  {aiStatus.coach_memory_count === 1 ? "y" : "ies"} for{" "}
                  <strong>{aiStatus.coach_memory_hero}</strong>.</>
              ) : null}
            </span>
          </label>
          <label className="form-field checkbox-field">
            <span>Agentic tools — let the coach query your database</span>
            <input
              type="checkbox"
              checked={draft.ai_agentic_tools !== false}
              onChange={(e) =>
                setDraft({ ...draft, ai_agentic_tools: e.target.checked })
              }
            />
            <span className="field-hint">
              ASI:One can call live functions (career stats, hand search, single-hand lookup)
              to pull real numbers from your DB instead of relying only on the static summary.
            </span>
          </label>
          <label className="form-field">
            <span>ASI:One model</span>
            <select
              value={(draft.asi1_model as string) ?? "asi1"}
              onChange={(e) => setDraft({ ...draft, asi1_model: e.target.value })}
            >
              {(aiStatus?.asi1_chat_models ?? ["asi1", "asi1-ultra", "asi1-mini"]).map(
                (m) => (
                  <option key={m} value={m}>
                    {m}
                    {m === "asi1" ? " (adaptive + tools — recommended)" : ""}
                    {m === "asi1-mini" ? " (fastest)" : ""}
                    {m === "asi1-ultra" ? " (deep reasoning)" : ""}
                  </option>
                ),
              )}
            </select>
            <span className="field-hint">
              Chat/tool model. On-demand web research uses DuckDuckGo +{" "}
              <code className="mono">asi1</code>; native{" "}
              <code className="mono">web_search</code> is used in Always mode. Image
              generation uses <code className="mono">asi1-mini</code>. See{" "}
              <a href="https://docs.asi1.ai/documentation/models" target="_blank" rel="noreferrer">
                docs.asi1.ai/models
              </a>
              .
            </span>
          </label>
          <div className="provider-status-section">
            <div className="provider-status-header">
              <span className="field-hint">Provider connections</span>
              <button
                type="button"
                className="secondary-btn small"
                disabled={testingAll || testingProvider !== null}
                onClick={() => void testAllProviders()}
              >
                {testingAll ? "Testing all…" : "Test all"}
              </button>
            </div>
            <ul className="provider-status-list">
              {AI_PROVIDERS.map((p) => {
                const st = providerStatus(p.id);
                const live = testResults[p.id];
                const statusLine = live
                  ? live.ok
                    ? `Live OK · ${live.sample ?? "response received"}`
                    : live.skipped
                      ? `Skipped · ${live.error ?? "key not set"}`
                      : `Failed · ${live.error ?? "unknown error"}`
                  : refreshingAi
                    ? "Checking…"
                  : st?.ready
                    ? `Ready · ${st.model ?? "configured"}`
                    : st?.error ??
                      (p.id === "asi1" && aiStatus?.keys_detected?.asi1
                        ? "Key detected — click Refresh"
                        : "Not configured");
                return (
                  <li key={p.id} className="provider-status-row">
                    <span className={`ai-dot ${providerDotClass(p.id)}`} />
                    <div className="provider-status-info">
                      <strong>{p.label}</strong>
                      <span className="field-hint mono">{p.envVar}</span>
                      <span className="provider-status-detail">{statusLine}</span>
                    </div>
                    <button
                      type="button"
                      className="secondary-btn small"
                      disabled={testingAll || testingProvider === p.id}
                      onClick={() => void testProvider(p.id)}
                    >
                      {testingProvider === p.id ? "Testing…" : "Test"}
                    </button>
                  </li>
                );
              })}
            </ul>
            {aiStatus?.env_path ? (
              <span className="field-hint">
                Env file: <code className="mono">{aiStatus.env_path}</code>
                {aiStatus.env_file_exists ? "" : " (missing — copy .env.example)"}
              </span>
            ) : null}
          </div>
          {showOllamaModelPicker ? (
            <label className="form-field">
              <span>Ollama model</span>
              <select
                value={selectedOllamaModel}
                onChange={(e) => setDraft({ ...draft, ollama_model: e.target.value })}
              >
                <option value="">Auto (best installed)</option>
                {installedModels.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
                {selectedOllamaModel &&
                !installedModels.includes(selectedOllamaModel) ? (
                  <option value={selectedOllamaModel}>{selectedOllamaModel} (not installed)</option>
                ) : null}
              </select>
              <span className="field-hint">
                Installed:{" "}
                {installedModels.length > 0 ? installedModels.join(", ") : "none — pull a model first"}
              </span>
              {selectedNotInstalled ? (
                <span className="field-hint warning-hint">
                  Selected model is not installed yet. In Ollama, run:{" "}
                  <code className="mono">ollama pull {selectedOllamaModel}</code>
                </span>
              ) : null}
              <div className="ollama-model-actions">
                <span className="field-hint">Recommended pulls:</span>
                {OLLAMA_RECOMMENDED_MODELS.map((model) => (
                  <button
                    key={model}
                    type="button"
                    className="secondary-btn small"
                    onClick={() => setDraft({ ...draft, ollama_model: model })}
                  >
                    {model}
                  </button>
                ))}
              </div>
            </label>
          ) : null}
          <label className="form-field">
            <span>Database path</span>
            <input
              type="text"
              value={draft.db_path ?? "poker_hands.db"}
              onChange={(e) => setDraft({ ...draft, db_path: e.target.value })}
            />
          </label>
        </div>
      </section>

      <div className="settings-actions">
        <button type="button" className="primary-btn" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save Settings"}
        </button>
        <button type="button" className="secondary-btn" onClick={runScan} disabled={scanning}>
          {scanning ? "Scanning…" : "Scan Now"}
        </button>
      </div>
    </div>
  );
}
