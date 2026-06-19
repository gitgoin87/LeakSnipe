import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  waitForBackend,
  type AiAnalysis,
  type AiStatus,
  type CoachMemoryEntry,
  type Dashboard,
} from "../lib/api";
import { HandAnalysisView } from "./HandAnalysisView";
import { AiVisualGenerator, type VisualPreset } from "./AiVisualGenerator";

const COACH_VISUAL_PRESETS: VisualPreset[] = [
  {
    label: "BTN open range",
    prompt:
      "A 13x13 preflop poker hand range grid chart showing a button (BTN) open-raising range, " +
      "pairs on the diagonal, suited combos upper-right, offsuit lower-left, raise hands highlighted",
  },
  {
    label: "BB vs BTN 3-bet",
    prompt:
      "A 13x13 preflop poker range grid for big blind 3-betting versus a button open, " +
      "value 3-bets and bluff 3-bets color-coded",
  },
  {
    label: "Board texture map",
    prompt:
      "A poker board texture diagram showing a wet, connected, two-tone flop with draw possibilities labeled",
  },
  {
    label: "Positions diagram",
    prompt:
      "A 9-handed poker table seating diagram labeling each position UTG, UTG+1, MP, LJ, HJ, CO, BTN, SB, BB",
  },
];

type ChatMessage = { role: "user" | "assistant"; content: string };

type AiCoachPanelProps = {
  dashboard: Dashboard | null;
  recentHandIds: string[];
};

export function AiCoachPanel({ dashboard, recentHandIds }: AiCoachPanelProps) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [sessionReport, setSessionReport] = useState<string | null>(null);
  const [handAnalysis, setHandAnalysis] = useState<AiAnalysis | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [providerPref, setProviderPref] = useState("asi1");
  const [datasetHands, setDatasetHands] = useState<number | null>(null);
  const [webContextUsed, setWebContextUsed] = useState(false);
  const [memoryEntries, setMemoryEntries] = useState<CoachMemoryEntry[] | null>(null);
  const [memoryCount, setMemoryCount] = useState<number>(0);
  const [showMemory, setShowMemory] = useState(false);
  const [refreshingStatus, setRefreshingStatus] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  const loadStatus = useCallback(async (reloadKeys = false) => {
    setRefreshingStatus(true);
    try {
      await waitForBackend(24, 250);
      const s = reloadKeys ? await api.aiReload() : await api.aiStatus();
      setStatus(s);
      if (s.ai_provider_pref) setProviderPref(s.ai_provider_pref);
      if (s.dataset_context_hands != null) setDatasetHands(s.dataset_context_hands);
      if (s.coach_memory_count != null) setMemoryCount(s.coach_memory_count);
      if (!s.ok && s.error) {
        setError(s.error);
      } else if (!s.llm_available && s.setup_hint) {
        setError(s.setup_hint);
      } else {
        setError(null);
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setStatus({ ok: false, llm_available: false, llm_provider: "none", ollama_ready: false });
      setError(message);
    } finally {
      setRefreshingStatus(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    if (dashboard) {
      const ctx = [
        `VPIP ${dashboard.vpip}% PFR ${dashboard.pfr}% AF ${dashboard.af}`,
        `Hands: ${dashboard.total_hands}`,
        dashboard.alerts?.slice(0, 5).map((a) => a.message).join("; "),
      ].join("\n");
      void api.setAiContext(ctx);
    }
  }, [dashboard]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const runSession = async () => {
    setLoading("session");
    setError(null);
    try {
      const res = await api.analyzeSession(20, providerPref === "auto" ? undefined : providerPref);
      setSessionReport(res.report);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Session analysis failed");
    } finally {
      setLoading(null);
    }
  };

  const runRecentHand = async () => {
    const handId = recentHandIds[0];
    if (!handId) {
      setError("No hands loaded to analyze");
      return;
    }
    setLoading("hand");
    setError(null);
    try {
      const res = await api.analyzeHand(
        handId,
        providerPref === "auto" ? undefined : providerPref,
      );
      setHandAnalysis({
        ...res.analysis,
        provider: res.analysis.provider ?? res.provider,
        model: res.analysis.model ?? res.model,
      });
      setWebContextUsed(Boolean(res.web_context_included ?? res.analysis.web_context_included));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Hand analysis failed");
    } finally {
      setLoading(null);
    }
  };

  const sendChat = async () => {
    const text = input.trim();
    if (!text) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    setLoading("chat");
    setError(null);
    try {
      const res = await api.chat(
        text,
        providerPref === "auto" ? undefined : providerPref,
      );
      setMessages((m) => [...m, { role: "assistant", content: res.reply }]);
      setWebContextUsed(Boolean(res.web_context_included));
      if (status?.ai_personalization !== false && status?.coach_memory_available) {
        setMemoryCount((c) => c + 1);
        if (showMemory) void loadMemory();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chat failed");
    } finally {
      setLoading(null);
    }
  };

  const clearChat = async () => {
    await api.clearChat();
    setMessages([]);
  };

  const loadMemory = useCallback(async () => {
    try {
      const res = await api.aiMemory(50);
      setMemoryEntries(res.entries ?? []);
      setMemoryCount(res.count ?? 0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load coach memory");
    }
  }, []);

  const toggleMemory = async () => {
    const next = !showMemory;
    setShowMemory(next);
    if (next) await loadMemory();
  };

  const clearMemory = async () => {
    try {
      await api.aiMemoryClear();
      setMemoryEntries([]);
      setMemoryCount(0);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not clear coach memory");
    }
  };

  const providerLabel = status?.llm_provider ?? "none";
  const activeOllamaModel = status?.ollama_model ?? providerLabel.replace(/^ollama:/, "");
  const selectedOllamaModel = status?.ollama_model_selected;
  const ollamaConnected = Boolean(status?.ollama_ready);
  const hasOllamaModels = (status?.ollama_models_installed?.length ?? 0) > 0;
  const usingAsi1 = providerLabel.startsWith("asi1");
  const usingOllama = providerLabel.startsWith("ollama");
  const available = Boolean(
    status?.llm_available || (ollamaConnected && hasOllamaModels),
  );
  const ollamaDefault = (status?.ai_provider_pref ?? "asi1") === "ollama";
  const asi1Preferred =
    (status?.ai_provider_pref ?? "asi1") === "asi1" || Boolean(status?.keys_detected?.asi1);
  const showOllamaSetup = !available && ollamaDefault;
  const showAsi1Setup = !available && asi1Preferred && !status?.asi1_ready;

  const statusLine = (() => {
    if (!available) {
      if (showAsi1Setup) {
        return status?.keys_detected?.asi1
          ? "ASI:One key detected — click Refresh below"
          : "Add ASI_ONE_API_KEY to .env";
      }
      if (showOllamaSetup) {
        return ollamaConnected && !hasOllamaModels
          ? "Ollama running — pull a model"
          : "Ollama not running";
      }
      return "No AI provider active";
    }
    if (usingAsi1 || (asi1Preferred && status?.asi1_ready)) {
      return `ASI:One cloud · ${status?.asi1_model ?? "asi1"}`;
    }
    if (usingOllama) {
      return `Ollama local · ${activeOllamaModel}`;
    }
    return `Provider: ${providerLabel}`;
  })();

  const datasetContextActive =
    Boolean(status?.ai_include_dataset_context ?? true) &&
    (datasetHands ?? status?.dataset_context_hands ?? 0) > 0;
  const webSearchMode = status?.ai_web_search_mode ?? (status?.ai_include_web_context === false ? "off" : "on_demand");
  const webContextEnabled = webSearchMode !== "off";
  const personalizationOn =
    status?.ai_personalization !== false && Boolean(status?.coach_memory_available);
  const agenticToolsOn = status?.ai_agentic_tools !== false;
  const memoryHero = status?.coach_memory_hero;

  return (
    <div className="ai-coach-panel">
      <div className="ai-status-bar">
        <span className={`ai-dot ${available ? "live" : "off"}`} />
        <span>{statusLine}</span>
        {usingOllama && ollamaConnected && activeOllamaModel ? (
          <span className="ai-hint">
            {selectedOllamaModel
              ? status?.ollama_model_pref_installed === false
                ? `Pref ${selectedOllamaModel} not installed — using ${activeOllamaModel}`
                : `Model: ${activeOllamaModel}`
              : "Local · auto model"}
          </span>
        ) : status?.gemini_ready ? (
          <span className="ai-hint">Gemini free tier · ~15 RPM</span>
        ) : null}
        {datasetContextActive ? (
          <span className="ai-hint">
            Using full database ({datasetHands ?? status?.dataset_context_hands} hands)
          </span>
        ) : null}
        {webContextUsed ? (
          <span className="ai-hint">Live web context used</span>
        ) : webContextEnabled ? (
          <span className="ai-hint">
            Web search {webSearchMode === "always" ? "always on" : "on-demand"}
          </span>
        ) : null}
        {personalizationOn ? (
          <span className="ai-hint">
            Memory on — remembers your sessions
            {memoryCount > 0 ? ` (${memoryCount})` : ""}
          </span>
        ) : null}
        {agenticToolsOn ? <span className="ai-hint">Agentic DB tools on</span> : null}
        <button
          type="button"
          className="secondary-btn small ai-status-refresh"
          disabled={refreshingStatus}
          onClick={() => void loadStatus(true)}
        >
          {refreshingStatus ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {!available ? (
        <div className="placeholder-card ai-setup-card">
          {showAsi1Setup ? (
            <>
              <p>
                <strong>ASI:One is the recommended provider.</strong> Add{" "}
                <code className="mono">ASI_ONE_API_KEY</code> to{" "}
                <code className="mono">.env</code> at the repo root, then fully restart LeakSnipe.
              </p>
              {status?.setup_hint ? <p className="ai-setup-hint">{status.setup_hint}</p> : null}
              <p className="ai-setup-hint">
                Get a key at{" "}
                <a href="https://asi1.ai" target="_blank" rel="noreferrer">
                  asi1.ai
                </a>
                . Ollama remains available as a local fallback in Settings.
              </p>
              <button
                type="button"
                className="secondary-btn"
                disabled={refreshingStatus}
                onClick={() => void loadStatus(true)}
              >
                {refreshingStatus ? "Refreshing…" : "Refresh AI status"}
              </button>
            </>
          ) : showOllamaSetup ? (
            <>
              <p>
                <strong>Ollama (local fallback).</strong> No API key needed — install Ollama and
                pull a model, or switch Settings → AI provider → ASI:One.
              </p>
              {status?.setup_hint ? <p className="ai-setup-hint">{status.setup_hint}</p> : null}
              <ol className="ai-setup-steps">
                <li>
                  Install from{" "}
                  <a href="https://ollama.com/download" target="_blank" rel="noreferrer">
                    ollama.com/download
                  </a>{" "}
                  and keep the Ollama app running
                </li>
                <li>
                  Open a terminal and run:{" "}
                  <code className="mono">
                    ollama pull {status?.ollama_recommended_pull ?? "deepseek-r1:8b"}
                  </code>
                </li>
                <li>
                  Alternatives:{" "}
                  <code className="mono">ollama pull qwen2.5:7b</code> or{" "}
                  <code className="mono">ollama pull qwen2.5:1.5b</code> (smaller / faster)
                </li>
                <li>Restart LeakSnipe, then refresh this tab</li>
                <li>
                  Pick your model in <strong>Settings → Ollama model</strong> (or leave Auto)
                </li>
              </ol>
              {status?.ollama_models_installed && status.ollama_models_installed.length > 0 ? (
                <p className="ai-keys-detected">
                  Models installed: {status.ollama_models_installed.join(", ")} — click Refresh
                  below if Ollama is already running.
                </p>
              ) : null}
              <button
                type="button"
                className="secondary-btn"
                disabled={refreshingStatus}
                onClick={() => void loadStatus(true)}
              >
                {refreshingStatus ? "Refreshing…" : "Refresh AI status"}
              </button>
            </>
          ) : (
            <>
              <p>
                <strong>Recommended: ASI:One cloud.</strong> Add{" "}
                <code className="mono">ASI_ONE_API_KEY</code> to{" "}
                <code className="mono">LeakSnipe/.env</code> (from{" "}
                <a href="https://asi1.ai" target="_blank" rel="noreferrer">
                  asi1.ai
                </a>
                ). Install sidecar deps:{" "}
                <code className="mono">pip install -r sidecar\requirements.txt</code> — uses the{" "}
                <code className="mono">openai</code> package, not uAgents.
              </p>
              {status?.setup_hint ? <p className="ai-setup-hint">{status.setup_hint}</p> : null}
              {status?.keys_detected ? (
                <p className="ai-keys-detected">
                  Detected in file:{" "}
                  {[
                    status.keys_detected.asi1 && "ASI_ONE_API_KEY",
                    status.keys_detected.openai && "OPENAI_API_KEY",
                    status.keys_detected.deepseek && "DEEPSEEK_API_KEY",
                    status.keys_detected.gemini && "GEMINI_API_KEY",
                    status.keys_detected.anthropic && "ANTHROPIC_API_KEY",
                  ]
                    .filter(Boolean)
                    .join(", ") || "none"}
                </p>
              ) : null}
              <p className="muted small">
                Local Ollama works offline but is slower and weaker for coaching. Use Settings →{" "}
                <strong>AI provider → ASI:One</strong> after adding your key.
              </p>
              <button
                type="button"
                className="secondary-btn"
                disabled={refreshingStatus}
                onClick={() => void loadStatus(true)}
              >
                {refreshingStatus ? "Refreshing…" : "Refresh AI status"}
              </button>
            </>
          )}
        </div>
      ) : null}

      <div className="ai-controls-row">
        <label className="form-field inline">
          <span>Provider</span>
          <select value={providerPref} onChange={(e) => setProviderPref(e.target.value)}>
            <option value="auto">Auto (cloud first → Ollama)</option>
            <option value="asi1">ASI:One (cloud — recommended)</option>
            <option value="openai">OpenAI</option>
            <option value="deepseek">DeepSeek (cloud)</option>
            <option value="gemini">Gemini (free tier)</option>
            <option value="anthropic">Anthropic Claude</option>
            <option value="ollama">Ollama (local only)</option>
          </select>
        </label>
        <button
          type="button"
          className="secondary-btn"
          onClick={runSession}
          disabled={!available || loading === "session"}
        >
          {loading === "session" ? "Analyzing session…" : "Analyze Session"}
        </button>
        <button
          type="button"
          className="secondary-btn"
          onClick={runRecentHand}
          disabled={!available || !recentHandIds.length || loading === "hand"}
        >
          {loading === "hand" ? "Analyzing hand…" : "Analyze Latest Hand"}
        </button>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      {sessionReport ? (
        <div className="ai-report-block">
          <h3 className="section-title">Session Report</h3>
          <pre className="ai-report-text">{sessionReport}</pre>
        </div>
      ) : null}

      {handAnalysis ? (
        <div className="ai-report-block">
          <h3 className="section-title">Hand Analysis</h3>
          <HandAnalysisView analysis={handAnalysis} compact />
        </div>
      ) : null}

      <div className="ai-report-block">
        <AiVisualGenerator
          available={Boolean(status?.asi1_image_ready)}
          model={status?.asi1_image_model}
          presets={COACH_VISUAL_PRESETS}
          title="Generate Poker Visual"
        />
      </div>

      {personalizationOn ? (
        <div className="ai-report-block ai-memory-block">
          <div className="ai-chat-header">
            <h3 className="section-title">
              Coach Memory{memoryHero ? ` · ${memoryHero}` : ""}
            </h3>
            <div className="ai-memory-actions">
              <button type="button" className="ghost-btn small" onClick={toggleMemory}>
                {showMemory ? "Hide" : `View${memoryCount ? ` (${memoryCount})` : ""}`}
              </button>
              <button
                type="button"
                className="ghost-btn small"
                onClick={clearMemory}
                disabled={memoryCount === 0}
              >
                Forget all
              </button>
            </div>
          </div>
          <p className="muted small">
            The coach builds its own local database of your prior sessions and key takeaways,
            and feeds them into future analysis to track your leaks over time.
          </p>
          {showMemory ? (
            memoryEntries && memoryEntries.length > 0 ? (
              <ul className="ai-memory-list">
                {memoryEntries.map((m) => (
                  <li key={m.id} className={`ai-memory-item kind-${m.kind}`}>
                    <span className="ai-memory-meta">
                      {(m.created_at || "").replace("T", " ").slice(0, 16)} · {m.kind}
                    </span>
                    {m.user_text ? <span className="ai-memory-user">You: {m.user_text}</span> : null}
                    {m.assistant_text ? (
                      <span className="ai-memory-coach">{m.assistant_text}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No stored memory yet — chat with the coach to build it.</p>
            )
          ) : null}
        </div>
      ) : null}

      <div className="ai-chat-section">
        <div className="ai-chat-header">
          <h3 className="section-title">Coach Chat</h3>
          <button type="button" className="ghost-btn small" onClick={clearChat}>
            Clear
          </button>
        </div>
        <div className="ai-chat-messages">
          {messages.length === 0 ? (
            <p className="muted">Ask about leaks, spots, or session review…</p>
          ) : (
            messages.map((m, i) => (
              <div key={i} className={`chat-bubble ${m.role}`}>
                {m.content}
              </div>
            ))
          )}
          <div ref={chatEndRef} />
        </div>
        <div className="ai-chat-input-row">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void sendChat()}
            placeholder="Ask your poker coach…"
            disabled={!available || loading === "chat"}
          />
          <button
            type="button"
            className="primary-btn"
            onClick={sendChat}
            disabled={!available || loading === "chat"}
          >
            {loading === "chat" ? "…" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
