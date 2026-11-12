//! Background-music library for the clip editor: audio files the user adds
//! (their own or royalty-free tracks), stored in `<data>\music`.

use std::fs;
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::ffmpeg::Ffmpeg;

pub const MAX_BYTES: u64 = 60 * 1024 * 1024;
const EXTENSIONS: [&str; 8] = ["mp3", "m4a", "aac", "wav", "flac", "ogg", "opus", "wma"];

#[derive(Serialize)]
pub struct Track {
    pub file: String,
    pub name: String,
    pub duration: Option<f64>,
    pub size: u64,
}

fn allowed(path: &Path) -> bool {
    path.extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| EXTENSIONS.contains(&e.to_lowercase().as_str()))
}

/// A safe file name: letters, digits, spaces, `-`, `_` and `.` only.
pub fn sanitise(name: &str) -> Option<String> {
    let base = Path::new(name).file_name()?.to_string_lossy().into_owned();
    let clean: String = base
        .chars()
        .map(|c| if c.is_alphanumeric() || matches!(c, ' ' | '-' | '_' | '.' | '(' | ')') { c } else { '_' })
        .collect();
    let clean = clean.trim().trim_start_matches('.').to_string();
    (!clean.is_empty() && clean.len() <= 120 && allowed(Path::new(&clean))).then_some(clean)
}

pub fn resolve(dir: &Path, file: &str) -> Option<PathBuf> {
    if file.contains(['/', '\\', ':']) || file.contains("..") {
        return None;
    }
    let path = dir.join(file);
    (path.is_file() && allowed(&path)).then_some(path)
}

pub fn list(dir: &Path, ff: Option<&Ffmpeg>) -> Vec<Track> {
    let mut tracks = Vec::new();
    let Ok(entries) = fs::read_dir(dir) else { return tracks };
    for entry in entries.flatten() {
        let path = entry.path();
        if !allowed(&path) {
            continue;
        }
        let file = path.file_name().unwrap().to_string_lossy().into_owned();
        let name = path.file_stem().unwrap().to_string_lossy().replace('_', " ");
        tracks.push(Track {
            duration: ff.and_then(|f| f.probe_duration(&path)),
            size: entry.metadata().map_or(0, |m| m.len()),
            file,
            name,
        });
    }
    tracks.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    tracks
}

/// Store an uploaded track. Rejects anything FFmpeg can't read as audio.
pub fn save(dir: &Path, name: &str, bytes: &[u8], ff: Option<&Ffmpeg>) -> Result<String, String> {
    let clean = sanitise(name).ok_or("Use an MP3, M4A, AAC, WAV, FLAC, OGG, OPUS or WMA file.")?;
    if bytes.is_empty() {
        return Err("The file is empty.".into());
    }
    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let mut path = dir.join(&clean);
    let stem = path.file_stem().unwrap().to_string_lossy().into_owned();
    let ext = path.extension().unwrap().to_string_lossy().into_owned();
    let mut n = 2;
    while path.exists() {
        path = dir.join(format!("{stem} ({n}).{ext}"));
        n += 1;
    }
    let temp = path.with_extension(format!("{ext}.part"));
    fs::write(&temp, bytes).map_err(|e| e.to_string())?;
    let readable = ff.map_or(true, |f| f.probe_duration(&temp).is_some_and(|d| d > 0.5));
    if !readable {
        let _ = fs::remove_file(&temp);
        return Err("That file doesn't contain audio FFmpeg can read.".into());
    }
    fs::rename(&temp, &path).map_err(|e| e.to_string())?;
    log::info!("Added music track {}", path.display());
    Ok(path.file_name().unwrap().to_string_lossy().into_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitises_names() {
        assert_eq!(sanitise("My Song.mp3").as_deref(), Some("My Song.mp3"));
        assert_eq!(sanitise("..\\..\\evil.mp3").as_deref(), Some("evil.mp3"));
        assert_eq!(sanitise("a<b>.flac").as_deref(), Some("a_b_.flac"));
        assert!(sanitise("song.exe").is_none());
        assert!(sanitise(".mp3").is_none());
    }
}
