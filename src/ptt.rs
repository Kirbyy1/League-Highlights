//! Push-to-talk for the microphone in clips: remembers when the chosen key or
//! mouse button was held, so a saved clip keeps the mic only while you were
//! talking. Polls the button state 50 times a second, and only while push to
//! talk is on (keyboard keys and mouse side buttons both work this way).

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use windows::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState;

/// The key or mouse button (virtual-key code); 0 = push to talk off.
static KEY: AtomicU32 = AtomicU32::new(0);
/// A game is running: the only time the key needs watching.
static IN_GAME: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

pub fn set_in_game(on: bool) {
    IN_GAME.store(on, Ordering::SeqCst);
}
/// Held intervals as (pressed, released) wall-clock times; released is
/// f64::INFINITY while the key is still down. Older than 15 minutes is dropped.
static HELD: Mutex<VecDeque<(f64, f64)>> = Mutex::new(VecDeque::new());
const KEEP_SECONDS: f64 = 15.0 * 60.0;
/// Speech starts a little before the key is pressed and trails after it.
const PAD_BEFORE: f64 = 0.15;
const PAD_AFTER: f64 = 0.35;

pub fn configure(enabled: bool, vk: u32) {
    let vk = if enabled && (1..=0xFE).contains(&vk) { vk } else { 0 };
    if KEY.swap(vk, Ordering::SeqCst) != vk {
        HELD.lock().unwrap().clear();
        if vk != 0 {
            log::info!("Push to talk on (key code {vk})");
        }
    }
}

pub fn active() -> bool {
    KEY.load(Ordering::SeqCst) != 0
}

pub fn start() {
    let _ = std::thread::Builder::new().name("push-to-talk".into()).spawn(|| {
        let mut down = false;
        loop {
            let vk = KEY.load(Ordering::SeqCst);
            if vk == 0 || !IN_GAME.load(Ordering::SeqCst) {
                // Off, or no game: one wake-up a second instead of fifty.
                if down {
                    down = false;
                    if let Some(last) = HELD.lock().unwrap().back_mut() {
                        last.1 = last.1.min(crate::util::now());
                    }
                }
                std::thread::sleep(Duration::from_secs(1));
                continue;
            }
            let now = crate::util::now();
            let pressed = unsafe { (GetAsyncKeyState(vk as i32) as u16 & 0x8000) != 0 };
            if pressed != down {
                down = pressed;
                let mut held = HELD.lock().unwrap();
                if pressed {
                    held.push_back((now, f64::INFINITY));
                } else if let Some(last) = held.back_mut() {
                    last.1 = now;
                }
                while held.front().is_some_and(|(_, end)| end.is_finite() && *end < now - KEEP_SECONDS) {
                    held.pop_front();
                }
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    });
}

/// Held intervals overlapping [start, end], as seconds from `start`.
pub fn intervals(start: f64, end: f64) -> Vec<(f64, f64)> {
    let held: Vec<(f64, f64)> = HELD.lock().unwrap().iter().copied().collect();
    relative(&held, start, end)
}

pub fn relative(held: &[(f64, f64)], start: f64, end: f64) -> Vec<(f64, f64)> {
    let mut out: Vec<(f64, f64)> = Vec::new();
    for &(a, b) in held {
        let a = (a - PAD_BEFORE).max(start);
        let b = (b.min(end) + PAD_AFTER).min(end);
        if b <= a {
            continue;
        }
        let (a, b) = (a - start, b - start);
        match out.last_mut() {
            Some(last) if a <= last.1 => last.1 = last.1.max(b),
            _ => out.push((a, b)),
        }
    }
    out
}

/// FFmpeg volume filter that mutes the mic outside the held intervals.
pub fn mute_filter(intervals: &[(f64, f64)]) -> String {
    if intervals.is_empty() {
        return "volume=0".into();
    }
    let on: Vec<String> = intervals.iter().map(|(a, b)| format!("between(t\\,{a:.2}\\,{b:.2})")).collect();
    format!("volume=0:enable='not({})'", on.join("+"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn held_keys_become_clip_times() {
        let held = [(100.0, 102.0), (102.2, 103.0), (110.0, f64::INFINITY), (50.0, 60.0)];
        // Clip from 99 to 112: the first two merge (padding closes the gap),
        // the open one runs to the end, the old one is outside.
        let r = relative(&held, 99.0, 112.0);
        assert_eq!(r.len(), 2);
        assert!((r[0].0 - 0.85).abs() < 1e-9 && (r[0].1 - 4.35).abs() < 1e-9);
        assert!((r[1].0 - 10.85).abs() < 1e-9 && (r[1].1 - 13.0).abs() < 1e-9);
    }

    #[test]
    fn mute_filter_keeps_only_talking() {
        assert_eq!(mute_filter(&[]), "volume=0");
        assert_eq!(mute_filter(&[(1.0, 2.5)]), "volume=0:enable='not(between(t\\,1.00\\,2.50))'");
    }
}
