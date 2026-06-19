import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWebviewWindow } from "@tauri-apps/api/webviewWindow";
import { api, waitForBackend, type LiveCurrentHand, type PlayerHudStats, type Settings } from "../lib/api";
import { resolveLayoutKey, SEAT_POSITIONS, buildHeroAnchoredSeatSlots, clampSeatXPct, HUD_BADGE_SCALE_DEFAULT, HUD_EDGE_MARGIN_PCT_DEFAULT } from "../lib/seatPositions";
import { SeatHudBadge } from "./SeatHudBadge";

type TableBounds = {
  hwnd: number;
  x: number;
  y: number;
  width: number;
  height: number;
  title: string;
};

type SeatEntry = {
  seat: number;
  name: string;
  xPct: number;
  yPct: number;
};

const POLL_MS = 2000;

export function LiveHudOverlay() {
  const [bounds, setBounds] = useState<TableBounds | null>(null);
  const [hand, setHand] = useState<LiveCurrentHand | null>(null);
  const [statsMap, setStatsMap] = useState<Record<string, PlayerHudStats>>({});
  const [settings, setSettings] = useState<Settings | null>(null);
  const [layoutMode, setLayoutMode] = useState(false);
  const [status, setStatus] = useState("Starting…");
  const containerRef = useRef<HTMLDivElement>(null);
  const lastHandId = useRef<string | null>(null);

  const opacity = useMemo(() => {
    const raw = Number(settings?.hud_opacity ?? 0.85);
    return Math.min(1, Math.max(0.3, raw));
  }, [settings?.hud_opacity]);

  const badgeScale = useMemo(() => {
    const raw = Number(settings?.hud_badge_scale ?? HUD_BADGE_SCALE_DEFAULT);
    return Math.min(2.5, Math.max(0.8, raw));
  }, [settings?.hud_badge_scale]);

  const edgeMarginPct = useMemo(() => {
    const raw = Number(settings?.hud_edge_margin_pct ?? HUD_EDGE_MARGIN_PCT_DEFAULT);
    return Math.min(0.25, Math.max(0.05, raw));
  }, [settings?.hud_edge_margin_pct]);

  const applyClickthrough = useCallback(async (ignore: boolean) => {
    try {
      const win = getCurrentWebviewWindow();
      await win.setIgnoreCursorEvents(ignore);
    } catch {
      // browser dev fallback
    }
  }, []);

  useEffect(() => {
    void applyClickthrough(!layoutMode);
  }, [layoutMode, applyClickthrough]);

  useEffect(() => {
    let cancelled = false;

    const boot = async () => {
      try {
        await waitForBackend();
        if (!cancelled) {
          setSettings(await api.settings());
        }
      } catch {
        if (!cancelled) setStatus("Waiting for sidecar…");
      }
    };
    void boot();

    const unlistenBounds = listen<TableBounds>("hud-table-bounds", (event) => {
      setBounds(event.payload);
      setStatus(event.payload.title || "Table detected");
    });

    const unlistenStatus = listen<string>("hud-status", (event) => {
      if (event.payload) setStatus(event.payload);
    });

    return () => {
      cancelled = true;
      void unlistenBounds.then((fn) => fn());
      void unlistenStatus.then((fn) => fn());
    };
  }, []);

  const refreshHand = useCallback(async () => {
    try {
      await waitForBackend();
      const [live, cfg] = await Promise.all([
        api.liveCurrentHand("BetACR"),
        api.settings(),
      ]);
      setSettings(cfg);
      setHand(live);

      const opponents = live.opponents.filter(Boolean);
      if (opponents.length === 0) {
        setStatsMap({});
        setStatus(live.hand_id ? "No opponents in latest hand" : "No hands imported yet");
        return;
      }

      if (live.hand_id !== lastHandId.current) {
        lastHandId.current = live.hand_id;
        setStatus(`Hand ${live.hand_id?.slice(0, 12) ?? ""}…`);
      }

      const res = await api.playerStatsBatch(opponents);
      setStatsMap(res.players);
      setStatus(
        bounds
          ? `${bounds.title} · ${opponents.length} players`
          : `${opponents.length} players (latest hand)`,
      );
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "HUD refresh failed");
    }
  }, [bounds]);

  useEffect(() => {
    void refreshHand();
    const id = window.setInterval(() => void refreshHand(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refreshHand]);

  const seats: SeatEntry[] = useMemo(() => {
    if (!hand?.seat_map) return [];
    const layoutKey = resolveLayoutKey(
      hand.max_seats || 6,
      (settings?.hud_seat_layout as string) ?? "auto",
    );
    const layout = SEAT_POSITIONS[layoutKey];
    const seatToSlot = buildHeroAnchoredSeatSlots(hand.seat_map, layoutKey);
    const entries: SeatEntry[] = [];
    const seenNames = new Set<string>();

    for (const [seatStr, info] of Object.entries(hand.seat_map)) {
      if (!info?.name || info.is_hero) continue;
      const name = info.name.trim();
      if (!name || seenNames.has(name)) continue;
      seenNames.add(name);
      const seat = Number(seatStr);
      const slot = seatToSlot[seat];
      const pos = slot != null ? layout[slot] : undefined;
      if (!pos) continue;
      entries.push({
        seat,
        name,
        xPct: clampSeatXPct(pos[0], edgeMarginPct),
        yPct: pos[1],
      });
    }
    return entries;
  }, [hand, settings?.hud_seat_layout, edgeMarginPct]);

  return (
    <div
      ref={containerRef}
      className="live-hud-root"
      style={{ opacity, ["--hud-badge-scale" as string]: badgeScale }}
    >
      <div className="live-hud-toolbar">
        <span
          className="live-hud-status"
          onPointerDown={(e) => {
            if (layoutMode && e.button === 0) {
              void getCurrentWebviewWindow().startDragging().catch(() => undefined);
            }
          }}
        >
          {status}
        </span>
        <button
          type="button"
          className={`live-hud-layout-btn ${layoutMode ? "active" : ""}`}
          onClick={() => setLayoutMode((v) => !v)}
          onMouseDown={() => void applyClickthrough(false)}
        >
          {layoutMode ? "Layout ON (drag status bar)" : "Layout"}
        </button>
      </div>

      {seats.length === 0 ? (
        <div className="live-hud-empty">
          {hand?.hand_id
            ? "No opponent seats in current hand"
            : "Play a hand — stats appear when ACR imports it"}
        </div>
      ) : (
        seats.map((seat) => (
          <div
            key={`${seat.seat}-${seat.name}`}
            className="live-seat-anchor"
            style={{
              left: `${seat.xPct * 100}%`,
              top: `${seat.yPct * 100}%`,
            }}
          >
            <SeatHudBadge
              name={seat.name}
              stats={statsMap[seat.name] ?? null}
              layoutMode={layoutMode}
            />
          </div>
        ))
      )}
    </div>
  );
}
