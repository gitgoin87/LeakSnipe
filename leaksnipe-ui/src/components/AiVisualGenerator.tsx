import { useState } from "react";
import { api } from "../lib/api";

export type VisualPreset = { label: string; prompt: string };

type AiVisualGeneratorProps = {
  available: boolean;
  model?: string;
  presets?: VisualPreset[];
  placeholder?: string;
  title?: string;
};

const STYLE_SUFFIX =
  "Clean, high-contrast poker study diagram on a dark background, " +
  "legible labels, no photorealism, infographic style.";

export function AiVisualGenerator({
  available,
  model,
  presets = [],
  placeholder = "Describe a poker visual (e.g. BTN open-raise range chart)…",
  title = "Generate Visual",
}: AiVisualGeneratorProps) {
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [caption, setCaption] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const generate = async (rawPrompt: string) => {
    const text = rawPrompt.trim();
    if (!text) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.aiGenerateImage(`${text}. ${STYLE_SUFFIX}`);
      if (res.url) {
        setImageUrl(res.url);
        setCaption(text);
      } else {
        setError(res.error || "No image returned");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Image generation failed");
    } finally {
      setLoading(false);
    }
  };

  if (!available) {
    return (
      <div className="ai-visual-block">
        <h3 className="section-title">{title}</h3>
        <p className="muted small">
          Poker visuals need an ASI:One key. Add <code className="mono">ASI_ONE_API_KEY</code> to{" "}
          <code className="mono">.env</code> and restart to enable image generation.
        </p>
      </div>
    );
  }

  return (
    <div className="ai-visual-block">
      <div className="ai-visual-header">
        <h3 className="section-title">{title}</h3>
        {model ? <span className="ai-hint">ASI:One · {model}</span> : null}
      </div>

      {presets.length > 0 ? (
        <div className="ai-visual-presets">
          {presets.map((p) => (
            <button
              key={p.label}
              type="button"
              className="ghost-btn small"
              disabled={loading}
              onClick={() => {
                setPrompt(p.prompt);
                void generate(p.prompt);
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      ) : null}

      <div className="ai-visual-input-row">
        <input
          type="text"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void generate(prompt)}
          placeholder={placeholder}
          disabled={loading}
        />
        <button
          type="button"
          className="primary-btn"
          onClick={() => void generate(prompt)}
          disabled={loading || !prompt.trim()}
        >
          {loading ? "Generating…" : "Generate"}
        </button>
      </div>

      {error ? <div className="error-banner">{error}</div> : null}

      {imageUrl ? (
        <figure className="ai-visual-figure">
          <img src={imageUrl} alt={caption ?? "Generated poker visual"} className="ai-visual-image" />
          {caption ? <figcaption className="muted small">{caption}</figcaption> : null}
        </figure>
      ) : null}
    </div>
  );
}
