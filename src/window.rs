//! Native library window: a Win32 window hosting a WebView2 view of the UI.
//!
//! The window lives on its own thread with its own message loop. WebView2 is
//! only loaded while the window is open; closing it drops the view and its
//! browser processes, so the tray app returns to its idle footprint.
//!
//! The Windows caption is removed (WM_NCCALCSIZE) and the page draws its own
//! title bar in the window's colours. The side and bottom resize borders,
//! shadow, Aero Snap and drag-to-restore stay native; the page asks for
//! drag / minimize / maximize / close through the WebView IPC bridge.

use std::cell::RefCell;
use std::num::NonZeroIsize;
use std::path::PathBuf;
use std::sync::atomic::{AtomicIsize, Ordering};
use std::sync::{mpsc, Once};
use std::time::Duration;

use raw_window_handle::{
    HandleError, HasWindowHandle, RawWindowHandle, Win32WindowHandle, WindowHandle,
};
use windows::core::{w, PCWSTR};
use windows::core::BOOL;
use windows::Win32::Foundation::{COLORREF, HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows::Win32::Graphics::Dwm::{DwmSetWindowAttribute, DWMWA_USE_IMMERSIVE_DARK_MODE};
use windows::Win32::Graphics::Gdi::CreateSolidBrush;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::HiDpi::{GetDpiForWindow, GetSystemMetricsForDpi};
use windows::Win32::UI::Input::KeyboardAndMouse::ReleaseCapture;
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, GetCursorPos, GetMessageW,
    GetSystemMetrics, IsIconic, IsWindow, IsZoomed, PostMessageW, PostQuitMessage,
    RegisterClassW, SendMessageW, SetForegroundWindow, SetWindowPos, ShowWindow,
    TranslateMessage, CW_USEDEFAULT, HTCAPTION, HTTOP, ICON_BIG, ICON_SMALL, MSG,
    NCCALCSIZE_PARAMS, SM_CXPADDEDBORDER, SM_CXSCREEN, SM_CYFRAME, SM_CYSCREEN, SWP_FRAMECHANGED,
    SWP_NOZORDER, SW_MAXIMIZE, SW_MINIMIZE, SW_RESTORE, SW_SHOW, WINDOW_EX_STYLE, WM_CLOSE,
    WM_DESTROY, WM_NCCALCSIZE, WM_NCLBUTTONDOWN, WM_SETICON, WM_SIZE, WNDCLASSW,
    WS_OVERLAPPEDWINDOW,
};

const CLASS: PCWSTR = w!("LeagueHighlightsRustLibrary");

/// Lean WebView2: fold the GPU, network and audio services into existing
/// processes, keep one renderer, and switch off browser features this local
/// page never uses. Includes wry's own defaults, which this replaces.
const BROWSER_ARGS: &str = concat!(
    "--disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection,",
    "SpareRendererForSitePerProcess,Translate,AutofillServerCommunication,",
    "OptimizationHints,MediaRouter,AudioServiceOutOfProcess,BackForwardCache ",
    "--enable-features=RemoveRedirectionBitmap,NetworkServiceInProcess2 ",
    "--in-process-gpu --renderer-process-limit=1 --disable-background-networking ",
    "--disable-component-update --disable-sync --disable-extensions --no-first-run ",
    "--disk-cache-size=1048576"
);
static OPEN: AtomicIsize = AtomicIsize::new(0);
const WM_RUN_SCRIPT: u32 = windows::Win32::UI::WindowsAndMessaging::WM_APP + 20;
static PENDING_SCRIPT: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);

pub fn is_open() -> bool {
    let hwnd = OPEN.load(Ordering::SeqCst);
    hwnd != 0 && unsafe { IsWindow(Some(HWND(hwnd as *mut _))).as_bool() }
}

/// Run JavaScript in the open window (from any thread) and bring it forward.
pub fn run_script(script: &str) {
    let hwnd = OPEN.load(Ordering::SeqCst);
    if hwnd == 0 {
        return;
    }
    *PENDING_SCRIPT.lock().unwrap() = Some(script.to_string());
    let hwnd = HWND(hwnd as *mut _);
    unsafe {
        let _ = PostMessageW(Some(hwnd), WM_RUN_SCRIPT, WPARAM(0), LPARAM(0));
        if IsIconic(hwnd).as_bool() {
            let _ = ShowWindow(hwnd, SW_RESTORE);
        }
        let _ = SetForegroundWindow(hwnd);
    }
}

thread_local! {
    static VIEW: RefCell<Option<wry::WebView>> = const { RefCell::new(None) };
}

struct Parent(isize);

impl HasWindowHandle for Parent {
    fn window_handle(&self) -> Result<WindowHandle<'_>, HandleError> {
        let hwnd = NonZeroIsize::new(self.0).ok_or(HandleError::Unavailable)?;
        let raw = RawWindowHandle::Win32(Win32WindowHandle::new(hwnd));
        Ok(unsafe { WindowHandle::borrow_raw(raw) })
    }
}

unsafe extern "system" fn proc(hwnd: HWND, message: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    match message {
        WM_NCCALCSIZE if wparam.0 != 0 => {
            // Let Windows compute the normal frame, then give the caption
            // area back to the client so the page can draw its own title bar.
            let params = &mut *(lparam.0 as *mut NCCALCSIZE_PARAMS);
            let top = params.rgrc[0].top;
            let result = DefWindowProcW(hwnd, message, wparam, lparam);
            params.rgrc[0].top = top;
            if IsZoomed(hwnd).as_bool() {
                // A maximized window overhangs the monitor by its frame.
                let dpi = GetDpiForWindow(hwnd);
                params.rgrc[0].top += GetSystemMetricsForDpi(SM_CYFRAME, dpi)
                    + GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi);
            }
            result
        }
        WM_SIZE => {
            let maximized = IsZoomed(hwnd).as_bool();
            let minimized = wparam.0 == 1; // SIZE_MINIMIZED
            VIEW.with(|view| {
                if let Some(view) = view.borrow().as_ref() {
                    // Minimized: hide the view so WebView2 stops rendering,
                    // and ask it to trim memory until the window returns.
                    use wry::{MemoryUsageLevel, WebViewExtWindows};
                    let _ = view.set_visible(!minimized);
                    let _ = view.set_memory_usage_level(if minimized {
                        MemoryUsageLevel::Low
                    } else {
                        MemoryUsageLevel::Normal
                    });
                    if !minimized {
                        let _ = view.evaluate_script(&format!("window.lhWindowState&&lhWindowState({maximized})"));
                    }
                }
            });
            DefWindowProcW(hwnd, message, wparam, lparam)
        }
        WM_RUN_SCRIPT => {
            if let Some(script) = PENDING_SCRIPT.lock().unwrap().take() {
                VIEW.with(|view| {
                    if let Some(view) = view.borrow().as_ref() {
                        let _ = view.evaluate_script(&script);
                    }
                });
            }
            LRESULT(0)
        }
        WM_DESTROY => {
            OPEN.store(0, Ordering::SeqCst);
            PostQuitMessage(0);
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, message, wparam, lparam),
    }
}

/// Start a native move or resize from the page (the WebView holds capture).
unsafe fn begin_nc_drag(hwnd: HWND, hit: u32) {
    let mut point = POINT::default();
    let _ = GetCursorPos(&mut point);
    let _ = ReleaseCapture();
    let packed = ((point.y as u32 & 0xFFFF) << 16) | (point.x as u32 & 0xFFFF);
    let _ = PostMessageW(Some(hwnd), WM_NCLBUTTONDOWN, WPARAM(hit as usize), LPARAM(packed as isize));
}

fn handle_ipc(hwnd: isize, message: &str) {
    let hwnd = HWND(hwnd as *mut _);
    unsafe {
        match message {
            "drag" => begin_nc_drag(hwnd, HTCAPTION),
            "resize-top" => begin_nc_drag(hwnd, HTTOP),
            "min" => {
                let _ = ShowWindow(hwnd, SW_MINIMIZE);
            }
            "max" => {
                let _ = ShowWindow(hwnd, if IsZoomed(hwnd).as_bool() { SW_RESTORE } else { SW_MAXIMIZE });
            }
            "close" => {
                let _ = PostMessageW(Some(hwnd), WM_CLOSE, WPARAM(0), LPARAM(0));
            }
            other => {
                if let Some(text) = other.strip_prefix("log:") {
                    let text: String = text.chars().take(300).collect();
                    if text.starts_with("uitest") {
                        log::info!("Library page {text}");
                    } else {
                        log::warn!("Library page error: {text}");
                    }
                }
            }
        }
    }
}

/// Bring the existing window forward, or open a new one.
/// Returns an error when WebView2 is unavailable (the caller falls back to
/// the default browser).
pub fn open(url: String, data_dir: PathBuf) -> Result<(), String> {
    let existing = OPEN.load(Ordering::SeqCst);
    if existing != 0 {
        let hwnd = HWND(existing as *mut _);
        unsafe {
            if IsWindow(Some(hwnd)).as_bool() {
                if IsIconic(hwnd).as_bool() {
                    let _ = ShowWindow(hwnd, SW_RESTORE);
                }
                let _ = SetForegroundWindow(hwnd);
                return Ok(());
            }
        }
    }
    let (ready_tx, ready_rx) = mpsc::channel();
    std::thread::Builder::new()
        .name("library-window".into())
        .spawn(move || run(url, data_dir, ready_tx))
        .map_err(|e| e.to_string())?;
    ready_rx
        .recv_timeout(Duration::from_secs(15))
        .unwrap_or_else(|_| Err("The library window did not open in time".into()))
}

fn run(url: String, data_dir: PathBuf, ready: mpsc::Sender<Result<(), String>>) {
    static REGISTER: Once = Once::new();
    unsafe {
        let instance = GetModuleHandleW(None).unwrap_or_default();
        REGISTER.call_once(|| {
            let class = WNDCLASSW {
                lpfnWndProc: Some(proc),
                hInstance: instance.into(),
                lpszClassName: CLASS,
                hbrBackground: CreateSolidBrush(COLORREF(0x0024_2626)),
                ..Default::default()
            };
            RegisterClassW(&class);
        });
        let hwnd = match CreateWindowExW(
            WINDOW_EX_STYLE(0),
            CLASS,
            w!("League Highlights"),
            WS_OVERLAPPEDWINDOW,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            1280,
            820,
            None,
            None,
            Some(instance.into()),
            None,
        ) {
            Ok(hwnd) => hwnd,
            Err(e) => {
                let _ = ready.send(Err(e.to_string()));
                return;
            }
        };

        let dark = BOOL(1);
        let _ = DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            &dark as *const BOOL as *const _,
            std::mem::size_of::<BOOL>() as u32,
        );
        for (kind, size) in [(ICON_BIG, 32), (ICON_SMALL, 16)] {
            let icon = crate::tray::app_icon(size);
            SendMessageW(hwnd, WM_SETICON, Some(WPARAM(kind as usize)), Some(LPARAM(icon.0 as isize)));
        }
        // DPI-scaled size, centred; SWP_FRAMECHANGED applies the custom frame.
        let scale = f64::from(GetDpiForWindow(hwnd).max(96)) / 96.0;
        let (sw, sh) = (GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));
        let width = ((1280.0 * scale) as i32).min(sw - 40);
        let height = ((820.0 * scale) as i32).min(sh - 80);
        let _ = SetWindowPos(
            hwnd,
            None,
            (sw - width) / 2,
            (sh - height) / 2,
            width,
            height,
            SWP_NOZORDER | SWP_FRAMECHANGED,
        );

        let hwnd_value = hwnd.0 as isize;
        let mut context = wry::WebContext::new(Some(data_dir));
        use wry::WebViewBuilderExtWindows;
        let webview = wry::WebViewBuilder::with_web_context(&mut context)
            .with_additional_browser_args(BROWSER_ARGS)
            .with_browser_accelerator_keys(false)
            .with_url(url)
            .with_background_color((38, 38, 36, 255))
            .with_ipc_handler(move |request| handle_ipc(hwnd_value, request.body()))
            .build(&Parent(hwnd_value));
        let webview = match webview {
            Ok(webview) => webview,
            Err(e) => {
                log::warn!("WebView2 is unavailable ({e}); opening the library in the browser");
                let _ = DestroyWindow(hwnd);
                let _ = ready.send(Err(e.to_string()));
                return;
            }
        };
        VIEW.with(|view| *view.borrow_mut() = Some(webview));
        OPEN.store(hwnd_value, Ordering::SeqCst);
        let _ = ShowWindow(hwnd, SW_SHOW);
        let _ = SetForegroundWindow(hwnd);
        let _ = ready.send(Ok(()));
        log::info!("Library window opened");

        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).as_bool() {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        VIEW.with(|view| view.borrow_mut().take());
        drop(context);
        log::info!("Library window closed");
    }
}
