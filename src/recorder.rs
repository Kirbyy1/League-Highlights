//! Rolling video buffer: one FFmpeg process writes 2-second Matroska segments.
//!
//! Improvements over the Python recorder:
//! * segment boundaries come from FFmpeg's own `-segment_list` (no ffprobe per
//!   segment when a clip is saved);
//! * stream time is anchored to the shared app clock from `-progress` output;
//! * one stderr thread handles progress, pruning and the disk guard;
//! * FFmpeg is placed in a kill-on-close job so it never outlives the app.

use std::collections::{HashMap, VecDeque};
use std::fs;
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::config::{Settings, BUFFER_SAFETY_SECONDS, MIN_FREE_DISK_MIB, SEGMENT_SECONDS};
use crate::ffmpeg::{encoder_args, Ffmpeg};
use crate::util::{disk_free_bytes, now};
use crate::win::GameWindow;

#[derive(Clone, Debug, Default, Serialize)]
pub struct Diagnostics {
    pub encoder: String,
    pub capture_backend: String,
    pub hardware_encoder: bool,
    pub frame: u64,
    pub fps: f64,
    pub duplicated_frames: u64,
    pub dropped_frames: u64,
    pub drop_rate: f64,
    pub speed: f64,
    pub updated_at: f64,
    pub health: &'static str,
}

#[derive(Clone, Debug)]
pub struct TimedSegment {
    pub path: PathBuf,
    pub start_wall: f64,
    pub end_wall: f64,
}

#[derive(Clone, Debug)]
struct Segment {
    path: PathBuf,
    start: f64,
    end: f64,
}

#[derive(Default)]
struct SegmentState {
    list_offset: u64,
    partial: String,
    segments: VecDeque<Segment>,
}

struct Shared {
    dir: PathBuf,
    list_path: PathBuf,
    segments: Mutex<SegmentState>,
    /// Wall time of stream timestamp zero. The minimum observed
    /// `wall - out_time` is the estimate with the least pipeline latency.
    anchor: Mutex<f64>,
    last_error: Mutex<Option<String>>,
    fatal: Mutex<Option<String>>,
    tail: Mutex<VecDeque<String>>,
    retain_seconds: f64,
    encoder: String,
    backend: String,
    diag: Arc<Mutex<Diagnostics>>,
    hold: Arc<AtomicUsize>,
}

struct Session {
    child: Child,
    stdin: Option<ChildStdin>,
    reader: Option<JoinHandle<()>>,
    shared: Arc<Shared>,
}

pub struct Recorder {
    ff: Arc<Ffmpeg>,
    dir: PathBuf,
    session: Mutex<Option<Session>>,
    diag: Arc<Mutex<Diagnostics>>,
    /// While > 0 no rolling segment is deleted (a clip is being assembled).
    hold: Arc<AtomicUsize>,
}

pub struct HoldGuard(Arc<AtomicUsize>);
impl Drop for HoldGuard {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

impl Recorder {
    pub fn new(ff: Arc<Ffmpeg>, dir: PathBuf) -> Recorder {
        Recorder {
            ff,
            dir,
            session: Mutex::new(None),
            diag: Arc::new(Mutex::new(Diagnostics {
                encoder: "Not started".into(),
                capture_backend: "Idle".into(),
                health: "Waiting",
                ..Default::default()
            })),
            hold: Arc::new(AtomicUsize::new(0)),
        }
    }

    pub fn running(&self) -> bool {
        let mut guard = self.session.lock().unwrap();
        match guard.as_mut() {
            Some(session) => matches!(session.child.try_wait(), Ok(None)),
            None => false,
        }
    }

    pub fn has_session(&self) -> bool {
        self.session.lock().unwrap().is_some()
    }

    pub fn diagnostics(&self) -> Diagnostics {
        self.diag.lock().unwrap().clone()
    }

    pub fn encoder(&self) -> Option<String> {
        self.session
            .lock()
            .unwrap()
            .as_ref()
            .map(|s| s.shared.encoder.clone())
    }

    pub fn hold(&self) -> HoldGuard {
        self.hold.fetch_add(1, Ordering::SeqCst);
        HoldGuard(self.hold.clone())
    }

    /// Error that requires the session to stop (low disk space).
    pub fn fatal_error(&self) -> Option<String> {
        let guard = self.session.lock().unwrap();
        guard.as_ref().and_then(|s| s.shared.fatal.lock().unwrap().clone())
    }

    pub fn last_error(&self) -> Option<String> {
        let guard = self.session.lock().unwrap();
        let session = guard.as_ref()?;
        let error = session.shared.last_error.lock().unwrap().clone();
        error.or_else(|| {
            let tail = session.shared.tail.lock().unwrap();
            (!tail.is_empty()).then(|| tail.iter().cloned().collect::<Vec<_>>().join(" | "))
        })
    }

    pub fn start(&self, settings: &Settings, window: &GameWindow) -> Result<(), String> {
        let mut last_error = String::from("Capture could not start.");
        for _ in 0..4 {
            let encoder = self.ff.choose_encoder()?;
            match self.start_with(settings, window, encoder) {
                Ok(()) => return Ok(()),
                Err(error) => {
                    self.stop();
                    if encoder == "libx264" {
                        return Err(error);
                    }
                    self.ff.mark_unhealthy(encoder, &error);
                    last_error = error;
                }
            }
        }
        Err(last_error)
    }

    /// CPU-only capture of a generated pattern, used by `--selftest --synthetic`.
    pub fn start_synthetic(&self, settings: &Settings, window: &GameWindow) -> Result<(), String> {
        self.clear_dir();
        self.launch(settings, window, "libx264", Backend::Synthetic)?;
        self.wait_until_healthy(Duration::from_millis(3500))
    }

    fn start_with(&self, s: &Settings, window: &GameWindow, encoder: &str) -> Result<(), String> {
        self.clear_dir();
        // GDI HWND capture of DirectX surfaces is often black, so capture the
        // composed desktop region instead: Desktop Duplication first, then GDI.
        if self.ff.supports_filter("ddagrab") {
            log::info!(
                "Using Desktop Duplication on output {} at {}x{}+{}+{}",
                window.monitor_index,
                window.width,
                window.height,
                window.offset_x,
                window.offset_y
            );
            // When NVENC records at a smaller size, the scaled frame goes back
            // to the GPU first. The plain download pipeline stays as the
            // fallback, e.g. for laptops whose display adapter is not NVIDIA's.
            let mut backends = vec![Backend::Ddagrab];
            if encoder == "h264_nvenc" && output_size(s, window).2 {
                backends.insert(0, Backend::DdagrabUpload);
            }
            for backend in backends {
                self.launch(s, window, encoder, backend)?;
                match self.wait_until_healthy(Duration::from_millis(3500)) {
                    Ok(()) => return Ok(()),
                    Err(error) => {
                        log::warn!("Desktop Duplication ({}) failed: {error}", backend.pipeline());
                        self.stop();
                        self.clear_dir();
                    }
                }
            }
            log::warn!("Retrying with GDI desktop capture");
        } else {
            log::warn!("This FFmpeg build has no ddagrab filter; using GDI desktop capture");
        }
        self.launch(s, window, encoder, Backend::Gdi)?;
        self.wait_until_healthy(Duration::from_millis(3500))
    }

    /// Wait for the first progress report instead of sleeping a fixed time.
    fn wait_until_healthy(&self, timeout: Duration) -> Result<(), String> {
        let started = Instant::now();
        loop {
            if !self.running() {
                return Err(self
                    .last_error()
                    .unwrap_or_else(|| "FFmpeg stopped immediately after capture started.".into()));
            }
            if self.diag.lock().unwrap().frame > 0 {
                return Ok(());
            }
            if started.elapsed() >= timeout {
                // Still alive: treat as started (slow first frame).
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }

    fn launch(
        &self,
        s: &Settings,
        window: &GameWindow,
        encoder: &str,
        backend: Backend,
    ) -> Result<(), String> {
        let list_path = self.dir.join("segments.csv");
        let pattern = self.dir.join("segment_%06d.mkv");
        let gop = (s.fps * SEGMENT_SECONDS).to_string();

        let mut args: Vec<String> = [
            "-hide_banner", "-loglevel", "warning", "-nostats", "-stats_period", "1",
            "-progress", "pipe:2", "-y",
        ]
        .iter()
        .map(|a| a.to_string())
        .collect();
        // Hardware encoding needs no CPU worker pools: one thread per stage
        // (51 -> 39 threads). CPU scaling and libx264 keep their defaults.
        let filters = filter_args(s, window, encoder, backend);
        if encoder != "libx264" {
            args.extend(["-threads".into(), "1".into()]);
            if filters.is_empty() {
                args.extend(["-filter_threads".into(), "1".into()]);
            }
        }
        if backend == Backend::DdagrabUpload {
            // The scaler is the only CPU filter: two slice threads keep 60 fps
            // from a 1080p source, larger sources get four. `dx` is the D3D11
            // device the scaled frames are uploaded to.
            let source_pixels = i64::from(window.width) * i64::from(window.height);
            let threads = if source_pixels > 2_500_000 { "4" } else { "2" };
            args.extend(
                ["-filter_threads", threads, "-init_hw_device", "d3d11va=dx", "-filter_hw_device", "dx"]
                    .map(String::from),
            );
        }
        args.extend(capture_args(s, window, backend));
        args.extend(filters);
        args.extend(["-fps_mode".into(), "cfr".into()]);
        args.extend(encoder_args(encoder, s.quality));
        args.extend(
            [
                "-g", &gop, "-keyint_min", &gop, "-force_key_frames",
                &format!("expr:gte(t,n_forced*{SEGMENT_SECONDS})"), "-an", "-f", "segment",
                "-segment_format", "matroska", "-segment_time", &SEGMENT_SECONDS.to_string(),
                "-segment_time_delta", "0.05", "-segment_list", &list_path.to_string_lossy(),
                "-segment_list_type", "csv", "-reset_timestamps", "1",
                &pattern.to_string_lossy(),
            ]
            .iter()
            .map(|a| a.to_string()),
        );
        log::info!("Starting FFmpeg capture: {}", args.join(" "));

        let mut child = self
            .ff
            .command(true)
            .args(&args)
            .stdin(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("Could not start FFmpeg: {e}"))?;
        crate::win::adopt_child(&child);

        {
            let mut d = self.diag.lock().unwrap();
            *d = Diagnostics {
                encoder: encoder.into(),
                capture_backend: backend.label().into(),
                hardware_encoder: encoder != "libx264",
                health: "Waiting",
                ..Default::default()
            };
        }
        let shared = Arc::new(Shared {
            dir: self.dir.clone(),
            list_path,
            segments: Mutex::new(SegmentState::default()),
            anchor: Mutex::new(f64::INFINITY),
            last_error: Mutex::new(None),
            fatal: Mutex::new(None),
            tail: Mutex::new(VecDeque::with_capacity(12)),
            retain_seconds: f64::from(s.buffer_seconds + BUFFER_SAFETY_SECONDS),
            encoder: encoder.into(),
            backend: backend.label().into(),
            diag: self.diag.clone(),
            hold: self.hold.clone(),
        });
        let stderr = child.stderr.take();
        let reader_shared = shared.clone();
        let reader = std::thread::Builder::new()
            .name("ffmpeg-capture".into())
            .spawn(move || {
                if let Some(stderr) = stderr {
                    read_stderr(stderr, &reader_shared);
                }
            })
            .ok();
        let stdin = child.stdin.take();
        *self.session.lock().unwrap() = Some(Session {
            child,
            stdin,
            reader,
            shared,
        });
        Ok(())
    }

    pub fn stop(&self) {
        let session = self.session.lock().unwrap().take();
        let Some(mut session) = session else { return };
        if let Some(mut stdin) = session.stdin.take() {
            let _ = stdin.write_all(b"q\n");
            let _ = stdin.flush();
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            match session.child.try_wait() {
                Ok(Some(_)) | Err(_) => break,
                Ok(None) if Instant::now() >= deadline => {
                    let _ = session.child.kill();
                    let _ = session.child.wait();
                    break;
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(50)),
            }
        }
        if let Some(reader) = session.reader.take() {
            let _ = reader.join();
        }
        let mut d = self.diag.lock().unwrap();
        d.capture_backend = "Idle".into();
    }

    /// Completed segments mapped onto the shared wall clock.
    pub fn timeline(&self) -> Vec<TimedSegment> {
        let shared = {
            let guard = self.session.lock().unwrap();
            match guard.as_ref() {
                Some(s) => s.shared.clone(),
                None => return Vec::new(),
            }
        };
        shared.refresh_segments();
        let anchor = *shared.anchor.lock().unwrap();
        if !anchor.is_finite() {
            return Vec::new();
        }
        let state = shared.segments.lock().unwrap();
        state
            .segments
            .iter()
            .filter(|seg| seg.end - seg.start > 0.1 && seg.path.is_file())
            .map(|seg| TimedSegment {
                path: seg.path.clone(),
                start_wall: anchor + seg.start,
                end_wall: anchor + seg.end,
            })
            .collect()
    }

    /// Block until completed footage reaches `end_wall` (or the timeout).
    pub fn wait_for_footage(&self, end_wall: f64, timeout: Duration) -> bool {
        let deadline = Instant::now() + timeout;
        loop {
            if self.timeline().last().is_some_and(|s| s.end_wall >= end_wall - 0.05) {
                return true;
            }
            if Instant::now() >= deadline || !self.running() {
                return false;
            }
            std::thread::sleep(Duration::from_millis(150));
        }
    }

    fn clear_dir(&self) {
        let _ = fs::create_dir_all(&self.dir);
        if let Ok(entries) = fs::read_dir(&self.dir) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().to_lowercase();
                if (name.starts_with("segment_") && name.ends_with(".mkv")) || name == "segments.csv" {
                    if let Err(e) = fs::remove_file(entry.path()) {
                        log::warn!("Could not delete old buffer file {}: {e}", entry.path().display());
                    }
                }
            }
        }
    }
}

#[derive(Clone, Copy, PartialEq)]
enum Backend {
    Ddagrab,
    /// Desktop Duplication, CPU downscale, then the small frame is uploaded
    /// back to a D3D11 texture for NVENC.
    DdagrabUpload,
    Gdi,
    /// Generated test pattern (self-test without the GPU or a game).
    Synthetic,
}

impl Backend {
    fn label(self) -> &'static str {
        match self {
            Backend::Ddagrab | Backend::DdagrabUpload => "Desktop Duplication",
            Backend::Gdi => "GDI desktop fallback",
            Backend::Synthetic => "Synthetic test pattern",
        }
    }

    /// Name used in the log to tell the two Desktop Duplication pipelines apart.
    fn pipeline(self) -> &'static str {
        match self {
            Backend::DdagrabUpload => "GPU upload",
            _ => "direct",
        }
    }
}

fn capture_args(s: &Settings, w: &GameWindow, backend: Backend) -> Vec<String> {
    let mouse = if s.draw_mouse { "1" } else { "0" };
    match backend {
        Backend::Ddagrab | Backend::DdagrabUpload => vec![
            "-f".into(),
            "lavfi".into(),
            "-i".into(),
            format!(
                "ddagrab=output_idx={}:framerate={}:draw_mouse={}:video_size={}x{}:offset_x={}:offset_y={}",
                w.monitor_index, s.fps, mouse, w.width, w.height, w.offset_x, w.offset_y
            ),
        ],
        Backend::Synthetic => vec![
            "-re".into(),
            "-f".into(),
            "lavfi".into(),
            "-i".into(),
            format!("testsrc2=s={}x{}:r={}", w.width, w.height, s.fps),
        ],
        Backend::Gdi => vec![
            "-rtbufsize".into(),
            "256M".into(),
            "-f".into(),
            "gdigrab".into(),
            "-framerate".into(),
            s.fps.to_string(),
            "-draw_mouse".into(),
            mouse.into(),
            "-offset_x".into(),
            w.offset_x.to_string(),
            "-offset_y".into(),
            w.offset_y.to_string(),
            "-video_size".into(),
            format!("{}x{}", w.width, w.height),
            "-i".into(),
            "desktop".into(),
        ],
    }
}

/// Recorded size and whether the captured frame has to be scaled to get it:
/// the largest even size that fits the chosen resolution with the window's
/// aspect ratio (no black-bar padding pass and no upscaling).
fn output_size(s: &Settings, w: &GameWindow) -> (i32, i32, bool) {
    let (width, height) = fit_within(w.width, w.height, s.width as i32, s.height as i32);
    let resize = width != w.width & !1 || height != w.height & !1;
    (width, height, resize)
}

fn filter_args(s: &Settings, w: &GameWindow, encoder: &str, backend: Backend) -> Vec<String> {
    // fast_bilinear costs ~40% less CPU than the default bicubic scaler when
    // downscaling and is visually equivalent for gameplay clips.
    let (width, height, resize) = output_size(s, w);
    let scale = format!("scale={width}:{height}:flags=fast_bilinear");
    let convert = if resize {
        format!("{scale},format=yuv420p")
    } else {
        "format=yuv420p".to_string()
    };
    match backend {
        // NVENC consumes ddagrab's D3D11 frames directly (zero copy) when no
        // resize is required.
        Backend::Ddagrab if encoder == "h264_nvenc" && !resize => Vec::new(),
        // Resizing has to go through the CPU (scale_d3d11 fails to allocate
        // its textures in current FFmpeg builds), but the small frame goes
        // back to a D3D11 texture afterwards. Given CPU frames NVENC opens a
        // CUDA context instead, which costs ~270 MB more private memory
        // (measured 445 MB vs 174 MB for 1080p -> 720p).
        Backend::DdagrabUpload => vec![
            "-vf".into(),
            format!("hwdownload,format=bgra,{scale},format=nv12,hwupload"),
        ],
        Backend::Ddagrab => vec!["-vf".into(), format!("hwdownload,format=bgra,{convert}")],
        Backend::Gdi | Backend::Synthetic => vec!["-vf".into(), convert],
    }
}

/// Largest even size inside `max` with the source aspect ratio (never upscales).
fn fit_within(src_w: i32, src_h: i32, max_w: i32, max_h: i32) -> (i32, i32) {
    let scale = (f64::from(max_w) / f64::from(src_w.max(1)))
        .min(f64::from(max_h) / f64::from(src_h.max(1)))
        .min(1.0);
    let even = |v: f64| ((v as i32) & !1).max(2);
    (even(f64::from(src_w) * scale), even(f64::from(src_h) * scale))
}

const PROGRESS_KEYS: [&str; 12] = [
    "frame", "fps", "dup_frames", "drop_frames", "speed", "out_time_us", "out_time_ms",
    "out_time", "progress", "bitrate", "total_size", "stream_0_0_q",
];

fn read_stderr(stderr: impl Read, shared: &Shared) {
    let mut reader = BufReader::new(stderr);
    let mut buffer = Vec::with_capacity(256);
    let mut progress: HashMap<String, String> = HashMap::new();
    let mut last_disk_check = Instant::now() - Duration::from_secs(60);
    let min_free = MIN_FREE_DISK_MIB * 1024 * 1024;
    loop {
        buffer.clear();
        match reader.read_until(b'\n', &mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
        let line = String::from_utf8_lossy(&buffer);
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Some((key, value)) = line.split_once('=') {
            if PROGRESS_KEYS.contains(&key) {
                progress.insert(key.to_string(), value.trim().to_string());
                if key == "progress" {
                    shared.publish(&progress);
                    shared.refresh_segments();
                    shared.prune();
                    if last_disk_check.elapsed() >= Duration::from_secs(5) {
                        last_disk_check = Instant::now();
                        if disk_free_bytes(&shared.dir).is_some_and(|free| free < min_free) {
                            let message = format!(
                                "Recording stopped because the drive has less than {MIN_FREE_DISK_MIB} MiB free."
                            );
                            log::error!("{message}");
                            *shared.fatal.lock().unwrap() = Some(message);
                        }
                    }
                }
                continue;
            }
        }
        log::warn!("FFmpeg: {line}");
        {
            let mut tail = shared.tail.lock().unwrap();
            if tail.len() >= 12 {
                tail.pop_front();
            }
            tail.push_back(line.to_string());
        }
        let lowered = line.to_lowercase();
        if lowered.contains("error") || lowered.contains("failed") || lowered.contains("cannot") {
            *shared.last_error.lock().unwrap() = Some(line.to_string());
        }
    }
}

impl Shared {
    fn publish(&self, progress: &HashMap<String, String>) {
        let int = |key: &str| {
            progress
                .get(key)
                .and_then(|v| v.parse::<f64>().ok())
                .map_or(0, |v| v.max(0.0) as u64)
        };
        let float = |key: &str| {
            progress
                .get(key)
                .map(|v| v.trim().trim_end_matches('x'))
                .and_then(|v| v.parse::<f64>().ok())
                .map_or(0.0, |v| v.max(0.0))
        };
        let out_time_us = progress
            .get("out_time_us")
            .and_then(|v| v.parse::<i64>().ok())
            .unwrap_or(0);
        if out_time_us > 0 {
            let estimate = now() - out_time_us as f64 / 1e6;
            let mut anchor = self.anchor.lock().unwrap();
            if estimate < *anchor {
                *anchor = estimate;
            }
        }
        let frame = int("frame");
        let dropped = int("drop_frames");
        let duplicated = int("dup_frames");
        let drop_rate = dropped as f64 / (frame + dropped).max(1) as f64 * 100.0;
        let fps = float("fps");
        let mut d = self.diag.lock().unwrap();
        *d = Diagnostics {
            encoder: self.encoder.clone(),
            capture_backend: self.backend.clone(),
            hardware_encoder: self.encoder != "libx264",
            frame,
            fps,
            duplicated_frames: duplicated,
            dropped_frames: dropped,
            drop_rate,
            speed: float("speed"),
            updated_at: now(),
            health: if drop_rate >= 2.0 {
                "Poor"
            } else if drop_rate >= 0.5 {
                "Warning"
            } else {
                "Good"
            },
        };
    }

    /// Parse new lines appended to FFmpeg's CSV segment list.
    fn refresh_segments(&self) {
        let mut state = self.segments.lock().unwrap();
        let Ok(mut file) = fs::File::open(&self.list_path) else { return };
        let Ok(len) = file.metadata().map(|m| m.len()) else { return };
        if len < state.list_offset {
            *state = SegmentState::default();
        }
        if len == state.list_offset {
            return;
        }
        if file.seek(SeekFrom::Start(state.list_offset)).is_err() {
            return;
        }
        let mut text = String::new();
        if file.read_to_string(&mut text).is_err() {
            return;
        }
        state.list_offset = len;
        let mut pending = std::mem::take(&mut state.partial);
        pending.push_str(&text);
        let complete_upto = pending.rfind('\n').map_or(0, |i| i + 1);
        let rest = pending.split_off(complete_upto);
        for line in pending.lines() {
            if let Some(segment) = parse_segment_line(&self.dir, line) {
                state.segments.push_back(segment);
            }
        }
        state.partial = rest;
    }

    fn prune(&self) {
        if self.hold.load(Ordering::SeqCst) > 0 {
            return;
        }
        let mut state = self.segments.lock().unwrap();
        let Some(latest_end) = state.segments.back().map(|s| s.end) else { return };
        while let Some(front) = state.segments.front() {
            if front.end >= latest_end - self.retain_seconds {
                break;
            }
            let segment = state.segments.pop_front().unwrap();
            if let Err(e) = fs::remove_file(&segment.path) {
                log::debug!("Could not delete rolling segment {}: {e}", segment.path.display());
            }
        }
    }
}

fn parse_segment_line(dir: &Path, line: &str) -> Option<Segment> {
    let mut parts = line.trim().rsplitn(3, ',');
    let end = parts.next()?.trim().parse::<f64>().ok()?;
    let start = parts.next()?.trim().parse::<f64>().ok()?;
    let name = parts.next()?.trim().trim_matches('"');
    let file = Path::new(name).file_name()?;
    Some(Segment {
        path: dir.join(file),
        start,
        end,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fits_keep_aspect() {
        assert_eq!(fit_within(2560, 1440, 1920, 1080), (1920, 1080));
        assert_eq!(fit_within(2560, 1080, 1920, 1080), (1920, 810));
        assert_eq!(fit_within(1600, 900, 1920, 1080), (1600, 900));
        assert_eq!(fit_within(3840, 2160, 1280, 720), (1280, 720));
        assert_eq!(fit_within(1001, 563, 1920, 1080), (1000, 562));
    }

    #[test]
    fn nvenc_resize_goes_back_to_the_gpu() {
        let window = GameWindow {
            hwnd: 0,
            pid: 0,
            title: String::new(),
            width: 1920,
            height: 1080,
            monitor_index: 0,
            offset_x: 0,
            offset_y: 0,
        };
        let small = Settings { width: 1280, height: 720, ..Settings::default() };
        assert!(output_size(&small, &window).2);
        assert_eq!(
            filter_args(&small, &window, "h264_nvenc", Backend::DdagrabUpload),
            ["-vf", "hwdownload,format=bgra,scale=1280:720:flags=fast_bilinear,format=nv12,hwupload"]
        );
        // The fallback and the other encoders keep CPU frames.
        assert_eq!(
            filter_args(&small, &window, "h264_amf", Backend::Ddagrab),
            ["-vf", "hwdownload,format=bgra,scale=1280:720:flags=fast_bilinear,format=yuv420p"]
        );
        // Native size stays zero copy.
        let native = Settings { width: 1920, height: 1080, ..Settings::default() };
        assert!(!output_size(&native, &window).2);
        assert!(filter_args(&native, &window, "h264_nvenc", Backend::Ddagrab).is_empty());
    }

    #[test]
    fn parses_segment_csv() {
        let s = parse_segment_line(Path::new("C:\\buf"), "segment_000003.mkv,6.000000,8.016667").unwrap();
        assert_eq!(s.path, Path::new("C:\\buf\\segment_000003.mkv"));
        assert!((s.start - 6.0).abs() < 1e-9);
        assert!((s.end - 8.016667).abs() < 1e-9);
        assert!(parse_segment_line(Path::new("C:\\"), "garbage").is_none());
    }
}
