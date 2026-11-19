//! Clip library on disk: `<clip>.mp4` + `<clip>.json` (+ `<clip>.jpg`).
//! The JSON layout is identical to the Python version, so existing libraries
//! open unchanged.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, UNIX_EPOCH};

use serde_json::{json, Map, Value};

use crate::ffmpeg::Ffmpeg;
use crate::models::{round3, MatchContext, StoredEvent};
use crate::scoring::suggest_trim;
use crate::util::{local_time, now};

fn parse_meta(text: &str) -> Map<String, Value> {
    match serde_json::from_str::<Value>(text.trim_start_matches('\u{feff}')) {
        Ok(Value::Object(map)) => map,
        _ => Map::new(),
    }
}

pub fn read_meta(path: &Path) -> Map<String, Value> {
    fs::read_to_string(path).map(|t| parse_meta(&t)).unwrap_or_default()
}

pub fn write_meta(path: &Path, meta: &Map<String, Value>) -> std::io::Result<()> {
    let text = serde_json::to_string_pretty(meta).map_err(std::io::Error::other)?;
    let temp = path.with_extension("json.tmp");
    fs::write(&temp, text)?;
    fs::rename(&temp, path)?;
    forget(path);
    Ok(())
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

/// Metadata text by JSON file name (lower case), with the size and time of
/// the file it was read from.
///
/// Listing the library used to open every clip's JSON, about 1 ms each
/// because every open is also virus-scanned: 157 clips took 175 ms, on every
/// refresh of the library window. With the cache a listing reads the directory
/// and only the files that changed. It keeps text rather than parsed objects
/// (about 3 KB per clip) and only exists while the library window is open, so
/// the tray app's idle memory is unchanged.
type MetaCache = HashMap<String, ((u64, u128), String)>;
static META_CACHE: Mutex<Option<(PathBuf, MetaCache)>> = Mutex::new(None);

/// Drop the cache (the library window closed).
pub fn drop_cache() {
    *META_CACHE.lock().unwrap() = None;
}

/// Forget one file's cached metadata (it was just rewritten).
fn forget(json: &Path) {
    if let Some((dir, cache)) = META_CACHE.lock().unwrap().as_mut() {
        if json.parent() == Some(dir.as_path()) {
            if let Some(name) = json.file_name() {
                cache.remove(&name.to_string_lossy().to_lowercase());
            }
        }
    }
}

/// All highlights, newest first, as metadata objects for the UI.
pub fn scan(dir: &Path) -> Vec<Value> {
    scan_with(dir, crate::window::is_open())
}

fn scan_with(dir: &Path, use_cache: bool) -> Vec<Value> {
    let mut clips = Vec::new();
    let Ok(entries) = fs::read_dir(dir) else { return clips };
    // One pass over the directory gives every file's size and time without
    // opening anything: lower-case name -> (name, size, modified).
    let mut listing: HashMap<String, (String, u64, Duration)> = HashMap::new();
    for entry in entries.flatten() {
        let Ok(info) = entry.metadata() else { continue };
        if !info.is_file() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().into_owned();
        let modified = info.modified().ok().and_then(|m| m.duration_since(UNIX_EPOCH).ok()).unwrap_or_default();
        listing.insert(name.to_lowercase(), (name, info.len(), modified));
    }
    let mut guard = META_CACHE.lock().unwrap();
    if use_cache {
        if guard.as_ref().is_none_or(|(cached_dir, _)| cached_dir != dir) {
            *guard = Some((dir.to_path_buf(), MetaCache::new()));
        }
    } else if guard.as_ref().is_some_and(|(cached_dir, _)| cached_dir != dir) {
        *guard = None;
    }
    let mut cache = if use_cache { guard.as_mut().map(|(_, cache)| cache) } else { None };
    let mut seen: HashSet<String> = HashSet::new();

    for (lower, (name, size, modified)) in &listing {
        let Some(stem_lower) = lower.strip_suffix(".mp4") else { continue };
        let stem = &name[..name.len() - 4];
        if stem.contains(".trim-") {
            continue;
        }
        let json_key = format!("{stem_lower}.json");
        let mut meta = match listing.get(&json_key) {
            None => Map::new(),
            Some((json_name, json_size, json_modified)) => {
                let signature = (*json_size, json_modified.as_nanos());
                match cache.as_mut() {
                    Some(cache) => {
                        seen.insert(json_key.clone());
                        match cache.get(&json_key) {
                            Some((cached, text)) if *cached == signature => parse_meta(text),
                            _ => {
                                let text = fs::read_to_string(dir.join(json_name)).unwrap_or_default();
                                let meta = parse_meta(&text);
                                cache.insert(json_key.clone(), (signature, text));
                                meta
                            }
                        }
                    }
                    None => read_meta(&dir.join(json_name)),
                }
            }
        };
        // Match reels from the Python version are hidden; reels made by this
        // version (event_kind "montage") are listed like any clip.
        let legacy_reel = meta.get("is_match_reel").and_then(Value::as_bool).unwrap_or(false)
            && meta.get("event_kind").and_then(Value::as_str) != Some("montage");
        if is_copy(stem, &meta) || legacy_reel {
            continue;
        }
        let mtime = modified.as_secs_f64();
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
        meta.insert("file".into(), json!(name));
        meta.insert("size_bytes".into(), json!(size));
        meta.insert("has_thumb".into(), json!(listing.contains_key(&format!("{stem_lower}.jpg"))));
        meta.insert("mtime".into(), json!(mtime));
        clips.push(Value::Object(meta));
    }
    if let Some(cache) = cache.as_mut() {
        cache.retain(|key, _| seen.contains(key));
    }
    drop(guard);
    clips.sort_by(|a, b| {
        let key = |v: &Value| (v["created_at"].as_str().unwrap_or("").to_string(), v["file"].as_str().unwrap_or("").to_string());
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
        // Never replace a result filled in from match history with UNKNOWN.
        let known = meta.get("match_result").and_then(Value::as_str).is_some_and(|r| r == "WIN" || r == "LOSE");
        if !(known && result.eq_ignore_ascii_case("UNKNOWN")) {
            meta.insert("match_result".into(), json!(result));
        }
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
    if result != "WIN" && result != "LOSE" {
        RESULTS_PENDING.store(true, std::sync::atomic::Ordering::SeqCst);
    }
    log::info!("Finalized game group {} with {changed} clip(s)", ctx.match_id);
}

/// Set when a match was saved without a Win or Loss (and at startup, for
/// older ones). The League Client watcher only scans the library while this
/// is set, instead of reading every clip's metadata once a minute.
pub static RESULTS_PENDING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(true);

/// Bumped when clip metadata changes in the background, so an open library
/// window knows to reload (for example a result filled in from match history).
pub static CHANGES: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Clip groups whose result is unknown: (match id, start, duration), one each.
pub fn unknown_results(dir: &Path) -> Vec<(String, f64, f64)> {
    let mut out: Vec<(String, f64, f64)> = Vec::new();
    let Ok(entries) = fs::read_dir(dir) else { return out };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let meta = read_meta(&path);
        let id = meta.get("match_id").and_then(Value::as_str).unwrap_or("");
        let result = meta.get("match_result").and_then(Value::as_str).unwrap_or("");
        let started = meta.get("match_started_at").and_then(Value::as_f64).unwrap_or(0.0);
        if id.is_empty() || started <= 0.0 || !result.eq_ignore_ascii_case("UNKNOWN") || out.iter().any(|(m, ..)| m == id) {
            continue;
        }
        let duration = meta.get("match_duration_seconds").and_then(Value::as_f64).unwrap_or(0.0);
        out.push((id.to_string(), started, duration));
    }
    out
}

/// Set the result of every clip in a match whose result is still unknown.
pub fn set_match_result(dir: &Path, match_id: &str, result: &str) -> usize {
    let mut changed = 0;
    let Ok(entries) = fs::read_dir(dir) else { return 0 };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let mut meta = read_meta(&path);
        let same = meta.get("match_id").and_then(Value::as_str) == Some(match_id);
        let unknown = meta.get("match_result").and_then(Value::as_str).is_none_or(|r| r.eq_ignore_ascii_case("UNKNOWN") || r.is_empty());
        if same && unknown {
            meta.insert("match_result".into(), json!(result));
            if write_meta(&path, &meta).is_ok() {
                changed += 1;
            }
        }
    }
    if changed > 0 {
        CHANGES.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }
    changed
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
pub fn set_favorite(path: &Path, favorite: bool) -> Result<(), String> {
    let meta_path = path.with_extension("json");
    let mut meta = read_meta(&meta_path);
    meta.insert("favorite".into(), json!(favorite));
    write_meta(&meta_path, &meta).map_err(|e| e.to_string())
}

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
        if clip["rating"].as_str() == Some("good") || clip["favorite"].as_bool() == Some(true) {
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
    meta.insert("thumb_v".into(), json!(crate::exporter::THUMB_VERSION));
    if let Some(ws) = window_start {
        meta.insert("clip_window_start_wall".into(), json!(ws + start));
        meta.insert("clip_window_end_wall".into(), json!(ws + end));
    }
    for key in ["file", "has_thumb", "mtime"] {
        meta.remove(key);
    }
    write_meta(&final_path.with_extension("json"), &meta).map_err(|e| e.to_string())?;
    thumbnail(ff, &final_path, &final_path.with_extension("jpg"), crate::exporter::thumb_time(trigger, new_duration));
    Ok(final_path.file_name().unwrap().to_string_lossy().into_owned())
}

#[cfg(test)]
mod reel_listing_tests {
    use super::*;

    #[test]
    fn reels_are_listed_but_python_reels_stay_hidden() {
        let dir = std::env::temp_dir().join(format!("lh-reels-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let clip = |name: &str, meta: Value| {
            fs::write(dir.join(format!("{name}.mp4")), b"x").unwrap();
            fs::write(dir.join(format!("{name}.json")), meta.to_string()).unwrap();
        };
        clip("A_MATCH_REEL_1", json!({"label": "MATCH REEL", "event_kind": "montage", "is_match_reel": true}));
        clip("B_REEL_PY", json!({"label": "MATCH REEL", "is_match_reel": true}));
        clip("C_KILL", json!({"label": "SINGLE KILL"}));
        let names: Vec<String> = scan(&dir).iter().filter_map(|c| c["file"].as_str().map(str::to_string)).collect();
        let _ = fs::remove_dir_all(&dir);
        assert!(names.contains(&"A_MATCH_REEL_1.mp4".to_string()), "{names:?}");
        assert!(names.contains(&"C_KILL.mp4".to_string()));
        assert!(!names.contains(&"B_REEL_PY.mp4".to_string()));
    }
}

#[cfg(test)]
mod scan_cache_tests {
    use super::*;

    #[test]
    fn cached_listing_matches_and_follows_changes() {
        let dir = std::env::temp_dir().join(format!("lh-scan-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        for (name, label) in [("A_KILL", "SINGLE KILL"), ("B_KILL", "DOUBLE KILL")] {
            fs::write(dir.join(format!("{name}.mp4")), b"video").unwrap();
            fs::write(dir.join(format!("{name}.jpg")), b"jpg").unwrap();
            write_meta(&dir.join(format!("{name}.json")), json!({"label": label, "created_at": name}).as_object().unwrap()).unwrap();
        }
        fs::write(dir.join("NO_META.mp4"), b"video").unwrap();
        let plain = scan_with(&dir, false);
        let first = scan_with(&dir, true);
        let again = scan_with(&dir, true); // served from the cache
        assert_eq!(plain, first);
        assert_eq!(first, again);
        assert_eq!(first.len(), 3);
        let clip = |list: &[Value], file: &str| list.iter().find(|c| c["file"] == file).cloned().unwrap();
        assert_eq!(clip(&first, "A_KILL.mp4")["has_thumb"], true);
        assert_eq!(clip(&first, "NO_META.mp4")["label"], "MANUAL CLIP");

        // Rewritten through the app: seen at once.
        set_rating(&dir.join("A_KILL.mp4"), "good").unwrap();
        assert_eq!(clip(&scan_with(&dir, true), "A_KILL.mp4")["rating"], "good");
        // Edited by another program (size changes): seen too.
        fs::write(dir.join("B_KILL.json"), r#"{"label": "TRIPLE KILL", "created_at": "B_KILL", "note": "edited"}"#).unwrap();
        assert_eq!(clip(&scan_with(&dir, true), "B_KILL.mp4")["label"], "TRIPLE KILL");
        // Deleted: gone from the listing and from the cache.
        delete(&dir.join("A_KILL.mp4"));
        let after = scan_with(&dir, true);
        assert!(after.iter().all(|c| c["file"] != "A_KILL.mp4"));
        assert!(!META_CACHE.lock().unwrap().as_ref().unwrap().1.contains_key("a_kill.json"));
        drop_cache();
        let _ = fs::remove_dir_all(&dir);
    }
}
