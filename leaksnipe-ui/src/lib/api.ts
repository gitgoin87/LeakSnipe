import { invoke } from "@tauri-apps/api/core";

const DEFAULT_API = "http://127.0.0.1:8765";

let cachedBase: string | null = null;

export async function getApiBase(): Promise<string> {
  if (cachedBase) return cachedBase;
  if (typeof window !== "undefined" && "__TAURI_INTERNALS__" in window) {
    try {
      const base = await invoke<string>("get_api_base_url");
      cachedBase = base;
      return base;
    } catch {
      // fall through
    }
  }
  const fallback = import.meta.env.VITE_API_BASE ?? DEFAULT_API;
  cachedBase = fallback;
  return fallback;
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const base = await getApiBase();
  const res = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text();
    try {
      const parsed = JSON.parse(text) as { detail?: string | { msg?: string }[] };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        throw new Error(parsed.detail);
      }
    } catch (err) {
      if (err instanceof Error && err.message && err.message !== text) {
        throw err;
      }
    }
    throw new Error(text || `API ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export type Alert = { level: string; message: string };

export type Dashboard = {
  ok: boolean;
  api_version?: string;
  total_hands: number;
  vpip: number;
  pfr: number;
  af: number;
  wtsd: number;
  wsd: number;
  cbet: number;
  by_position: Record<string, { total: number; vpip: number; pfr: number }>;
  hands_by_site: Record<string, number>;
  by_site_stats: Record<
    string,
    { total: number; vpip: number; pfr: number; net: number; won: number; lost: number }
  >;
  alerts: Alert[];
  db_path: string;
  last_import_at?: string | null;
  last_import_count?: number;
  import_status?: ImportStatus;
  stats_cached?: boolean;
};

export type ImportFolderStatus = {
  path: string;
  site: string;
  exists: boolean;
};

export type ImportStatus = {
  watcher_running: boolean;
  poll_interval_sec: number;
  watch_folders: ImportFolderStatus[];
  watch_folder_count: number;
  existing_folder_count: number;
  last_scan_at: string | null;
  last_scan_saved: number;
  last_scan_files: number;
  files_tracked: number;
};

export type HandSummary = {
  hand_id: string;
  site: string;
  date: string | null;
  game_type: string;
  table_name: string;
  hero_cards: string;
  hero_won: number;
  hero_position: string;
  hero_name: string;
  hero_player: string;
  pot: number;
  is_tournament: boolean;
  tags: string[];
};

export type PlayerInfo = {
  name: string;
  stack: number;
  is_hero: boolean;
};

export type StreetAction = {
  player: string;
  action: string;
  amount: number;
};

export type Street = {
  name: string;
  cards: string[];
  actions: StreetAction[];
};

export type HandDetail = HandSummary & {
  board_cards: string[];
  streets: Street[];
  players: Record<string, PlayerInfo>;
  winners: { name: string; amount: number }[];
  raw_text: string;
  max_seats: number;
  button_seat: number;
  rake: number;
};

export type HandsResponse = {
  ok: boolean;
  total: number;
  offset: number;
  limit: number;
  hands: HandSummary[];
};

export type RecentHandsResponse = {
  ok: boolean;
  count: number;
  total?: number;
  last_import_at: string | null;
  last_import_count: number;
  import_status?: ImportStatus;
  hands: HandSummary[];
};

export type ScanDir = { path: string; site: string };

export type Settings = {
  hero_names: Record<string, string>;
  scan_dirs: ScanDir[];
  auto_refresh: boolean;
  refresh_interval: number;
  theme: string;
  db_path?: string;
  advanced_mode?: boolean;
  live_hud_enabled?: boolean;
  [key: string]: unknown;
};

export type AiAnalysis = {
  summary?: string;
  play_style?: string;
  mistakes_found?: number;
  tags?: string[];
  ev_estimate?: string;
  confidence?: number;
  provider?: string;
};

export type AiStatus = {
  ok?: boolean;
  llm_available: boolean;
  llm_provider: string;
  provider_chain?: string[];
  ai_provider_pref?: string;
  asi1_ready?: boolean;
  asi1_model?: string;
  asi1_base_url?: string;
  asi1_rate_note?: string;
  openai_ready?: boolean;
  gemini_ready?: boolean;
  gemini_models?: string[];
  gemini_rate_note?: string;
  ollama_ready?: boolean;
  ollama_base_url?: string;
  ollama_model?: string;
  ollama_models_installed?: string[];
  ollama_recommended_pull?: string;
  ollama_pull_alternatives?: string[];
  ollama_setup_note?: string;
  env_path?: string;
  env_file_exists?: boolean;
  keys_detected?: {
    asi1?: boolean;
    openai?: boolean;
    gemini?: boolean;
    anthropic?: boolean;
  };
  setup_hint?: string | null;
  error?: string;
};

export async function waitForBackend(maxAttempts = 50, delayMs = 200): Promise<void> {
  const base = await getApiBase();
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const res = await fetch(`${base}/health`);
      if (res.ok) {
        const data = (await res.json()) as { status?: string; api_version?: string };
        if (!data.api_version) {
          throw new Error(
            "Stale API on port 8765 (missing v0.2 sidecar). Close other LeakSnipe windows and restart.",
          );
        }
        return;
      }
    } catch (err) {
      if (err instanceof Error && err.message.includes("Stale API")) {
        throw err;
      }
      // retry connection errors
    }
    await new Promise((r) => setTimeout(r, delayMs));
  }
  throw new Error("Python sidecar did not become ready. Run: pip install -r sidecar/requirements.txt");
}

function normalizeHandDetail(
  res: { ok?: boolean; hand?: HandDetail } & Partial<HandDetail>,
): HandDetail {
  if (res.hand && res.hand.hand_id) return res.hand;
  const { ok: _ok, hand: _hand, ...rest } = res;
  if (!rest.hand_id) {
    throw new Error("Hand detail response missing hand_id");
  }
  return rest as HandDetail;
}

export const api = {
  dashboard: (wait = false) =>
    apiFetch<Dashboard>(`/api/dashboard${wait ? "?wait=true" : ""}`),
  stats: () => apiFetch<{ ok: boolean; stats: Dashboard; summary_text: string }>("/api/stats"),
  hands: (limit = 50, offset = 0) =>
    apiFetch<HandsResponse>(`/api/hands?limit=${limit}&offset=${offset}`),
  recentHands: (limit = 50) =>
    apiFetch<RecentHandsResponse>(`/api/hands/recent?limit=${limit}`),
  hand: async (id: string) => {
    const res = await apiFetch<{ ok?: boolean; hand?: HandDetail } & Partial<HandDetail>>(
      `/api/hands/${encodeURIComponent(id)}`,
    );
    return { ok: true, hand: normalizeHandDetail(res) };
  },
  importStatus: () =>
    apiFetch<ImportStatus & { ok: boolean; api_version?: string; total_hands?: number }>(
      "/api/import/status",
    ),
  settings: () => apiFetch<Settings>("/api/settings"),
  updateSettings: (settings: Partial<Settings>) =>
    apiFetch<Settings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ settings }),
    }),
  watchFolders: () => apiFetch<ScanDir[]>("/api/watch-folders"),
  scanImport: () => apiFetch<{ ok: boolean; saved: number; files_scanned: number }>("/api/import/scan", { method: "POST" }),
  analyzeHand: (handId: string, provider?: string) =>
    apiFetch<{ ok: boolean; analysis: AiAnalysis; provider?: string }>(
      "/api/analyze/hand",
      {
        method: "POST",
        body: JSON.stringify({ hand_id: handId, provider }),
      },
    ),
  analyzeSession: (limit = 20, provider?: string) =>
    apiFetch<{ ok: boolean; report: string; hands_analyzed: number }>(
      "/api/analyze/session",
      {
        method: "POST",
        body: JSON.stringify({ limit, provider }),
      },
    ),
  aiStatus: () => apiFetch<AiStatus>("/api/ai/status"),
  chat: (message: string, provider?: string) =>
    apiFetch<{ ok: boolean; reply: string; provider?: string }>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, provider }),
    }),
  clearChat: () =>
    apiFetch<{ ok: boolean }>("/api/chat", { method: "DELETE" }),
  setAiContext: (context: string) =>
    apiFetch<{ ok: boolean }>("/api/ai/context", {
      method: "POST",
      body: JSON.stringify({ context }),
    }),
};
