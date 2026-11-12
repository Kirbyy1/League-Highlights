//! Thin wrappers around the Win32 APIs the app needs.

use std::os::windows::io::AsRawHandle;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use windows::core::{w, PCWSTR, PWSTR};
use windows::core::BOOL;
use windows::Win32::Foundation::{
    CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, HANDLE, HWND, LPARAM, RECT,
};
use windows::Win32::Graphics::Dxgi::{CreateDXGIFactory1, IDXGIFactory1};
use windows::Win32::Graphics::Gdi::{
    GetMonitorInfoW, MonitorFromWindow, HMONITOR, MONITORINFO, MONITORINFOEXW,
    MONITOR_DEFAULTTONEAREST,
};
use windows::Win32::Security::Cryptography::{BCryptGenRandom, BCRYPT_USE_SYSTEM_PREFERRED_RNG};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows::Win32::System::Registry::{
    RegCloseKey, RegCreateKeyW, RegDeleteValueW, RegGetValueW, RegSetValueExW, HKEY,
    HKEY_CURRENT_USER, REG_SZ, RRF_RT_REG_SZ,
};
use windows::Win32::System::Threading::{
    CreateMutexW, OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_WIN32,
    PROCESS_QUERY_LIMITED_INFORMATION,
};
use windows::Win32::UI::Shell::{
    FOLDERID_Videos, SHGetKnownFolderPath, ShellExecuteW, KF_FLAG_DEFAULT,
};
use windows::Win32::UI::WindowsAndMessaging::{
    EnumWindows, GetWindowRect, GetWindowTextW, GetWindowThreadProcessId, IsIconic,
    IsWindowVisible, SW_SHOWNORMAL,
};

use crate::util::{from_wide, wide, wide_path};

#[derive(Clone, Debug, PartialEq)]
pub struct GameWindow {
    pub hwnd: isize,
    pub pid: u32,
    pub title: String,
    pub width: i32,
    pub height: i32,
    pub monitor_index: u32,
    pub offset_x: i32,
    pub offset_y: i32,
}

impl GameWindow {
    pub fn same_geometry(&self, other: &GameWindow) -> bool {
        self.hwnd == other.hwnd
            && self.width == other.width
            && self.height == other.height
            && self.monitor_index == other.monitor_index
            && self.offset_x == other.offset_x
            && self.offset_y == other.offset_y
    }
}

const GAME_PROCESSES: [&str; 2] = ["league of legends.exe", "leagueoflegends.exe"];

/// Find the visible in-game window (not the launcher/client).
pub fn find_league_window() -> Option<GameWindow> {
    let mut found: Vec<GameWindow> = Vec::new();
    unsafe extern "system" fn callback(hwnd: HWND, lparam: LPARAM) -> BOOL {
        let list = &mut *(lparam.0 as *mut Vec<GameWindow>);
        if let Some(window) = inspect_window(hwnd) {
            list.push(window);
        }
        BOOL(1)
    }
    unsafe {
        let _ = EnumWindows(Some(callback), LPARAM(&mut found as *mut _ as isize));
    }
    found.into_iter().max_by_key(|w| i64::from(w.width) * i64::from(w.height))
}

unsafe fn inspect_window(hwnd: HWND) -> Option<GameWindow> {
    if !IsWindowVisible(hwnd).as_bool() || IsIconic(hwnd).as_bool() {
        return None;
    }
    let mut pid = 0u32;
    GetWindowThreadProcessId(hwnd, Some(&mut pid));
    if pid == 0 {
        return None;
    }
    let name = process_image_path(pid)?;
    let file = Path::new(&name)
        .file_name()?
        .to_string_lossy()
        .to_lowercase();
    if !GAME_PROCESSES.contains(&file.as_str()) {
        return None;
    }
    let mut title = [0u16; 256];
    let len = GetWindowTextW(hwnd, &mut title);
    let title = String::from_utf16_lossy(&title[..len.max(0) as usize]).trim().to_string();
    if title.is_empty() {
        return None;
    }
    let mut rect = RECT::default();
    GetWindowRect(hwnd, &mut rect).ok()?;
    let width = rect.right - rect.left;
    let height = rect.bottom - rect.top;
    if width < 640 || height < 360 {
        return None;
    }

    let monitor = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST);
    let mut info = MONITORINFOEXW::default();
    info.monitorInfo.cbSize = std::mem::size_of::<MONITORINFOEXW>() as u32;
    let (mut offset_x, mut offset_y, mut display_number) = (0, 0, None);
    if GetMonitorInfoW(monitor, &mut info as *mut MONITORINFOEXW as *mut MONITORINFO).as_bool() {
        let m = info.monitorInfo.rcMonitor;
        offset_x = (rect.left - m.left).max(0);
        offset_y = (rect.top - m.top).max(0);
        let device = from_wide(&info.szDevice);
        display_number = device
            .rsplit("DISPLAY")
            .next()
            .and_then(|n| n.parse::<u32>().ok());
    }
    let monitor_index = dxgi_output_index(monitor)
        .or_else(|| display_number.map(|n| n.saturating_sub(1)))
        .unwrap_or(0);

    Some(GameWindow {
        hwnd: hwnd.0 as isize,
        pid,
        title,
        width,
        height,
        monitor_index,
        offset_x,
        offset_y,
    })
}

/// ddagrab's `output_idx` indexes the outputs of the default DXGI adapter.
/// Matching by HMONITOR is exact, unlike guessing from the `DISPLAYn` name.
/// The lookup is cached per monitor: creating a DXGI factory loads the GPU
/// driver, which must not happen on every 1.3 s window poll.
pub fn dxgi_output_index(monitor: HMONITOR) -> Option<u32> {
    static CACHE: std::sync::Mutex<Option<(isize, Option<u32>)>> = std::sync::Mutex::new(None);
    let key = monitor.0 as isize;
    if let Some((cached, index)) = *CACHE.lock().unwrap() {
        if cached == key {
            return index;
        }
    }
    let index = unsafe {
        (|| {
            let factory: IDXGIFactory1 = CreateDXGIFactory1().ok()?;
            let adapter = factory.EnumAdapters1(0).ok()?;
            for index in 0..16 {
                let Ok(output) = adapter.EnumOutputs(index) else { break };
                if output.GetDesc().is_ok_and(|desc| desc.Monitor == monitor) {
                    return Some(index);
                }
            }
            None
        })()
    };
    *CACHE.lock().unwrap() = Some((key, index));
    index
}

pub fn process_image_path(pid: u32) -> Option<String> {
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?;
        let mut buffer = [0u16; 1024];
        let mut size = buffer.len() as u32;
        let ok = QueryFullProcessImageNameW(
            handle,
            PROCESS_NAME_WIN32,
            PWSTR(buffer.as_mut_ptr()),
            &mut size,
        );
        let _ = CloseHandle(handle);
        ok.ok()?;
        Some(String::from_utf16_lossy(&buffer[..size as usize]))
    }
}

/// Return the image paths of running processes with the given file names.
pub fn find_process_paths(names: &[&str]) -> Vec<String> {
    let mut paths = Vec::new();
    unsafe {
        let Ok(snapshot) = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) else {
            return paths;
        };
        let mut entry = PROCESSENTRY32W {
            dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32,
            ..Default::default()
        };
        let mut ok = Process32FirstW(snapshot, &mut entry).is_ok();
        while ok {
            let exe = from_wide(&entry.szExeFile).to_lowercase();
            if names.iter().any(|n| n.eq_ignore_ascii_case(&exe)) {
                if let Some(path) = process_image_path(entry.th32ProcessID) {
                    paths.push(path);
                }
            }
            ok = Process32NextW(snapshot, &mut entry).is_ok();
        }
        let _ = CloseHandle(snapshot);
    }
    paths
}

// ---------------------------------------------------------------------------
// Child-process job: FFmpeg can never outlive the app, even after a crash.
// ---------------------------------------------------------------------------

struct Job(HANDLE);
unsafe impl Send for Job {}
unsafe impl Sync for Job {}

static JOB: OnceLock<Option<Job>> = OnceLock::new();

fn job() -> Option<&'static Job> {
    JOB.get_or_init(|| unsafe {
        let handle = CreateJobObjectW(None, PCWSTR::null()).ok()?;
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const std::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
        .ok()?;
        Some(Job(handle))
    })
    .as_ref()
}

pub fn adopt_child(child: &std::process::Child) {
    if let Some(job) = job() {
        unsafe {
            let _ = AssignProcessToJobObject(job.0, HANDLE(child.as_raw_handle()));
        }
    }
}

// ---------------------------------------------------------------------------
// Single instance
// ---------------------------------------------------------------------------

/// Returns false when another copy is already running.
pub fn acquire_single_instance() -> bool {
    unsafe {
        match CreateMutexW(None, true, w!("Local\\LeagueHighlightsRust")) {
            Ok(handle) => {
                // The handle stays open for the whole process lifetime.
                let _ = handle;
                GetLastError() != ERROR_ALREADY_EXISTS
            }
            Err(_) => true,
        }
    }
}

// ---------------------------------------------------------------------------
// Launch with Windows (same Run value name as the Python version)
// ---------------------------------------------------------------------------

const RUN_KEY: PCWSTR = w!("Software\\Microsoft\\Windows\\CurrentVersion\\Run");
const RUN_VALUE: PCWSTR = w!("League Highlights");

pub fn startup_enabled() -> bool {
    let mut buffer = [0u16; 1024];
    let mut size = (buffer.len() * 2) as u32;
    unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            RUN_KEY,
            RUN_VALUE,
            RRF_RT_REG_SZ,
            None,
            Some(buffer.as_mut_ptr() as *mut _),
            Some(&mut size),
        )
        .is_ok()
            && !from_wide(&buffer).trim().is_empty()
    }
}

pub fn set_startup(enabled: bool) -> Result<(), String> {
    unsafe {
        let mut key = HKEY::default();
        RegCreateKeyW(HKEY_CURRENT_USER, RUN_KEY, &mut key)
            .ok()
            .map_err(|e| e.to_string())?;
        let result = if enabled {
            let exe = std::env::current_exe().map_err(|e| e.to_string())?;
            let command = format!("\"{}\" --startup", exe.display());
            let data = wide(&command);
            let bytes = std::slice::from_raw_parts(data.as_ptr() as *const u8, data.len() * 2);
            RegSetValueExW(key, RUN_VALUE, None, REG_SZ, Some(bytes)).ok()
        } else {
            let _ = RegDeleteValueW(key, RUN_VALUE);
            Ok(())
        };
        let _ = RegCloseKey(key);
        result.map_err(|e| e.to_string())
    }
}

// ---------------------------------------------------------------------------
// Shell helpers
// ---------------------------------------------------------------------------

pub fn videos_folder() -> Option<PathBuf> {
    unsafe {
        let path = SHGetKnownFolderPath(&FOLDERID_Videos, KF_FLAG_DEFAULT, None).ok()?;
        let text = path.to_string().ok();
        windows::Win32::System::Com::CoTaskMemFree(Some(path.0 as *const _));
        text.map(PathBuf::from)
    }
}

pub fn shell_open(target: &str) {
    let target = wide(target);
    unsafe {
        ShellExecuteW(
            None,
            w!("open"),
            PCWSTR(target.as_ptr()),
            None,
            None,
            SW_SHOWNORMAL,
        );
    }
}

pub fn reveal_in_explorer(path: &Path) {
    let args = wide(&format!("/select,\"{}\"", path.display()));
    unsafe {
        ShellExecuteW(
            None,
            w!("open"),
            w!("explorer.exe"),
            PCWSTR(args.as_ptr()),
            None,
            SW_SHOWNORMAL,
        );
    }
}

pub fn open_folder(path: &Path) {
    let target = wide_path(path);
    unsafe {
        ShellExecuteW(
            None,
            w!("open"),
            PCWSTR(target.as_ptr()),
            None,
            None,
            SW_SHOWNORMAL,
        );
    }
}

pub fn random_token() -> String {
    let mut bytes = [0u8; 18];
    unsafe {
        let _ = BCryptGenRandom(None, &mut bytes, BCRYPT_USE_SYSTEM_PREFERRED_RNG);
    }
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Put files on the clipboard (CF_HDROP), so Ctrl+V in Discord, Explorer or
/// a chat app pastes the video itself.
pub fn copy_files_to_clipboard(paths: &[PathBuf]) -> Result<(), String> {
    use windows::Win32::System::DataExchange::{CloseClipboard, EmptyClipboard, OpenClipboard, SetClipboardData};
    use windows::Win32::Foundation::GlobalFree;
    use windows::Win32::System::Memory::{GlobalAlloc, GlobalLock, GlobalUnlock, GMEM_MOVEABLE, GMEM_ZEROINIT};
    const CF_HDROP: u32 = 15;
    const HEADER: usize = 20; // DROPFILES: pFiles, pt.x, pt.y, fNC, fWide

    let mut list: Vec<u16> = Vec::new();
    for path in paths {
        list.extend(wide_path(path)); // includes the terminating 0
    }
    list.push(0);
    let bytes = HEADER + list.len() * 2;
    unsafe {
        let memory = GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, bytes).map_err(|e| e.to_string())?;
        let pointer = GlobalLock(memory) as *mut u8;
        if pointer.is_null() {
            let _ = GlobalFree(Some(memory));
            return Err("Could not allocate clipboard memory".into());
        }
        let header = [HEADER as u32, 0, 0, 0, 1]; // fWide = TRUE
        std::ptr::copy_nonoverlapping(header.as_ptr() as *const u8, pointer, HEADER);
        std::ptr::copy_nonoverlapping(list.as_ptr() as *const u8, pointer.add(HEADER), list.len() * 2);
        let _ = GlobalUnlock(memory);
        if OpenClipboard(None).is_err() {
            let _ = GlobalFree(Some(memory));
            return Err("The clipboard is in use by another app".into());
        }
        let _ = EmptyClipboard();
        let result = SetClipboardData(CF_HDROP, Some(HANDLE(memory.0)));
        let _ = CloseClipboard();
        if result.is_err() {
            let _ = GlobalFree(Some(memory));
            return Err("Could not copy to the clipboard".into());
        }
    }
    Ok(())
}

pub fn message_box(title: &str, text: &str) {
    use windows::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONINFORMATION, MB_OK};
    let title = wide(title);
    let text = wide(text);
    unsafe {
        MessageBoxW(None, PCWSTR(text.as_ptr()), PCWSTR(title.as_ptr()), MB_OK | MB_ICONINFORMATION);
    }
}

