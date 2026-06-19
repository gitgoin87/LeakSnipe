import { useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Database,
  FolderOpen,
  Layers,
  Settings2,
  Spade,
  TrendingUp,
} from "lucide-react";
import { api, Dashboard, HandSummary, ScanDir, Settings, waitForBackend } from "./lib/api";

function StatCard({
  label,
  value,
  suffix = "",
  accent = "text-emerald-400",
}: {
  label: string;
  value: string | number;
  suffix?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/60 p-4 backdrop-blur-sm">
      <p className="text-xs font-medium uppercase tracking-wider text-zinc-500">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums ${accent}`}>
        {value}
        {suffix && <span className="text-lg text-zinc-400">{suffix}</span>}
      </p>
    </div>
  );
}

function alertColor(level: string) {
  if (level === "green") return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  if (level === "yellow") return "border-amber-500/30 bg-amber-500/10 text-amber-300";
  return "border-rose-500/30 bg-rose-500/10 text-rose-300";
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

function formatWon(amount: number, isTournament: boolean) {
  if (isTournament) return `${amount >= 0 ? "+" : ""}${amount.toLocaleString()} chips`;
  return `${amount >= 0 ? "+" : ""}$${amount.toFixed(2)}`;
}

export default function App() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [hands, setHands] = useState<HandSummary[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [folders, setFolders] = useState<ScanDir[]>([]);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        await waitForBackend();
        const [dash, handsRes, cfg, watch] = await Promise.all([
          api.dashboard(),
          api.hands(50, 0),
          api.settings(),
          api.watchFolders(),
        ]);
        if (cancelled) return;
        setDashboard(dash);
        setHands(handsRes.hands);
        setSettings(cfg);
        setFolders(watch);
        setError(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load data");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="min-h-screen bg-[#0a0f0d] text-zinc-100">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_top,_rgba(16,185,129,0.08),_transparent_50%)]" />
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_bottom_right,_rgba(234,179,8,0.05),_transparent_45%)]" />

      <header className="relative border-b border-zinc-800/80 bg-zinc-950/70 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-500/15 ring-1 ring-emerald-500/30">
              <Spade className="h-5 w-5 text-emerald-400" />
            </div>
            <div>
              <h1 className="text-lg font-semibold tracking-tight">LeakSnipe</h1>
              <p className="text-sm text-zinc-500">Poker leak tracker · Tauri + Python</p>
            </div>
          </div>
          <div
            className={`flex items-center gap-2 rounded-full px-3 py-1 text-sm ring-1 ${
              loading
                ? "bg-amber-500/10 text-amber-300 ring-amber-500/30"
                : error
                  ? "bg-rose-500/10 text-rose-300 ring-rose-500/30"
                  : "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30"
            }`}
          >
            <Activity className="h-4 w-4" />
            {loading ? "Connecting…" : error ? "Backend error" : "Live"}
          </div>
        </div>
      </header>

      <main className="relative mx-auto max-w-7xl space-y-6 p-6">
        {error && (
          <div className="flex items-start gap-3 rounded-xl border border-rose-500/30 bg-rose-500/10 p-4 text-sm text-rose-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <p className="font-medium">Could not reach Python API</p>
              <p className="mt-1 text-rose-300/80">{error}</p>
              <p className="mt-2 text-xs text-rose-300/60">
                Start manually:{" "}
                <code className="rounded bg-black/30 px-1.5 py-0.5">
                  python leak-snipe-desktop/backend/main.py
                </code>
              </p>
            </div>
          </div>
        )}

        <section>
          <div className="mb-4 flex items-center gap-2 text-zinc-300">
            <TrendingUp className="h-5 w-5 text-emerald-400" />
            <h2 className="font-medium">Dashboard</h2>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Total hands"
              value={dashboard?.total_hands ?? "—"}
              accent="text-zinc-100"
            />
            <StatCard label="VPIP" value={dashboard?.vpip ?? "—"} suffix="%" />
            <StatCard label="PFR" value={dashboard?.pfr ?? "—"} suffix="%" accent="text-amber-400" />
            <StatCard label="Aggression" value={dashboard?.af ?? "—"} accent="text-sky-400" />
          </div>
          {dashboard && dashboard.alerts.length > 0 && (
            <div className="mt-4 space-y-2">
              {dashboard.alerts.slice(0, 4).map((a, i) => (
                <div
                  key={i}
                  className={`rounded-lg border px-3 py-2 text-sm ${alertColor(a.level)}`}
                >
                  {a.message}
                </div>
              ))}
            </div>
          )}
        </section>

        <div className="grid gap-6 lg:grid-cols-3">
          <section className="lg:col-span-2 rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-5">
            <div className="mb-4 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-zinc-300">
                <Layers className="h-5 w-5 text-emerald-400" />
                <h2 className="font-medium">Recent hands</h2>
              </div>
              <span className="text-xs text-zinc-500">First 50 · newest first</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead>
                  <tr className="border-b border-zinc-800 text-xs uppercase tracking-wider text-zinc-500">
                    <th className="pb-2 pr-3 font-medium">Date</th>
                    <th className="pb-2 pr-3 font-medium">Site</th>
                    <th className="pb-2 pr-3 font-medium">Cards</th>
                    <th className="pb-2 pr-3 font-medium">Pos</th>
                    <th className="pb-2 font-medium text-right">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {hands.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="py-8 text-center text-zinc-500">
                        {loading ? "Loading hands…" : "No hands in database yet"}
                      </td>
                    </tr>
                  ) : (
                    hands.map((h) => (
                      <tr
                        key={h.hand_id}
                        className="border-b border-zinc-800/50 transition hover:bg-zinc-900/40"
                      >
                        <td className="py-2.5 pr-3 text-zinc-400">{formatDate(h.date)}</td>
                        <td className="py-2.5 pr-3">
                          <span className="rounded bg-zinc-800/80 px-2 py-0.5 text-xs text-zinc-300">
                            {h.site}
                          </span>
                        </td>
                        <td className="py-2.5 pr-3 font-mono text-emerald-300/90">
                          {h.hero_cards || "—"}
                        </td>
                        <td className="py-2.5 pr-3 text-zinc-400">{h.hero_position || "—"}</td>
                        <td
                          className={`py-2.5 text-right tabular-nums font-medium ${
                            h.hero_won >= 0 ? "text-emerald-400" : "text-rose-400"
                          }`}
                        >
                          {formatWon(h.hero_won, h.is_tournament)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <div className="space-y-6">
            <section className="rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-5">
              <div className="mb-4 flex items-center gap-2 text-zinc-300">
                <Settings2 className="h-5 w-5 text-amber-400" />
                <h2 className="font-medium">Settings</h2>
              </div>
              {settings ? (
                <dl className="space-y-3 text-sm">
                  <div>
                    <dt className="text-xs uppercase tracking-wider text-zinc-500">Theme</dt>
                    <dd className="mt-0.5 text-zinc-200">{settings.theme}</dd>
                  </div>
                  <div>
                    <dt className="text-xs uppercase tracking-wider text-zinc-500">Hero names</dt>
                    <dd className="mt-1 space-y-1">
                      {Object.entries(settings.hero_names)
                        .filter(([, v]) => v)
                        .map(([site, name]) => (
                          <div key={site} className="flex justify-between gap-2 text-zinc-300">
                            <span className="text-zinc-500">{site}</span>
                            <span className="truncate font-mono text-xs">{name}</span>
                          </div>
                        ))}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs uppercase tracking-wider text-zinc-500">Database</dt>
                    <dd className="mt-0.5 truncate font-mono text-xs text-zinc-400">
                      {dashboard?.db_path ?? settings.db_path ?? "poker_hands.db"}
                    </dd>
                  </div>
                </dl>
              ) : (
                <p className="text-sm text-zinc-500">Loading settings…</p>
              )}
            </section>

            <section className="rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-5">
              <div className="mb-4 flex items-center gap-2 text-zinc-300">
                <FolderOpen className="h-5 w-5 text-sky-400" />
                <h2 className="font-medium">Watch folders</h2>
              </div>
              {folders.length === 0 ? (
                <p className="text-sm text-zinc-500">No scan directories configured</p>
              ) : (
                <ul className="space-y-2 text-sm">
                  {folders.map((f, i) => (
                    <li
                      key={`${f.path}-${i}`}
                      className="rounded-lg border border-zinc-800/60 bg-zinc-900/40 px-3 py-2"
                    >
                      <span className="text-xs text-amber-400/90">{f.site}</span>
                      <p className="mt-0.5 truncate font-mono text-xs text-zinc-400">{f.path}</p>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {dashboard && Object.keys(dashboard.hands_by_site).length > 0 && (
              <section className="rounded-xl border border-zinc-800/80 bg-zinc-950/50 p-5">
                <div className="mb-4 flex items-center gap-2 text-zinc-300">
                  <Database className="h-5 w-5 text-emerald-400" />
                  <h2 className="font-medium">By site</h2>
                </div>
                <ul className="space-y-2 text-sm">
                  {Object.entries(dashboard.hands_by_site).map(([site, count]) => (
                    <li key={site} className="flex justify-between text-zinc-300">
                      <span>{site}</span>
                      <span className="tabular-nums text-zinc-500">{count.toLocaleString()}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
