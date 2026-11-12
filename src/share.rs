//! Discord-ready copies: a size-targeted re-encode of (part of) a clip,
//! written to `<clip folder>\Shared`, which the library never lists.
//! The original highlight is never changed.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::ffmpeg::{last_line, Ffmpeg};

const AUDIO_KBPS: u32 = 96;

#[derive(Debug, PartialEq)]
pub struct Plan {
    pub video_kbps: u32,
    pub max_height: u32,
    pub fps: u32,
}

/// Pick bitrate, resolution and frame rate for a size budget.
pub fn plan(duration: f64, target_bytes: u64, factor: f64) -> Plan {
    let duration = duration.max(1.0);
    // Leave ~4% for MP4 headers and rate-control overshoot.
    let total_kbps = target_bytes as f64 * 8.0 / duration / 1000.0 * 0.96 * factor;
    let video_kbps = (total_kbps - f64::from(AUDIO_KBPS) - 8.0).max(250.0) as u32;
    // Fewer pixels when the budget is tight, so each frame stays sharp.
    let max_height = match video_kbps {
        0..=1499 => 540,
        1500..=3999 => 720,
        _ => 1080,
    };
    let fps = if video_kbps >= 6000 { 60 } else { 30 };
    Plan { video_kbps, max_height, fps }
}

fn encoder_args(encoder: &str, kbps: u32) -> Vec<String> {
    let (b, max, buf) = (format!("{kbps}k"), format!("{}k", kbps * 13 / 10), format!("{}k", kbps * 2));
    let list: Vec<&str> = match encoder {
        "h264_nvenc" => vec![
            "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-multipass", "fullres",
            "-b:v", &b, "-maxrate", &max, "-bufsize", &buf,
        ],
        "h264_qsv" => vec!["-c:v", "h264_qsv", "-preset", "medium", "-b:v", &b, "-maxrate", &max, "-bufsize", &buf],
        "h264_amf" => vec!["-c:v", "h264_amf", "-quality", "quality", "-rc", "vbr_peak", "-b:v", &b, "-maxrate", &max, "-bufsize", &buf],
        _ => vec!["-c:v", "libx264", "-preset", "veryfast", "-b:v", &b, "-maxrate", &max, "-bufsize", &buf],
    };
    list.into_iter().map(String::from).collect()
}

/// Encode `source[start..end]` to fit `target_bytes`. Returns (path, size).
pub fn discord_copy(ff: &Ffmpeg, source: &Path, start: f64, end: f64, target_bytes: u64) -> Result<(PathBuf, u64), String> {
    let duration = (end - start).max(0.0);
    if duration < 1.0 {
        return Err("Select at least one second to export.".into());
    }
    let dir = source.parent().ok_or("Invalid clip path")?.join("Shared");
    fs::create_dir_all(&dir).map_err(|e| format!("Could not create {}: {e}", dir.display()))?;
    let stem = source.file_stem().unwrap_or_default().to_string_lossy().into_owned();
    let mut output = dir.join(format!("{stem}_discord.mp4"));
    let mut n = 2;
    while output.exists() {
        output = dir.join(format!("{stem}_discord_{n}.mp4"));
        n += 1;
    }
    let encoder = ff.choose_encoder()?;
    for factor in [1.0, 0.85, 0.7] {
        let p = plan(duration, target_bytes, factor);
        let mut args: Vec<String> = vec![
            "-hide_banner".into(), "-loglevel".into(), "error".into(), "-y".into(),
            "-ss".into(), format!("{start:.3}"), "-i".into(), source.to_string_lossy().into_owned(),
            "-t".into(), format!("{duration:.3}"),
            "-map".into(), "0:v:0".into(), "-map".into(), "0:a?".into(),
            "-vf".into(), format!("scale=-2:'min({},ih)':flags=bicubic,fps={},format=yuv420p", p.max_height, p.fps),
        ];
        args.extend(encoder_args(encoder, p.video_kbps));
        args.extend(
            ["-c:a", "aac", "-b:a", &format!("{AUDIO_KBPS}k"), "-ac", "2", "-movflags", "+faststart", "-f", "mp4"]
                .iter()
                .map(|s| s.to_string()),
        );
        let partial = output.with_extension("mp4.part");
        args.push(partial.to_string_lossy().into_owned());
        if let Err(e) = ff.run(&args, Duration::from_secs(300)) {
            let _ = fs::remove_file(&partial);
            return Err(format!("Discord export failed: {}", last_line(&e)));
        }
        let size = fs::metadata(&partial).map_or(0, |m| m.len());
        if size > 0 && size <= target_bytes {
            fs::rename(&partial, &output).map_err(|e| e.to_string())?;
            log::info!("Discord copy {} ({size} bytes, {} kbps, {}p{})", output.display(), p.video_kbps, p.max_height, p.fps);
            return Ok((output, size));
        }
        log::info!("Discord copy was {size} bytes (limit {target_bytes}); retrying smaller");
        let _ = fs::remove_file(&partial);
    }
    Err("Could not fit the clip under the upload limit. Trim it shorter and try again.".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plans_scale_with_budget() {
        let short = plan(10.0, 9_500_000, 1.0);
        assert_eq!((short.max_height, short.fps), (1080, 60));
        let long = plan(45.0, 9_500_000, 1.0);
        assert_eq!((long.max_height, long.fps), (720, 30));
        let tiny = plan(120.0, 9_500_000, 1.0);
        assert_eq!(tiny.max_height, 540);
        // Budget check: video + audio stays under the target.
        let p = plan(30.0, 9_500_000, 1.0);
        assert!(f64::from(p.video_kbps + AUDIO_KBPS) * 30.0 / 8.0 * 1000.0 < 9_500_000.0);
    }
}
