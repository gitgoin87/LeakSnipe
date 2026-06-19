use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent};

const API_PORT: u16 = 8765;

struct BackendProcess(Mutex<Option<Child>>);

fn project_root() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| manifest.clone())
}

fn backend_script() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest
        .parent()
        .map(|p| p.join("backend").join("main.py"))
        .unwrap_or_else(|| manifest.join("backend").join("main.py"))
}

fn python_candidates() -> Vec<String> {
    let mut candidates = vec![
        "python".to_string(),
        "python3".to_string(),
        "py".to_string(),
    ];
    if let Ok(explicit) = std::env::var("LEAKSNIPE_PYTHON") {
        if !explicit.trim().is_empty() {
            candidates.insert(0, explicit);
        }
    }
    candidates
}

fn spawn_backend() -> Result<Child, String> {
    let script = backend_script();
    if !script.exists() {
        return Err(format!("Backend script not found: {}", script.display()));
    }

    let root = project_root();
    let port = std::env::var("LEAKSNIPE_API_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(API_PORT);

    let mut last_err = String::new();
    for py in python_candidates() {
        let mut cmd = if py == "py" {
            let mut c = Command::new(&py);
            c.arg("-3");
            c
        } else {
            Command::new(&py)
        };

        cmd.arg(&script)
            .env("LEAKSNIPE_ROOT", &root)
            .env("LEAKSNIPE_API_PORT", port.to_string())
            .env("LEAKSNIPE_API_HOST", "127.0.0.1")
            .stdout(Stdio::null())
            .stderr(Stdio::null());

        match cmd.spawn() {
            Ok(child) => return Ok(child),
            Err(err) => last_err = format!("{py}: {err}"),
        }
    }

    Err(format!(
        "Could not start Python backend ({last_err}). Install Python 3.9+ or set LEAKSNIPE_PYTHON."
    ))
}

fn stop_backend(state: &BackendProcess) {
    if let Ok(mut guard) = state.0.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[tauri::command]
fn get_api_base_url() -> String {
    let port = std::env::var("LEAKSNIPE_API_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(API_PORT);
    format!("http://127.0.0.1:{port}")
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend = BackendProcess(Mutex::new(None));

    match spawn_backend() {
        Ok(child) => {
            if let Ok(mut guard) = backend.0.lock() {
                *guard = Some(child);
            }
            std::thread::sleep(Duration::from_millis(400));
        }
        Err(err) => eprintln!("[leak-snipe] {err}"),
    }

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(backend)
        .invoke_handler(tauri::generate_handler![get_api_base_url])
        .build(tauri::generate_context!())
        .expect("error while running tauri application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            if let Some(state) = app_handle.try_state::<BackendProcess>() {
                stop_backend(&state);
            }
        }
    });
}
