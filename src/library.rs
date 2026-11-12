//! Clip library on disk: `<clip>.mp4` + `<clip>.json` (+ `<clip>.jpg`).
//! The JSON layout is identical to the Python version, so existing libraries
//! open unchanged.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, UNIX_EPOCH};

use serde_json::{json, Map, Value};

use crate::ffmpeg::Ffmpeg;
use crate::models::{round3, MatchContext, StoredEvent};
use crate::scoring::suggest_trim;
use crate::util::{local_time, now};

pub fn read_meta(path: &Path) -> Map<String, Value> {
    fs::read_to_string(path)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| match v {
            Value::Object(map) => Some(map),
            _ => None,
        })
        .unwrap_or_default()
}

pub fn write_meta(path: &Path, meta: &Map<String, Value>) -> std::io::Result<()> {
    let text = serde_json::to_string_pretty(meta).map_err(std::io::Error::other)?;
    let temp = path.with_extension("json.tmp");
    fs::write(&temp, text)?;
    fs::rename(&temp, path)
}

fn is_copy(stem: &str, meta: &Map<String, Value>) -> bool {
    let flag = |k: &str| meta.get(k).and_then(Value::as_bool).unwrap_or(false);
    let lower = stem.to_lowercase();
    flag("is_discord_copy")
        || flag("is_share_copy")
        || lower.contains("_discord")
        || lower.ends_with("_share")
        || lower.contains("_share_")
}

/// All highlights, newest first, as metadata objects for the UI.
pub fn scan(dir: &Path) -> Vec<Value> {
    let mut clips = Vec::new();
    let Ok(entries) = fs::read_dir(dir) else { return clips };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()).map(str::to_lowercase).as_deref() != Some("mp4") {
            continue;
        }
        let Some(stem) = path.file_stem().map(|s| s.to_string_lossy().into_owned()) else { continue };
        if stem.contains(".trim-") {
            continue;
        }
        let mut meta = read_meta(&path.with_extension("json"));
        if is_copy(&stem, &meta) || meta.get("is_match_reel").and_then(Value::as_bool).unwrap_or(false) {
            continue;
        }
        let Ok(info) = entry.metadata() else { continue };
        let mtime = info
            .modified()
            .ok()
            .and_then(|m| m.duration_since(UNIX_EPOCH).ok())
            .map_or(0.0, |d| d.as_secs_f64());
        if !meta.get("created_at").is_some_and(Value::is_string) {
            meta.insert("created_at".into(), json!(local_time(mtime).iso()));
        }
        // Clips from before post-match review existed count as reviewed, so
        // an existing library doesn't suddenly show everything as new.
        if !meta.get("reviewed").is_some_and(Value::is_boolean) {
            meta.insert("reviewed".into(), json!(true));
        }
        if !meta.get("label").is_some_and(Value::is_string) {
            meta.insert("label".into(), json!("MANUAL CLIP"));
        }
        meta.insert("file".into(), json!(path.file_name().unwrap().to_string_lossy()));
        meta.insert("size_bytes".into(), json!(info.len()));
        meta.insert("has_thumb".into(), json!(path.with_extension("jpg").is_file()));
        meta.insert("mtime".into(), json!(mtime));
        clips.push(Value::Object(meta));
    }
    clips.sort_by(|a, b| {
        let key = |v: &Value| v["created_at"].as_str().unwrap_or("").to_string();
        key(b).cmp(&key(a))
    });
    clips
}

/// Resolve a clip file name from the UI to a path inside the library.
pub fn resolve(dir: &Path, file: &str) -> Option<PathBuf> {
    if file.is_empty() || file.contains(['/', '\\', ':']) || file.contains("..") {
        return None;
    }
    let path = dir.join(file);
    path.is_file().then_some(path)
}

pub fn finalize_match(dir: &Path, ctx: &MatchContext, result: &str) {
    let ended_at = now();
    let mut changed = 0;
    let Ok(entries) = fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let mut meta = read_meta(&path);
        if meta.get("match_id").and_then(Value::as_str) != Some(ctx.match_id.as_str()) {
            continue;
        }
        let mut fill = |key: &str, value: &str| {
            if meta.get(key).and_then(Value::as_str).unwrap_or("").is_empty() {
                meta.insert(key.into(), json!(value));
            }
        };
        fill("player_name", &ctx.player_name);
        fill("champion_name", &ctx.champion_name);
        fill("game_mode", &ctx.game_mode);
        meta.insert("map_name".into(), json!(ctx.map_name));
        meta.insert("match_result".into(), json!(result));
        meta.insert("match_started_at".into(), json!(ctx.started_at));
        meta.insert("match_ended_at".into(), json!(ended_at));
        meta.insert("match_kills".into(), json!(ctx.kills));
        meta.insert("match_deaths".into(), json!(ctx.deaths));
        meta.insert("match_assists".into(), json!(ctx.assists));
        let duration = if ctx.duration_seconds > 0.0 {
            ctx.duration_seconds
        } else {
            (ended_at - ctx.started_at).max(0.0)
        };
        meta.insert("match_duration_seconds".into(), json!(duration));
        meta.insert("team".into(), json!(ctx.team));
        if write_meta(&path, &meta).is_ok() {
            changed += 1;
        }
    }
    log::info!("Finalized game group {} with {changed} clip(s)", ctx.match_id);
}

pub fn set_rating(path: &Path, rating: &str) -> Result<(), String> {
    if !["", "good", "bad"].contains(&rating) {
        return Err("Rating must be good, bad, or empty.".into());
    }
    let meta_path = path.with_extension("json");
    let mut meta = read_meta(&meta_path);
    meta.insert("rating".into(), json!(rating));
    write_meta(&meta_path, &meta).map_err(|e| e.to_string())
}

/// Mark a clip as reviewed (post-match triage) or not.
pub fn set_reviewed(path: &Path, reviewed: bool) -> Result<(), String> {
    let meta_path = path.with_extension("json");
    let mut meta = read_meta(&meta_path);
    meta.insert("reviewed".into(), json!(reviewed));
    write_meta(&meta_path, &meta).map_err(|e| e.to_string())
}

/// Clips in a match the user hasn't reviewed yet.
pub fn unreviewed_in_match(dir: &Path, match_id: &str) -> usize {
    scan(dir)
        .iter()
        .filter(|c| c["match_id"].as_str() == Some(match_id))
        .filter(|c| !c["reviewed"].as_bool().unwrap_or(false))
        .count()
}

#[derive(serde::Serialize)]
pub struct Storage {
    pub clips: usize,
    pub library_bytes: u64,
    pub shared_bytes: u64,
    pub free_bytes: Option<u64>,
}

fn folder_bytes(dir: &Path) -> (usize, u64) {
    let mut clips = 0;
    let mut bytes = 0;
    if let Ok(entries) = fs::read_dir(dir) {
        for entry in entries.flatten() {
            let Ok(meta) = entry.metadata() else { continue };
            if meta.is_file() {
                bytes += meta.len();
                if entry.path().extension().and_then(|e| e.to_str()) == Some("mp4") {
                    clips += 1;
                }
            }
        }
    }
    (clips, bytes)
}

pub fn storage(dir: &Path) -> Storage {
    let (clips, library_bytes) = folder_bytes(dir);
    let (_, shared_bytes) = folder_bytes(&dir.join("Shared"));
    Storage { clips, library_bytes, shared_bytes, free_bytes: crate::util::disk_free_bytes(dir) }
}

/// Clean-up: clips older than `days` that are not rated Good. With
/// `dry_run` only counts. Returns (clips, bytes).
pub fn cleanup(dir: &Path, days: u32, dry_run: bool) -> (usize, u64) {
    if days == 0 {
        return (0, 0);
    }
    let cutoff = local_time(now() - f64::from(days) * 86_400.0).iso();
    let mut count = 0;
    let mut bytes = 0;
    for clip in scan(dir) {
        if clip["rating"].as_str() == Some("good") {
            continue;
        }
        let created = clip["created_at"].as_str().unwrap_or("");
        if created.is_empty() || created >= cutoff.as_str() {
            continue;
        }
        let Some(path) = clip["file"].as_str().and_then(|f| resolve(dir, f)) else { continue };
        count += 1;
        bytes += clip["size_bytes"].as_u64().unwrap_or(0);
        if !dry_run {
            delete(&path);
        }
    }
    if !dry_run && count > 0 {
        log::info!("Clean-up removed {count} clip(s) older than {days} days ({bytes} bytes)");
    }
    (count, bytes)
}

pub fn delete(path: &Path) {
    for p in [path.to_path_buf(), path.with_extension("jpg"), path.with_extension("json")] {
        if p.exists() {
            if let Err(e) = fs::remove_file(&p) {
                log::warn!("Could not delete {}: {e}", p.display());
            }
        }
    }
}

pub fn thumbnail(ff: &Ffmpeg, video: &Path, out: &Path, at_seconds: f64) {
    let args: Vec<String> = vec![
        "-hide_banner".into(),
        "-loglevel".into(),
        "error".into(),
        "-y".into(),
        "-ss".into(),
        format!("{:.2}", at_seconds.max(0.0)),
        "-i".into(),
        video.to_string_lossy().into_owned(),
        "-frames:v".into(),
        "1".into(),
        "-vf".into(),
        "scale=480:-2".into(),
        "-q:v".into(),
        "3".into(),
        out.to_string_lossy().into_owned(),
    ];
    if let Err(e) = ff.run(&args, Duration::from_secs(30)) {
        log::warn!("Could not generate thumbnail: {e}");
    }
}

fn replace_with_retry(source: &Path, target: &Path) -> std::io::Result<()> {
    let mut delay = Duration::from_millis(80);
    let deadline = std::time::Instant::now() + Duration::from_secs(4);
    loop {
        match fs::rename(source, target) {
            Ok(()) => return Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied && std::time::Instant::now() < deadline => {
                std::thread::sleep(delay);
                delay = (delay * 4 / 3).min(Duration::from_millis(250));
            }
            Err(e) => return Err(e),
        }
    }
}

/// Lossless trim (stream copy). Returns the resulting file name.
pub fn trim(ff: &Ffmpeg, source: &Path, start: f64, end: f64, replace: bool) -> Result<String, String> {
    let meta_path = source.with_extension("json");
    let mut meta = read_meta(&meta_path);
    let duration = meta.get("duration_seconds").and_then(Value::as_f64).unwrap_or(f64::MAX);
    let start = start.max(0.0);
    let end = end.min(duration);
    if end - start < 0.25 {
        return Err("The trimmed clip must be at least 0.25 seconds long.".into());
    }
    let stem = source.file_stem().unwrap().to_string_lossy().into_owned();
    let output = if replace {
        source.with_file_name(format!("{stem}.trim-working.mp4"))
    } else {
        let mut candidate = source.with_file_name(format!("{stem}_trimmed.mp4"));
        let mut index = 2;
        while candidate.exists() {
            candidate = source.with_file_name(format!("{stem}_trimmed_{index}.mp4"));
            index += 1;
        }
        candidate
    };
    let args: Vec<String> = vec![
        "-hide_banner".into(),
        "-loglevel".into(),
        "error".into(),
        "-y".into(),
        "-ss".into(),
        format!("{start:.3}"),
        "-i".into(),
        source.to_string_lossy().into_owned(),
        "-t".into(),
        format!("{:.3}", end - start),
        "-map".into(),
        "0:v:0".into(),
        "-map".into(),
        "0:a?".into(),
        "-c".into(),
        "copy".into(),
        "-avoid_negative_ts".into(),
        "make_zero".into(),
        "-movflags".into(),
        "+faststart".into(),
        output.to_string_lossy().into_owned(),
    ];
    ff.run(&args, Duration::from_secs(180))?;
    if fs::metadata(&output).map_or(0, |m| m.len()) < 4096 {
        let _ = fs::remove_file(&output);
        return Err("FFmpeg created an empty trimmed clip.".into());
    }
    let new_duration = end - start;

    let final_path = if replace {
        let backup = source.with_file_name(format!("{stem}.trim-backup.mp4"));
        let swap = replace_with_retry(source, &backup).and_then(|_| replace_with_retry(&output, source));
        if let Err(e) = swap {
            if backup.exists() && !source.exists() {
                let _ = replace_with_retry(&backup, source);
            }
            let _ = fs::remove_file(&output);
            return Err(format!("Could not replace the original clip: {e}"));
        }
        let _ = fs::remove_file(&backup);
        source.to_path_buf()
    } else {
        output
    };

    // Shift events and Smart Trim data into the new timeline.
    let events: Vec<StoredEvent> = meta
        .get("events")
        .and_then(Value::as_array)
        .map(|list| list.iter().filter_map(StoredEvent::from_json).collect())
        .unwrap_or_default();
    let shifted: Vec<StoredEvent> = events
        .into_iter()
        .filter(|e| e.relative_time >= start && e.relative_time <= end)
        .map(|e| StoredEvent { relative_time: e.relative_time - start, ..e })
        .collect();
    let trigger = meta
        .get("trigger_relative_seconds")
        .and_then(Value::as_f64)
        .map_or(new_duration, |t| t - start)
        .clamp(0.0, new_duration);
    let manual = meta.get("event_kind").and_then(Value::as_str).unwrap_or("manual") == "manual";
    let suggestion = suggest_trim(new_duration, &shifted, Some(trigger), manual);
    let window_start = meta.get("clip_window_start_wall").and_then(Value::as_f64);

    if !replace {
        meta.insert("created_at".into(), json!(local_time(now()).iso()));
    }
    meta.insert("duration_seconds".into(), json!(round3(new_duration)));
    meta.insert("trimmed".into(), json!(true));
    meta.insert("trim_start_seconds".into(), json!(round3(start)));
    meta.insert("trim_end_seconds".into(), json!(round3(end)));
    meta.insert("trimmed_from".into(), json!(source.file_name().unwrap().to_string_lossy()));
    let size = fs::metadata(&final_path).map_or(0, |m| m.len());
    meta.insert("size_bytes".into(), json!(size));
    meta.insert("discord_ready".into(), json!(size < 10_000_000));
    meta.insert("events".into(), Value::Array(shifted.iter().map(StoredEvent::to_json).collect()));
    meta.insert("trigger_relative_seconds".into(), json!(round3(trigger)));
    meta.insert("suggested_trim_start".into(), json!(round3(suggestion.start)));
    meta.insert("suggested_trim_end".into(), json!(round3(suggestion.end)));
    meta.insert("smart_trim_reason".into(), json!(suggestion.reason));
    if let Some(ws) = window_start {
        meta.insert("clip_window_start_wall".into(), json!(ws + start));
        meta.insert("clip_window_end_wall".into(), json!(ws + end));
    }
    for key in ["file", "has_thumb", "mtime"] {
        meta.remove(key);
    }
    write_meta(&final_path.with_extension("json"), &meta).map_err(|e| e.to_string())?;
    thumbnail(ff, &final_path, &final_path.with_extension("jpg"), (new_duration * 0.45).max(0.1));
    Ok(final_path.file_name().unwrap().to_string_lossy().into_owned())
}
