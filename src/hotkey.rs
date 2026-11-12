//! Global clip shortcut via a low-level keyboard hook.
//!
//! Event driven: zero CPU while no key is pressed (the Python version polled
//! GetAsyncKeyState 40 times per second). The key is never swallowed, so the
//! game still receives it.

use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::mpsc::Sender;
use std::sync::OnceLock;

use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState;
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, DispatchMessageW, GetMessageW, SetWindowsHookExW, TranslateMessage,
    HC_ACTION, KBDLLHOOKSTRUCT, MSG, WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN,
    WM_SYSKEYUP,
};

use crate::models::Cmd;

static KEY: AtomicU32 = AtomicU32::new(0x77);
static MODIFIERS: AtomicU32 = AtomicU32::new(0);
static ENABLED: AtomicBool = AtomicBool::new(true);
static DOWN: AtomicBool = AtomicBool::new(false);
static LAST_TRIGGER_MS: AtomicU64 = AtomicU64::new(0);
static SENDER: OnceLock<Sender<Cmd>> = OnceLock::new();

const MOD_CTRL: u32 = 1;
const MOD_ALT: u32 = 2;
const MOD_SHIFT: u32 = 4;
const MOD_WIN: u32 = 8;

pub fn configure(vk: u32, modifiers: &[String]) {
    let mut bits = 0;
    for m in modifiers {
        bits |= match m.as_str() {
            "ctrl" => MOD_CTRL,
            "alt" => MOD_ALT,
            "shift" => MOD_SHIFT,
            "win" => MOD_WIN,
            _ => 0,
        };
    }
    KEY.store(vk, Ordering::SeqCst);
    MODIFIERS.store(bits, Ordering::SeqCst);
    DOWN.store(false, Ordering::SeqCst);
}

/// Disabled while the settings page records a new shortcut.
pub fn set_enabled(enabled: bool) {
    ENABLED.store(enabled, Ordering::SeqCst);
}

fn pressed(vk: i32) -> bool {
    unsafe { (GetAsyncKeyState(vk) as u16 & 0x8000) != 0 }
}

fn modifiers_match() -> bool {
    let bits = MODIFIERS.load(Ordering::SeqCst);
    (bits & MOD_CTRL == 0 || pressed(0x11))
        && (bits & MOD_ALT == 0 || pressed(0x12))
        && (bits & MOD_SHIFT == 0 || pressed(0x10))
        && (bits & MOD_WIN == 0 || pressed(0x5B) || pressed(0x5C))
}

unsafe extern "system" fn hook(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code == HC_ACTION as i32 {
        let info = &*(lparam.0 as *const KBDLLHOOKSTRUCT);
        if info.vkCode == KEY.load(Ordering::Relaxed) {
            let message = wparam.0 as u32;
            if message == WM_KEYDOWN || message == WM_SYSKEYDOWN {
                // Ignore auto-repeat while the key is held.
                if !DOWN.swap(true, Ordering::SeqCst) && ENABLED.load(Ordering::SeqCst) && modifiers_match() {
                    let now_ms = (crate::util::now() * 1000.0) as u64;
                    let last = LAST_TRIGGER_MS.load(Ordering::SeqCst);
                    if now_ms.saturating_sub(last) >= 600 {
                        LAST_TRIGGER_MS.store(now_ms, Ordering::SeqCst);
                        if let Some(tx) = SENDER.get() {
                            let _ = tx.send(Cmd::Hotkey);
                        }
                    }
                }
            } else if message == WM_KEYUP || message == WM_SYSKEYUP {
                DOWN.store(false, Ordering::SeqCst);
            }
        }
    }
    CallNextHookEx(None, code, wparam, lparam)
}

pub fn start(tx: Sender<Cmd>) {
    let _ = SENDER.set(tx);
    let _ = std::thread::Builder::new().name("hotkey".into()).spawn(|| unsafe {
        let module = GetModuleHandleW(None).unwrap_or_default();
        match SetWindowsHookExW(WH_KEYBOARD_LL, Some(hook), module, 0) {
            Ok(_) => log::info!("Global clip hotkey active (low-level keyboard hook)"),
            Err(e) => {
                log::error!("Could not install the global shortcut hook: {e}");
                return;
            }
        }
        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    });
}
