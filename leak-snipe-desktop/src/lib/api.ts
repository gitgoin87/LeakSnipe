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
      // fall through to default
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
    throw new Error(text || `API ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export type Dashboard = {
  total_hands: number;
  vpip: number;
  pfr: number;
  af: number;
  wtsd: number;
  wsd: number;
  cbet: number;
  hands_by_site: Record<string, number>;
  by_site_stats: Record<string, { total: number; vpip: number; pfr: number; net: number }>;
  alerts: { level: string; message: string }[];
  db_path: string;
  project_root: string;
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
  pot: number;
  is_tournament: boolean;
  tags: string[];
};

export type HandsResponse = {
  total: number;
  offset: number;
  limit: number;
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
  [key: string]: unknown;
};

export async function waitForBackend(maxAttempts = 40, delayMs = 250): Promise<void> {
  const base = await getApiBase();
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const res = await fetch(`${base}/health`);
      if (res.ok) return;
    } catch {
      // retry
    }
    await new Promise((r) => setTimeout(r, delayMs));
  }
  throw new Error("Python backend did not become ready in time");
}

export const api = {
  dashboard: () => apiFetch<Dashboard>("/api/dashboard"),
  hands: (limit = 50, offset = 0) =>
    apiFetch<HandsResponse>(`/api/hands?limit=${limit}&offset=${offset}`),
  settings: () => apiFetch<Settings>("/api/settings"),
  watchFolders: () => apiFetch<ScanDir[]>("/api/watch-folders"),
};
