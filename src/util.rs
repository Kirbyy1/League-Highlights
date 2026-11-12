//! Clock, local-time formatting and small string helpers.
//!
//! Every timestamp inside the app ("wall" seconds) comes from one QPC-based
//! clock anchored to Unix time once at startup. Video progress, WASAPI packet
//! positions and Riot events therefore share a single monotonic timeline that
//! cannot jump when Windows adjusts the system clock.

use std::path::Path;
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

use windows::core::PCWSTR;
use windows::Win32::Foundation::{FILETIME, SYSTEMTIME};
use windows::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
use windows::Win32::System::Performance::{QueryPerformanceCounter, QueryPerformanceFrequency};
use windows::Win32::System::Time::{FileTimeToSystemTime, SystemTimeToTzSpecificLocalTime};

struct ClockBase {
    freq: f64,
    offset: f64,
}

static CLOCK: OnceLock<ClockBase> = OnceLock::new();

fn raw_qpc() -> i64 {
    let mut value = 0i64;
    unsafe {
        let _ = QueryPerformanceCounter(&mut value);
    }
    value
}

fn clock() -> &'static ClockBase {
    CLOCK.get_or_init(|| {
        let mut freq = 0i64;
        unsafe {
            let _ = QueryPerformanceFrequency(&mut freq);
        }
        let freq = freq.max(1) as f64;
        let qpc_seconds = raw_qpc() as f64 / freq;
        let unix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);
        ClockBase {
            freq,
            offset: unix - qpc_seconds,
        }
    })
}

/// Monotonic wall-clock seconds (Unix epoch based).
pub fn now() -> f64 {
    let base = clock();
    raw_qpc() as f64 / base.freq + base.offset
}

/// Convert a WASAPI QPC position (100 ns units) to app wall seconds.
pub fn from_qpc_100ns(value: u64) -> f64 {
    value as f64 / 1e7 + clock().offset
}

#[derive(Clone, Copy, Debug, Default)]
pub struct LocalTime {
    pub year: u16,
    pub month: u16,
    pub day: u16,
    pub hour: u16,
    pub minute: u16,
    pub second: u16,
    pub millis: u16,
}

pub fn local_time(epoch: f64) -> LocalTime {
    let ticks = ((epoch.max(0.0) + 11_644_473_600.0) * 1e7) as u64;
    let ft = FILETIME {
        dwLowDateTime: ticks as u32,
        dwHighDateTime: (ticks >> 32) as u32,
    };
    let mut utc = SYSTEMTIME::default();
    let mut local = SYSTEMTIME::default();
    unsafe {
        if FileTimeToSystemTime(&ft, &mut utc).is_err() {
            return LocalTime::default();
        }
        if SystemTimeToTzSpecificLocalTime(None, &utc, &mut local).is_err() {
            local = utc;
        }
    }
    LocalTime {
        year: local.wYear,
        month: local.wMonth,
        day: local.wDay,
        hour: local.wHour,
        minute: local.wMinute,
        second: local.wSecond,
        millis: local.wMilliseconds,
    }
}

impl LocalTime {
    /// `20260112_183005`, used by match identifiers.
    pub fn compact(&self) -> String {
        format!(
            "{:04}{:02}{:02}_{:02}{:02}{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        )
    }

    /// `2026-01-12_18-30-05`, used by clip file names.
    pub fn file_stamp(&self) -> String {
        format!(
            "{:04}-{:02}-{:02}_{:02}-{:02}-{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        )
    }

    /// `2026-01-12T18:30:05`, compatible with Python's `datetime.isoformat()`.
    pub fn iso(&self) -> String {
        format!(
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        )
    }

    pub fn log_stamp(&self) -> String {
        format!(
            "{:04}-{:02}-{:02} {:02}:{:02}:{:02}.{:03}",
            self.year, self.month, self.day, self.hour, self.minute, self.second, self.millis
        )
    }
}

/// Python `re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")`.
pub fn underscore_alnum(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    let mut pending = false;
    for ch in value.chars() {
        if ch.is_ascii_alphanumeric() {
            if pending && !out.is_empty() {
                out.push('_');
            }
            pending = false;
            out.push(ch);
        } else {
            pending = true;
        }
    }
    out
}

pub fn slug(value: &str) -> String {
    let mut text = underscore_alnum(value);
    text.truncate(36);
    if text.is_empty() {
        "Player".into()
    } else {
        text
    }
}

/// Lower-case and collapse whitespace (Python `casefold` + `split/join`).
pub fn normalise_name(value: &str) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
}

pub fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain(std::iter::once(0)).collect()
}

pub fn wide_path(path: &Path) -> Vec<u16> {
    use std::os::windows::ffi::OsStrExt;
    path.as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect()
}

pub fn from_wide(buffer: &[u16]) -> String {
    let len = buffer.iter().position(|&c| c == 0).unwrap_or(buffer.len());
    String::from_utf16_lossy(&buffer[..len])
}

pub fn disk_free_bytes(path: &Path) -> Option<u64> {
    let wide = wide_path(path);
    let mut available = 0u64;
    unsafe {
        GetDiskFreeSpaceExW(PCWSTR(wide.as_ptr()), Some(&mut available), None, None).ok()?;
    }
    Some(available)
}

pub fn base64(input: &[u8]) -> String {
    const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(input.len().div_ceil(3) * 4);
    for chunk in input.chunks(3) {
        let b = [
            chunk[0],
            *chunk.get(1).unwrap_or(&0),
            *chunk.get(2).unwrap_or(&0),
        ];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        out.push(TABLE[(n >> 18) as usize & 63] as char);
        out.push(TABLE[(n >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 { TABLE[(n >> 6) as usize & 63] as char } else { '=' });
        out.push(if chunk.len() > 2 { TABLE[n as usize & 63] as char } else { '=' });
    }
    out
}

pub fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).unwrap_or("");
                if let Ok(byte) = u8::from_str_radix(hex, 16) {
                    out.push(byte);
                    i += 3;
                    continue;
                }
                out.push(b'%');
            }
            b'+' => out.push(b' '),
            other => out.push(other),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugs_match_python() {
        assert_eq!(underscore_alnum("Kai'Sa  - Main"), "Kai_Sa_Main");
        assert_eq!(underscore_alnum("__a__"), "a");
        assert_eq!(slug(""), "Player");
        assert_eq!(slug("Faker#KR1"), "Faker_KR1");
    }

    #[test]
    fn base64_encodes() {
        assert_eq!(base64(b"riot:abc"), "cmlvdDphYmM=");
        assert_eq!(base64(b"ab"), "YWI=");
    }

    #[test]
    fn percent_decoding() {
        assert_eq!(percent_decode("a%20b+c"), "a b c");
    }
}
