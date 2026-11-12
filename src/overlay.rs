//! Small always-on-top toast shown over the game when a clip is saved.
//!
//! A click-through, non-activating layered window drawn with GDI: no WebView,
//! no focus steal, and it never takes input from the game. It lives on the
//! tray thread (which already runs a message loop) and hides itself after a
//! few seconds.

use std::sync::atomic::{AtomicIsize, Ordering};
use std::sync::Mutex;

use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows::Win32::Graphics::Gdi::{
    BeginPaint, CreateFontW, CreateRoundRectRgn, CreateSolidBrush, DeleteObject, DrawTextW,
    EndPaint, FillRect, GetMonitorInfoW, InvalidateRect, MonitorFromPoint, MonitorFromWindow,
    SelectObject, SetBkMode, SetTextColor, SetWindowRgn, CLEARTYPE_QUALITY, CLIP_DEFAULT_PRECIS,
    DEFAULT_CHARSET, DT_END_ELLIPSIS, DT_LEFT, DT_NOPREFIX, DT_SINGLELINE, DT_VCENTER, HFONT,
    MONITORINFO, MONITOR_DEFAULTTONEAREST, MONITOR_DEFAULTTOPRIMARY, OUT_DEFAULT_PRECIS,
    PAINTSTRUCT, TRANSPARENT,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::HiDpi::GetDpiForWindow;
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, KillTimer, RegisterClassW, SetLayeredWindowAttributes,
    SetTimer, SetWindowPos, ShowWindow, HWND_TOPMOST, LWA_ALPHA, SWP_NOACTIVATE, SW_HIDE,
    SW_SHOWNOACTIVATE, WM_PAINT, WM_TIMER, WNDCLASSW, WS_EX_LAYERED, WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_POPUP,
};

use crate::util::wide;

const CLASS: PCWSTR = w!("LeagueHighlightsToast");
const TIMER_HIDE: usize = 1;
static HWND_VALUE: AtomicIsize = AtomicIsize::new(0);

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Tone {
    Info,
    Saved,
    Error,
}

struct Content {
    title: String,
    detail: String,
    tone: Tone,
}

static CONTENT: Mutex<Option<Content>> = Mutex::new(None);

/// Queue a toast; the tray thread presents it (see `tray::overlay`).
pub fn set(title: &str, detail: &str, tone: Tone) {
    *CONTENT.lock().unwrap() = Some(Content {
        title: title.to_string(),
        detail: detail.to_string(),
        tone,
    });
}

fn font(height: i32, weight: i32) -> HFONT {
    unsafe {
        CreateFontW(
            height,
            0,
            0,
            0,
            weight,
            0,
            0,
            0,
            DEFAULT_CHARSET.0 as u32,
            OUT_DEFAULT_PRECIS.0 as u32,
            CLIP_DEFAULT_PRECIS.0 as u32,
            CLEARTYPE_QUALITY.0 as u32,
            0,
            w!("Segoe UI"),
        )
    }
}

unsafe extern "system" fn proc(hwnd: HWND, message: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match message {
        WM_PAINT => {
            let mut ps = PAINTSTRUCT::default();
            let hdc = BeginPaint(hwnd, &mut ps);
            let scale = f64::from(GetDpiForWindow(hwnd).max(96)) / 96.0;
            let px = |v: f64| (v * scale).round() as i32;
            let mut rect = RECT::default();
            let _ = windows::Win32::UI::WindowsAndMessaging::GetClientRect(hwnd, &mut rect);
            let background = CreateSolidBrush(COLORREF(0x001f_1d1d));
            FillRect(hdc, &rect, background);
            let _ = DeleteObject(background);

            let content = CONTENT.lock().unwrap();
            if let Some(content) = content.as_ref() {
                // Accent bar: gold for saved, red for errors, grey for info.
                let accent = match content.tone {
                    Tone::Saved => 0x006e_aac8,
                    Tone::Error => 0x0057_40e8,
                    Tone::Info => 0x0095_9a9b,
                };
                let bar = RECT { left: 0, top: 0, right: px(4.0), bottom: rect.bottom };
                let brush = CreateSolidBrush(COLORREF(accent));
                FillRect(hdc, &bar, brush);
                let _ = DeleteObject(brush);

                SetBkMode(hdc, TRANSPARENT);
                let flags = DT_LEFT | DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX;
                let title_font = font(-px(15.0), 600);
                let old = SelectObject(hdc, title_font);
                SetTextColor(hdc, COLORREF(0x00e7_ebec));
                let mut title_rect = RECT { left: px(18.0), top: px(9.0), right: rect.right - px(14.0), bottom: px(33.0) };
                let mut title = wide(&content.title);
                title.pop();
                DrawTextW(hdc, &mut title, &mut title_rect, flags);
                let detail_font = font(-px(12.5), 400);
                SelectObject(hdc, detail_font);
                SetTextColor(hdc, COLORREF(0x0095_9a9b));
                let mut detail_rect = RECT { left: px(18.0), top: px(32.0), right: rect.right - px(14.0), bottom: px(54.0) };
                let mut detail = wide(&content.detail);
                detail.pop();
                DrawTextW(hdc, &mut detail, &mut detail_rect, flags);
                SelectObject(hdc, old);
                let _ = DeleteObject(title_font);
                let _ = DeleteObject(detail_font);
            }
            let _ = EndPaint(hwnd, &ps);
            LRESULT(0)
        }
        WM_TIMER if wparam.0 == TIMER_HIDE => {
            let _ = KillTimer(hwnd, TIMER_HIDE);
            let _ = ShowWindow(hwnd, SW_HIDE);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, message, wparam, lparam),
    }
}

unsafe fn ensure_window() -> Option<HWND> {
    let existing = HWND_VALUE.load(Ordering::SeqCst);
    if existing != 0 {
        return Some(HWND(existing as *mut _));
    }
    let instance = GetModuleHandleW(None).ok()?;
    let class = WNDCLASSW {
        lpfnWndProc: Some(proc),
        hInstance: instance.into(),
        lpszClassName: CLASS,
        ..Default::default()
    };
    RegisterClassW(&class);
    let hwnd = CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
        CLASS,
        w!("League Highlights"),
        WS_POPUP,
        0,
        0,
        10,
        10,
        None,
        None,
        instance,
        None,
    )
    .ok()?;
    let _ = SetLayeredWindowAttributes(hwnd, COLORREF(0), 238, LWA_ALPHA);
    HWND_VALUE.store(hwnd.0 as isize, Ordering::SeqCst);
    Some(hwnd)
}

/// Show the queued toast in the top-right corner of the game's monitor.
/// Must run on the tray (UI) thread.
pub fn present() {
    unsafe {
        let Some(hwnd) = ensure_window() else { return };
        let game = crate::win::find_league_window();
        let monitor = match &game {
            Some(window) => MonitorFromWindow(HWND(window.hwnd as *mut _), MONITOR_DEFAULTTONEAREST),
            None => MonitorFromPoint(Default::default(), MONITOR_DEFAULTTOPRIMARY),
        };
        let mut info = MONITORINFO { cbSize: std::mem::size_of::<MONITORINFO>() as u32, ..Default::default() };
        if !GetMonitorInfoW(monitor, &mut info).as_bool() {
            return;
        }
        let scale = f64::from(GetDpiForWindow(hwnd).max(96)) / 96.0;
        let (width, height, margin) = ((330.0 * scale) as i32, (62.0 * scale) as i32, (22.0 * scale) as i32);
        let area = info.rcMonitor;
        let x = area.right - width - margin;
        let y = area.top + margin + (40.0 * scale) as i32;
        let _ = SetWindowPos(hwnd, HWND_TOPMOST, x, y, width, height, SWP_NOACTIVATE);
        let radius = (14.0 * scale) as i32;
        let region = CreateRoundRectRgn(0, 0, width + 1, height + 1, radius, radius);
        SetWindowRgn(hwnd, region, true);
        let _ = InvalidateRect(hwnd, None, true);
        let _ = ShowWindow(hwnd, SW_SHOWNOACTIVATE);
        SetTimer(hwnd, TIMER_HIDE, 2800, None);
    }
}
