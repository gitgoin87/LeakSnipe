import { useCallback, useEffect, useState } from "react";
import { api, waitForBackend, type AiStatus, type ScanDir, type Settings } from "../lib/api";

const SITE_OPTIONS = ["BetACR", "ACR", "CoinPoker", "GGPoker", "ReplayPoker"];

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

  const loadAiStatus = useCallback(async () => {
    try {
      await waitForBackend();
      setAiStatus(await api.aiStatus());
    } catch {
      setAiStatus({ ok: false, llm_available: false, ollama_ready: false });
    }
  }, []);

  useEffect(() => {
    void loadAiStatus();
  }, [loadAiStatus]);

  useEffect(() => {
    setDraft(settings);
  }, [settings]);

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

  return (
    <div className="settings-panel">
      {message ? <div className="success-banner">{message}</div> : null}
      <div className="ai-status-bar settings-ai-status">
        <span className={`ai-dot ${aiConnected ? "live" : "off"}`} />
        <span>
          {aiConnected
            ? aiStatus?.ollama_ready
              ? `Ollama connected · ${aiStatus?.ollama_model ?? "model ready"}`
              : `AI ready · ${aiStatus?.llm_provider ?? "cloud"}`
            : aiStatus?.ollama_ready
              ? "Ollama running — pull a model"
              : "AI not connected — start Ollama or add API keys"}
        </span>
        <button type="button" className="secondary-btn" onClick={() => void loadAiStatus()}>
          Refresh
        </button>
      </div>
      {error ? <div className="error-banner">{error}</div> : null}

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
              value={(draft.ai_provider as string) ?? "ollama"}
              onChange={(e) => setDraft({ ...draft, ai_provider: e.target.value })}
            >
              <option value="ollama">Ollama (local — default)</option>
              <option value="auto">Auto (Ollama → cloud fallbacks)</option>
              <option value="asi1">ASI:One</option>
              <option value="openai">OpenAI</option>
              <option value="gemini">Gemini (free tier)</option>
            </select>
            <span className="field-hint">
              Ollama: install from ollama.com, then <code>ollama pull deepseek-r1:8b</code>
            </span>
          </label>
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
