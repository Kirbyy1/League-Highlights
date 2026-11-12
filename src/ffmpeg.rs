//! Locating FFmpeg, picking a healthy encoder and running short jobs.

use std::collections::HashMap;
use std::ffi::OsStr;
use std::io::Read;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;
pub const BELOW_NORMAL_PRIORITY_CLASS: u32 = 0x0000_4000;

const ENCODERS: [&str; 4] = ["h264_nvenc", "h264_qsv", "h264_amf", "libx264"];

pub struct Ffmpeg {
    pub exe: PathBuf,
    filters: OnceLock<String>,
    encoders: Mutex<EncoderState>,
    /// Heavy jobs (clip mux, trim) run one at a time so they never compete
    /// with each other while the recorder and the game are running.
    job_gate: Mutex<()>,
}

#[derive(Default)]
struct EncoderState {
    selected: Option<&'static str>,
    unhealthy_until: HashMap<&'static str, Instant>,
}

impl Ffmpeg {
    pub fn locate() -> Option<Ffmpeg> {
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(Path::to_path_buf))
            .unwrap_or_default();
        let candidates = [
            exe_dir.join("ffmpeg.exe"),
            exe_dir.join("ffmpeg").join("ffmpeg.exe"),
            exe_dir.join("tools").join("ffmpeg").join("bin").join("ffmpeg.exe"),
        ];
        let found = candidates
            .into_iter()
            .find(|p| p.is_file())
            .or_else(|| search_path("ffmpeg.exe"))?;
        log::info!("Using FFmpeg at {}", found.display());
        Some(Ffmpeg {
            exe: found,
            filters: OnceLock::new(),
            encoders: Mutex::new(EncoderState::default()),
            job_gate: Mutex::new(()),
        })
    }

    pub fn command(&self, low_priority: bool) -> Command {
        let mut command = Command::new(&self.exe);
        let mut flags = CREATE_NO_WINDOW;
        if low_priority {
            flags |= BELOW_NORMAL_PRIORITY_CLASS;
        }
        command
            .creation_flags(flags)
            .stdin(Stdio::null())
            .stdout(Stdio::null());
        command
    }

    pub fn supports_filter(&self, name: &str) -> bool {
        let text = self.filters.get_or_init(|| {
            self.capture(&["-hide_banner", "-filters"], Duration::from_secs(20))
                .unwrap_or_default()
        });
        text.lines()
            .any(|line| line.split_whitespace().nth(1) == Some(name))
    }

    pub fn choose_encoder(&self) -> Result<&'static str, String> {
        let now = Instant::now();
        {
            let state = self.encoders.lock().unwrap();
            if let Some(selected) = state.selected {
                if state.unhealthy_until.get(selected).is_none_or(|t| *t <= now) {
                    return Ok(selected);
                }
            }
        }
        for encoder in ENCODERS {
            let blocked = self
                .encoders
                .lock()
                .unwrap()
                .unhealthy_until
                .get(encoder)
                .is_some_and(|t| *t > now);
            if blocked {
                continue;
            }
            if self.test_encoder(encoder) {
                log::info!("Selected video encoder: {encoder}");
                self.encoders.lock().unwrap().selected = Some(encoder);
                return Ok(encoder);
            }
        }
        Err("No usable H.264 encoder was found in FFmpeg.".into())
    }

    pub fn mark_unhealthy(&self, encoder: &str, reason: &str) {
        if let Some(known) = ENCODERS.iter().find(|e| **e == encoder) {
            let mut state = self.encoders.lock().unwrap();
            state
                .unhealthy_until
                .insert(known, Instant::now() + Duration::from_secs(300));
            if state.selected == Some(*known) {
                state.selected = None;
            }
            log::warn!("Encoder {encoder} marked unhealthy for 300s: {reason}");
        }
    }

    fn test_encoder(&self, encoder: &str) -> bool {
        let args = [
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=256x256:r=30",
            "-frames:v",
            "2",
            "-c:v",
            encoder,
            "-f",
            "null",
            "-",
        ];
        match self.run_unqueued(&args, Duration::from_secs(15), false) {
            Ok(()) => true,
            Err(reason) => {
                log::warn!("Encoder {encoder} is unavailable: {}", last_line(&reason));
                false
            }
        }
    }

    /// Run a heavy job through the single-job queue.
    pub fn run<S: AsRef<OsStr>>(&self, args: &[S], timeout: Duration) -> Result<(), String> {
        let _gate = self.job_gate.lock().unwrap_or_else(|e| e.into_inner());
        self.run_unqueued(args, timeout, true)
    }

    fn run_unqueued<S: AsRef<OsStr>>(
        &self,
        args: &[S],
        timeout: Duration,
        low_priority: bool,
    ) -> Result<(), String> {
        let mut child = self
            .command(low_priority)
            .args(args)
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("Could not start FFmpeg: {e}"))?;
        crate::win::adopt_child(&child);
        let mut stderr = child.stderr.take();
        let reader = std::thread::spawn(move || {
            let mut text = String::new();
            if let Some(pipe) = stderr.as_mut() {
                let _ = pipe.read_to_string(&mut text);
            }
            text
        });
        let deadline = Instant::now() + timeout;
        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break Some(status),
                Ok(None) if Instant::now() >= deadline => {
                    let _ = child.kill();
                    let _ = child.wait();
                    break None;
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(40)),
                Err(e) => return Err(e.to_string()),
            }
        };
        let output = reader.join().unwrap_or_default();
        match status {
            Some(s) if s.success() => Ok(()),
            Some(s) => Err(if output.trim().is_empty() {
                format!("FFmpeg exited with {s}")
            } else {
                output.trim().to_string()
            }),
            None => Err("FFmpeg timed out".into()),
        }
    }

    /// FFmpeg's stream summary for a file (`ffmpeg -i`), without ffprobe.
    pub fn probe(&self, path: &Path) -> Option<String> {
        let mut child = self
            .command(false)
            .args(["-hide_banner", "-i"])
            .arg(path)
            .stderr(Stdio::piped())
            .spawn()
            .ok()?;
        crate::win::adopt_child(&child);
        let mut text = String::new();
        let _ = child.stderr.take()?.read_to_string(&mut text);
        let _ = child.wait();
        Some(text)
    }

    pub fn probe_duration(&self, path: &Path) -> Option<f64> {
        parse_duration(&self.probe(path)?)
    }

    pub fn has_audio(&self, path: &Path) -> bool {
        self.probe(path).is_some_and(|t| t.contains("Audio:"))
    }

    fn capture(&self, args: &[&str], timeout: Duration) -> Option<String> {
        let mut child = self
            .command(false)
            .args(args)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .ok()?;
        crate::win::adopt_child(&child);
        let mut stdout = child.stdout.take()?;
        let reader = std::thread::spawn(move || {
            let mut text = String::new();
            let _ = stdout.read_to_string(&mut text);
            text
        });
        let deadline = Instant::now() + timeout;
        while child.try_wait().ok()?.is_none() {
            if Instant::now() >= deadline {
                let _ = child.kill();
                break;
            }
            std::thread::sleep(Duration::from_millis(40));
        }
        reader.join().ok()
    }
}

/// `Duration: 00:03:21.45,` -> 201.45
fn parse_duration(text: &str) -> Option<f64> {
    let rest = text.split("Duration: ").nth(1)?;
    let stamp = rest.split(',').next()?.trim();
    let mut parts = stamp.split(':');
    let h: f64 = parts.next()?.parse().ok()?;
    let m: f64 = parts.next()?.parse().ok()?;
    let s: f64 = parts.next()?.parse().ok()?;
    Some(h * 3600.0 + m * 60.0 + s)
}

#[cfg(test)]
mod probe_tests {
    #[test]
    fn parses_ffmpeg_duration() {
        let text = "Input #0, mp3, from 'x.mp3':\n  Duration: 00:03:21.45, start: 0.025, bitrate: 320 kb/s\n";
        assert_eq!(super::parse_duration(text), Some(201.45));
        assert_eq!(super::parse_duration("Duration: N/A, bitrate: N/A"), None);
    }
}

pub fn last_line(text: &str) -> &str {
    text.lines().rev().find(|l| !l.trim().is_empty()).unwrap_or(text)
}

fn search_path(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path)
        .map(|dir| dir.join(name))
        .find(|candidate| candidate.is_file())
}

/// Encoder arguments, identical to the tuned Python presets.
pub fn encoder_args(encoder: &str, quality: u32) -> Vec<String> {
    let q = quality.to_string();
    let list: Vec<&str> = match encoder {
        "h264_nvenc" => vec![
            "-c:v", encoder, "-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", &q, "-b:v", "0",
            "-maxrate", "20M", "-bufsize", "40M",
        ],
        "h264_qsv" => vec!["-c:v", encoder, "-preset", "medium", "-global_quality", &q],
        "h264_amf" => vec![
            "-c:v", encoder, "-quality", "speed", "-rc", "cqp", "-qp_i", &q, "-qp_p", &q,
        ],
        _ => vec!["-c:v", "libx264", "-preset", "veryfast", "-crf", &q, "-sc_threshold", "0"],
    };
    list.into_iter().map(String::from).collect()
}
