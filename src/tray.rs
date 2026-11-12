//! Notification-area icon, menu and balloons. Runs on the main thread.

use std::sync::atomic::{AtomicIsize, Ordering};
use std::sync::mpsc::Sender;
use std::sync::{Arc, Mutex, OnceLock};

use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{BOOL, HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Shell::{
    Shell_NotifyIconW, NIF_ICON, NIF_INFO, NIF_MESSAGE, NIF_TIP, NIIF_INFO, NIIF_NOSOUND,
    NIM_ADD, NIM_DELETE, NIM_MODIFY, NOTIFYICONDATAW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreateIconFromResourceEx, CreatePopupMenu, CreateWindowExW, DefWindowProcW,
    DestroyMenu, DestroyWindow, DispatchMessageW, FindWindowW, GetCursorPos, GetMessageW,
    GetSystemMetrics, LoadIconW, PostMessageW, PostQuitMessage, RegisterClassW,
    RegisterWindowMessageW, SetForegroundWindow, SetMenuDefaultItem, TrackPopupMenu,
    TranslateMessage, HICON, HMENU, IDI_APPLICATION, LR_DEFAULTCOLOR, MF_CHECKED, MF_GRAYED,
    MF_SEPARATOR, MF_STRING, MSG, SM_CXSMICON, TPM_BOTTOMALIGN, TPM_RIGHTBUTTON, WINDOW_EX_STYLE,
    WM_APP, WM_COMMAND, WM_DESTROY, WM_LBUTTONDBLCLK, WM_NULL, WM_RBUTTONUP, WNDCLASSW,
    WS_OVERLAPPED,
};

use crate::config::{Paths, SharedSettings};
use crate::controller::{title_case, SharedStatus};
use crate::models::Cmd;
use crate::util::wide;
use crate::web::Web;

const WM_TRAY: u32 = WM_APP + 1;
const WM_REFRESH: u32 = WM_APP + 2;
const WM_BALLOON: u32 = WM_APP + 3;
pub const WM_OPEN_LIBRARY: u32 = WM_APP + 4;
const WM_OVERLAY: u32 = WM_APP + 5;
/// Shell_NotifyIcon callback when the user clicks a balloon.
const NIN_BALLOONUSERCLICK: u32 = 0x0405;

const ID_SAVE: usize = 10;
const ID_TOGGLE: usize = 11;
const ID_LIBRARY: usize = 12;
const ID_FOLDER: usize = 13;
const ID_STARTUP: usize = 14;
const ID_QUIT: usize = 15;

const CLASS: PCWSTR = w!("LeagueHighlightsRustTray");
static ICON: &[u8] = include_bytes!("../assets/league_highlights.ico");

pub struct TrayContext {
    pub status: SharedStatus,
    pub tx: Sender<Cmd>,
    pub cfg: SharedSettings,
    pub paths: Paths,
    pub web: Arc<Web>,
}

static CONTEXT: OnceLock<TrayContext> = OnceLock::new();
static HWND_VALUE: AtomicIsize = AtomicIsize::new(0);
static BALLOON: Mutex<Option<(String, String)>> = Mutex::new(None);
/// The last balloon asked the user to review new highlights.
static REVIEW_BALLOON: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
static ICON_HANDLE: AtomicIsize = AtomicIsize::new(0);
static TASKBAR_CREATED: AtomicIsize = AtomicIsize::new(0);

fn post(message: u32) {
    let hwnd = HWND_VALUE.load(Ordering::SeqCst);
    if hwnd != 0 {
        unsafe {
            let _ = PostMessageW(HWND(hwnd as *mut _), message, WPARAM(0), LPARAM(0));
        }
    }
}

pub fn refresh() {
    post(WM_REFRESH);
}

/// In-game toast (top-right of the game's monitor).
pub fn overlay(title: &str, detail: &str, tone: crate::overlay::Tone) {
    crate::overlay::set(title, detail, tone);
    post(WM_OVERLAY);
}

/// Balloon that opens the review view when clicked.
pub fn notify_review(title: &str, text: &str) {
    notify(title, text);
    REVIEW_BALLOON.store(true, Ordering::SeqCst);
}

pub fn notify(title: &str, text: &str) {
    REVIEW_BALLOON.store(false, Ordering::SeqCst);
    *BALLOON.lock().unwrap() = Some((title.to_string(), text.to_string()));
    post(WM_BALLOON);
}

/// Ask an already running instance to open its library.
pub fn signal_existing_instance() -> bool {
    unsafe {
        match FindWindowW(CLASS, PCWSTR::null()) {
            Ok(hwnd) if !hwnd.0.is_null() => {
                let _ = PostMessageW(hwnd, WM_OPEN_LIBRARY, WPARAM(0), LPARAM(0));
                true
            }
            _ => false,
        }
    }
}

fn copy_into(dst: &mut [u16], text: &str) {
    let encoded: Vec<u16> = text.encode_utf16().take(dst.len() - 1).collect();
    dst[..encoded.len()].copy_from_slice(&encoded);
    dst[encoded.len()] = 0;
}

fn load_icon() -> HICON {
    app_icon(unsafe { GetSystemMetrics(SM_CXSMICON) }.max(16))
}

/// Decode the embedded .ico at the requested pixel size.
pub fn app_icon(want: i32) -> HICON {
    // Pick the ICO entry closest to the small-icon size and decode it directly
    // from the embedded file (no resource compiler needed).
    let count = u16::from_le_bytes([ICON[4], ICON[5]]) as usize;
    let mut best: Option<(i32, usize, usize, i32)> = None;
    for i in 0..count {
        let entry = 6 + i * 16;
        let size = match ICON[entry] {
            0 => 256,
            n => i32::from(n),
        };
        let bytes = u32::from_le_bytes(ICON[entry + 8..entry + 12].try_into().unwrap()) as usize;
        let offset = u32::from_le_bytes(ICON[entry + 12..entry + 16].try_into().unwrap()) as usize;
        let score = if size >= want { size - want } else { (want - size) * 4 };
        if best.is_none_or(|b| score < b.0) && offset + bytes <= ICON.len() {
            best = Some((score, offset, bytes, size));
        }
    }
    unsafe {
        if let Some((_, offset, bytes, _)) = best {
            if let Ok(icon) = CreateIconFromResourceEx(
                &ICON[offset..offset + bytes],
                BOOL(1),
                0x0003_0000,
                want,
                want,
                LR_DEFAULTCOLOR,
            ) {
                return icon;
            }
        }
        LoadIconW(None, IDI_APPLICATION).unwrap_or_default()
    }
}

fn base_data(hwnd: HWND) -> NOTIFYICONDATAW {
    NOTIFYICONDATAW {
        cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: 1,
        ..Default::default()
    }
}

fn tooltip() -> String {
    let Some(ctx) = CONTEXT.get() else { return "League Highlights".into() };
    let status = ctx.status.read().unwrap();
    format!("League Highlights — {}", title_case(status.state))
}

unsafe fn add_icon(hwnd: HWND) {
    let mut data = base_data(hwnd);
    data.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
    data.uCallbackMessage = WM_TRAY;
    data.hIcon = HICON(ICON_HANDLE.load(Ordering::SeqCst) as *mut _);
    copy_into(&mut data.szTip, &tooltip());
    let _ = Shell_NotifyIconW(NIM_ADD, &data);
}

unsafe fn update_tooltip(hwnd: HWND) {
    let mut data = base_data(hwnd);
    data.uFlags = NIF_TIP;
    copy_into(&mut data.szTip, &tooltip());
    let _ = Shell_NotifyIconW(NIM_MODIFY, &data);
}

unsafe fn show_balloon(hwnd: HWND) {
    let Some((title, text)) = BALLOON.lock().unwrap().take() else { return };
    let mut data = base_data(hwnd);
    data.uFlags = NIF_INFO;
    data.dwInfoFlags = NIIF_INFO | NIIF_NOSOUND;
    copy_into(&mut data.szInfoTitle, &title);
    copy_into(&mut data.szInfo, &text);
    let _ = Shell_NotifyIconW(NIM_MODIFY, &data);
}

unsafe fn append(menu: HMENU, id: usize, text: &str, flags: windows::Win32::UI::WindowsAndMessaging::MENU_ITEM_FLAGS) {
    let label = wide(text);
    let _ = AppendMenuW(menu, MF_STRING | flags, id, PCWSTR(label.as_ptr()));
}

unsafe fn show_menu(hwnd: HWND) {
    let Some(ctx) = CONTEXT.get() else { return };
    let Ok(menu) = CreatePopupMenu() else { return };
    let (state, paused) = {
        let s = ctx.status.read().unwrap();
        (title_case(s.state), s.paused)
    };
    let hotkey = ctx.cfg.read().unwrap().hotkey_display.clone();
    let none = windows::Win32::UI::WindowsAndMessaging::MENU_ITEM_FLAGS(0);
    append(menu, 0, &format!("League Highlights — {state}"), MF_GRAYED);
    let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
    append(menu, ID_LIBRARY, "Open library", none);
    append(menu, ID_SAVE, &format!("Save clip now\t{hotkey}"), none);
    append(menu, ID_TOGGLE, if paused { "Resume recording" } else { "Pause recording" }, none);
    append(menu, ID_FOLDER, "Open clips folder", none);
    let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
    let startup = if crate::win::startup_enabled() { MF_CHECKED } else { none };
    append(menu, ID_STARTUP, "Launch with Windows", startup);
    let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
    append(menu, ID_QUIT, "Quit", none);
    let _ = SetMenuDefaultItem(menu, ID_LIBRARY as u32, 0);

    let mut point = POINT::default();
    let _ = GetCursorPos(&mut point);
    let _ = SetForegroundWindow(hwnd);
    let _ = TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_BOTTOMALIGN, point.x, point.y, 0, hwnd, None);
    let _ = PostMessageW(hwnd, WM_NULL, WPARAM(0), LPARAM(0));
    let _ = DestroyMenu(menu);
}

fn command(hwnd: HWND, id: usize) {
    let Some(ctx) = CONTEXT.get() else { return };
    match id {
        ID_LIBRARY => ctx.web.open(),
        ID_SAVE => {
            let _ = ctx.tx.send(Cmd::Hotkey);
        }
        ID_TOGGLE => {
            let _ = ctx.tx.send(Cmd::ToggleRecording);
        }
        ID_FOLDER => {
            let dir = ctx.cfg.read().unwrap().clip_dir();
            let _ = std::fs::create_dir_all(&dir);
            crate::win::open_folder(&dir);
        }
        ID_STARTUP => {
            let enable = !crate::win::startup_enabled();
            match crate::win::set_startup(enable) {
                Ok(()) => {
                    let mut s = ctx.cfg.write().unwrap();
                    s.launch_with_windows = enable;
                    let _ = s.save(&ctx.paths.settings);
                }
                Err(e) => log::error!("Could not change Windows startup: {e}"),
            }
        }
        ID_QUIT => unsafe {
            let _ = DestroyWindow(hwnd);
        },
        _ => {}
    }
}

unsafe extern "system" fn window_proc(hwnd: HWND, message: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    let taskbar_created = TASKBAR_CREATED.load(Ordering::SeqCst) as u32;
    match message {
        WM_TRAY => {
            match lparam.0 as u32 {
                WM_RBUTTONUP => show_menu(hwnd),
                WM_LBUTTONDBLCLK => command(hwnd, ID_LIBRARY),
                NIN_BALLOONUSERCLICK => {
                    if let Some(ctx) = CONTEXT.get() {
                        if REVIEW_BALLOON.swap(false, Ordering::SeqCst) {
                            ctx.web.open_review();
                        } else {
                            ctx.web.open();
                        }
                    }
                }
                _ => {}
            }
            LRESULT(0)
        }
        WM_REFRESH => {
            update_tooltip(hwnd);
            LRESULT(0)
        }
        WM_OVERLAY => {
            crate::overlay::present();
            LRESULT(0)
        }
        WM_BALLOON => {
            show_balloon(hwnd);
            LRESULT(0)
        }
        WM_OPEN_LIBRARY => {
            command(hwnd, ID_LIBRARY);
            LRESULT(0)
        }
        WM_COMMAND => {
            command(hwnd, wparam.0 & 0xFFFF);
            LRESULT(0)
        }
        WM_DESTROY => {
            let data = base_data(hwnd);
            let _ = Shell_NotifyIconW(NIM_DELETE, &data);
            HWND_VALUE.store(0, Ordering::SeqCst);
            PostQuitMessage(0);
            LRESULT(0)
        }
        m if m != 0 && m == taskbar_created => {
            // Explorer restarted: put the icon back.
            add_icon(hwnd);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, message, wparam, lparam),
    }
}

/// Create the tray icon and run the message loop until Quit.
pub fn run(context: TrayContext) {
    let _ = CONTEXT.set(context);
    unsafe {
        let instance = GetModuleHandleW(None).unwrap_or_default();
        let class = WNDCLASSW {
            lpfnWndProc: Some(window_proc),
            hInstance: instance.into(),
            lpszClassName: CLASS,
            ..Default::default()
        };
        RegisterClassW(&class);
        TASKBAR_CREATED.store(RegisterWindowMessageW(w!("TaskbarCreated")) as isize, Ordering::SeqCst);
        let Ok(hwnd) = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            CLASS,
            w!("League Highlights"),
            WS_OVERLAPPED,
            0,
            0,
            0,
            0,
            None,
            None,
            instance,
            None,
        ) else {
            log::error!("Could not create the tray window");
            return;
        };
        ICON_HANDLE.store(load_icon().0 as isize, Ordering::SeqCst);
        HWND_VALUE.store(hwnd.0 as isize, Ordering::SeqCst);
        add_icon(hwnd);
        if BALLOON.lock().unwrap().is_some() {
            show_balloon(hwnd);
        }
        if std::env::var_os("LH_TOAST_TEST").is_some() {
            // Developer check: show the in-game toast once without a game.
            crate::overlay::set("2v1 Double Kill", "Saved · 18s · score 88", crate::overlay::Tone::Saved);
            crate::overlay::present();
        }
        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
}
