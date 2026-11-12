//! `LeagueHighlights.exe --selftest`: records the primary monitor for a few
//! seconds and saves one clip, exercising capture, audio, segment timing,
//! the export mux and the thumbnail without needing a League game.

use std::sync::Arc;
use std::time::Duration;

use windows::Win32::Foundation::POINT;
use windows::Win32::Graphics::Gdi::{MonitorFromPoint, MONITOR_DEFAULTTOPRIMARY};
use windows::Win32::UI::WindowsAndMessaging::{GetSystemMetrics, SM_CXSCREEN, SM_CYSCREEN};

use crate::audio::Audio;
use crate::config::{Paths, SharedSettings};
use crate::exporter::Exporter;
use crate::ffmpeg::Ffmpeg;
use crate::models::{EventTracker, HighlightRequest};
use crate::recorder::Recorder;
use crate::util::now;
use crate::win::GameWindow;

pub fn run(paths: &Paths, cfg: SharedSettings, ff: Option<Arc<Ffmpeg>>, seconds: u64, synthetic: bool) -> Result<String, String> {
    let ff = ff.ok_or("FFmpeg was not found")?;
    let (width, height, monitor_index) = unsafe {
        let monitor = MonitorFromPoint(POINT { x: 0, y: 0 }, MONITOR_DEFAULTTOPRIMARY);
        (
            GetSystemMetrics(SM_CXSCREEN),
            GetSystemMetrics(SM_CYSCREEN),
            crate::win::dxgi_output_index(monitor).unwrap_or(0),
        )
    };
    let (width, height) = if synthetic { (640, 360) } else { (width, height) };
    let window = GameWindow {
        hwnd: 0,
        pid: 0,
        title: "Self-test".into(),
        width,
        height,
        monitor_index,
        offset_x: 0,
        offset_y: 0,
    };
    let settings = cfg.read().unwrap().clone();
    let recorder = Arc::new(Recorder::new(ff.clone(), paths.root.join("selftest-buffer")));
    let audio = Arc::new(Audio::default());
    let warnings = audio.start(&settings);
    let started = now();
    let start = if synthetic {
        let small = crate::config::Settings { width: 640, height: 360, fps: 30, ..settings.clone() };
        recorder.start_synthetic(&small, &window)
    } else {
        recorder.start(&settings, &window)
    };
    if let Err(e) = start {
        audio.stop();
        return Err(format!("Capture failed: {e}"));
    }
    let startup = now() - started;
    std::thread::sleep(Duration::from_secs(seconds));
    let exporter = Exporter {
        cfg,
        paths: paths.clone(),
        ff,
        recorder: recorder.clone(),
        audio: audio.clone(),
        tracker: Arc::new(EventTracker::new(120.0)),
    };
    let request = HighlightRequest {
        label: "SELF TEST".into(),
        triggered_at_wall: Some(now()),
        highlight_score: 100,
        ..Default::default()
    };
    let export_started = now();
    let result = exporter.export(&request);
    let export_seconds = now() - export_started;
    let diag = recorder.diagnostics();
    recorder.stop();
    audio.stop();
    let _ = std::fs::remove_dir_all(paths.root.join("selftest-buffer"));
    let path = result?;
    let meta = crate::library::read_meta(&path.with_extension("json"));
    Ok(format!(
        "Saved {}\nCapture: {} via {} ({}x{} on output {monitor_index}), started in {startup:.1}s\n\
         FPS {:.1}, frames {}, dropped {}, duplicated {}\nAudio: {} {}\n\
         Clip duration {:.1}s, size {} bytes, thumbnail {}, export took {export_seconds:.1}s",
        path.display(),
        diag.encoder,
        diag.capture_backend,
        width,
        height,
        diag.fps,
        diag.frame,
        diag.dropped_frames,
        diag.duplicated_frames,
        meta.get("audio_sources").map(|v| v.to_string()).unwrap_or_default(),
        if warnings.is_empty() { String::new() } else { format!("(warnings: {})", warnings.join("; ")) },
        meta.get("duration_seconds").and_then(|v| v.as_f64()).unwrap_or(0.0),
        meta.get("size_bytes").map(|v| v.to_string()).unwrap_or_default(),
        if path.with_extension("jpg").is_file() { "yes" } else { "no" },
    ))
}
