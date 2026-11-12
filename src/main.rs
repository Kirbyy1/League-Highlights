#![cfg_attr(not(test), windows_subsystem = "windows")]
//! League Highlights — lightweight Rust rewrite.
//!
//! Threads: main (tray message loop), controller, export worker, Live Client
//! detector, keyboard hook, FFmpeg stderr reader, one per audio source, and
//! the library web server only after it is first opened.

mod assets;
mod audio;
mod config;
mod controller;
mod edit;
mod exporter;
mod ffmpeg;
mod hotkey;
mod lcu;
mod learn;
mod library;
mod live;
mod logger;
mod models;
mod music;
mod recorder;
mod scoring;
mod overlay;
mod selftest;
mod setup;
mod update;
mod share;
mod tray;
mod util;
mod web;
mod win;
mod window;

use std::sync::atomic::AtomicUsize;
use std::sync::mpsc;
use std::sync::{Arc, RwLock};

use config::{Paths, Settings, BUFFER_SAFETY_SECONDS};
use controller::{Controller, Status};
use models::EventTracker;

fn main() {
    // Per-monitor DPI awareness: crisp window and physical-pixel window
    // rectangles, so the capture region is correct on scaled displays.
    unsafe {
        let _ = windows::Win32::UI::HiDpi::SetProcessDpiAwarenessContext(
            windows::Win32::UI::HiDpi::DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
        );
    }
    let paths = Paths::new();
    let _ = std::fs::create_dir_all(&paths.logs);
    logger::init(&paths.logs);
    let startup_launch = std::env::args().any(|a| a == "--startup");

    if !win::acquire_single_instance() {
        // Already running: bring up its library instead of starting twice.
        tray::signal_existing_instance();
        return;
    }
    log::info!("Starting League Highlights {}", env!("CARGO_PKG_VERSION"));

    let settings = Settings::load(&paths.settings);
    let _ = std::fs::create_dir_all(settings.clip_dir());
    let _ = std::fs::create_dir_all(&paths.buffer);
    let _ = std::fs::remove_dir_all(paths.root.join("export"));
    if !paths.settings.exists() {
        let _ = settings.save(&paths.settings);
    }
    hotkey::configure(settings.hotkey_vk, &settings.hotkey_modifiers);
    let start_minimized = settings.start_minimized;
    let tracker_retention = f64::from(settings.buffer_seconds + BUFFER_SAFETY_SECONDS + 120);
    let cfg = Arc::new(RwLock::new(settings));

    let ff = ffmpeg::Ffmpeg::locate().map(Arc::new);
    if ff.is_none() {
        log::error!("FFmpeg was not found next to the executable or on PATH");
    }

    if std::env::args().any(|a| a == "--selftest") {
        let synthetic = std::env::args().any(|a| a == "--synthetic");
        let result = selftest::run(&paths, cfg.clone(), ff, 8, synthetic);
        let text = match &result {
            Ok(report) => format!("Self-test passed\n\n{report}"),
            Err(error) => format!("Self-test failed\n\n{error}"),
        };
        log::info!("{}", text.replace('\n', " | "));
        let _ = std::fs::write(paths.root.join("selftest.txt"), &text);
        if std::env::var_os("LH_DATA_DIR").is_none() {
            win::message_box("League Highlights self-test", &text);
        }
        std::process::exit(if result.is_ok() { 0 } else { 1 });
    }
    let recorder = ff
        .as_ref()
        .map(|ff| Arc::new(recorder::Recorder::new(ff.clone(), paths.buffer.clone())));
    let audio = Arc::new(audio::Audio::default());
    let tracker = Arc::new(EventTracker::new(tracker_retention));
    let live = live::LiveShared::new();
    let status = Arc::new(RwLock::new(Status {
        state: "STARTING",
        ..Default::default()
    }));
    let busy = Arc::new(AtomicUsize::new(0));

    let (tx, rx) = mpsc::channel();
    let (export_tx, export_rx) = mpsc::channel();

    hotkey::start(tx.clone());

    let detector = live::Detector::new(paths.root.clone(), cfg.clone(), tx.clone(), tracker.clone(), live.clone());
    let _ = std::thread::Builder::new().name("live-events".into()).spawn(move || detector.run());

    if let (Some(ff), Some(recorder)) = (ff.clone(), recorder.clone()) {
        let exporter = exporter::Exporter {
            cfg: cfg.clone(),
            paths: paths.clone(),
            ff,
            recorder,
            audio: audio.clone(),
            tracker: tracker.clone(),
        };
        let busy = busy.clone();
        let done_tx = tx.clone();
        let _ = std::thread::Builder::new()
            .name("export".into())
            .spawn(move || controller::export_worker(exporter, export_rx, busy, done_tx));
    }

    let controller = Controller::new(
        cfg.clone(),
        paths.clone(),
        ff.clone(),
        recorder.clone(),
        audio,
        tracker,
        live.clone(),
        status.clone(),
        busy.clone(),
        export_tx,
    );
    let controller_thread = std::thread::Builder::new()
        .name("controller".into())
        .spawn(move || controller.run(rx))
        .expect("controller thread");

    let web = Arc::new(web::Web::new(web::WebCtx {
        cfg: cfg.clone(),
        paths: paths.clone(),
        status: status.clone(),
        tx: tx.clone(),
        ff,
        recorder,
        live,
        busy,
        assets: Arc::new(assets::Assets::new(paths.root.join("assets").join("champions"))),
    }));

    if std::env::var_os("LH_DATA_DIR").is_some() {
        // Portable/sandbox mode: expose the library address in the data folder.
        if let Some(url) = web.url() {
            let _ = std::fs::write(paths.root.join("library-url.txt"), url);
        }
    }
    if !startup_launch {
        if start_minimized {
            tray::notify("League Highlights is running", "Right-click the tray icon to open the library.");
        } else {
            web.open();
        }
    }

    tray::run(tray::TrayContext {
        status,
        tx: tx.clone(),
        cfg,
        paths,
        web,
    });

    log::info!("Shutting down");
    let _ = tx.send(models::Cmd::Quit);
    let _ = controller_thread.join();
    log::info!("Stopped");
}
