import { invoke } from "@tauri-apps/api/core";

const DEFAULT_API = "http://127.0.0.1:8765";

/** Fast fail for startup tabs — avoids indefinite hang when sidecar is blocked. */
export const STARTUP_FETCH_TIMEOUT_MS = 15_000;

/** Poll sidecar health without hammering Tauri IPC. */
export const HEALTH_POLL_INTERVAL_MS = 8_000;

let cachedBase: string | null = null;

function mergeAbortSignals(
  ...signals: (AbortSignal | undefined | null)[]
): AbortSignal | undefined {
  const active = signals.filter((s): s is AbortSignal => Boolean(s));
  if (active.length === 0) return undefined;
  if (active.length === 1) return active[0];
  const controller = new AbortController();
  const onAbort = () => controller.abort();
  for (const signal of active) {
    if (signal.aborted) {
      controller.abort();
      return controller.signal;
    }
    signal.addEventListener("abort", onAbort, { once: true });
  }
  return controller.signal;
}

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

async function apiFetch<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number; signal?: AbortSignal },
): Promise<T> {
  const base = await getApiBase();
  const { timeoutMs, signal: callerSignal, ...fetchInit } = init ?? {};
  const timeoutSignal = timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined;
  const signal = mergeAbortSignals(callerSignal, timeoutSignal);
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, {
      ...fetchInit,
      signal,
      headers: {
        "Content-Type": "application/json",
        ...fetchInit.headers,
      },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const low = msg.toLowerCase();
    if (err instanceof DOMException && err.name === "TimeoutError") {
      throw new Error(
        `Request timed out (${path}). ASI:One hand analysis can take 1–3 minutes — retry or check %TEMP%\\leaksnipe_sidecar.log`,
      );
    }
    if (
      low.includes("failed to fetch") ||
      low.includes("networkerror") ||
      low.includes("network request failed") ||
      low.includes("connection reset") ||
      low.includes("forcibly closed")
    ) {
      throw new Error(
        `Python sidecar unreachable at ${base}. Your poker_hands.db is intact — use Settings → Start Sidecar or rerun Launch-LeakSnipe.bat. Log: %TEMP%\\leaksnipe_sidecar.log`,
      );
    }
    throw err instanceof Error ? err : new Error(msg);
  }
  if (!res.ok) {
    const text = await res.text();
    try {
      const parsed = JSON.parse(text) as {
        detail?: string | { msg?: string }[];
        error?: string;
        setup_hint?: string;
      };
      if (typeof parsed.detail === "string" && parsed.detail.trim()) {
        throw new Error(parsed.detail);
      }
      if (typeof parsed.error === "string" && parsed.error.trim()) {
        const hint = parsed.setup_hint ? ` ${parsed.setup_hint}` : "";
        throw new Error(`${parsed.error}${hint}`);
      }
      if (Array.isArray(parsed.detail) && parsed.detail[0]?.msg) {
        throw new Error(parsed.detail[0].msg);
      }
    } catch (err) {
      if (err instanceof Error && err.message && err.message !== text) {
        throw err;
      }
    }
    if (res.status === 503) {
      throw new Error(
        text.trim() || "AI service unavailable — check Settings → AI status or add ASI_ONE_API_KEY to .env",
      );
    }
    throw new Error(text.trim() || `API ${res.status} ${res.statusText}`);
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

/** True while leak stats are still the non-blocking placeholder shell. */
export function isDashboardStatsWarming(dashboard: Dashboard): boolean {
  if (dashboard.stats_cached === false) return true;
  if (dashboard.stats_cached !== true) return false;
  // Guard against sidecar race: stats_cached=true paired with placeholder zeros.
  return (
    dashboard.total_hands > 0 &&
    dashboard.vpip === 0 &&
    dashboard.pfr === 0 &&
    dashboard.af === 0 &&
    Object.keys(dashboard.by_position).length === 0 &&
    dashboard.alerts.length === 0
  );
}

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

export type PlayerPositionStats = {
  hands: number;
  vpip: number;
  pfr: number;
};

export type PlayerHudStats = {
  name: string;
  hands: number;
  vpip: number;
  pfr: number;
  af: number;
  fold_cbet: number;
  wtsd: number;
  three_bet?: number;
  auto_type: string;
  manual_type?: string;
  effective_type: string;
  by_position?: Record<string, PlayerPositionStats>;
  cached?: boolean;
};

export type PlayerStatsBatchResponse = {
  ok: boolean;
  players: Record<string, PlayerHudStats>;
};

export type LiveSeatInfo = {
  name: string;
  is_hero: boolean;
};

export type LiveCurrentHand = {
  ok: boolean;
  hand_id: string | null;
  site: string | null;
  max_seats: number;
  seat_map: Record<string, LiveSeatInfo>;
  opponents: string[];
  table_name: string | null;
  imported_at?: string | null;
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
  db_path?: string;
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
  ai_provider?: string;
  ollama_model?: string;
  asi1_model?: string;
  ai_include_dataset_context?: boolean;
  ai_include_web_context?: boolean;
  ai_web_search_mode?: "off" | "on_demand" | "always";
  ai_personalization?: boolean;
  ai_agentic_tools?: boolean;
  advanced_mode?: boolean;
  live_hud_enabled?: boolean;
  live_hud_backend?: "python" | "tauri" | string;
  hud_opacity?: number;
  hud_seat_layout?: string;
  hud_density?: string;
  hud_edge_margin_pct?: number;
  hud_badge_scale?: number;
  [key: string]: unknown;
};

export type AiStreetGrade = {
  street: string;
  board?: string;
  hero_action?: string;
  facing?: string;
  grade?: string;
  comment?: string;
};

export type AiHeroActionGrade = {
  street: string;
  player?: string;
  action_type?: string;
  amount?: number;
  pot_after?: number | null;
  grade?: string;
  comment?: string;
  pot_odds?: number;
  multiway?: boolean;
  num_players_in_pot?: number;
};

export type PotOddsSpotFact = {
  street: string;
  to_call?: number;
  pot_odds?: number;
  multiway?: boolean;
  num_players_in_pot?: number;
  num_callers_facing?: number;
  pot_size_breakdown?: Record<string, number>;
};

export type AiAnalysis = {
  outcome?: "won" | "lost" | "split" | "break_even" | string;
  streets?: AiStreetGrade[];
  hero_actions?: AiHeroActionGrade[];
  spot_facts?: PotOddsSpotFact[];
  summary?: string;
  biggest_leak?: string | null;
  play_style?: string;
  mistakes_found?: number;
  tags?: string[];
  ev_estimate?: string;
  confidence?: number;
  provider?: string;
  model?: string;
  web_context_included?: boolean;
  analysis?: string;
  street_notes?: Record<string, string>;
};

export type CoachMemoryEntry = {
  id: number;
  hero: string;
  kind: string;
  user_text: string;
  assistant_text: string;
  provider: string;
  created_at: string;
};

export type CoachMemoryResponse = {
  ok: boolean;
  hero: string;
  enabled: boolean;
  count: number;
  entries: CoachMemoryEntry[];
  error?: string | null;
};

export type AiImageResult = {
  ok: boolean;
  url: string | null;
  images: { url: string }[];
  model?: string;
  message?: string;
  error?: string | null;
};

export type AiProviderStatus = {
  ready: boolean;
  model?: string | null;
  error?: string | null;
  key_configured?: boolean | null;
  env_var?: string | null;
};

export type AiProviderTestResult = {
  ok: boolean;
  provider: string;
  model?: string | null;
  sample?: string | null;
  error?: string | null;
  skipped?: boolean;
};

export type AiStatus = {
  ok?: boolean;
  llm_available: boolean;
  llm_provider: string;
  provider_chain?: string[];
  ai_provider_pref?: string;
  providers?: Record<string, AiProviderStatus>;
  asi1_ready?: boolean;
  asi1_model?: string;
  asi1_base_url?: string;
  asi1_rate_note?: string;
  asi1_setup_note?: string;
  asi1_image_ready?: boolean;
  asi1_image_model?: string;
  asi1_chat_model?: string;
  asi1_chat_models?: string[];
  ai_personalization?: boolean;
  ai_agentic_tools?: boolean;
  coach_memory_available?: boolean;
  coach_memory_hero?: string;
  coach_memory_count?: number;
  asi1_session_persisted?: boolean;
  cloud_recommended?: boolean;
  recommended_provider?: string | null;
  openai_ready?: boolean;
  openai_model?: string;
  deepseek_ready?: boolean;
  deepseek_model?: string;
  deepseek_models?: string[];
  deepseek_base_url?: string;
  gemini_ready?: boolean;
  gemini_models?: string[];
  gemini_rate_note?: string;
  claude_ready?: boolean;
  ollama_ready?: boolean;
  ollama_base_url?: string;
  ollama_model?: string;
  ollama_model_selected?: string | null;
  ollama_model_pref_installed?: boolean;
  ollama_models_installed?: string[];
  ollama_recommended_pull?: string;
  ollama_pull_alternatives?: string[];
  ollama_setup_note?: string;
  env_path?: string;
  env_file_exists?: boolean;
  keys_detected?: {
    asi1?: boolean;
    asi1_primary?: boolean;
    asi1_fallback?: boolean;
    openai?: boolean;
    gemini?: boolean;
    anthropic?: boolean;
    deepseek?: boolean;
  };
  asi1_routing_mode?: "split" | "single";
  asi1_dual_note?: string | null;
  setup_hint?: string | null;
  ai_include_dataset_context?: boolean;
  ai_include_web_context?: boolean;
  ai_web_search_mode?: "off" | "on_demand" | "always";
  dataset_context_ready?: boolean;
  dataset_context_hands?: number;
  web_context_enabled?: boolean;
  error?: string;
};

export type EquityRequest = {
  hero: string;
  board?: string;
  villain_hand?: string;
  villain_range?: string;
  villain_position?: string;
  action_context?: string;
  iters?: number;
};

export type EquityResult = {
  ok: boolean;
  mode?: string;
  hero?: string;
  board?: string;
  iterations?: number;
  hero_equity?: number;
  hero_win?: number;
  hero_tie?: number;
  equity?: number[];
  villain?: string;
  villain_range?: string;
  villain_range_pct?: number;
  villain_position?: string;
  villain_action?: string;
  rows?: { label: string; equity: number; range_pct: number }[];
};

export type Omaha8Request = {
  hero: string;
  opponents?: number;
  villains?: string[];
  board?: string;
  iters?: number;
};

export type Omaha8Result = {
  ok: boolean;
  hero?: string;
  board?: string;
  players?: number;
  iterations?: number;
  high_equity?: number;
  low_equity?: number;
  scoop_equity?: number;
  overall_equity?: number;
  low_possible_pct?: number;
};

export type StudRequest = {
  hero: string;
  villain_hand?: string;
  villain_range?: string;
  opponents?: number;
  dead_cards?: string;
  iters?: number;
};

export type Stud8Request = {
  hero: string;
  villains?: string[];
  opponents?: number;
  dead_cards?: string;
  iters?: number;
};

export type RangeEntry = { range: string; pct: number };

export type EquityRangesResult = {
  ok: boolean;
  note: string;
  rfi: Record<string, RangeEntry>;
  three_bet: Record<string, RangeEntry>;
  bb_defend_vs_steal: RangeEntry;
};

export type TheoryGame = {
  id: string;
  name: string;
  description: string;
  default_iterations: number;
  max_iterations: number;
  default_ante_per_player?: number;
  default_num_players?: number;
  default_bb?: number;
  default_stack_bb?: number;
};

export type TheoryGamesResult = {
  ok: boolean;
  note: string;
  games: TheoryGame[];
};

export type CfrRequest = {
  game: string;
  iterations?: number;
  seed?: number;
  ante_per_player?: number;
  num_players?: number;
  bb?: number;
  stack_bb?: number;
};

export type CfrResult = {
  ok: boolean;
  game_id?: string;
  game_name?: string;
  iterations: number;
  exploitability?: number;
  ev?: { player_0: number; player_1: number };
  strategy?: Record<string, Record<string, number>>;
  config?: {
    ante_per_player?: number;
    num_players?: number;
    dead_money?: number;
    pot_base_bb?: number;
    bb?: number;
    stack_bb?: number;
  };
  note?: string;
  description?: string;
};

export type ValueNetRequest = {
  hero: string;
  board?: string;
  pot_odds?: number;
  position?: number;
  ante_per_player?: number;
  dead_money?: number;
  bb?: number;
  stack_bb?: number;
};

export type ValueNetResult = {
  ok: boolean;
  hero: string;
  board?: string;
  value_pct: number;
  source: string;
  model_path?: string | null;
  note?: string;
};

export type ValueNetTrainRequest = {
  n_samples?: number;
  epochs?: number;
  seed?: number;
};

export type ValueNetTrainResult = {
  ok: boolean;
  backend: string;
  path: string;
  val_mae?: number;
  n_samples?: number;
};

export type TheoryOverview = {
  ok: boolean;
  module: string;
  components: string[];
  chart_depths_bb: number[];
  chart_positions: string[];
  cfr_games: string[];
  torch_available: boolean;
  defaults: { ante_per_player: number; num_players: number; bb: number };
  note: string;
};

export type TheoryDepthsResult = {
  ok: boolean;
  depths: number[];
};

export type ChartCell = {
  notation: string;
  action: string;
  freq: number;
  bucket?: string;
  source?: string;
  nn_value_pct?: number;
};

export type TheoryChartResult = {
  ok: boolean;
  stack_bb: number;
  position: string;
  mode: string;
  ante_per_player: number;
  num_players: number;
  pot_base_bb: number;
  dead_money: number;
  source: string;
  cfr: {
    exploitability?: number;
    iterations?: number;
    buckets?: Record<string, Record<string, number>>;
  };
  cells: Record<string, ChartCell>;
  grid: (ChartCell | null)[][];
  legend: string[];
  note?: string;
};

const AI_PROVIDER_LABELS: Record<string, string> = {
  asi1: "ASI:One",
  openai: "OpenAI",
  deepseek: "DeepSeek",
  gemini: "Gemini",
  anthropic: "Claude",
  ollama: "Ollama",
};

export function parseAiProviderRef(ref?: string): { provider: string; model?: string } {
  if (!ref) return { provider: "" };
  const colon = ref.indexOf(":");
  if (colon > 0) {
    return { provider: ref.slice(0, colon), model: ref.slice(colon + 1) };
  }
  if (ref === "responses-api" || ref.startsWith("gpt-")) {
    return { provider: "openai", model: ref };
  }
  if (ref.startsWith("claude")) {
    return { provider: "anthropic", model: ref };
  }
  return { provider: ref };
}

export function formatAiProviderLabel(provider?: string, model?: string): string {
  const parsed = parseAiProviderRef(provider);
  const id = (parsed.provider || "").toLowerCase();
  const label = AI_PROVIDER_LABELS[id] ?? (parsed.provider || "AI");
  const modelName = model || parsed.model;
  return modelName ? `${label} · ${modelName}` : label;
}

export function formatAiProviderFromStatus(status?: AiStatus | null): string {
  if (!status?.llm_provider || status.llm_provider === "none") {
    return formatAiProviderLabel(status?.recommended_provider ?? undefined);
  }
  return formatAiProviderLabel(status.llm_provider);
}

export function isOllamaProviderRef(provider?: string): boolean {
  const id = parseAiProviderRef(provider).provider.toLowerCase();
  return id === "ollama";
}

/** Fast timeout for startup polling — avoid 5s stalls per attempt when sidecar is still booting. */
const BACKEND_HEALTH_POLL_MS = 800;

async function fetchHealthOk(base: string, timeoutMs: number): Promise<boolean> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${base}/health`, { signal: controller.signal });
    if (!res.ok) return false;
    const data = (await res.json()) as { api_version?: string };
    return Boolean(data.api_version);
  } catch {
    return false;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function checkBackendHealth(opts?: { includeSidecarStatus?: boolean }): Promise<boolean> {
  try {
    const base = await getApiBase();
    if (await fetchHealthOk(base, BACKEND_HEALTH_POLL_MS)) return true;
  } catch {
    // fall through to Tauri sidecar status when requested
  }

  if (opts?.includeSidecarStatus === false) {
    return false;
  }
  const status = await getSidecarStatus();
  return status?.healthy ?? false;
}

export type SidecarStatus = {
  healthy: boolean;
  deps_installed: boolean;
  port: number;
  log_path: string;
  last_error: string;
};

export async function getSidecarStatus(): Promise<SidecarStatus | null> {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) {
    return null;
  }
  try {
    return await invoke<SidecarStatus>("sidecar_status");
  } catch {
    return null;
  }
}

export async function restartSidecar(): Promise<void> {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) {
    throw new Error("Restart sidecar requires the LeakSnipe desktop app");
  }
  await invoke("restart_sidecar");
  cachedBase = null;
}

export async function launchSidecarWindow(): Promise<void> {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) {
    throw new Error("Launch sidecar requires the LeakSnipe desktop app");
  }
  await invoke("launch_sidecar_window");
  cachedBase = null;
}

export async function waitForBackend(maxAttempts = 80, delayMs = 250): Promise<void> {
  const base = await getApiBase();
  for (let i = 0; i < maxAttempts; i++) {
    try {
      if (await fetchHealthOk(base, BACKEND_HEALTH_POLL_MS)) return;

      const status = await getSidecarStatus();
      if (status?.healthy) return;

      const res = await fetch(`${base}/health`, {
        signal: AbortSignal.timeout(BACKEND_HEALTH_POLL_MS),
      });
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
  throw new Error(
    "Python sidecar is not running on port 8765. Your poker_hands.db is intact — click Start Sidecar, run Start-Sidecar.bat, or restart via Launch-LeakSnipe.bat. First-time setup: Install-Sidecar.bat. Log: %TEMP%\\leaksnipe_sidecar.log",
  );
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
  dashboard: (wait = false, signal?: AbortSignal) =>
    apiFetch<Dashboard>(`/api/dashboard${wait ? "?wait=true" : ""}`, {
      timeoutMs: wait ? 45_000 : STARTUP_FETCH_TIMEOUT_MS,
      signal,
    }),
  stats: () =>
    apiFetch<{ ok: boolean; stats: Dashboard; summary_text: string }>("/api/stats", {
      timeoutMs: 45_000,
    }),
  hands: (limit = 50, offset = 0, signal?: AbortSignal) =>
    apiFetch<HandsResponse>(`/api/hands?limit=${limit}&offset=${offset}`, {
      timeoutMs: STARTUP_FETCH_TIMEOUT_MS,
      signal,
    }),
  recentHands: (limit = 50, signal?: AbortSignal) =>
    apiFetch<RecentHandsResponse>(`/api/hands/recent?limit=${limit}`, {
      timeoutMs: STARTUP_FETCH_TIMEOUT_MS,
      signal,
    }),
  hand: async (id: string) => {
    const res = await apiFetch<{ ok?: boolean; hand?: HandDetail } & Partial<HandDetail>>(
      `/api/hands/${encodeURIComponent(id)}`,
    );
    return { ok: true, hand: normalizeHandDetail(res) };
  },
  playerStats: (name: string) =>
    apiFetch<{ ok: boolean; player: PlayerHudStats }>(
      `/api/players/${encodeURIComponent(name)}/stats`,
    ),
  playerStatsBatch: (names: string[]) => {
    const q = encodeURIComponent(names.join(","));
    return apiFetch<PlayerStatsBatchResponse>(`/api/players/stats?names=${q}`);
  },
  liveCurrentHand: (site?: string) => {
    const q = site ? `?site=${encodeURIComponent(site)}` : "";
    return apiFetch<LiveCurrentHand>(`/api/live/current-hand${q}`);
  },
  refreshPlayerTypes: () =>
    apiFetch<{ ok: boolean; players: number; total_hands: number }>(
      "/api/players/refresh",
      { method: "POST" },
    ),
  importStatus: (signal?: AbortSignal) =>
    apiFetch<ImportStatus & { ok: boolean; api_version?: string; total_hands?: number }>(
      "/api/import/status",
      { timeoutMs: STARTUP_FETCH_TIMEOUT_MS, signal },
    ),
  settings: (signal?: AbortSignal) =>
    apiFetch<Settings>("/api/settings", { timeoutMs: STARTUP_FETCH_TIMEOUT_MS, signal }),
  updateSettings: (settings: Partial<Settings>) =>
    apiFetch<Settings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ settings }),
    }),
  watchFolders: (signal?: AbortSignal) =>
    apiFetch<ScanDir[]>("/api/watch-folders", { timeoutMs: STARTUP_FETCH_TIMEOUT_MS, signal }),
  scanImport: () =>
    apiFetch<{ ok: boolean; saved: number; files_scanned: number; reparsed?: number }>(
      "/api/import/scan",
      { method: "POST" },
    ),
  reparseHeroHands: () =>
    apiFetch<{ ok: boolean; reparsed: number }>("/api/import/reparse-hero", { method: "POST" }),
  analyzeHand: (handId: string, provider?: string) =>
    apiFetch<{
      ok: boolean;
      analysis: AiAnalysis;
      provider?: string;
      model?: string;
      dataset_context_hands?: number;
      dataset_context_included?: boolean;
      web_context_included?: boolean;
    }>(
      "/api/analyze/hand",
      {
        method: "POST",
        body: JSON.stringify({ hand_id: handId, provider }),
        timeoutMs: 300_000,
      },
    ),
  analyzeSession: (limit = 20, provider?: string) =>
    apiFetch<{
      ok: boolean;
      report: string;
      hands_analyzed: number;
      dataset_context_hands?: number;
      dataset_context_included?: boolean;
      web_context_included?: boolean;
    }>(
      "/api/analyze/session",
      {
        method: "POST",
        body: JSON.stringify({ limit, provider }),
      },
    ),
  aiStatus: () => apiFetch<AiStatus>("/api/ai/status", { timeoutMs: 120_000 }),
  aiReload: () => apiFetch<AiStatus>("/api/ai/reload", { method: "POST", timeoutMs: 120_000 }),
  aiDatasetContext: () =>
    apiFetch<{
      ok: boolean;
      hand_count: number;
      include_enabled: boolean;
      profile: Record<string, unknown>;
      text: string;
    }>("/api/ai/dataset-context"),
  aiWebContext: (q: string) =>
    apiFetch<{
      ok: boolean;
      query: string;
      text: string;
      snippets: { title: string; body: string; url: string }[];
      retrieved_at?: string;
      error?: string;
    }>(`/api/ai/web-context?q=${encodeURIComponent(q)}`),
  aiTestProvider: (provider: string) =>
    apiFetch<AiProviderTestResult>(`/api/ai/test/${encodeURIComponent(provider)}`, {
      method: "POST",
      timeoutMs: 120_000,
    }),
  aiTestAll: () =>
    apiFetch<{ ok: boolean; results: Record<string, AiProviderTestResult> }>(
      "/api/ai/test-all",
      { method: "POST", timeoutMs: 180_000 },
    ),
  chat: (message: string, provider?: string) =>
    apiFetch<{ ok: boolean; reply: string; provider?: string; web_context_included?: boolean }>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ message, provider }),
      timeoutMs: 180_000,
    }),
  clearChat: () =>
    apiFetch<{ ok: boolean }>("/api/chat", { method: "DELETE" }),
  setAiContext: (context: string) =>
    apiFetch<{ ok: boolean }>("/api/ai/context", {
      method: "POST",
      body: JSON.stringify({ context }),
    }),
  aiGenerateImage: (prompt: string, opts?: { model?: string; size?: string }) =>
    apiFetch<AiImageResult>("/api/ai/image", {
      method: "POST",
      body: JSON.stringify({ prompt, ...opts }),
    }),
  aiMemory: (limit = 50) =>
    apiFetch<CoachMemoryResponse>(`/api/ai/memory?limit=${limit}`),
  aiMemoryClear: () =>
    apiFetch<{ ok: boolean; hero?: string; cleared?: number; error?: string }>(
      "/api/ai/memory",
      { method: "DELETE" },
    ),
  aiMemoryAdd: (text: string) =>
    apiFetch<{ ok: boolean; hero?: string; error?: string }>("/api/ai/memory", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  equity: (body: EquityRequest) =>
    apiFetch<EquityResult>("/api/equity", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  equityOmaha8: (body: Omaha8Request) =>
    apiFetch<Omaha8Result>("/api/equity/omaha8", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  equityStud: (body: StudRequest) =>
    apiFetch<EquityResult>("/api/equity/stud", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  equityStud8: (body: Stud8Request) =>
    apiFetch<Omaha8Result>("/api/equity/stud8", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  equityRanges: () => apiFetch<EquityRangesResult>("/api/equity/ranges"),

  theoryGames: () => apiFetch<TheoryGamesResult>("/api/theory/games"),
  theoryOverview: () => apiFetch<TheoryOverview>("/api/theory"),
  theoryDepths: () => apiFetch<TheoryDepthsResult>("/api/theory/depths"),
  theoryChart: (params: {
    stack_bb: number;
    position: string;
    ante_per_player?: number;
    num_players?: number;
    include_nn?: boolean;
  }) => {
    const q = new URLSearchParams({
      stack_bb: String(params.stack_bb),
      position: params.position,
      ante_per_player: String(params.ante_per_player ?? 500),
      num_players: String(params.num_players ?? 9),
      include_nn: String(params.include_nn ?? true),
    });
    return apiFetch<TheoryChartResult>(`/api/theory/charts?${q}`);
  },
  theoryCfr: (body: CfrRequest) =>
    apiFetch<CfrResult>("/api/theory/cfr", { method: "POST", body: JSON.stringify(body) }),
  theoryValue: (body: ValueNetRequest) =>
    apiFetch<ValueNetResult>("/api/theory/value", { method: "POST", body: JSON.stringify(body) }),
  theoryValueTrain: (body: ValueNetTrainRequest) =>
    apiFetch<ValueNetTrainResult>("/api/theory/value/train", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
