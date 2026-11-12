//! First-run / on-demand setup check: everything recording depends on, each
//! with a plain-language result and, where possible, a one-click fix.

use std::fs;
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::config::{Settings, MIN_FREE_DISK_MIB};
use crate::ffmpeg::Ffmpeg;

#[derive(Serialize)]
pub struct Check {
    pub id: &'static str,
    pub title: &'static str,
    /// ok | warn | fail | info
    pub status: &'static str,
    pub detail: String,
    /// UI action: "download-ffmpeg", "settings:<section>", "open-folder"
    pub fix: Option<(&'static str, &'static str)>,
}

fn check(id: &'static str, title: &'static str, status: &'static str, detail: impl Into<String>) -> Check {
    Check { id, title, status, detail: detail.into(), fix: None }
}

/// League's window mode from `Config/game.cfg`: 0 fullscreen, 1 windowed, 2 borderless.
fn league_window_mode() -> Option<(u32, PathBuf)> {
    let mut candidates: Vec<PathBuf> = crate::win::find_process_paths(&["leagueclientux.exe", "leagueclient.exe", "league of legends.exe"])
        .into_iter()
        .filter_map(|p| Path::new(&p).parent().map(Path::to_path_buf))
        .flat_map(|dir| {
            // The game exe lives in <install>\Game; the client in <install>.
            let parent = dir.parent().map(Path::to_path_buf);
            [Some(dir), parent].into_iter().flatten()
        })
        .map(|dir| dir.join("Config").join("game.cfg"))
        .collect();
    let drive = std::env::var("SystemDrive").unwrap_or_else(|_| "C:".into());
    candidates.push(PathBuf::from(format!("{drive}\\Riot Games\\League of Legends\\Config\\game.cfg")));
    for path in candidates {
        let Ok(text) = fs::read_to_string(&path) else { continue };
        let mut in_general = false;
        for line in text.lines() {
            let line = line.trim();
            if line.starts_with('[') {
                in_general = line.eq_ignore_ascii_case("[General]");
            } else if in_general {
                if let Some(value) = line.strip_prefix("WindowMode=") {
                    if let Ok(mode) = value.trim().parse() {
                        return Some((mode, path));
                    }
                }
            }
        }
    }
    None
}

pub fn run(settings: &Settings, ff: Option<&Ffmpeg>) -> Vec<Check> {
    let mut checks = Vec::new();

    match ff {
        Some(ff) => checks.push(check("ffmpeg", "FFmpeg", "ok", format!("Found at {}", ff.exe.display()))),
        None => checks.push(Check {
            fix: Some(("Download FFmpeg", "download-ffmpeg")),
            ..check("ffmpeg", "FFmpeg", "fail", "Recording needs ffmpeg.exe next to LeagueHighlights.exe. Reinstalling League Highlights restores it.")
        }),
    }

    if let Some(ff) = ff {
        match ff.choose_encoder() {
            Ok("libx264") => checks.push(Check {
                fix: Some(("Recording settings", "settings:Recording")),
                ..check("encoder", "Video encoder", "warn", "No hardware encoder found; recording uses the CPU (libx264). Update your graphics driver, or lower the resolution and frame rate.")
            }),
            Ok(encoder) => {
                let vendor = match encoder {
                    "h264_nvenc" => "NVIDIA NVENC",
                    "h264_qsv" => "Intel Quick Sync",
                    "h264_amf" => "AMD AMF",
                    other => other,
                };
                checks.push(check("encoder", "Video encoder", "ok", format!("{vendor} hardware encoding")));
            }
            Err(e) => checks.push(check("encoder", "Video encoder", "fail", e)),
        }
        checks.push(if ff.supports_filter("ddagrab") {
            check("capture", "Screen capture", "ok", "Desktop Duplication (GPU capture)")
        } else {
            check("capture", "Screen capture", "warn", "This FFmpeg build lacks Desktop Duplication; the slower GDI capture is used.")
        });
    }

    checks.push(match league_window_mode() {
        Some((0, _)) => Check {
            fix: None,
            ..check("league", "League display mode", "fail", "League is set to Fullscreen, which can't be recorded. In League: Settings → Video → Window Mode → Borderless.")
        },
        Some((1, _)) => check("league", "League display mode", "ok", "Windowed"),
        Some((2, _)) => check("league", "League display mode", "ok", "Borderless"),
        Some((mode, _)) => check("league", "League display mode", "info", format!("Unknown window mode {mode}; Borderless is recommended.")),
        None => check("league", "League display mode", "info", "League's settings weren't found. Make sure League runs in Borderless mode."),
    });

    if settings.system_audio_enabled {
        let devices = crate::audio::list_devices(true);
        checks.push(if devices.is_empty() {
            Check {
                fix: Some(("Audio settings", "settings:Audio")),
                ..check("audio", "Game audio", "warn", "No audio output device was found; clips will be saved without sound.")
            }
        } else {
            let name = if settings.system_audio_device.is_empty() { "Windows default output".to_string() } else { settings.system_audio_device.clone() };
            check("audio", "Game audio", "ok", format!("Recording {name}"))
        });
    } else {
        checks.push(Check {
            fix: Some(("Audio settings", "settings:Audio")),
            ..check("audio", "Game audio", "info", "System audio is off; clips will have no game sound.")
        });
    }

    let dir = settings.clip_dir();
    let writable = fs::create_dir_all(&dir).is_ok() && {
        let probe = dir.join(".write-test");
        let ok = fs::write(&probe, b"ok").is_ok();
        let _ = fs::remove_file(&probe);
        ok
    };
    checks.push(if writable {
        check("folder", "Clip folder", "ok", dir.display().to_string())
    } else {
        Check {
            fix: Some(("Choose a folder", "settings:Storage")),
            ..check("folder", "Clip folder", "fail", format!("Can't write to {}", dir.display()))
        }
    });

    let free = crate::util::disk_free_bytes(&dir);
    checks.push(match free {
        Some(bytes) if bytes < MIN_FREE_DISK_MIB * 1024 * 1024 => Check {
            fix: Some(("Clean up", "cleanup")),
            ..check("disk", "Free space", "fail", format!("Only {} MB free; recording stops below {MIN_FREE_DISK_MIB} MB.", bytes / 1_048_576))
        },
        Some(bytes) if bytes < 10 * 1024 * 1024 * 1024 => Check {
            fix: Some(("Clean up", "cleanup")),
            ..check("disk", "Free space", "warn", format!("{:.1} GB free", bytes as f64 / 1e9))
        },
        Some(bytes) => check("disk", "Free space", "ok", format!("{:.0} GB free", bytes as f64 / 1e9)),
        None => check("disk", "Free space", "info", "Couldn't read free space"),
    });

    checks.push(check(
        "hotkey",
        "Clip shortcut",
        "ok",
        format!("{} saves the last {} seconds", settings.hotkey_display, settings.buffer_seconds),
    ));
    checks
}
