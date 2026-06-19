mod hud;

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{Manager, RunEvent, State};

const API_PORT: u16 = 8765;
const HEALTH_CACHE_TTL: Duration = Duration::from_secs(2);

struct BackendState {
    child: Option<Child>,
    /// True when an external process already owns port 8765 (do not kill on exit).
    external: bool,
    last_error: String,
    last_restart_attempt: Option<Instant>,
    restart_failures: u32,
}

struct BackendProcess(Arc<Mutex<BackendState>>);

impl Clone for BackendProcess {
    fn clone(&self) -> Self {
        BackendProcess(Arc::clone(&self.0))
    }
}

#[derive(Serialize)]
struct SidecarStatus {
    healthy: bool,
    deps_installed: bool,
    port: u16,
    log_path: String,
    last_error: String,
}

fn api_port() -> u16 {
    std::env::var("LEAKSNIPE_API_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(API_PORT)
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap_or_else(|_| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."))
}

#[cfg(windows)]
fn normalize_windows_path(path: &PathBuf) -> String {
    let raw = path.to_string_lossy();
    let stripped = raw.strip_prefix(r"\\?\").unwrap_or(&raw);
    stripped.replace('/', "\\")
}

#[cfg(not(windows))]
fn normalize_windows_path(path: &PathBuf) -> String {
    path.to_string_lossy().into_owned()
}

fn backend_script() -> PathBuf {
    repo_root().join("sidecar").join("server.py")
}

fn sidecar_deps_installed() -> bool {
    let root = repo_root();
    let venv_py = root.join(".venv").join("Scripts").join("python.exe");
    let marker = root.join(".venv").join(".sidecar-deps-ok");
    venv_py.exists() && marker.exists()
}

fn is_windows_store_python_stub(path: &str) -> bool {
    let normalized = path.replace('/', "\\").to_ascii_lowercase();
    normalized.contains("\\microsoft\\windowsapps\\python")
        || normalized.contains("\\windowsapps\\pythonsoftwarefoundation")
        || normalized.contains("\\windowsapps\\python")
}

fn push_python_candidate(candidates: &mut Vec<String>, path: impl AsRef<str>) {
    let path = path.as_ref().trim();
    if path.is_empty() || is_windows_store_python_stub(path) {
        return;
    }
    if !candidates.iter().any(|existing| existing == path) {
        candidates.push(path.to_string());
    }
}

fn python_candidates() -> Vec<String> {
    let mut candidates = Vec::new();
    if let Ok(explicit) = std::env::var("LEAKSNIPE_PYTHON") {
        push_python_candidate(&mut candidates, explicit);
    }
    let venv_py = repo_root()
        .join(".venv")
        .join("Scripts")
        .join("python.exe");
    if venv_py.exists() {
        push_python_candidate(&mut candidates, venv_py.to_string_lossy());
    }
    if which_py_launcher() {
        candidates.push("py".to_string());
    }
    #[cfg(windows)]
    {
        if let Ok(prog) = std::env::var("ProgramFiles") {
            let root = PathBuf::from(prog).join("Python");
            if root.is_dir() {
                if let Ok(entries) = std::fs::read_dir(&root) {
                    for entry in entries.flatten() {
                        let exe = entry.path().join("python.exe");
                        if exe.exists() {
                            push_python_candidate(&mut candidates, exe.to_string_lossy());
                        }
                    }
                }
            }
        }
    }
    candidates
}

#[cfg(windows)]
fn which_py_launcher() -> bool {
    Command::new("where")
        .arg("py")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

#[cfg(not(windows))]
fn which_py_launcher() -> bool {
    Command::new("which")
        .arg("py")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn sidecar_log_path() -> PathBuf {
    std::env::temp_dir().join("leaksnipe_sidecar.log")
}

fn sidecar_log_tail(max_chars: usize) -> String {
    let path = sidecar_log_path();
    let Ok(text) = std::fs::read_to_string(&path) else {
        return String::new();
    };
    if text.len() <= max_chars {
        return text;
    }
    text[text.len().saturating_sub(max_chars)..].to_string()
}

fn sidecar_managed_externally() -> bool {
    matches!(
        std::env::var("LEAKSNIPE_SIDECAR_EXTERNAL").as_deref(),
        Ok("1") | Ok("true") | Ok("yes")
    )
}

fn sidecar_healthy(port: u16) -> bool {
    use std::io::{Read, Write};
    use std::net::{SocketAddr, TcpStream};

    let addr: SocketAddr = format!("127.0.0.1:{port}").parse().unwrap_or_else(|_| {
        SocketAddr::from(([127, 0, 0, 1], port))
    });
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(1500)) else {
        return false;
    };
    let deadline = Instant::now() + Duration::from_millis(2500);
    let req = format!(
        "GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }

    // Windows often splits headers and JSON body across TCP reads; one read misses api_version.
    let mut body = Vec::with_capacity(512);
    let mut buf = [0u8; 512];
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            break;
        }
        let _ = stream.set_read_timeout(Some(remaining));
        match stream.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                body.extend_from_slice(&buf[..n]);
                let text = String::from_utf8_lossy(&body);
                if text.contains("\"status\"") && text.contains("\"api_version\"") {
                    return true;
                }
                if body.len() >= 2048 {
                    break;
                }
            }
            Err(_) => break,
        }
    }
    false
}

fn sidecar_healthy_cached(port: u16) -> bool {
    static CACHE: std::sync::OnceLock<Mutex<Option<(Instant, bool)>>> = std::sync::OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(None));
    if let Ok(guard) = cache.lock() {
        if let Some((at, ok)) = *guard {
            if at.elapsed() < HEALTH_CACHE_TTL {
                return ok;
            }
        }
    }
    let ok = sidecar_healthy(port);
    if let Ok(mut guard) = cache.lock() {
        *guard = Some((Instant::now(), ok));
    }
    ok
}

fn port_in_use(port: u16) -> bool {
    use std::net::TcpListener;
    TcpListener::bind(("127.0.0.1", port)).is_err()
}

fn wait_for_healthy_sidecar(port: u16, attempts: u32, delay: Duration) -> bool {
    for _ in 0..attempts {
        if sidecar_healthy(port) {
            return true;
        }
        std::thread::sleep(delay);
    }
    false
}

fn free_api_port(port: u16) {
    if sidecar_healthy(port) {
        return;
    }
    #[cfg(windows)]
    {
        let script = format!(
            "Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | \
             Select-Object -ExpandProperty OwningProcess -Unique | \
             ForEach-Object {{ Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }}"
        );
        let _ = Command::new("powershell")
            .args(["-NoProfile", "-NonInteractive", "-Command", &script])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        std::thread::sleep(Duration::from_millis(500));
    }
}

struct SpawnOutcome {
    child: Option<Child>,
    external: bool,
}

/// Spawn sidecar or reuse an already-healthy listener on the API port.
/// When `force` is true, skip the external-managed wait (user-initiated restart).
fn spawn_backend(force: bool) -> Result<SpawnOutcome, String> {
    let script = backend_script();
    if !script.exists() {
        return Err(format!("Sidecar server not found: {}", script.display()));
    }

    let root = repo_root();
    let port = api_port();

    if sidecar_healthy_cached(port) {
        eprintln!("[leaksnipe-ui] Reusing healthy sidecar on port {port}");
        return Ok(SpawnOutcome {
            child: None,
            external: true,
        });
    }

    // Launch-LeakSnipe.bat / Start-Sidecar.bat may own the process — wait, never kill or respawn.
    if !force && sidecar_managed_externally() {
        eprintln!("[leaksnipe-ui] Waiting for externally managed sidecar on port {port}...");
        if wait_for_healthy_sidecar(port, 60, Duration::from_millis(500)) {
            eprintln!("[leaksnipe-ui] External sidecar ready on port {port}");
            return Ok(SpawnOutcome {
                child: None,
                external: true,
            });
        }
        let tail = sidecar_log_tail(1200);
        let tail_hint = if tail.is_empty() {
            String::new()
        } else {
            format!("\nLog tail:\n{tail}")
        };
        return Err(format!(
            "External sidecar on port {port} did not become healthy within 30s. \
             Check Start-Sidecar.bat or the sidecar console window. Log: {}{tail_hint}",
            sidecar_log_path().display()
        ));
    }

    // Another launcher may have just started the sidecar (Launch-LeakSnipe.bat / Start-Sidecar.bat).
    if wait_for_healthy_sidecar(port, 24, Duration::from_millis(500)) {
        eprintln!("[leaksnipe-ui] Reusing sidecar on port {port} (started by peer)");
        return Ok(SpawnOutcome {
            child: None,
            external: true,
        });
    }

    if port_in_use(port) {
        eprintln!("[leaksnipe-ui] Port {port} in use but health check slow - waiting for sidecar...");
        if wait_for_healthy_sidecar(port, 40, Duration::from_millis(500)) {
            eprintln!("[leaksnipe-ui] Reusing sidecar on port {port} after extended wait");
            return Ok(SpawnOutcome {
                child: None,
                external: true,
            });
        }
        eprintln!("[leaksnipe-ui] Port {port} still unhealthy after 20s - clearing stale listener");
        free_api_port(port);
        if wait_for_healthy_sidecar(port, 24, Duration::from_millis(500)) {
            eprintln!("[leaksnipe-ui] Reusing sidecar on port {port} after peer startup");
            return Ok(SpawnOutcome {
                child: None,
                external: true,
            });
        }
    }

    let log_path = sidecar_log_path();
    let log_file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .ok();

    let candidates = python_candidates();
    if candidates.is_empty() {
        return Err(
            "No usable Python found (.venv missing or only Windows Store stub). Run Install-Sidecar.bat"
                .to_string(),
        );
    }

    let mut last_err = String::new();
    for py in candidates {
        if py != "py" && is_windows_store_python_stub(&py) {
            continue;
        }
        let mut cmd = if py == "py" {
            let mut c = Command::new(&py);
            c.arg("-3");
            c
        } else {
            Command::new(&py)
        };

        cmd.arg(&script)
            .current_dir(&root)
            .env("LEAKSNIPE_ROOT", &root)
            .env("LEAKSNIPE_API_PORT", port.to_string())
            .env("LEAKSNIPE_API_HOST", "127.0.0.1");

        if let Some(ref file) = log_file {
            cmd.stdout(Stdio::from(file.try_clone().map_err(|e| e.to_string())?))
                .stderr(Stdio::from(file.try_clone().map_err(|e| e.to_string())?));
        } else {
            cmd.stdout(Stdio::null()).stderr(Stdio::null());
        }

        match cmd.spawn() {
            Ok(mut child) => {
                eprintln!(
                    "[leaksnipe-ui] Sidecar started (pid {}). Log: {}",
                    child.id(),
                    log_path.display()
                );
                for _ in 0..80 {
                    if sidecar_healthy(port) {
                        return Ok(SpawnOutcome {
                            child: Some(child),
                            external: false,
                        });
                    }
                    if child.try_wait().ok().flatten().is_some() {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(250));
                }
                if child.try_wait().ok().flatten().is_none() && port_in_use(port) {
                    eprintln!(
                        "[leaksnipe-ui] Sidecar pid {} bound port {port} - waiting for health...",
                        child.id()
                    );
                    for _ in 0..40 {
                        if sidecar_healthy(port) {
                            return Ok(SpawnOutcome {
                                child: Some(child),
                                external: false,
                            });
                        }
                        if child.try_wait().ok().flatten().is_some() {
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(250));
                    }
                }
                let _ = child.kill();
                let _ = child.wait();
                last_err = format!(
                    "{py}: sidecar exited or never became healthy (see {})",
                    log_path.display()
                );
                if port_in_use(port) {
                    break;
                }
            }
            Err(err) => last_err = format!("{py}: {err}"),
        }
    }

    let deps_hint = if sidecar_deps_installed() {
        "Python deps look installed (.venv) - sidecar failed to start. Check the log or run Start-Sidecar.bat"
    } else {
        "Run Install-Sidecar.bat once, then Start-Sidecar.bat (creates .venv and installs deps)"
    };
    let tail = sidecar_log_tail(1200);
    let tail_hint = if tail.is_empty() {
        String::new()
    } else {
        format!("\nLog tail:\n{tail}")
    };
    Err(format!(
        "Could not start Python sidecar ({last_err}). {deps_hint}. Log: {}{tail_hint}",
        sidecar_log_path().display()
    ))
}

fn apply_spawn(state: &BackendProcess, outcome: SpawnOutcome) {
    if let Ok(mut guard) = state.0.lock() {
        if let Some(mut old) = guard.child.take() {
            if !guard.external {
                let port = api_port();
                if sidecar_healthy(port) {
                    guard.child = Some(old);
                    guard.external = true;
                    guard.last_error.clear();
                    guard.restart_failures = 0;
                    if let Some(mut duplicate) = outcome.child {
                        let _ = duplicate.kill();
                        let _ = duplicate.wait();
                    }
                    return;
                }
                let _ = old.kill();
                let _ = old.wait();
            }
        }
        guard.child = outcome.child;
        guard.external = outcome.external;
        guard.last_error.clear();
        if guard.external || guard.child.is_some() {
            guard.restart_failures = 0;
        }
    }
}

fn sidecar_start_script() -> PathBuf {
    repo_root().join("scripts").join("start-sidecar.ps1")
}

fn launch_sidecar_via_ps1() -> Result<(), String> {
    let port = api_port();
    if sidecar_healthy(port) {
        eprintln!("[leaksnipe-ui] Sidecar already healthy on port {port} - skipping start-sidecar.ps1");
        return Ok(());
    }

    let root = repo_root();
    let ps1 = sidecar_start_script();
    if !ps1.exists() {
        return Err(format!(
            "Sidecar launcher not found: {}. Run Start-Sidecar.bat manually.",
            ps1.display()
        ));
    }
    let ps1_path = normalize_windows_path(&ps1);
    let root_path = normalize_windows_path(&root);
    let status = Command::new("powershell")
        .args([
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            &ps1_path,
        ])
        .current_dir(&root_path)
        .env("LEAKSNIPE_ROOT", &root_path)
        .status()
        .map_err(|err| format!("Could not run start-sidecar.ps1: {err}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "start-sidecar.ps1 failed (exit {}). Run Start-Sidecar.bat manually. Log: {}",
            status.code().unwrap_or(-1),
            sidecar_log_path().display()
        ))
    }
}

fn stop_backend(state: &BackendProcess) {
    if let Ok(mut guard) = state.0.lock() {
        if guard.external {
            guard.child = None;
            return;
        }
        if let Some(mut child) = guard.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

const MAX_SIDECAR_RESTART_FAILURES: u32 = 3;
const SIDECAR_RESTART_COOLDOWN: Duration = Duration::from_secs(60);

fn start_sidecar_monitor(state: BackendProcess) {
    std::thread::spawn(move || {
        loop {
            std::thread::sleep(Duration::from_secs(15));
            let port = api_port();
            if sidecar_healthy(port) {
                if let Ok(mut guard) = state.0.lock() {
                    if guard.child.is_none() && !guard.external {
                        guard.external = true;
                    }
                    guard.restart_failures = 0;
                }
                continue;
            }

            let (should_restart, cooldown_elapsed) = {
                let Ok(guard) = state.0.lock() else {
                    continue;
                };
                if guard.external {
                    // Externally launched sidecar died — retry via start-sidecar.ps1 only.
                    if guard.restart_failures >= MAX_SIDECAR_RESTART_FAILURES {
                        (false, false)
                    } else if let Some(last) = guard.last_restart_attempt {
                        (true, last.elapsed() >= SIDECAR_RESTART_COOLDOWN)
                    } else {
                        (true, true)
                    }
                } else if guard.restart_failures >= MAX_SIDECAR_RESTART_FAILURES {
                    (false, false)
                } else if let Some(last) = guard.last_restart_attempt {
                    (
                        true,
                        last.elapsed() >= SIDECAR_RESTART_COOLDOWN,
                    )
                } else {
                    (true, true)
                }
            };

            if !should_restart {
                continue;
            }
            if !cooldown_elapsed {
                continue;
            }

            if sidecar_healthy(port) {
                if let Ok(mut guard) = state.0.lock() {
                    if guard.child.is_none() {
                        guard.external = true;
                    }
                    guard.restart_failures = 0;
                    guard.last_error.clear();
                }
                continue;
            }

            if let Ok(mut guard) = state.0.lock() {
                guard.last_restart_attempt = Some(Instant::now());
            }

            eprintln!("[leaksnipe-ui] Sidecar offline on port {port} - attempting restart...");
            let external_only = state
                .0
                .lock()
                .map(|g| g.external)
                .unwrap_or(false);
            let restart_result = if external_only {
                launch_sidecar_via_ps1().and_then(|()| {
                    if sidecar_healthy(port) {
                        Ok(SpawnOutcome {
                            child: None,
                            external: true,
                        })
                    } else {
                        Err(format!(
                            "External sidecar still unhealthy after start-sidecar.ps1. Log: {}",
                            sidecar_log_path().display()
                        ))
                    }
                })
            } else {
                spawn_backend(false)
            };
            match restart_result {
                Ok(outcome) => apply_spawn(&state, outcome),
                Err(err) => {
                    eprintln!("[leaksnipe-ui] Sidecar auto-restart failed: {err}");
                    if let Ok(mut guard) = state.0.lock() {
                        guard.last_error = err;
                        guard.restart_failures = guard.restart_failures.saturating_add(1);
                        if guard.restart_failures >= MAX_SIDECAR_RESTART_FAILURES {
                            eprintln!(
                                "[leaksnipe-ui] Sidecar auto-restart paused after {} failures. Run Start-Sidecar.bat manually.",
                                MAX_SIDECAR_RESTART_FAILURES
                            );
                        }
                    }
                }
            }
        }
    });
}

fn python_hud_pid_path() -> PathBuf {
    std::env::temp_dir().join("leaksnipe_python_hud.pid")
}

fn write_python_hud_pid(pid: u32) -> Result<(), String> {
    std::fs::write(python_hud_pid_path(), pid.to_string())
        .map_err(|err| format!("Could not write HUD PID file: {err}"))
}

#[cfg(windows)]
fn process_is_running(pid: u32) -> bool {
    let output = Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/NH"])
        .output();
    match output {
        Ok(out) => {
            let text = String::from_utf8_lossy(&out.stdout);
            text.contains(&pid.to_string())
        }
        Err(_) => false,
    }
}

#[cfg(not(windows))]
fn process_is_running(pid: u32) -> bool {
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn stop_python_hud_process() -> Result<bool, String> {
    let pid_path = python_hud_pid_path();
    let mut killed = false;

    if let Ok(content) = std::fs::read_to_string(&pid_path) {
        if let Ok(pid) = content.trim().parse::<u32>() {
            if process_is_running(pid) {
                #[cfg(windows)]
                {
                    killed = Command::new("taskkill")
                        .args(["/PID", &pid.to_string(), "/T", "/F"])
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .status()
                        .map(|s| s.success())
                        .unwrap_or(false);
                }
                #[cfg(not(windows))]
                {
                    killed = Command::new("kill")
                        .args(["-TERM", &pid.to_string()])
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .status()
                        .map(|s| s.success())
                        .unwrap_or(false);
                }
            }
        }
        let _ = std::fs::remove_file(&pid_path);
    }

    #[cfg(windows)]
    if !killed {
        let script = r#"
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'poker_gui\.py.*--live-hud' } |
  ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    $true
  } | Select-Object -First 1
"#;
        if let Ok(out) = Command::new("powershell")
            .args(["-NoProfile", "-NonInteractive", "-Command", script])
            .output()
        {
            let text = String::from_utf8_lossy(&out.stdout);
            killed = text.trim().eq_ignore_ascii_case("true");
        }
    }

    Ok(killed)
}

#[tauri::command]
fn launch_python_hud() -> Result<(), String> {
    let root = repo_root();
    let script = root.join("poker_gui.py");
    if !script.exists() {
        return Err(format!(
            "Python HUD fallback not found: {}",
            script.display()
        ));
    }

    let log_dir = root.join("logs");
    let _ = std::fs::create_dir_all(&log_dir);
    let log_path = std::env::temp_dir().join("leaksnipe_python_hud.log");

    let mut last_err = String::new();
    for py in python_candidates() {
        let mut cmd = if py == "py" {
            let mut c = Command::new(&py);
            c.arg("-3");
            c
        } else {
            Command::new(&py)
        };

        let log_file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path);

        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            const CREATE_NEW_PROCESS_GROUP: u32 = 0x00000200;
            cmd.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
        }

        if let Ok(file) = log_file {
            let _ = cmd.stdout(Stdio::from(file.try_clone().unwrap_or_else(|_| {
                std::fs::File::open(&log_path).unwrap()
            })));
            let _ = cmd.stderr(Stdio::from(file));
        } else {
            cmd.stdout(Stdio::null()).stderr(Stdio::null());
        }

        match cmd
            .arg(&script)
            .arg("--live-hud")
            .current_dir(&root)
            .env("LEAKSNIPE_ROOT", &root)
            .spawn()
        {
            Ok(child) => {
                let _ = write_python_hud_pid(child.id());
                return Ok(());
            }
            Err(err) => last_err = format!("{py}: {err}"),
        }
    }

    Err(format!(
        "Could not launch poker_gui.py ({last_err}). Install Python 3.9+ and pywin32 (pip install pywin32). Log: {}",
        log_path.display()
    ))
}

#[tauri::command]
fn stop_python_hud() -> Result<(), String> {
    let _ = stop_python_hud_process()?;
    Ok(())
}

#[tauri::command]
fn is_python_hud_running() -> bool {
    let pid_path = python_hud_pid_path();
    let Ok(content) = std::fs::read_to_string(&pid_path) else {
        return false;
    };
    let Ok(pid) = content.trim().parse::<u32>() else {
        let _ = std::fs::remove_file(&pid_path);
        return false;
    };
    if process_is_running(pid) {
        return true;
    }
    let _ = std::fs::remove_file(&pid_path);
    false
}

#[tauri::command]
fn get_api_base_url() -> String {
    format!("http://127.0.0.1:{}", api_port())
}

#[tauri::command]
fn sidecar_status(state: State<BackendProcess>) -> SidecarStatus {
    let port = api_port();
    let last_error = state
        .0
        .lock()
        .map(|g| g.last_error.clone())
        .unwrap_or_default();
    SidecarStatus {
        healthy: sidecar_healthy_cached(port),
        deps_installed: sidecar_deps_installed(),
        port,
        log_path: sidecar_log_path().to_string_lossy().into_owned(),
        last_error,
    }
}

#[tauri::command]
fn restart_sidecar(state: State<BackendProcess>) -> Result<(), String> {
    let port = api_port();
    if let Ok(mut guard) = state.0.lock() {
        guard.restart_failures = 0;
        guard.last_restart_attempt = None;
    }
    if sidecar_healthy(port) {
        apply_spawn(
            &state,
            SpawnOutcome {
                child: None,
                external: true,
            },
        );
        return Ok(());
    }
    stop_backend(&state);
    if launch_sidecar_via_ps1().is_ok() && sidecar_healthy(port) {
        apply_spawn(
            &state,
            SpawnOutcome {
                child: None,
                external: true,
            },
        );
        return Ok(());
    }
    let outcome = spawn_backend(true)?;
    apply_spawn(&state, outcome);
    Ok(())
}

#[tauri::command]
fn launch_sidecar_window(state: State<BackendProcess>) -> Result<(), String> {
    let port = api_port();
    if sidecar_healthy(port) {
        apply_spawn(
            &state,
            SpawnOutcome {
                child: None,
                external: true,
            },
        );
        return Ok(());
    }
    launch_sidecar_via_ps1()?;
    if !wait_for_healthy_sidecar(port, 60, Duration::from_millis(500)) {
        return Err(format!(
            "Sidecar did not become healthy after start-sidecar.ps1. Log: {}",
            sidecar_log_path().display()
        ));
    }
    apply_spawn(
        &state,
        SpawnOutcome {
            child: None,
            external: true,
        },
    );
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend = BackendProcess(Arc::new(Mutex::new(BackendState {
        child: None,
        external: false,
        last_error: String::new(),
        last_restart_attempt: None,
        restart_failures: 0,
    })));

    // Do not block the Tauri window on sidecar spawn/health — UI shows immediately with offline banner.
    let backend_for_spawn = backend.clone();
    std::thread::spawn(move || {
        match spawn_backend(false) {
            Ok(outcome) => apply_spawn(&backend_for_spawn, outcome),
            Err(err) => {
                eprintln!("[leaksnipe-ui] {err}");
                if let Ok(mut guard) = backend_for_spawn.0.lock() {
                    guard.last_error = err;
                }
            }
        }
    });

    start_sidecar_monitor(backend.clone());

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(hud::HudController::new())
        .manage(backend)
        .invoke_handler(tauri::generate_handler![
            get_api_base_url,
            sidecar_status,
            restart_sidecar,
            launch_sidecar_window,
            launch_python_hud,
            stop_python_hud,
            is_python_hud_running,
            hud::hud_start,
            hud::hud_stop,
            hud::hud_is_running,
            hud::hud_diagnose,
        ])
        .build(tauri::generate_context!())
        .expect("error while running tauri application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            let _ = stop_python_hud_process();
            if let Some(state) = app_handle.try_state::<BackendProcess>() {
                stop_backend(&state);
            }
        }
    });
}
