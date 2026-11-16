//! Media jobs on saved clips: vertical copies for TikTok and Shorts, match
//! montages, shrinking old clips, and redoing old thumbnails.
//! Every job writes to a `.part` file first, so a failure never leaves a
//! broken clip behind.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::exporter::{thumb_time, THUMB_VERSION};
use crate::ffmpeg::{last_line, Ffmpeg};
use crate::library::{self, read_meta, write_meta};
use crate::util::{local_time, now};

/// Bitrate for vertical copies (1080x1920).
const VERTICAL_KBPS: u32 = 8000;
/// Bitrate for shrunk clips at 1080p (scaled by resolution below).
const SHRINK_KBPS_1080P: f64 = 6000.0;

fn unique(dir: &Path, stem: &str, ext: &str) -> PathBuf {
    let mut path = dir.join(format!("{stem}.{ext}"));
    let mut n = 2;
    while path.exists() {
        path = dir.join(format!("{stem}_{n}.{ext}"));
        n += 1;
    }
    path
}

fn s(v: &str) -> String {
    v.to_string()
}

/// The vertical crop: a 9:16 window, scaled to 1080x1920. `x` is an FFmpeg
/// expression for the window's centre as a fraction of the width (0.5 =
/// middle); it may change over time to follow the champion.
pub fn vertical_filter_at(x: &str) -> String {
    // Even crop width (1080p gives 606, not 607.5) and square pixels.
    format!(
        "crop=w=trunc(ih*9/32)*2:h=ih:x='clip(({x})*in_w-out_w/2,0,in_w-out_w)':y=0,scale=1080:1920:flags=bicubic,setsar=1,format=yuv420p"
    )
}

#[cfg(test)]
pub fn vertical_filter() -> String {
    vertical_filter_at("0.5")
}

// ---- following the champion ----
// Your own health bar is the only yellow bar in League (allies are blue,
// enemies red), so the champion is found by looking for it in small frames.

const TRACK_W: usize = 480;
const TRACK_H: usize = 270;
const TRACK_FPS: f64 = 2.0;

fn bar_pixel(r: u8, g: u8, b: u8) -> bool {
    r > 190 && g > 150 && b < 110 && r >= g && r.saturating_sub(b) > 110
}

/// Centre of the longest yellow bar in a frame, as a fraction of the width.
/// The top strip (scoreboard), the HUD at the bottom and the minimap column
/// are ignored.
pub fn find_bar(frame: &[u8]) -> Option<f64> {
    let mut best: Option<(usize, f64)> = None;
    for y in TRACK_H * 6 / 100..TRACK_H * 80 / 100 {
        let row = &frame[y * TRACK_W * 3..(y + 1) * TRACK_W * 3];
        let mut run = 0usize;
        for x in 0..=TRACK_W {
            let hit = x < TRACK_W && bar_pixel(row[x * 3], row[x * 3 + 1], row[x * 3 + 2]);
            if hit {
                run += 1;
                continue;
            }
            if run >= 8 {
                let start = x - run;
                let in_minimap_column = start * 100 > TRACK_W * 82 && y * 10 > TRACK_H * 6;
                if !in_minimap_column && best.is_none_or(|(len, _)| run > len) {
                    best = Some((run, (start as f64 + run as f64 / 2.0) / TRACK_W as f64));
                }
            }
            run = 0;
        }
    }
    best.map(|(_, x)| x)
}

/// Clean the per-frame positions: a rolling median drops stray yellow things
/// (gold, items), gaps (death, fog) keep the last position, and a short
/// average turns jumps into pans. None when the bar was rarely found.
pub fn smooth_track(raw: &[Option<f64>]) -> Option<Vec<f64>> {
    let found = raw.iter().filter(|x| x.is_some()).count();
    if raw.is_empty() || found * 10 < raw.len() * 4 {
        return None;
    }
    let median: Vec<Option<f64>> = (0..raw.len())
        .map(|i| {
            let mut w: Vec<f64> = raw[i.saturating_sub(3)..(i + 4).min(raw.len())].iter().flatten().copied().collect();
            if w.is_empty() {
                return None;
            }
            w.sort_by(f64::total_cmp);
            Some(w[w.len() / 2])
        })
        .collect();
    let first = median.iter().flatten().next().copied()?;
    let mut filled = Vec::with_capacity(raw.len());
    let mut last = first;
    for m in &median {
        if let Some(v) = m {
            last = *v;
        }
        filled.push(last);
    }
    Some(
        (0..filled.len())
            .map(|i| {
                let w = &filled[i.saturating_sub(2)..(i + 3).min(filled.len())];
                w.iter().sum::<f64>() / w.len() as f64
            })
            .collect(),
    )
}

/// A piecewise-linear FFmpeg expression in `t` through the positions, one
/// per second at most (fewer for long clips, so the expression stays short).
pub fn path_expression(points: &[f64], fps: f64) -> String {
    if points.is_empty() {
        return "0.5".into();
    }
    let step = ((points.len() as f64 / 90.0).ceil() as usize).max(fps as usize).max(1);
    let keys: Vec<(f64, f64)> = points.iter().enumerate().step_by(step).map(|(i, x)| (i as f64 / fps, *x)).collect();
    let mut expr = format!("{:.4}", keys.last().unwrap().1);
    for pair in keys.windows(2).rev() {
        let ((t0, x0), (t1, x1)) = (pair[0], pair[1]);
        expr = format!("if(lt(t,{t1:.2}),{x0:.4}+({:.4})*(t-{t0:.2})/{:.2},{expr})", x1 - x0, t1 - t0);
    }
    expr
}

/// Where the champion is over `clip[start..end]`, as a crop expression.
fn follow_champion(ff: &Ffmpeg, clip: &Path, start: f64, end: f64, work: &Path) -> Option<String> {
    fs::create_dir_all(work).ok()?;
    let raw = work.join(format!("track-{}.rgb", std::process::id()));
    let args = vec![
        s("-hide_banner"), s("-loglevel"), s("error"), s("-y"), s("-ss"), format!("{start:.3}"), s("-t"), format!("{:.3}", end - start),
        s("-i"), clip.to_string_lossy().into_owned(),
        s("-vf"), format!("fps={TRACK_FPS},scale={TRACK_W}:{TRACK_H}"), s("-f"), s("rawvideo"), s("-pix_fmt"), s("rgb24"),
        raw.to_string_lossy().into_owned(),
    ];
    let ran = ff.run(&args, Duration::from_secs(120));
    let data = fs::read(&raw).unwrap_or_default();
    let _ = fs::remove_file(&raw);
    ran.ok()?;
    let frame = TRACK_W * TRACK_H * 3;
    let positions: Vec<Option<f64>> = data.chunks_exact(frame).map(find_bar).collect();
    let found = positions.iter().flatten().count();
    let path = smooth_track(&positions);
    log::info!("Vertical crop: found your champion in {found} of {} frames{}", positions.len(), if path.is_some() { "" } else { "; using the centre" });
    path.map(|p| path_expression(&p, TRACK_FPS))
}

/// A 9:16 copy of `clip[start..end]` in `<clip folder>\Shared`.
pub fn vertical_copy(ff: &Ffmpeg, clip: &Path, start: f64, end: f64, caption: Option<&str>) -> Result<PathBuf, String> {
    let duration = (end - start).max(0.0);
    if duration < 1.0 {
        return Err("Select at least one second to export.".into());
    }
    let dir = clip.parent().ok_or("Invalid clip path")?.join("Shared");
    fs::create_dir_all(&dir).map_err(|e| format!("Could not create {}: {e}", dir.display()))?;
    let stem = clip.file_stem().unwrap_or_default().to_string_lossy().into_owned();
    let output = unique(&dir, &format!("{stem}_vertical"), "mp4");
    let partial = output.with_extension("mp4.part");
    let encoder = ff.choose_encoder()?;
    let mut args = vec![
        s("-hide_banner"), s("-loglevel"), s("error"), s("-y"),
        s("-ss"), format!("{start:.3}"), s("-i"), clip.to_string_lossy().into_owned(), s("-t"), format!("{duration:.3}"),
        s("-map"), s("0:v:0"), s("-map"), s("0:a?"),
        s("-vf"), {
            let x = follow_champion(ff, clip, start, end, &dir).unwrap_or_else(|| s("0.5"));
            match caption.and_then(|c| crate::share::caption_filter(c, &dir)) {
                Some(text) => format!("{},{text}", vertical_filter_at(&x)),
                None => vertical_filter_at(&x),
            }
        },
    ];
    args.extend(crate::share::encoder_args(encoder, VERTICAL_KBPS));
    args.extend(["-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart", "-f", "mp4"].map(s));
    args.push(partial.to_string_lossy().into_owned());
    let result = ff.run(&args, Duration::from_secs(300));
    crate::share::remove_captions(&dir);
    if let Err(e) = result {
        let _ = fs::remove_file(&partial);
        return Err(format!("Vertical export failed: {}", last_line(&e)));
    }
    fs::rename(&partial, &output).map_err(|e| e.to_string())?;
    log::info!("Vertical copy {}", output.display());
    Ok(output)
}

fn concat_escape(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/").replace('\'', "'\\''")
}

/// Join clips (in the given order) into one new clip in the library.
/// The video is copied, not re-encoded; returns the new file name.
pub fn montage(ff: &Ffmpeg, dir: &Path, clips: &[PathBuf], work: &Path, label: &str) -> Result<String, String> {
    if clips.len() < 2 {
        return Err("Pick at least two clips for a montage.".into());
    }
    let metas: Vec<Map<String, Value>> = clips.iter().map(|c| read_meta(&c.with_extension("json"))).collect();
    let all_audio = metas.iter().all(|m| m.get("audio_included").and_then(Value::as_bool).unwrap_or(true));
    fs::create_dir_all(work).map_err(|e| e.to_string())?;
    let list = work.join("montage.txt");
    let text: String = clips.iter().map(|c| format!("file '{}'\n", concat_escape(c))).collect();
    fs::write(&list, text).map_err(|e| e.to_string())?;

    let first = &metas[0];
    let champion = first.get("champion_name").and_then(Value::as_str).unwrap_or("").to_string();
    let stamp = local_time(now());
    let tag = label.replace(' ', "_");
    let prefix = if champion.is_empty() { tag } else { format!("{}_{tag}", champion.replace(' ', "_")) };
    let output = unique(dir, &format!("{prefix}_{}", stamp.file_stamp()), "mp4");
    let partial = output.with_extension("mp4.part");
    let mut args = vec![
        s("-hide_banner"), s("-loglevel"), s("error"), s("-y"), s("-f"), s("concat"), s("-safe"), s("0"),
        s("-i"), list.to_string_lossy().into_owned(), s("-map"), s("0:v:0"),
    ];
    if all_audio {
        args.extend(["-map", "0:a:0?", "-c", "copy"].map(s));
    } else {
        // Some clips have no sound: keep the picture only rather than drift.
        args.extend(["-an", "-c:v", "copy"].map(s));
    }
    args.extend(["-avoid_negative_ts", "make_zero", "-movflags", "+faststart", "-f", "mp4"].map(s));
    args.push(partial.to_string_lossy().into_owned());
    let result = ff.run(&args, Duration::from_secs(300));
    let _ = fs::remove_dir_all(work);
    if let Err(e) = result {
        let _ = fs::remove_file(&partial);
        return Err(format!("Montage failed: {}", last_line(&e)));
    }
    fs::rename(&partial, &output).map_err(|e| e.to_string())?;

    let duration: f64 = metas.iter().map(|m| m.get("duration_seconds").and_then(Value::as_f64).unwrap_or(0.0)).sum();
    let size = fs::metadata(&output).map_or(0, |m| m.len());
    let mut meta = Map::new();
    for key in ["match_id", "player_name", "champion_name", "game_mode", "match_started_at", "match_result",
        "match_kills", "match_deaths", "match_assists", "match_duration_seconds", "match_ended_at"] {
        if let Some(v) = first.get(key) {
            meta.insert(key.into(), v.clone());
        }
    }
    meta.insert("label".into(), json!(label));
    meta.insert("created_at".into(), json!(stamp.iso()));
    meta.insert("duration_seconds".into(), json!((duration * 1000.0).round() / 1000.0));
    meta.insert("size_bytes".into(), json!(size));
    meta.insert("automatic".into(), json!(false));
    meta.insert("event_kind".into(), json!("montage"));
    meta.insert("reviewed".into(), json!(true));
    meta.insert("rating".into(), json!(""));
    meta.insert("favorite".into(), json!(false));
    meta.insert("audio_included".into(), json!(all_audio));
    meta.insert("events".into(), json!([]));
    meta.insert("montage_of".into(), json!(clips.iter().filter_map(|c| c.file_name()).map(|f| f.to_string_lossy().into_owned()).collect::<Vec<_>>()));
    meta.insert("thumb_v".into(), json!(THUMB_VERSION));
    write_meta(&output.with_extension("json"), &meta).map_err(|e| e.to_string())?;
    library::thumbnail(ff, &output, &output.with_extension("jpg"), (duration / 4.0).min(3.0));
    log::info!("Montage {} from {} clips ({duration:.1}s)", output.display(), clips.len());
    Ok(output.file_name().unwrap_or_default().to_string_lossy().into_owned())
}

/// Redo thumbnails taken before they were placed at the play.
/// `keep_going` is checked between clips so a game can stop the job.
pub fn refresh_thumbnails(ff: &Ffmpeg, dir: &Path, keep_going: &dyn Fn() -> bool) -> usize {
    let mut done = 0;
    let Ok(entries) = fs::read_dir(dir) else { return 0 };
    for entry in entries.flatten() {
        let meta_path = entry.path();
        if meta_path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let mut meta = read_meta(&meta_path);
        if meta.get("thumb_v").and_then(Value::as_u64).unwrap_or(1) >= THUMB_VERSION {
            continue;
        }
        let video = meta_path.with_extension("mp4");
        if !video.is_file() {
            continue;
        }
        if !keep_going() {
            break;
        }
        let duration = meta.get("duration_seconds").and_then(Value::as_f64).unwrap_or(0.0);
        let moment = meta.get("trigger_relative_seconds").and_then(Value::as_f64).unwrap_or(duration / 2.0);
        library::thumbnail(ff, &video, &video.with_extension("jpg"), thumb_time(moment, duration));
        meta.insert("thumb_v".into(), json!(THUMB_VERSION));
        if write_meta(&meta_path, &meta).is_ok() {
            done += 1;
        }
    }
    if done > 0 {
        log::info!("Refreshed {done} thumbnail(s)");
        library::CHANGES.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }
    done
}

/// Bitrate for a shrunk clip: 6 Mbps at 1080p, scaled by pixel count.
pub fn shrink_kbps(height: u32) -> u32 {
    let scale = f64::from(height.max(360)) / 1080.0;
    (SHRINK_KBPS_1080P * scale * scale).round().max(1500.0) as u32
}

/// Re-encode clips older than `days` at a lower bitrate, replacing the file
/// only when the result is smaller. Starred and Good clips are kept as they
/// are. Returns (clips shrunk, bytes saved).
pub fn shrink_old(ff: &Ffmpeg, dir: &Path, days: u32, height: u32, keep_going: &dyn Fn() -> bool) -> (usize, u64) {
    if days == 0 {
        return (0, 0);
    }
    let cutoff = local_time(now() - f64::from(days) * 86_400.0).iso();
    let Ok(encoder) = ff.choose_encoder() else { return (0, 0) };
    let kbps = shrink_kbps(height);
    let (mut count, mut saved) = (0usize, 0u64);
    for clip in library::scan(dir) {
        let created = clip["created_at"].as_str().unwrap_or("");
        let keep = clip["favorite"].as_bool() == Some(true) || clip["rating"].as_str() == Some("good");
        if created.is_empty() || created >= cutoff.as_str() || keep || clip["shrunk"].as_bool() == Some(true) {
            continue;
        }
        let Some(path) = clip["file"].as_str().and_then(|f| library::resolve(dir, f)) else { continue };
        if !keep_going() {
            break;
        }
        let before = fs::metadata(&path).map_or(0, |m| m.len());
        let partial = path.with_extension("mp4.part");
        let mut args = vec![
            s("-hide_banner"), s("-loglevel"), s("error"), s("-y"), s("-i"), path.to_string_lossy().into_owned(),
            s("-map"), s("0:v:0"), s("-map"), s("0:a?"),
        ];
        args.extend(crate::share::encoder_args(encoder, kbps));
        args.extend(["-c:a", "copy", "-movflags", "+faststart", "-f", "mp4"].map(s));
        args.push(partial.to_string_lossy().into_owned());
        let meta_path = path.with_extension("json");
        let mut meta = read_meta(&meta_path);
        match ff.run(&args, Duration::from_secs(600)) {
            Ok(()) => {
                let after = fs::metadata(&partial).map_or(0, |m| m.len());
                if after > 4096 && after < before {
                    if fs::rename(&partial, &path).is_ok() {
                        count += 1;
                        saved += before - after;
                        meta.insert("size_bytes".into(), json!(after));
                        meta.insert("discord_ready".into(), json!(after < 10_000_000));
                    }
                } else {
                    let _ = fs::remove_file(&partial);
                }
                // Mark it either way, so a clip that can't get smaller isn't retried.
                meta.insert("shrunk".into(), json!(true));
                let _ = write_meta(&meta_path, &meta);
            }
            Err(e) => {
                let _ = fs::remove_file(&partial);
                log::warn!("Could not shrink {}: {}", path.display(), last_line(&e));
            }
        }
    }
    if count > 0 {
        log::info!("Shrunk {count} clip(s) older than {days} days, saving {saved} bytes");
        library::CHANGES.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }
    (count, saved)
}

/// The clips a match reel is made from: the match's best plays (up to three,
/// by score), in the order they happened. Deaths and montages are left out.
pub fn reel_parts(dir: &Path, match_id: &str) -> Vec<PathBuf> {
    let mut clips: Vec<Value> = library::scan(dir)
        .into_iter()
        .filter(|c| c["match_id"].as_str() == Some(match_id))
        .filter(|c| !matches!(c["event_kind"].as_str(), Some("death") | Some("montage")))
        .collect();
    if clips.iter().any(|c| c["label"].as_str() == Some("MATCH REEL")) {
        return Vec::new();
    }
    clips.sort_by_key(|c| std::cmp::Reverse(c["highlight_score"].as_i64().unwrap_or(0)));
    clips.truncate(3);
    clips.sort_by(|a, b| a["created_at"].as_str().cmp(&b["created_at"].as_str()));
    clips.iter().filter_map(|c| c["file"].as_str().and_then(|f| library::resolve(dir, f))).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn vertical_crop_keeps_the_middle() {
        assert!(vertical_filter().starts_with("crop=w=trunc(ih*9/32)*2:h=ih:x='clip((0.5)*in_w-out_w/2,0,in_w-out_w)'"));
        assert!(vertical_filter().contains("scale=1080:1920") && vertical_filter().contains("setsar=1"));
    }

    fn frame_with_bar(x0: usize, len: usize, y: usize) -> Vec<u8> {
        let mut f = vec![30u8; TRACK_W * TRACK_H * 3];
        for x in x0..x0 + len {
            let i = (y * TRACK_W + x) * 3;
            f[i..i + 3].copy_from_slice(&[235, 200, 40]);
        }
        f
    }

    #[test]
    fn finds_the_yellow_bar() {
        let x = find_bar(&frame_with_bar(200, 20, 90)).unwrap();
        assert!((x - 210.0 / 480.0).abs() < 1e-9);
        assert!(find_bar(&frame_with_bar(200, 5, 90)).is_none(), "too short to be a bar");
        assert!(find_bar(&frame_with_bar(420, 20, 200)).is_none(), "minimap column");
        assert!(find_bar(&frame_with_bar(200, 20, 250)).is_none(), "bottom HUD");
    }

    #[test]
    fn track_drops_strays_and_fills_gaps() {
        let mut raw = vec![Some(0.45); 12];
        raw[5] = Some(0.1); // a gold icon
        raw[8] = None; // dead for a moment
        let p = smooth_track(&raw).unwrap();
        assert!(p.iter().all(|x| (x - 0.45).abs() < 1e-9), "{p:?}");
        assert!(smooth_track(&[None, None, Some(0.3), None, None]).is_none(), "rarely found");
        let e = path_expression(&[0.4, 0.4, 0.6, 0.6], 2.0);
        assert_eq!(e, "if(lt(t,1.00),0.4000+(0.2000)*(t-0.00)/1.00,0.6000)");
    }

    #[test]
    fn shrink_bitrate_follows_resolution() {
        assert_eq!(shrink_kbps(1080), 6000);
        assert_eq!(shrink_kbps(720), 2667);
        assert_eq!(shrink_kbps(1440), 10667);
        assert_eq!(shrink_kbps(240), 1500);
    }
}
