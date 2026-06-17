import { useCallback, useEffect, useRef, useState } from "react";
import { api, waitForBackend, type AiAnalysis, type AiStatus, type Dashboard } from "../lib/api";

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
  const [providerPref, setProviderPref] = useState("ollama");
  const chatEndRef = useRef<HTMLDivElement>(null);

  const loadStatus = useCallback(async () => {
    try {
      await waitForBackend();
      const s = await api.aiStatus();
      setStatus(s);
      if (s.ai_provider_pref) setProviderPref(s.ai_provider_pref);
      setError(null);
    } catch (err) {
      setStatus({ ok: false, llm_available: false, llm_provider: "none", ollama_ready: false });
      setError(err instanceof Error ? err.message : "Could not reach LeakSnipe backend");
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
      setHandAnalysis(res.analysis);
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

  const providerLabel = status?.llm_provider ?? "none";
  const ollamaConnected = Boolean(status?.ollama_ready);
  const hasOllamaModels = (status?.ollama_models_installed?.length ?? 0) > 0;
  const available = Boolean(
    status?.llm_available || (ollamaConnected && hasOllamaModels),
  );
  const ollamaDefault = (status?.ai_provider_pref ?? "ollama") === "ollama";
  const showOllamaSetup = !available && (ollamaDefault || !ollamaConnected || !hasOllamaModels);

  return (
    <div className="ai-coach-panel">
      <div className="ai-status-bar">
        <span className={`ai-dot ${available ? "live" : "off"}`} />
        <span>
          {available
            ? ollamaConnected
              ? `Ollama · ${status?.ollama_model ?? providerLabel}`
              : `Provider: ${providerLabel}`
            : ollamaDefault
              ? ollamaConnected && !hasOllamaModels
                ? "Ollama running — pull a model"
                : "Ollama not running"
              : "No AI provider active"}
        </span>
        {ollamaConnected && status?.ollama_model ? (
          <span className="ai-hint">Local · no API key</span>
        ) : status?.gemini_ready ? (
          <span className="ai-hint">Gemini free tier · ~15 RPM</span>
        ) : null}
      </div>

      {!available ? (
        <div className="placeholder-card ai-setup-card">
          {showOllamaSetup ? (
            <>
              <p>
                <strong>Default provider: Ollama (local).</strong> No API key needed — install
                Ollama and pull a model.
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
              </ol>
              {status?.ollama_models_installed && status.ollama_models_installed.length > 0 ? (
                <p className="ai-keys-detected">
                  Models installed: {status.ollama_models_installed.join(", ")} — click Refresh
                  below if Ollama is already running.
                </p>
              ) : null}
              <button type="button" className="secondary-btn" onClick={() => void loadStatus()}>
                Refresh AI status
              </button>
            </>
          ) : (
            <>
              <p>
                <strong>No AI provider active.</strong> Cloud keys are read from{" "}
                <code className="mono">LeakSnipe/.env</code> at the repo root.
              </p>
              {status?.setup_hint ? <p className="ai-setup-hint">{status.setup_hint}</p> : null}
              {status?.keys_detected ? (
                <p className="ai-keys-detected">
                  Detected in file:{" "}
                  {[
                    status.keys_detected.asi1 && "ASI_ONE_API_KEY",
                    status.keys_detected.openai && "OPENAI_API_KEY",
                    status.keys_detected.gemini && "GEMINI_API_KEY",
                    status.keys_detected.anthropic && "ANTHROPIC_API_KEY",
                  ]
                    .filter(Boolean)
                    .join(", ") || "none"}
                </p>
              ) : null}
              <p className="muted small">
                Or switch Settings → AI provider → <strong>Ollama</strong> for free local inference.
              </p>
            </>
          )}
        </div>
      ) : null}

      <div className="ai-controls-row">
        <label className="form-field inline">
          <span>Provider</span>
          <select value={providerPref} onChange={(e) => setProviderPref(e.target.value)}>
            <option value="ollama">Ollama (local — default)</option>
            <option value="auto">Auto (Ollama → cloud fallbacks)</option>
            <option value="asi1">ASI:One</option>
            <option value="openai">OpenAI</option>
            <option value="gemini">Gemini (free tier)</option>
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
          <p>{handAnalysis.summary}</p>
          {handAnalysis.play_style ? (
            <p className="muted">Style: {handAnalysis.play_style} · EV: {handAnalysis.ev_estimate}</p>
          ) : null}
          {handAnalysis.provider ? (
            <p className="muted mono">via {handAnalysis.provider}</p>
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
