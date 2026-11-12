//! Library, player and settings UI served on 127.0.0.1.
//!
//! The server starts the first time the library is opened, so it costs
//! nothing while unused. The browser does video decoding and seeking. Access
//! needs a random per-run token (exchanged for a SameSite=Strict cookie), the
//! Host header must be the loopback address (DNS-rebinding guard), and every
//! POST must carry a custom header (blocks cross-site form posts).

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::Sender;
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tiny_http::{Header, Method, Request, Response, Server, StatusCode};

use crate::config::{Paths, Settings, SharedSettings};
use crate::controller::{diagnostics, SharedStatus};
use crate::ffmpeg::Ffmpeg;
use crate::live::LiveShared;
use crate::models::Cmd;
use crate::recorder::Recorder;
use crate::assets::{Art, Assets};
use crate::{audio, hotkey, library, win};

static INDEX: &str = include_str!("../ui/index.html");
static ICON: &[u8] = include_bytes!("../assets/league_highlights.ico");
const MAX_RANGE: u64 = 8 * 1024 * 1024;

pub struct WebCtx {
    pub cfg: SharedSettings,
    pub paths: Paths,
    pub status: SharedStatus,
    pub tx: Sender<Cmd>,
    pub ff: Option<Arc<Ffmpeg>>,
    pub recorder: Option<Arc<Recorder>>,
    pub live: Arc<LiveShared>,
    pub busy: Arc<AtomicUsize>,
    pub assets: Arc<Assets>,
}

pub struct Web {
    ctx: Arc<WebCtx>,
    running: Mutex<Option<(u16, String)>>,
}

impl Web {
    pub fn new(ctx: WebCtx) -> Web {
        Web {
            ctx: Arc::new(ctx),
            running: Mutex::new(None),
        }
    }

    fn ensure_started(&self) -> Option<(u16, String)> {
        let mut running = self.running.lock().unwrap();
        if running.is_none() {
            let server = match Server::http("127.0.0.1:0") {
                Ok(s) => s,
                Err(e) => {
                    log::error!("Could not start the library server: {e}");
                    return None;
                }
            };
            let port = server.server_addr().to_ip().map(|a| a.port()).unwrap_or(0);
            let token = win::random_token();
            log::info!("Library UI listening on 127.0.0.1:{port}");
            let ctx = self.ctx.clone();
            let thread_token = token.clone();
            let _ = std::thread::Builder::new().name("web".into()).spawn(move || {
                let server = Arc::new(server);
                for request in server.incoming_requests() {
                    let ctx = ctx.clone();
                    let token = thread_token.clone();
                    let _ = std::thread::Builder::new()
                        .name("web-request".into())
                        .spawn(move || handle(&ctx, request, port, &token));
                }
            });
            *running = Some((port, token));
        }
        running.clone()
    }

    /// Open the library in its own window (WebView2); fall back to the
    /// default browser only if WebView2 is missing.
    /// Open (or focus) the window on the post-match review view.
    pub fn open_review(&self) {
        if crate::window::is_open() {
            crate::window::run_script("window.openReview && openReview()");
            return;
        }
        let Some(url) = self.url() else { return };
        let url = format!("{url}#review");
        let data_dir = self.ctx.paths.root.join("webview");
        if let Err(e) = crate::window::open(url.clone(), data_dir) {
            log::warn!("Library window unavailable: {e}");
            win::shell_open(&url);
        }
    }

    pub fn open(&self) {
        let Some(mut url) = self.url() else { return };
        if std::env::var_os("LH_UI_TEST").is_some() {
            // Developer check: the page plays a clip and logs the result.
            url.push_str("#uitest");
        }
        let data_dir = self.ctx.paths.root.join("webview");
        if let Err(e) = crate::window::open(url.clone(), data_dir) {
            log::warn!("Library window unavailable: {e}");
            win::shell_open(&url);
        }
    }

    pub fn url(&self) -> Option<String> {
        let (port, token) = self.ensure_started()?;
        Some(format!("http://127.0.0.1:{port}/?t={token}"))
    }
}

fn header(name: &str, value: &str) -> Header {
    Header::from_bytes(name.as_bytes(), value.as_bytes()).unwrap()
}

fn get_header(request: &Request, name: &str) -> Option<String> {
    request
        .headers()
        .iter()
        .find(|h| h.field.as_str().as_str().eq_ignore_ascii_case(name))
        .map(|h| h.value.as_str().to_string())
}

fn json_response(value: &Value) -> Response<std::io::Cursor<Vec<u8>>> {
    Response::from_string(value.to_string())
        .with_header(header("Content-Type", "application/json; charset=utf-8"))
        .with_header(header("Cache-Control", "no-store"))
}

fn error(code: u16, message: &str) -> Response<std::io::Cursor<Vec<u8>>> {
    json_response(&json!({ "error": message })).with_status_code(StatusCode(code))
}

fn read_body(request: &mut Request) -> Value {
    let mut text = String::new();
    let _ = request.as_reader().take(256 * 1024).read_to_string(&mut text);
    serde_json::from_str(&text).unwrap_or(Value::Null)
}

fn cookie_token(request: &Request) -> Option<String> {
    let cookies = get_header(request, "Cookie")?;
    cookies
        .split(';')
        .filter_map(|c| c.trim().split_once('='))
        .find(|(k, _)| *k == "lh")
        .map(|(_, v)| v.to_string())
}

fn handle(ctx: &WebCtx, mut request: Request, port: u16, token: &str) {
    let host_ok = get_header(&request, "Host")
        .is_some_and(|h| h == format!("127.0.0.1:{port}") || h == format!("localhost:{port}"));
    if !host_ok {
        let _ = request.respond(error(403, "Forbidden"));
        return;
    }
    let url = request.url().to_string();
    let (path, query) = url.split_once('?').unwrap_or((url.as_str(), ""));
    let path = path.to_string();
    let params: Vec<(String, String)> = query
        .split('&')
        .filter_map(|p| p.split_once('='))
        .map(|(k, v)| (crate::util::percent_decode(k), crate::util::percent_decode(v)))
        .collect();

    // Token exchange: /?t=TOKEN -> cookie -> redirect to /
    if let Some((_, t)) = params.iter().find(|(k, _)| k == "t") {
        if t == token {
            let response = Response::empty(StatusCode(302))
                .with_header(header("Location", "/"))
                .with_header(header(
                    "Set-Cookie",
                    &format!("lh={token}; HttpOnly; SameSite=Strict; Path=/"),
                ));
            let _ = request.respond(response);
            return;
        }
    }
    if cookie_token(&request).as_deref() != Some(token) {
        let page = "<!doctype html><meta charset=utf-8><title>League Highlights</title>\
            <body style='font:15px system-ui;background:#0e1116;color:#c9d1d9;padding:40px'>\
            Open the library from the League Highlights tray icon.";
        let _ = request.respond(
            Response::from_string(page)
                .with_status_code(StatusCode(403))
                .with_header(header("Content-Type", "text/html; charset=utf-8")),
        );
        return;
    }
    let method = request.method().clone();
    if method == Method::Post && get_header(&request, "X-LH").as_deref() != Some("1") {
        let _ = request.respond(error(403, "Missing request header"));
        return;
    }

    let clip_dir = ctx.cfg.read().unwrap().clip_dir();
    let segments: Vec<&str> = path.trim_start_matches('/').split('/').collect();
    let response = match (&method, segments.as_slice()) {
        (Method::Get, [""]) => {
            let _ = request.respond(
                Response::from_string(INDEX)
                    .with_header(header("Content-Type", "text/html; charset=utf-8"))
                    .with_header(header("Cache-Control", "no-store"))
                    .with_header(header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")),
            );
            return;
        }
        (Method::Get, ["favicon.ico"]) => {
            let _ = request.respond(
                Response::from_data(ICON).with_header(header("Content-Type", "image/x-icon")),
            );
            return;
        }
        (Method::Get, ["media", file]) | (Method::Get, ["thumb", file]) => {
            let file = crate::util::percent_decode(file);
            let is_thumb = segments[0] == "thumb";
            let resolved = library::resolve(&clip_dir, &file).map(|p| {
                if is_thumb {
                    p.with_extension("jpg")
                } else {
                    p
                }
            });
            match resolved.filter(|p| p.is_file()) {
                Some(p) => {
                    serve_file(request, &p, if is_thumb { "image/jpeg" } else { "video/mp4" });
                    return;
                }
                None => error(404, "Not found"),
            }
        }
        (Method::Get, ["champ", name, kind]) => {
            let name = crate::util::percent_decode(name);
            let enabled = ctx.cfg.read().unwrap().champion_art;
            let found = Art::parse(kind)
                .filter(|_| enabled)
                .and_then(|art| ctx.assets.champion(&name, art));
            match found {
                Some((path, ctype)) => {
                    serve_file(request, &path, ctype);
                    return;
                }
                None => error(404, "No art"),
            }
        }
        (Method::Get, ["api", "status"]) => json_response(&status_json(ctx)),
        (Method::Get, ["api", "clips"]) => json_response(&json!({
            "clips": library::scan(&clip_dir),
            "clip_dir": clip_dir.to_string_lossy(),
        })),
        (Method::Get, ["api", "settings"]) => json_response(&settings_json(&ctx.cfg.read().unwrap())),
        (Method::Get, ["api", "devices"]) => json_response(&json!({
            "system": audio::list_devices(true),
            "microphone": audio::list_devices(false),
        })),
        (Method::Post, ["api", "settings"]) => {
            let body = read_body(&mut request);
            match update_settings(ctx, body) {
                Ok(value) => json_response(&value),
                Err(e) => error(400, &e),
            }
        }
        (Method::Post, ["api", "hotkey-capture"]) => {
            let body = read_body(&mut request);
            hotkey::set_enabled(!body["active"].as_bool().unwrap_or(false));
            json_response(&json!({"ok": true}))
        }
        (Method::Post, ["api", "action", action]) => {
            match *action {
                "save" => {
                    let _ = ctx.tx.send(Cmd::Hotkey);
                }
                "toggle" => {
                    let _ = ctx.tx.send(Cmd::ToggleRecording);
                }
                "open-folder" => {
                    let _ = std::fs::create_dir_all(&clip_dir);
                    win::open_folder(&clip_dir);
                }
                _ => {}
            }
            json_response(&json!({"ok": true}))
        }
        (Method::Get, ["api", "storage"]) => {
            let days = ctx.cfg.read().unwrap().cleanup_days;
            let (count, bytes) = library::cleanup(&clip_dir, days.max(30), true);
            json_response(&json!({
                "storage": library::storage(&clip_dir),
                "cleanup_days": days,
                "cleanup_preview": { "days": days.max(30), "clips": count, "bytes": bytes },
            }))
        }
        (Method::Post, ["api", "cleanup"]) => {
            let body = read_body(&mut request);
            let days = body["days"].as_u64().unwrap_or(30).clamp(1, 3650) as u32;
            let dry_run = body["dry_run"].as_bool().unwrap_or(true);
            let (clips, bytes) = library::cleanup(&clip_dir, days, dry_run);
            json_response(&json!({ "clips": clips, "bytes": bytes, "dry_run": dry_run }))
        }
        (Method::Post, ["api", "clips", action]) => {
            let body = read_body(&mut request);
            let paths: Vec<std::path::PathBuf> = body["files"]
                .as_array()
                .map(|files| files.iter().filter_map(|f| f.as_str()).filter_map(|f| library::resolve(&clip_dir, f)).collect())
                .unwrap_or_default();
            match *action {
                "delete" => {
                    paths.iter().for_each(|p| library::delete(p));
                    json_response(&json!({ "ok": true, "deleted": paths.len() }))
                }
                "copy" => match win::copy_files_to_clipboard(&paths) {
                    Ok(()) => json_response(&json!({ "ok": true, "copied": paths.len() })),
                    Err(e) => error(400, &e),
                },
                "reviewed" => {
                    for path in &paths {
                        let _ = library::set_reviewed(path, true);
                    }
                    json_response(&json!({ "ok": true }))
                }
                _ => error(404, "Unknown action"),
            }
        }
        (Method::Post, ["api", "clip", file, action]) => {
            let file = crate::util::percent_decode(file);
            let body = read_body(&mut request);
            match library::resolve(&clip_dir, &file) {
                None => error(404, "Clip not found"),
                Some(clip) => match clip_action(ctx, &clip, action, &body) {
                    Ok(v) => json_response(&v),
                    Err(e) => error(400, &e),
                },
            }
        }
        _ => error(404, "Not found"),
    };
    let _ = request.respond(response);
}

fn clip_action(ctx: &WebCtx, clip: &Path, action: &str, body: &Value) -> Result<Value, String> {
    match action {
        "rate" => {
            library::set_rating(clip, body["rating"].as_str().unwrap_or(""))?;
            Ok(json!({"ok": true}))
        }
        "delete" => {
            library::delete(clip);
            Ok(json!({"ok": true}))
        }
        "reviewed" => {
            library::set_reviewed(clip, body["reviewed"].as_bool().unwrap_or(true))?;
            Ok(json!({"ok": true}))
        }
        "copy" => {
            win::copy_files_to_clipboard(&[clip.to_path_buf()])?;
            Ok(json!({"ok": true}))
        }
        "discord" => {
            let ff = ctx.ff.as_ref().ok_or("FFmpeg is missing")?;
            let start = body["start"].as_f64().unwrap_or(0.0).max(0.0);
            let end = body["end"].as_f64().ok_or("Missing end")?;
            let target = (ctx.cfg.read().unwrap().discord_target_mb * 1_000_000.0) as u64;
            let (path, size) = crate::share::discord_copy(ff, clip, start, end, target)?;
            let copied = win::copy_files_to_clipboard(&[path.clone()]).is_ok();
            Ok(json!({ "ok": true, "file": path.file_name().map(|f| f.to_string_lossy().into_owned()), "size": size, "copied": copied }))
        }
        "reveal" => {
            win::reveal_in_explorer(clip);
            Ok(json!({"ok": true}))
        }
        "trim" => {
            let ff = ctx.ff.as_ref().ok_or("FFmpeg is missing")?;
            let start = body["start"].as_f64().ok_or("Missing start")?;
            let end = body["end"].as_f64().ok_or("Missing end")?;
            let replace = body["replace"].as_bool().unwrap_or(false);
            let file = library::trim(ff, clip, start, end, replace)?;
            Ok(json!({"ok": true, "file": file}))
        }
        _ => Err("Unknown action".into()),
    }
}

fn status_json(ctx: &WebCtx) -> Value {
    let status = ctx.status.read().unwrap().clone();
    let (live_text, live_connected) = ctx.live.status.lock().unwrap().clone();
    let current = ctx.live.current_match.lock().unwrap().clone();
    json!({
        "status": status,
        "now": crate::util::now(),
        "diagnostics": diagnostics(&ctx.recorder),
        "live": { "text": live_text, "connected": live_connected, "match": current },
        "queued": ctx.busy.load(Ordering::SeqCst),
        "ffmpeg": ctx.ff.as_ref().map(|f| f.exe.to_string_lossy().into_owned()),
        "version": env!("CARGO_PKG_VERSION"),
    })
}

fn settings_json(settings: &Settings) -> Value {
    let mut value = serde_json::to_value(settings).unwrap_or(Value::Null);
    if let Value::Object(map) = &mut value {
        // Keep the UI payload to the fields it edits.
        map.retain(|k, _| Settings::default_keys().contains(k));
        map.insert("launch_with_windows".into(), json!(win::startup_enabled()));
    }
    value
}

impl Settings {
    fn default_keys() -> Vec<String> {
        match serde_json::to_value(Settings::default()) {
            Ok(Value::Object(map)) => map.keys().cloned().collect(),
            _ => Vec::new(),
        }
    }
}

fn update_settings(ctx: &WebCtx, body: Value) -> Result<Value, String> {
    let Value::Object(changes) = body else {
        return Err("Expected a JSON object".into());
    };
    let known = Settings::default_keys();
    let (old, new) = {
        let current = ctx.cfg.read().unwrap().clone();
        let Ok(Value::Object(mut merged)) = serde_json::to_value(&current) else {
            return Err("Settings could not be read".into());
        };
        for (key, value) in changes {
            if known.contains(&key) && key != "segment_seconds" {
                merged.insert(key, value);
            }
        }
        let mut new = Settings::from_map(merged);
        new.extra = current.extra.clone();
        (current, new)
    };
    if new.clip_dir != old.clip_dir {
        let dir = new.clip_dir();
        if !dir.is_absolute() {
            return Err("The clip folder must be an absolute path".into());
        }
        std::fs::create_dir_all(&dir).map_err(|e| format!("Could not use {}: {e}", dir.display()))?;
    }
    if new.launch_with_windows != win::startup_enabled() {
        win::set_startup(new.launch_with_windows)?;
    }
    if new.hotkey_vk != old.hotkey_vk || new.hotkey_modifiers != old.hotkey_modifiers {
        hotkey::configure(new.hotkey_vk, &new.hotkey_modifiers);
        log::info!("Global clip hotkey changed to {}", new.hotkey_display);
    }
    new.save(&ctx.paths.settings).map_err(|e| e.to_string())?;
    *ctx.cfg.write().unwrap() = new.clone();
    let _ = ctx.tx.send(Cmd::SettingsChanged);
    Ok(settings_json(&new))
}

fn parse_range(value: &str, len: u64) -> Option<(u64, u64)> {
    let spec = value.trim().strip_prefix("bytes=")?.split(',').next()?.trim();
    let (start, end) = spec.split_once('-')?;
    if len == 0 {
        return None;
    }
    let (start, end) = if start.is_empty() {
        let suffix: u64 = end.parse().ok()?;
        (len.saturating_sub(suffix), len - 1)
    } else {
        let start: u64 = start.parse().ok()?;
        let end = if end.is_empty() { len - 1 } else { end.parse::<u64>().ok()?.min(len - 1) };
        (start, end)
    };
    (start <= end && start < len).then_some((start, end))
}

fn serve_file(request: Request, path: &Path, content_type: &str) {
    let Ok(mut file) = File::open(path) else {
        let _ = request.respond(error(404, "Not found"));
        return;
    };
    let len = file.metadata().map(|m| m.len()).unwrap_or(0);
    let range = get_header(&request, "Range").and_then(|r| parse_range(&r, len));
    let base = |r: Response<std::io::Take<File>>| {
        r.with_header(header("Content-Type", content_type))
            .with_header(header("Accept-Ranges", "bytes"))
            .with_header(header("Cache-Control", if content_type.starts_with("video") { "no-cache" } else { "max-age=86400" }))
    };
    match range {
        Some((start, end)) => {
            // Cap each response so one open-ended request never ties up a
            // thread for the whole file; browsers request the next range.
            let end = end.min(start + MAX_RANGE - 1);
            if file.seek(SeekFrom::Start(start)).is_err() {
                let _ = request.respond(error(416, "Bad range"));
                return;
            }
            let length = end - start + 1;
            let response = Response::new(StatusCode(206), Vec::new(), file.take(length), Some(length as usize), None);
            let _ = request.respond(
                base(response).with_header(header("Content-Range", &format!("bytes {start}-{end}/{len}"))),
            );
        }
        None => {
            let response = Response::new(StatusCode(200), Vec::new(), file.take(len), Some(len as usize), None);
            let _ = request.respond(base(response));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ranges() {
        assert_eq!(parse_range("bytes=0-", 100), Some((0, 99)));
        assert_eq!(parse_range("bytes=10-19", 100), Some((10, 19)));
        assert_eq!(parse_range("bytes=-10", 100), Some((90, 99)));
        assert_eq!(parse_range("bytes=200-", 100), None);
        assert_eq!(parse_range("items=0-1", 100), None);
    }
}

