//! The controller thread: watches for the game window, starts/stops capture,
//! applies the queue filter, recovers from recorder crashes and feeds the
//! single export worker.

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::audio::Audio;
use crate::config::{Paths, SharedSettings};
use crate::exporter::Exporter;
use crate::ffmpeg::Ffmpeg;
use crate::lcu;
use crate::library;
use crate::live::LiveShared;
use crate::models::{Cmd, EventTracker, HighlightRequest, MatchContext};
use crate::recorder::{Diagnostics, Recorder};
use crate::tray;
use crate::util::now;
use crate::win::{find_league_window, GameWindow};

#[derive(Clone, Debug, Serialize, Default)]
pub struct Status {
    pub state: &'static str,
    pub detail: String,
    pub recording_since: Option<f64>,
    pub paused: bool,
    pub queued: usize,
    pub last_clip: Option<String>,
    pub last_error: Option<String>,
    pub warnings: Vec<String>,
}

pub type SharedStatus = Arc<RwLock<Status>>;

pub struct Controller {
    pub cfg: SharedSettings,
    #[allow(dead_code)]
    pub paths: Paths,
    pub ff: Option<Arc<Ffmpeg>>,
    pub recorder: Option<Arc<Recorder>>,
    pub audio: Arc<Audio>,
    pub tracker: Arc<EventTracker>,
    pub live: Arc<LiveShared>,
    pub status: SharedStatus,
    pub busy: Arc<AtomicUsize>,
    pub export_tx: Sender<HighlightRequest>,

    window: Option<GameWindow>,
    geometry_changes: u32,
    paused: bool,
    blocked: bool,
    recovery_attempts: Vec<Instant>,
    recovery_at: Option<Instant>,
    restart_pending: bool,
    capture_signature: String,
    policy: Option<lcu::Decision>,
    policy_checks: u32,
    finished: HashMap<String, (MatchContext, String)>,
    agent: ureq::Agent,
    last_cleanup: Option<Instant>,
}

impl Controller {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        cfg: SharedSettings,
        paths: Paths,
        ff: Option<Arc<Ffmpeg>>,
        recorder: Option<Arc<Recorder>>,
        audio: Arc<Audio>,
        tracker: Arc<EventTracker>,
        live: Arc<LiveShared>,
        status: SharedStatus,
        busy: Arc<AtomicUsize>,
        export_tx: Sender<HighlightRequest>,
    ) -> Controller {
        let capture_signature = cfg.read().unwrap().capture_signature();
        Controller {
            cfg,
            paths,
            ff,
            recorder,
            audio,
            tracker,
            live,
            status,
            busy,
            export_tx,
            window: None,
            geometry_changes: 0,
            paused: false,
            blocked: false,
            recovery_attempts: Vec::new(),
            recovery_at: None,
            restart_pending: false,
            capture_signature,
            policy: None,
            policy_checks: 0,
            finished: HashMap::new(),
            agent: crate::live::local_agent(),
            last_cleanup: None,
        }
    }

    fn recording(&self) -> bool {
        self.recorder.as_ref().is_some_and(|r| r.running())
    }

    fn set_state(&self, state: &'static str, detail: impl Into<String>) {
        let detail = detail.into();
        let changed = {
            let mut status = self.status.write().unwrap();
            let changed = status.state != state || status.detail != detail;
            status.state = state;
            status.detail = detail;
            status.queued = self.busy.load(Ordering::SeqCst);
            changed
        };
        if changed {
            tray::refresh();
        }
    }

    pub fn run(mut self, rx: Receiver<Cmd>) {
        // Matches saved without a result (older versions, or a missed GameEnd).
        lcu::fill_unknown_results_later(self.cfg.read().unwrap().clip_dir());
        if self.ff.is_none() {
            self.set_state(
                "ERROR",
                "FFmpeg is missing. Put ffmpeg.exe next to LeagueHighlights.exe (scripts\\download_ffmpeg.ps1).",
            );
        } else {
            self.set_state("WAITING FOR LEAGUE", "Start a League game in Borderless mode");
        }
        let mut next_poll = Instant::now();
        loop {
            let timeout = next_poll.saturating_duration_since(Instant::now());
            match rx.recv_timeout(timeout) {
                Ok(Cmd::Quit) | Err(RecvTimeoutError::Disconnected) => break,
                Ok(cmd) => self.handle(cmd),
                Err(RecvTimeoutError::Timeout) => {}
            }
            if Instant::now() >= next_poll {
                self.poll();
                let active = self.window.is_some() || self.recording();
                next_poll = Instant::now() + Duration::from_millis(if active { 1300 } else { 3000 });
            }
        }
        self.live.stop.store(true, Ordering::SeqCst);
        if let Some(recorder) = &self.recorder {
            recorder.stop();
        }
        self.audio.stop();
    }

    fn handle(&mut self, cmd: Cmd) {
        match cmd {
            Cmd::Hotkey => self.manual_clip(),
            Cmd::Highlight(request) => {
                if self.recording() {
                    self.submit(request);
                } else {
                    log::info!("Automatic event {} detected while recording was inactive", request.clean_label());
                }
            }
            Cmd::MatchStarted(ctx) => {
                log::info!("Controller joined match session {}", ctx.match_id);
            }
            Cmd::MatchEnded(ctx, result) => {
                let dir = self.cfg.read().unwrap().clip_dir();
                library::finalize_match(&dir, &ctx, &result);
                if result != "WIN" && result != "LOSE" {
                    lcu::fill_unknown_results_later(dir.clone());
                }
                // Prompt a quick post-match review of this game's new clips.
                let fresh = library::unreviewed_in_match(&dir, &ctx.match_id);
                if fresh > 0 {
                    let outcome = match result.as_str() {
                        "WIN" => " · Victory",
                        "LOSE" => " · Defeat",
                        _ => "",
                    };
                    tray::notify_review(
                        &format!("{fresh} new highlight{}", if fresh == 1 { "" } else { "s" }),
                        &format!("{}{outcome} — click to review", if ctx.champion_name.is_empty() { "Your match" } else { &ctx.champion_name }),
                    );
                }
                self.finished.insert(ctx.match_id.clone(), (ctx, result));
                if self.finished.len() > 20 {
                    self.finished.clear();
                }
            }
            Cmd::ToggleRecording => {
                self.paused = !self.paused;
                self.status.write().unwrap().paused = self.paused;
                if self.paused {
                    if self.recording() {
                        self.stop_recording("Paused manually");
                    }
                    self.set_state("STOPPED", "Paused — choose Resume recording to continue");
                } else {
                    self.blocked = false;
                    self.set_state("WAITING FOR LEAGUE", "Start a League game in Borderless mode");
                    self.poll();
                }
            }
            Cmd::SettingsChanged => {
                let signature = self.cfg.read().unwrap().capture_signature();
                if signature != self.capture_signature {
                    self.capture_signature = signature;
                    self.restart_pending = self.recording();
                }
                self.policy = None;
                self.policy_checks = 0;
                if self.recording() {
                    let detail = self.recording_detail();
                    self.set_state("RECORDING", detail);
                }
            }
            Cmd::ExportDone(result, label) => {
                let remaining = self.busy.load(Ordering::SeqCst);
                match result {
                    Ok(file) => {
                        let match_id = library::read_meta(
                            &self.cfg.read().unwrap().clip_dir().join(&file).with_extension("json"),
                        )
                        .get("match_id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string();
                        if let Some((ctx, result)) = self.finished.get(&match_id) {
                            library::finalize_match(&self.cfg.read().unwrap().clip_dir(), ctx, result);
                        }
                        self.status.write().unwrap().last_clip = Some(file.clone());
                        let meta = library::read_meta(&self.cfg.read().unwrap().clip_dir().join(&file).with_extension("json"));
                        let duration = meta.get("duration_seconds").and_then(|v| v.as_f64()).unwrap_or(0.0);
                        let score = meta.get("highlight_score").and_then(|v| v.as_i64()).unwrap_or(0);
                        let detail = if score > 0 && meta.get("automatic").and_then(|v| v.as_bool()).unwrap_or(false) {
                            format!("Saved · {:.0}s · score {score}", duration)
                        } else {
                            format!("Saved · {:.0}s", duration)
                        };
                        self.announce(&title_case(&label), &detail, crate::overlay::Tone::Saved);
                    }
                    Err(error) => {
                        log::error!("Could not save {label}: {error}");
                        self.status.write().unwrap().last_error = Some(error.clone());
                        self.announce("Highlight not saved", &error, crate::overlay::Tone::Error);
                    }
                }
                if remaining == 0 {
                    if self.recording() {
                        let detail = self.recording_detail();
                        self.set_state("RECORDING", detail);
                    } else {
                        self.set_state("WAITING FOR LEAGUE", "Start a League game in Borderless mode");
                    }
                } else {
                    self.set_state("SAVING CLIP", format!("{remaining} highlight(s) in the save queue"));
                }
            }
            Cmd::Quit => {}
        }
    }

    fn manual_clip(&mut self) {
        let wall = now();
        let context = self.live.current_match.lock().unwrap().clone();
        let game_time = context.as_ref().map_or(0.0, |c| c.duration_seconds);
        let match_id = context.as_ref().map(|c| c.match_id.clone()).unwrap_or_default();
        self.tracker.record("MANUAL_TRIGGER", game_time, &match_id, wall);
        if !self.recording() {
            // Only nag while a game is open; the shortcut is global and F8
            // in other apps must not pop up balloons.
            log::info!("Clip shortcut pressed while recording is inactive");
            if self.window.is_some() {
                tray::notify("Not recording", "Recording is not active for this game.");
            }
            return;
        }
        let request = HighlightRequest {
            label: "MANUAL CLIP".into(),
            event_kind: "manual".into(),
            event_game_time: (game_time > 0.0).then_some(game_time),
            triggered_at_wall: Some(wall),
            highlight_score: 100,
            score_reasons: vec!["manual clip".into()],
            ..Default::default()
        }
        .with_context(context.as_ref());
        self.submit(request);
        let (overlay, hotkey) = {
            let s = self.cfg.read().unwrap();
            (s.overlay_notifications, s.hotkey_display.clone())
        };
        if overlay && self.window.is_some() {
            tray::overlay("Saving clip…", &format!("{hotkey} pressed"), crate::overlay::Tone::Info);
        }
    }

    /// In game: toast over the game. Otherwise: a tray balloon.
    fn announce(&self, title: &str, detail: &str, tone: crate::overlay::Tone) {
        let (overlay, balloon) = {
            let s = self.cfg.read().unwrap();
            (s.overlay_notifications, s.notify_on_save)
        };
        if overlay && self.window.is_some() {
            tray::overlay(title, detail, tone);
        } else if balloon || tone == crate::overlay::Tone::Error {
            tray::notify(title, detail);
        }
    }

    /// Auto clean-up, at most every 6 hours and never while saving.
    fn maybe_cleanup(&mut self) {
        if self.last_cleanup.is_some_and(|t| t.elapsed() < Duration::from_secs(6 * 3600)) {
            return;
        }
        if self.busy.load(Ordering::SeqCst) > 0 {
            return;
        }
        self.last_cleanup = Some(Instant::now());
        let (days, dir) = {
            let s = self.cfg.read().unwrap();
            (s.cleanup_days, s.clip_dir())
        };
        if days > 0 {
            library::cleanup(&dir, days, false);
        }
    }

    fn submit(&mut self, request: HighlightRequest) {
        let context = self.live.current_match.lock().unwrap().clone();
        let mut request = request.with_context(context.as_ref());
        if request.triggered_at_wall.is_none() {
            request.triggered_at_wall = Some(request.event_ended_at.or(request.event_started_at).unwrap_or_else(now));
        }
        let label = request.clean_label();
        let queued = self.busy.fetch_add(1, Ordering::SeqCst) + 1;
        if self.export_tx.send(request).is_err() {
            self.busy.fetch_sub(1, Ordering::SeqCst);
            return;
        }
        let detail = if queued > 1 {
            format!("Saving {} ({queued} in queue)", title_case(&label))
        } else {
            format!("Saving {}", title_case(&label))
        };
        self.set_state("SAVING CLIP", detail);
    }

    fn recording_detail(&self) -> String {
        let s = self.cfg.read().unwrap();
        let encoder = self
            .recorder
            .as_ref()
            .and_then(|r| r.encoder())
            .unwrap_or_else(|| "unknown encoder".into());
        let encoder = if encoder == "libx264" { "libx264 (CPU fallback)".to_string() } else { encoder };
        let quality = match s.quality {
            0..=20 => "High quality",
            21..=24 => "Balanced",
            25..=28 => "Smaller files",
            _ => "Minimum size",
        };
        let smart = if s.smart_highlights_enabled {
            format!(" • Smart: {}", title_case(&s.smart_sensitivity.replace('_', " ")))
        } else {
            String::new()
        };
        format!(
            "{}×{} • {} FPS • {quality} • {encoder} • Audio: {} • {} saves last {}s{smart}",
            s.width,
            s.height,
            s.fps,
            self.audio.summary(),
            s.hotkey_display,
            s.buffer_seconds
        )
    }

    fn start_recording(&mut self, window: &GameWindow) {
        let Some(recorder) = self.recorder.clone() else { return };
        self.set_state("STARTING", "Starting video and configured audio sources");
        let settings = self.cfg.read().unwrap().clone();
        let warnings = self.audio.start(&settings);
        match recorder.start(&settings, window) {
            Ok(()) => {
                self.blocked = false;
                self.recovery_attempts.clear();
                self.recovery_at = None;
                self.capture_signature = settings.capture_signature();
                {
                    let mut status = self.status.write().unwrap();
                    status.recording_since = Some(now());
                    status.warnings = warnings;
                    status.last_error = None;
                }
                let detail = self.recording_detail();
                self.set_state("RECORDING", detail);
            }
            Err(error) => {
                log::error!("Recording could not start: {error}");
                recorder.stop();
                self.audio.stop();
                self.blocked = true;
                self.status.write().unwrap().last_error = Some(error.clone());
                self.set_state("ERROR", error);
                self.schedule_recovery();
            }
        }
    }

    fn stop_recording(&mut self, reason: &str) {
        if let Some(recorder) = &self.recorder {
            // Capture health goes in the log so a match can be checked afterwards.
            let d = recorder.diagnostics();
            if recorder.has_session() && d.frame > 0 {
                log::info!(
                    "Capture summary: {} frames via {} ({}), {} dropped ({:.2}%), {} duplicated",
                    d.frame, d.capture_backend, d.encoder, d.dropped_frames, d.drop_rate, d.duplicated_frames
                );
            }
            recorder.stop();
        }
        self.audio.stop();
        self.status.write().unwrap().recording_since = None;
        log::info!("Recording stopped: {reason}");
    }

    fn schedule_recovery(&mut self) {
        if self.recovery_at.is_some() {
            return;
        }
        let cutoff = Instant::now() - Duration::from_secs(120);
        self.recovery_attempts.retain(|t| *t >= cutoff);
        if self.recovery_attempts.len() >= 3 {
            log::error!("Automatic capture recovery stopped after 3 attempts");
            return;
        }
        let delay = Duration::from_secs(3 * (self.recovery_attempts.len() as u64 + 1));
        log::warn!("Scheduling capture recovery in {}s", delay.as_secs());
        self.recovery_at = Some(Instant::now() + delay);
    }

    fn poll(&mut self) {
        if self.ff.is_none() {
            return;
        }
        self.maybe_cleanup();
        let found = find_league_window();
        self.live.game_present.store(found.is_some(), Ordering::SeqCst);
        let Some(window) = found else {
            if self.window.take().is_some() {
                log::info!("League game window closed");
            }
            self.blocked = false;
            self.recovery_at = None;
            self.policy = None;
            self.policy_checks = 0;
            if self.recording() {
                self.stop_recording("League closed");
            }
            if self.busy.load(Ordering::SeqCst) == 0 && !self.paused {
                self.set_state("WAITING FOR LEAGUE", "Start a League game in Borderless mode");
            }
            return;
        };

        let new_window = self.window.as_ref().is_none_or(|w| w.hwnd != window.hwnd);
        if new_window {
            log::info!("Detected League window: {} ({}x{})", window.title, window.width, window.height);
            self.blocked = false;
            self.policy = None;
            self.policy_checks = 0;
            self.geometry_changes = 0;
        }
        let geometry_changed = self.window.as_ref().is_some_and(|w| !w.same_geometry(&window));
        self.window = Some(window.clone());

        if let Some(recorder) = self.recorder.clone() {
            if recorder.has_session() {
                if let Some(fatal) = recorder.fatal_error() {
                    self.stop_recording(&fatal);
                    self.blocked = true;
                    self.status.write().unwrap().last_error = Some(fatal.clone());
                    self.set_state("ERROR", fatal);
                    return;
                }
                if !recorder.running() {
                    let error = recorder
                        .last_error()
                        .unwrap_or_else(|| "The video recorder stopped unexpectedly.".into());
                    log::error!("Recorder stopped: {error}");
                    self.stop_recording(&error);
                    self.blocked = true;
                    self.status.write().unwrap().last_error = Some(error.clone());
                    self.set_state("ERROR", error);
                    self.schedule_recovery();
                    return;
                }
                // Restart when the capture region changed (resolution or
                // monitor) or capture settings changed, once nothing is saving.
                if geometry_changed {
                    self.geometry_changes += 1;
                }
                let idle = self.busy.load(Ordering::SeqCst) == 0;
                if idle && (self.restart_pending || self.geometry_changes >= 2) {
                    log::info!("Restarting capture to apply new settings or window geometry");
                    self.restart_pending = false;
                    self.geometry_changes = 0;
                    self.stop_recording("Applying capture settings");
                    self.start_recording(&window);
                }
                return;
            }
        }

        if let Some(at) = self.recovery_at {
            if Instant::now() >= at {
                self.recovery_at = None;
                self.recovery_attempts.push(Instant::now());
                self.blocked = false;
                self.set_state("STARTING", "Recovering capture after an unexpected stop");
            }
        }

        let (auto_start, enabled, needs_policy) = {
            let s = self.cfg.read().unwrap();
            (s.auto_start, s.recording_enabled, s.needs_queue_policy())
        };
        if self.paused || self.blocked || !auto_start || !enabled {
            if !enabled && !self.paused {
                self.set_state("STOPPED", "Highlight recording is turned off in Settings");
            }
            return;
        }
        if needs_policy && !self.policy_allows() {
            return;
        }
        self.start_recording(&window);
    }

    /// Queue filter. Returns true when recording may start.
    fn policy_allows(&mut self) -> bool {
        if let Some(decision) = &self.policy {
            if decision.known {
                if !decision.allowed {
                    self.set_state("SKIPPED", decision.reason.clone());
                }
                return decision.allowed;
            }
        }
        let info = lcu::current_queue(&self.agent);
        let live_mode = self
            .live
            .current_match
            .lock()
            .unwrap()
            .as_ref()
            .map(|c| c.game_mode.clone())
            .unwrap_or_default();
        let settings = self.cfg.read().unwrap().clone();
        let decision = lcu::decide(&settings, info.as_ref(), &live_mode);
        self.policy_checks += 1;
        log::info!("Queue filter: {}", decision.reason);
        let allowed = decision.allowed;
        if !allowed {
            let state = if decision.known { "SKIPPED" } else { "WAITING FOR LEAGUE" };
            self.set_state(state, decision.reason.clone());
        }
        // Unknown after ~60 s of checks: give up instead of polling forever.
        if decision.known || self.policy_checks >= 45 {
            self.policy = Some(lcu::Decision { known: true, ..decision });
        }
        allowed
    }
}

pub fn title_case(text: &str) -> String {
    text.split_whitespace()
        .map(|word| {
            let lower = word.to_lowercase();
            let mut chars = lower.chars();
            chars
                .next()
                .map(|c| c.to_uppercase().collect::<String>() + chars.as_str())
                .unwrap_or_default()
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// The single export worker: clips are saved strictly one at a time.
pub fn export_worker(
    exporter: Exporter,
    rx: Receiver<HighlightRequest>,
    busy: Arc<AtomicUsize>,
    controller_tx: Sender<Cmd>,
) {
    for request in rx {
        let label = request.clean_label();
        let result = exporter
            .export(&request)
            .map(|path| path.file_name().unwrap_or_default().to_string_lossy().into_owned());
        busy.fetch_sub(1, Ordering::SeqCst);
        if controller_tx.send(Cmd::ExportDone(result, label)).is_err() {
            break;
        }
    }
}

pub fn diagnostics(recorder: &Option<Arc<Recorder>>) -> Diagnostics {
    recorder.as_ref().map(|r| r.diagnostics()).unwrap_or_default()
}
