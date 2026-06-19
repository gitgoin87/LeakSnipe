import { invoke } from "@tauri-apps/api/core";



function isTauri(): boolean {

  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

}



export type HudBackend = "python" | "tauri";



export type HudDiagnostics = {

  hud_running: boolean;

  overlay_exists: boolean;

  is_dev: boolean;

  table_count: number;

  table_titles: string[];

  webview_url: string;

};



export function resolveHudBackend(settings?: { live_hud_backend?: string } | null): HudBackend {

  const raw = (settings?.live_hud_backend as string | undefined) ?? "python";

  return raw === "tauri" ? "tauri" : "python";

}



export async function startLiveHud(): Promise<void> {

  if (!isTauri()) {

    throw new Error("Tauri Live HUD requires the LeakSnipe desktop app (Tauri)");

  }

  await invoke("hud_start");

}



export async function stopLiveHud(): Promise<void> {

  if (!isTauri()) return;

  await invoke("hud_stop");

}



export async function isLiveHudRunning(): Promise<boolean> {

  if (!isTauri()) return false;

  return invoke<boolean>("hud_is_running");

}



export async function diagnoseLiveHud(): Promise<HudDiagnostics> {

  if (!isTauri()) {

    return {

      hud_running: false,

      overlay_exists: false,

      is_dev: import.meta.env.DEV,

      table_count: 0,

      table_titles: [],

      webview_url: "browser (not Tauri)",

    };

  }

  return invoke<HudDiagnostics>("hud_diagnose");

}



export async function launchPythonLiveHud(): Promise<void> {

  if (!isTauri()) {

    throw new Error("Python Live HUD requires the LeakSnipe desktop app");

  }

  await invoke("launch_python_hud");

}



export async function stopPythonLiveHud(): Promise<void> {

  if (!isTauri()) return;

  await invoke("stop_python_hud");

}



export async function isPythonHudRunning(): Promise<boolean> {

  if (!isTauri()) return false;

  return invoke<boolean>("is_python_hud_running");

}



/** @deprecated Use launchPythonLiveHud */

export async function launchPythonHudFallback(): Promise<void> {

  return launchPythonLiveHud();

}



export async function syncLiveHud(

  enabled: boolean,

  backend: HudBackend = "python",

): Promise<void> {

  if (!isTauri()) {

    if (enabled && backend === "tauri") {

      throw new Error("Tauri Live HUD requires the LeakSnipe desktop app");

    }

    return;

  }



  // Python HUD runs as a detached subprocess — never auto-start the Tauri webview overlay.

  if (backend !== "tauri") {

    try {

      const running = await isLiveHudRunning();

      if (running) {

        await stopLiveHud();

      }

    } catch (err) {

      console.warn("[Live HUD] Could not stop Tauri overlay:", err);

    }

    if (!enabled) {

      try {

        await stopPythonLiveHud();

      } catch (err) {

        console.warn("[Live HUD] Could not stop Python HUD:", err);

      }

    }

    return;

  }



  try {

    const running = await isLiveHudRunning();

    if (enabled && !running) {

      await startLiveHud();

    } else if (!enabled && running) {

      await stopLiveHud();

    } else if (enabled && running) {

      await startLiveHud();

    }

  } catch (err) {

    const message = err instanceof Error ? err.message : String(err);

    console.error("[Live HUD]", message);

    throw new Error(message);

  }

}



export async function testLiveHud(): Promise<HudDiagnostics> {

  await startLiveHud();

  return diagnoseLiveHud();

}


