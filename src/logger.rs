//! Minimal file logger: %LOCALAPPDATA%\LeagueHighlights\logs\league_highlights.log

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::Mutex;

use log::{LevelFilter, Log, Metadata, Record};

struct FileLogger {
    file: Mutex<Option<File>>,
}

impl Log for FileLogger {
    fn enabled(&self, metadata: &Metadata) -> bool {
        metadata.level() <= log::max_level()
    }

    fn log(&self, record: &Record) {
        // Only this crate's records (dependencies stay quiet).
        if !self.enabled(record.metadata()) || !record.target().starts_with(env!("CARGO_CRATE_NAME")) {
            return;
        }
        let stamp = crate::util::local_time(crate::util::now()).log_stamp();
        let line = format!("{stamp} [{}] {}\n", record.level(), record.args());
        if let Some(file) = self.file.lock().unwrap().as_mut() {
            let _ = file.write_all(line.as_bytes());
        }
        #[cfg(debug_assertions)]
        eprint!("{line}");
    }

    fn flush(&self) {
        if let Some(file) = self.file.lock().unwrap().as_mut() {
            let _ = file.flush();
        }
    }
}

pub fn init(dir: &Path) {
    let path = dir.join("league_highlights.log");
    // Keep the log bounded: start fresh once it passes 5 MB.
    if std::fs::metadata(&path).is_ok_and(|m| m.len() > 5 * 1024 * 1024) {
        let _ = std::fs::rename(&path, dir.join("league_highlights.old.log"));
    }
    let file = OpenOptions::new().create(true).append(true).open(&path).ok();
    let logger = Box::leak(Box::new(FileLogger { file: Mutex::new(file) }));
    if log::set_logger(logger).is_ok() {
        log::set_max_level(if cfg!(debug_assertions) { LevelFilter::Debug } else { LevelFilter::Info });
    }
}
