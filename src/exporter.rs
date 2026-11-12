//! Turns rolling segments into a saved highlight.
//!
//! One FFmpeg process per clip (the Python version used ~25: an ffprobe per
//! segment, concat, mux, copy and thumbnail). Video is stream-copied, audio is
//! mixed from the WAV buffers, and the thumbnail comes from the same run as a
//! second output. The clip is written as `.part` and renamed when complete.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use serde_json::{json, Map, Value};

use crate::audio::Audio;
use crate::config::{Paths, SharedSettings, MIN_FREE_DISK_MIB, SEGMENT_SECONDS};
use crate::ffmpeg::{last_line, Ffmpeg};
use crate::library::write_meta;
use crate::models::{round3, EventTracker, HighlightRequest, StoredEvent};
use crate::recorder::{Recorder, TimedSegment};
use crate::scoring::suggest_trim;
use crate::util::{disk_free_bytes, local_time, now, underscore_alnum};

pub struct Exporter {
    pub cfg: SharedSettings,
    pub paths: Paths,
    pub ff: Arc<Ffmpeg>,
    pub recorder: Arc<Recorder>,
    pub audio: Arc<Audio>,
    pub tracker: Arc<EventTracker>,
}

fn fallback_duration(label: &str, buffer: u32) -> f64 {
    let seconds = match label {
        "SINGLE KILL" => 12,
        "DOUBLE KILL" => 16,
        "TRIPLE KILL" => 20,
        "QUADRA KILL" => 26,
        "PENTAKILL" => 32,
        "ELDER DRAGON" => 16,
        "MANUAL CLIP" => buffer,
        l if l.contains("DRAGON") || l.contains("BARON") => 14,
        _ => buffer,
    };
    f64::from(seconds.min(buffer))
}

fn request_event_type(r: &HighlightRequest) -> String {
    if r.event_kind == "manual" || !r.automatic {
        return "MANUAL_TRIGGER".into();
    }
    let label = r.clean_label();
    if r.event_kind == "victory" {
        return "VICTORY".into();
    }
    if r.event_kind == "objective" {
        return "OBJECTIVE_STEAL".into();
    }
    if label.contains("BARON") {
        return if label.contains("STEAL") { "BARON_STEAL" } else { "BARON" }.into();
    }
    if label.contains("DRAGON") {
        return if label.contains("STEAL") { "DRAGON_STEAL" } else { "DRAGON" }.into();
    }
    if ["DOUBLE", "TRIPLE", "QUADRA", "PENTA"].iter().any(|t| label.contains(t)) {
        return "MULTIKILL".into();
    }
    if label.contains("KILL") {
        return "CHAMPION_KILL".into();
    }
    r.event_kind.to_uppercase()
}

fn concat_escape(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/").replace('\'', "'\\''")
}

fn unique_path(path: PathBuf) -> PathBuf {
    if !path.exists() {
        return path;
    }
    let stem = path.file_stem().unwrap().to_string_lossy().into_owned();
    (2..)
        .map(|n| path.with_file_name(format!("{stem}_{n}.mp4")))
        .find(|p| !p.exists())
        .unwrap()
}

impl Exporter {
    pub fn export(&self, request: &HighlightRequest) -> Result<PathBuf, String> {
        let s = self.cfg.read().unwrap().clone();
        let clip_dir = s.clip_dir();
        fs::create_dir_all(&clip_dir).map_err(|e| format!("Could not create {}: {e}", clip_dir.display()))?;
        if disk_free_bytes(&clip_dir).is_some_and(|free| free < MIN_FREE_DISK_MIB * 1024 * 1024) {
            return Err(format!(
                "Not enough free disk space to save a highlight. Free at least {MIN_FREE_DISK_MIB} MiB and try again."
            ));
        }
        if !self.recorder.running() {
            return Err("Recording is not active.".into());
        }

        let label = request.clean_label();
        let precise = request.desired_window().filter(|_| s.discord_auto_trim_events && request.automatic);

        // Objectives arrive immediately: wait until the post-roll exists.
        let needed_end = precise.map_or_else(|| request.triggered_at_wall.unwrap_or_else(now), |w| w.1);
        let delay = needed_end - now();
        if delay > 0.0 {
            log::info!("Waiting {delay:.1}s for post-event footage");
            std::thread::sleep(Duration::from_secs_f64((delay + 0.15).min(f64::from(s.buffer_seconds))));
        }
        let segment_wait = Duration::from_secs(u64::from(SEGMENT_SECONDS) + 3);
        if !self.recorder.wait_for_footage(needed_end, segment_wait) {
            log::warn!("Latest footage did not reach the requested end; using what is available");
        }

        let _hold = self.recorder.hold();
        let timeline = self.recorder.timeline();
        if timeline.is_empty() {
            return Err(format!(
                "The recording buffer is still warming up. Try {} again in a few seconds.",
                s.hotkey_display
            ));
        }
        let (selected, trim_start, trim_duration, requested) = match precise {
            Some((start, end)) => select_precise(&timeline, start, end)?,
            None => {
                let requested = fallback_duration(&label, s.buffer_seconds);
                select_tail(&timeline, requested)
            }
        };
        if trim_duration < 3.0 {
            return Err(format!(
                "The recording buffer is still warming up. Try {} again in a few seconds.",
                s.hotkey_display
            ));
        }
        let selected_start = selected[0].start_wall;
        let selected_end = selected[selected.len() - 1].end_wall;
        let duration = selected.iter().map(|seg| seg.end_wall - seg.start_wall).sum::<f64>();

        let created = now();
        let stamp = local_time(created);
        let mut file_label = underscore_alnum(&label.to_uppercase());
        if file_label.is_empty() {
            file_label = "HIGHLIGHT".into();
        }
        let champion = underscore_alnum(&request.champion_name);
        let prefix = if champion.is_empty() { file_label } else { format!("{champion}_{file_label}") };
        let output = unique_path(clip_dir.join(format!("{prefix}_{}.mp4", stamp.file_stamp())));
        let partial = output.with_extension("mp4.part");
        let thumb = output.with_extension("jpg");

        let work = self.paths.root.join("export").join(format!("{}", (created * 1000.0) as u64));
        fs::create_dir_all(&work).map_err(|e| e.to_string())?;
        let result = self.mux(&s, &work, &selected, selected_start, selected_end, duration, &partial, &thumb);
        let _ = fs::remove_dir_all(&work);
        let audio = match result {
            Ok(audio) => audio,
            Err(e) => {
                let _ = fs::remove_file(&partial);
                return Err(e);
            }
        };
        let size = fs::metadata(&partial).map_or(0, |m| m.len());
        if size < 4096 {
            let _ = fs::remove_file(&partial);
            return Err("The saved highlight file is incomplete.".into());
        }
        fs::rename(&partial, &output).map_err(|e| e.to_string())?;

        let window_start = selected_start + trim_start;
        let window_end = window_start + trim_duration;
        let trigger_wall = request
            .triggered_at_wall
            .or(request.event_ended_at)
            .or(request.event_started_at)
            .unwrap_or(selected_end);
        let trigger_relative = (trigger_wall - selected_start).clamp(0.0, duration);
        let mut events = self.tracker.events_for_clip(selected_start, selected_end, &request.match_id);
        let expected = request_event_type(request);
        if !events
            .iter()
            .any(|e| e.event_type == expected && (e.relative_time - trigger_relative).abs() <= 1.5)
        {
            events.push(StoredEvent {
                relative_time: trigger_relative,
                event_type: expected,
                game_time: request.event_game_time,
            });
            events.sort_by(|a, b| a.relative_time.total_cmp(&b.relative_time));
        }
        let manual = request.event_kind == "manual" || !request.automatic;
        let suggestion = suggest_trim(duration, &events, Some(trigger_relative), manual);

        let mut meta = Map::new();
        let mut put = |k: &str, v: Value| {
            meta.insert(k.to_string(), v);
        };
        put("label", json!(label));
        put("created_at", json!(stamp.iso()));
        put("duration_seconds", json!(round3(duration)));
        put("match_id", json!(request.match_id));
        put("player_name", json!(request.player_name));
        put("champion_name", json!(request.champion_name));
        put("game_mode", json!(request.game_mode));
        put("match_started_at", json!(request.match_started_at));
        put("event_kind", json!(request.event_kind));
        put("event_game_time", json!(request.event_game_time));
        put("automatic", json!(request.automatic));
        put("trigger_relative_seconds", json!(round3(trigger_relative)));
        put("events", Value::Array(events.iter().map(StoredEvent::to_json).collect()));
        put("suggested_trim_start", json!(round3(suggestion.start)));
        put("suggested_trim_end", json!(round3(suggestion.end)));
        put("smart_trim_reason", json!(suggestion.reason));
        put("video_buffer_start_wall", json!(selected_start));
        put("video_buffer_end_wall", json!(selected_end));
        put("highlight_score", json!(request.highlight_score));
        put("score_reasons", json!(request.score_reasons));
        put("victim_names", json!(request.victim_names));
        put("victim_champions", json!(request.victim_champions));
        put("assister_names", json!(request.assister_names));
        put("event_started_at", json!(request.event_started_at));
        put("event_ended_at", json!(request.event_ended_at));
        put("clip_window_start_wall", json!(window_start));
        put("clip_window_end_wall", json!(window_end));
        put("is_match_reel", json!(false));
        put("rating", json!(""));
        put("reviewed", json!(false));
        put("requested_duration_seconds", json!(requested));
        put("selected_buffer_seconds", json!(round3(selected_end - selected_start)));
        put("trim_start_seconds", json!(round3(trim_start)));
        put("precise_event_window", json!(precise.is_some()));
        put("audio_included", json!(audio.0 || audio.1));
        put("system_audio_included", json!(audio.0));
        put("microphone_included", json!(audio.1));
        let mut sources = Vec::new();
        if audio.0 {
            sources.push("system");
        }
        if audio.1 {
            sources.push("microphone");
        }
        put("audio_sources", json!(sources));
        put("system_audio_device", json!(self.audio.device_name(crate::audio::Kind::System)));
        put("microphone_device", json!(self.audio.device_name(crate::audio::Kind::Microphone)));
        put("system_audio_volume", json!(s.system_audio_volume));
        put("microphone_volume", json!(s.microphone_volume));
        put("size_bytes", json!(size));
        put("discord_ready", json!(size < 10_000_000));
        put("quality_mode", json!("original_recording"));
        write_meta(&output.with_extension("json"), &meta).map_err(|e| e.to_string())?;
        log::info!("Saved clip to {} ({size} bytes, {duration:.1}s)", output.display());
        Ok(output)
    }

    /// Returns (system audio included, microphone included).
    #[allow(clippy::too_many_arguments)]
    fn mux(
        &self,
        s: &crate::config::Settings,
        work: &Path,
        selected: &[TimedSegment],
        start: f64,
        end: f64,
        duration: f64,
        partial: &Path,
        thumb: &Path,
    ) -> Result<(bool, bool), String> {
        let list = work.join("segments.txt");
        let text: String = selected
            .iter()
            .map(|seg| format!("file '{}'\n", concat_escape(&seg.path)))
            .collect();
        fs::write(&list, text).map_err(|e| e.to_string())?;

        let audio = self.audio.export(start, end, work);
        let mut inputs: Vec<(PathBuf, f64)> = Vec::new();
        if let Some(p) = &audio.system {
            inputs.push((p.clone(), f64::from(s.system_audio_volume) / 100.0));
        }
        if let Some(p) = &audio.microphone {
            inputs.push((p.clone(), f64::from(s.microphone_volume) / 100.0));
        }
        let flags = (audio.system.is_some(), audio.microphone.is_some());
        let thumb_at = (duration / 3.0).clamp(0.0, 2.0);

        match self.ff.run(&mux_args(s, &list, &inputs, partial, thumb, thumb_at), Duration::from_secs(120)) {
            Ok(()) => Ok(flags),
            Err(error) if !inputs.is_empty() => {
                log::warn!("Could not attach audio ({}); saving a video-only clip", last_line(&error));
                self.ff
                    .run(&mux_args(s, &list, &[], partial, thumb, thumb_at), Duration::from_secs(90))
                    .map(|_| (false, false))
            }
            Err(error) => Err(format!("Could not save the clip: {}", last_line(&error))),
        }
    }
}

fn mux_args(
    s: &crate::config::Settings,
    list: &Path,
    audio: &[(PathBuf, f64)],
    partial: &Path,
    thumb: &Path,
    thumb_at: f64,
) -> Vec<String> {
    let mut a: Vec<String> = ["-hide_banner", "-loglevel", "error", "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i"]
        .iter()
        .map(|x| x.to_string())
        .collect();
    a.push(list.to_string_lossy().into_owned());
    for (path, _) in audio {
        a.push("-i".into());
        a.push(path.to_string_lossy().into_owned());
    }
    a.extend(["-map", "0:v:0", "-c:v", "copy"].iter().map(|x| x.to_string()));
    // Normalise each source to 48 kHz stereo; mix only at export so the
    // video stream is never re-encoded by audio settings.
    let common = "aresample=48000:async=1000:first_pts=0,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo";
    match audio.len() {
        0 => a.push("-an".into()),
        1 => {
            let gain = audio[0].1;
            let mut filter = format!("{common},volume={gain:.3}");
            if gain > 1.0 {
                filter.push_str(",alimiter=limit=0.98");
            }
            a.extend(["-map".into(), "1:a:0".into(), "-af".into(), filter]);
        }
        _ => {
            let mut parts = Vec::new();
            let mut labels = String::new();
            for (index, (_, gain)) in audio.iter().enumerate() {
                parts.push(format!("[{}:a]{common},volume={gain:.3}[a{}]", index + 1, index + 1));
                labels.push_str(&format!("[a{}]", index + 1));
            }
            parts.push(format!(
                "{labels}amix=inputs={}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.94[aout]",
                audio.len()
            ));
            a.extend(["-filter_complex".into(), parts.join(";"), "-map".into(), "[aout]".into()]);
        }
    }
    if !audio.is_empty() {
        a.extend(
            ["-c:a", "aac", "-b:a", &format!("{}k", s.audio_bitrate_kbps), "-ar", "48000", "-ac", "2"]
                .iter()
                .map(|x| x.to_string()),
        );
    }
    a.extend(
        ["-avoid_negative_ts", "make_zero", "-movflags", "+faststart", "-f", "mp4"]
            .iter()
            .map(|x| x.to_string()),
    );
    a.push(partial.to_string_lossy().into_owned());
    // Second output: thumbnail from the same run.
    a.extend(
        [
            "-map".into(),
            "0:v:0".into(),
            "-ss".into(),
            format!("{thumb_at:.2}"),
            "-frames:v".into(),
            "1".into(),
            "-vf".into(),
            "scale=480:-2".into(),
            "-q:v".into(),
            "3".into(),
            "-f".into(),
            "image2".into(),
            thumb.to_string_lossy().into_owned(),
        ],
    );
    a
}

fn select_precise(timeline: &[TimedSegment], desired_start: f64, desired_end: f64) -> Result<(Vec<TimedSegment>, f64, f64, f64), String> {
    let available_start = timeline[0].start_wall;
    let available_end = timeline[timeline.len() - 1].end_wall;
    let start = desired_start.max(available_start);
    let end = desired_end.min(available_end);
    if end <= start {
        return Err("The event is no longer inside the rolling buffer.".into());
    }
    if start > desired_start + 0.2 {
        log::warn!("The beginning of the event clip was limited by the current buffer");
    }
    if end < desired_end - 0.2 {
        log::warn!("The end of the event clip was limited by available footage");
    }
    let selected: Vec<TimedSegment> = timeline
        .iter()
        .filter(|seg| seg.end_wall > start && seg.start_wall < end)
        .cloned()
        .collect();
    if selected.is_empty() {
        return Err("Could not locate the event inside the rolling buffer.".into());
    }
    let trim_start = (start - selected[0].start_wall).max(0.0);
    Ok((selected, trim_start, end - start, desired_end - desired_start))
}

fn select_tail(timeline: &[TimedSegment], requested: f64) -> (Vec<TimedSegment>, f64, f64, f64) {
    let count = ((requested / f64::from(SEGMENT_SECONDS)).ceil() as usize).max(1);
    let selected: Vec<TimedSegment> = timeline[timeline.len().saturating_sub(count)..].to_vec();
    let total = selected[selected.len() - 1].end_wall - selected[0].start_wall;
    let trim_duration = requested.min(total);
    ((selected), (total - trim_duration).max(0.0), trim_duration, requested)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn timeline() -> Vec<TimedSegment> {
        (0..20)
            .map(|i| TimedSegment {
                path: PathBuf::from(format!("s{i}.mkv")),
                start_wall: 100.0 + f64::from(i) * 2.0,
                end_wall: 102.0 + f64::from(i) * 2.0,
            })
            .collect()
    }

    #[test]
    fn precise_selection_covers_window() {
        let (selected, trim_start, trim, _) = select_precise(&timeline(), 111.0, 125.0).unwrap();
        assert_eq!(selected.first().unwrap().start_wall, 110.0);
        assert_eq!(selected.last().unwrap().end_wall, 126.0);
        assert_eq!(trim_start, 1.0);
        assert_eq!(trim, 14.0);
        assert!(select_precise(&timeline(), 10.0, 20.0).is_err());
    }

    #[test]
    fn tail_selection_takes_latest_segments() {
        let (selected, _, trim, _) = select_tail(&timeline(), 45.0);
        assert_eq!(selected.len(), 20);
        assert_eq!(trim, 40.0);
        let (selected, _, _, _) = select_tail(&timeline(), 12.0);
        assert_eq!(selected.len(), 6);
    }

    #[test]
    fn concat_paths_are_escaped() {
        assert_eq!(concat_escape(Path::new("C:\\a\\it's.mkv")), "C:/a/it'\\''s.mkv");
    }
}
