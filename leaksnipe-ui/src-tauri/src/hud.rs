use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use serde::Serialize;
use tauri::webview::WebviewWindowBuilder;
use tauri::{AppHandle, Emitter, Manager, State, WebviewUrl};

const FALLBACK_X: f64 = 120.0;
const FALLBACK_Y: f64 = 80.0;
const FALLBACK_W: f64 = 900.0;
const FALLBACK_H: f64 = 650.0;

#[derive(Clone, Serialize)]
pub struct TableBounds {
    pub hwnd: isize,
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub title: String,
}

#[derive(Serialize)]
pub struct HudDiagnostics {
    pub hud_running: bool,
    pub overlay_exists: bool,
    pub is_dev: bool,
    pub table_count: u32,
    pub table_titles: Vec<String>,
    pub webview_url: String,
}

pub struct HudController {
    running: AtomicBool,
    stop_flag: Arc<AtomicBool>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl HudController {
    pub fn new() -> Self {
        Self {
            running: AtomicBool::new(false),
            stop_flag: Arc::new(AtomicBool::new(false)),
            thread: Mutex::new(None),
        }
    }
}

fn hud_webview_url() -> WebviewUrl {
    // App URL resolves via Vite dev server in debug and dist/hud.html in release.
    WebviewUrl::App("hud.html".into())
}

fn hud_webview_url_label() -> String {
    #[cfg(debug_assertions)]
    {
        "http://localhost:1420/hud.html (dev)".to_string()
    }
    #[cfg(not(debug_assertions))]
    {
        "hud.html (production)".to_string()
    }
}

fn ensure_overlay_window(app: &AppHandle) -> Result<(), String> {
    if app.get_webview_window("live-hud").is_some() {
        return Ok(());
    }

    // On Windows, WebviewWindowBuilder::build must not run on the IPC thread (WebView2 deadlock).
    WebviewWindowBuilder::new(app, "live-hud", hud_webview_url())
        .title("LeakSnipe Live HUD")
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(false)
        .visible(false)
        .inner_size(FALLBACK_W, FALLBACK_H)
        .position(FALLBACK_X, FALLBACK_Y)
        .build()
        .map_err(|e| format!("Failed to create HUD overlay: {e}"))?;

    Ok(())
}

fn show_fallback_overlay(app: &AppHandle) {
    let Some(win) = app.get_webview_window("live-hud") else {
        return;
    };
    let _ = win.set_position(tauri::PhysicalPosition::new(FALLBACK_X as i32, FALLBACK_Y as i32));
    let _ = win.set_size(tauri::PhysicalSize::new(FALLBACK_W as u32, FALLBACK_H as u32));
    let _ = win.show();
    let _ = app.emit(
        "hud-status",
        "Manual overlay — snap to ACR table when detected",
    );
}

fn position_overlay(app: &AppHandle, bounds: &TableBounds) {
    let Some(win) = app.get_webview_window("live-hud") else {
        return;
    };
    let _ = win.set_position(tauri::PhysicalPosition::new(bounds.x, bounds.y));
    let _ = win.set_size(tauri::PhysicalSize::new(bounds.width, bounds.height));
    let _ = win.show();
}

#[cfg(windows)]
fn title_looks_like_poker_table(title: &str) -> bool {
    let tl = title.to_lowercase();

    const LOBBY_PATTERNS: &[&str] = &["acr poker lobby", "winning poker lobby", "coinpoker lobby"];
    if LOBBY_PATTERNS.iter().any(|lp| tl.contains(lp)) {
        return false;
    }

    const KEYWORDS: &[&str] = &[
        "hold'em",
        "holdem",
        "omaha",
        "stud",
        "acr poker",
        "americas cardroom",
        "winning poker",
        "betacr",
        "coinpoker",
        "no limit",
        "pot limit",
        "fixed limit",
    ];
    if KEYWORDS.iter().any(|p| tl.contains(p)) {
        return true;
    }

    tl.contains("table") && (tl.contains("ante") || tl.contains("limit") || tl.contains("tournament"))
}

#[cfg(windows)]
fn collect_table_windows() -> Vec<TableBounds> {
    use std::ffi::OsString;
    use std::os::windows::ffi::OsStringExt;
    use windows::Win32::Foundation::{BOOL, HWND, LPARAM, RECT};
    use windows::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetWindowRect, GetWindowTextLengthW, GetWindowTextW, IsWindowVisible,
    };

    struct Search {
        tables: Vec<TableBounds>,
        lobbies: Vec<TableBounds>,
    }

    unsafe extern "system" fn enum_cb(hwnd: HWND, lparam: LPARAM) -> BOOL {
        let search = &mut *(lparam.0 as *mut Search);
        if IsWindowVisible(hwnd).as_bool() == false {
            return BOOL(1);
        }

        let len = GetWindowTextLengthW(hwnd);
        if len == 0 {
            return BOOL(1);
        }

        let mut buf = vec![0u16; (len + 1) as usize];
        let read = GetWindowTextW(hwnd, &mut buf);
        if read == 0 {
            return BOOL(1);
        }
        buf.truncate(read as usize);
        let title = OsString::from_wide(&buf).to_string_lossy().to_string();

        if !title_looks_like_poker_table(&title) {
            return BOOL(1);
        }

        let mut rect = RECT::default();
        if GetWindowRect(hwnd, &mut rect).is_err() {
            return BOOL(1);
        }

        let w = rect.right - rect.left;
        let h = rect.bottom - rect.top;
        if w < 150 || h < 100 {
            return BOOL(1);
        }

        let entry = TableBounds {
            hwnd: hwnd.0 as isize,
            x: rect.left,
            y: rect.top,
            width: w as u32,
            height: h as u32,
            title,
        };

        let tl = entry.title.to_lowercase();
        if tl.contains("lobby") {
            search.lobbies.push(entry);
        } else {
            search.tables.push(entry);
        }

        BOOL(1)
    }

    let mut search = Search {
        tables: Vec::new(),
        lobbies: Vec::new(),
    };

    unsafe {
        let _ = EnumWindows(
            Some(enum_cb),
            LPARAM(&mut search as *mut Search as isize),
        );
    }

    if !search.tables.is_empty() {
        return search.tables;
    }
    search.lobbies
}

#[cfg(not(windows))]
fn collect_table_windows() -> Vec<TableBounds> {
    Vec::new()
}

#[cfg(windows)]
fn find_primary_table() -> Option<TableBounds> {
    collect_table_windows().into_iter().next()
}

#[cfg(not(windows))]
fn find_primary_table() -> Option<TableBounds> {
    None
}

fn detection_loop(app: AppHandle, stop_flag: Arc<AtomicBool>) {
    let mut last: Option<TableBounds> = None;
    let mut misses: u32 = 0;

    while !stop_flag.load(Ordering::SeqCst) {
        if let Some(bounds) = find_primary_table() {
            misses = 0;
            let changed = last
                .as_ref()
                .map(|p| {
                    p.x != bounds.x
                        || p.y != bounds.y
                        || p.width != bounds.width
                        || p.height != bounds.height
                        || p.hwnd != bounds.hwnd
                })
                .unwrap_or(true);

            if changed {
                position_overlay(&app, &bounds);
                let _ = app.emit("hud-table-bounds", &bounds);
                last = Some(bounds);
            }
        } else {
            misses += 1;
            if misses == 1 || misses % 10 == 0 {
                let _ = app.emit(
                    "hud-status",
                    "No ACR table detected — showing manual overlay",
                );
            }
        }
        thread::sleep(Duration::from_millis(1500));
    }
}

async fn create_overlay_off_ipc_thread(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        ensure_overlay_window(&app)?;
        show_fallback_overlay(&app);
        Ok::<(), String>(())
    })
    .await
    .map_err(|e| format!("HUD window thread failed: {e}"))?
}

#[tauri::command]
pub async fn hud_start(app: AppHandle, hud: State<'_, HudController>) -> Result<(), String> {
    if hud.running.load(Ordering::SeqCst) {
        if app.get_webview_window("live-hud").is_some() {
            show_fallback_overlay(&app);
        } else {
            create_overlay_off_ipc_thread(app.clone()).await?;
        }
        return Ok(());
    }

    create_overlay_off_ipc_thread(app.clone()).await?;

    hud.stop_flag.store(false, Ordering::SeqCst);
    hud.running.store(true, Ordering::SeqCst);

    let stop_flag = hud.stop_flag.clone();
    let app_handle = app.clone();
    let handle = thread::spawn(move || detection_loop(app_handle, stop_flag));

    if let Ok(mut guard) = hud.thread.lock() {
        *guard = Some(handle);
    }

    Ok(())
}

#[tauri::command]
pub fn hud_stop(app: AppHandle, hud: State<'_, HudController>) -> Result<(), String> {
    hud.stop_flag.store(true, Ordering::SeqCst);
    hud.running.store(false, Ordering::SeqCst);

    if let Ok(mut guard) = hud.thread.lock() {
        if let Some(handle) = guard.take() {
            let _ = handle.join();
        }
    }

    if let Some(win) = app.get_webview_window("live-hud") {
        let _ = win.hide();
    }

    Ok(())
}

#[tauri::command]
pub fn hud_is_running(hud: State<'_, HudController>) -> bool {
    hud.running.load(Ordering::SeqCst)
}

#[tauri::command]
pub fn hud_diagnose(app: AppHandle, hud: State<'_, HudController>) -> HudDiagnostics {
    let tables = collect_table_windows();
    HudDiagnostics {
        hud_running: hud.running.load(Ordering::SeqCst),
        overlay_exists: app.get_webview_window("live-hud").is_some(),
        is_dev: cfg!(debug_assertions),
        table_count: tables.len() as u32,
        table_titles: tables.into_iter().map(|t| t.title).collect(),
        webview_url: hud_webview_url_label(),
    }
}
