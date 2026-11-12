//! System (WASAPI loopback) and microphone capture into fixed ring buffers.
//!
//! Replaces PyAudio. Each source runs one timer-driven thread (10 wakeups/s) that asks
//! WASAPI to deliver 48 kHz stereo 16-bit PCM directly (AUTOCONVERTPCM), so no
//! resampling or Python callbacks are involved. Samples are placed by the
//! packet's QPC timestamp: silence gaps in loopback capture (nothing playing)
//! stay correctly aligned instead of shifting later audio earlier. A default
//! device change (e.g. plugging in a headset) reopens the stream.

use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;

use windows::Win32::Devices::FunctionDiscovery::PKEY_Device_FriendlyName;

use windows::Win32::Media::Audio::{
    eCapture, eConsole, eRender, EDataFlow, IAudioCaptureClient, IAudioClient, IMMDevice,
    IMMDeviceEnumerator, MMDeviceEnumerator, AUDCLNT_BUFFERFLAGS_SILENT,
    AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,
    AUDCLNT_STREAMFLAGS_LOOPBACK,
    AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY, DEVICE_STATE_ACTIVE, WAVEFORMATEX,
    WAVEFORMATEXTENSIBLE,
};
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoTaskMemFree, CLSCTX_ALL, COINIT_MULTITHREADED, STGM_READ,
};
use windows::Win32::System::Com::StructuredStorage::PropVariantToStringAlloc;

use crate::config::Settings;
use crate::util::from_qpc_100ns;

const TARGET_RATE: u32 = 48_000;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Kind {
    System,
    Microphone,
}

impl Kind {
    fn label(self) -> &'static str {
        match self {
            Kind::System => "system",
            Kind::Microphone => "microphone",
        }
    }
}

/// Stereo 16-bit ring addressed by absolute frame number.
struct Ring {
    rate: u32,
    cap_frames: i64,
    data: Vec<i16>,
    next_frame: i64,
    anchor: Option<f64>,
}

impl Ring {
    fn new(rate: u32, seconds: u32) -> Ring {
        let cap_frames = i64::from(rate) * i64::from(seconds.max(10));
        Ring {
            rate,
            cap_frames,
            data: vec![0; cap_frames as usize * 2],
            next_frame: 0,
            anchor: None,
        }
    }

    fn slot(&self, frame: i64) -> usize {
        (frame.rem_euclid(self.cap_frames) as usize) * 2
    }

    fn zero(&mut self, from: i64, to: i64) {
        let from = from.max(to - self.cap_frames);
        for frame in from..to {
            let i = self.slot(frame);
            self.data[i] = 0;
            self.data[i + 1] = 0;
        }
    }

    /// `samples` is interleaved stereo; `None` means a silent packet.
    fn push(&mut self, wall_start: f64, frames: usize, samples: Option<&[i16]>) {
        let rate = f64::from(self.rate);
        let start = match self.anchor {
            None => {
                self.anchor = Some(wall_start);
                self.next_frame = 0;
                0
            }
            Some(anchor) => {
                let predicted = ((wall_start - anchor) * rate).round() as i64;
                if predicted > self.next_frame + (rate * 0.02) as i64 {
                    // Real gap (loopback delivers nothing while silent).
                    self.zero(self.next_frame, predicted);
                    predicted
                } else if predicted < self.next_frame - (rate * 0.05) as i64 {
                    // Device clock drifted ahead of QPC: re-align.
                    predicted.max(self.next_frame - self.cap_frames + 1)
                } else {
                    self.next_frame
                }
            }
        };
        for offset in 0..frames {
            let i = self.slot(start + offset as i64);
            match samples {
                Some(s) => {
                    self.data[i] = s[offset * 2];
                    self.data[i + 1] = s[offset * 2 + 1];
                }
                None => {
                    self.data[i] = 0;
                    self.data[i + 1] = 0;
                }
            }
        }
        self.next_frame = start + frames as i64;
    }

    fn read(&self, start_wall: f64, end_wall: f64) -> Option<Vec<i16>> {
        let anchor = self.anchor?;
        let rate = f64::from(self.rate);
        let f0 = ((start_wall - anchor) * rate).floor() as i64;
        let f1 = ((end_wall - anchor) * rate).ceil() as i64;
        if f1 <= f0 {
            return None;
        }
        let lo = f0.max((self.next_frame - self.cap_frames).max(0));
        let hi = f1.min(self.next_frame);
        if hi - lo < (rate * 0.25) as i64 {
            return None;
        }
        let mut out = vec![0i16; (f1 - f0) as usize * 2];
        for frame in lo..hi {
            let src = self.slot(frame);
            let dst = (frame - f0) as usize * 2;
            out[dst] = self.data[src];
            out[dst + 1] = self.data[src + 1];
        }
        Some(out)
    }
}

struct Source {
    kind: Kind,
    device_name: Mutex<String>,
    ring: Mutex<Ring>,
    stop: AtomicBool,
    thread: Mutex<Option<JoinHandle<()>>>,
}

#[derive(Default)]
pub struct AudioExport {
    pub system: Option<PathBuf>,
    pub microphone: Option<PathBuf>,
}

#[derive(Default)]
pub struct Audio {
    sources: Mutex<Vec<Arc<Source>>>,
}

impl Audio {
    /// Start the configured sources. Failures are returned as warnings; the
    /// recording continues with whatever audio is available (or video only).
    pub fn start(&self, settings: &Settings) -> Vec<String> {
        self.stop();
        // Exports read at most the retained video (buffer + 12 s safety) plus
        // the segment being closed, so a few extra seconds are enough.
        let ring_seconds = settings.buffer_seconds + 16;
        let mut warnings = Vec::new();
        let mut started = Vec::new();
        let wanted = [
            (Kind::System, settings.system_audio_enabled, settings.system_audio_device.clone()),
            (Kind::Microphone, settings.microphone_enabled, settings.microphone_device.clone()),
        ];
        for (kind, enabled, preferred) in wanted {
            if !enabled {
                continue;
            }
            let source = Arc::new(Source {
                kind,
                device_name: Mutex::new(String::new()),
                ring: Mutex::new(Ring::new(TARGET_RATE, ring_seconds)),
                stop: AtomicBool::new(false),
                thread: Mutex::new(None),
            });
            let (ready_tx, ready_rx) = mpsc::channel();
            let thread_source = source.clone();
            let handle = std::thread::Builder::new()
                .name(format!("audio-{}", kind.label()))
                .spawn(move || capture_thread(thread_source, preferred, ring_seconds, ready_tx))
                .ok();
            *source.thread.lock().unwrap() = handle;
            match ready_rx.recv_timeout(Duration::from_secs(5)) {
                Ok(Ok(name)) => {
                    log::info!("Capturing {} audio from {name}", kind.label());
                    started.push(source);
                }
                Ok(Err(error)) => {
                    let message = format!("{} audio unavailable: {error}", capitalise(kind.label()));
                    log::warn!("{message}");
                    warnings.push(message);
                    join_source(&source);
                }
                Err(_) => {
                    warnings.push(format!("{} audio did not start in time", capitalise(kind.label())));
                    source.stop.store(true, Ordering::SeqCst);
                }
            }
        }
        *self.sources.lock().unwrap() = started;
        warnings
    }

    pub fn stop(&self) {
        let sources = std::mem::take(&mut *self.sources.lock().unwrap());
        for source in sources {
            join_source(&source);
        }
    }

    pub fn summary(&self) -> String {
        let sources = self.sources.lock().unwrap();
        let names: Vec<&str> = sources
            .iter()
            .map(|s| match s.kind {
                Kind::System => "System",
                Kind::Microphone => "Mic",
            })
            .collect();
        if names.is_empty() {
            "Video only".into()
        } else {
            names.join(" + ")
        }
    }

    pub fn device_name(&self, kind: Kind) -> String {
        self.sources
            .lock()
            .unwrap()
            .iter()
            .find(|s| s.kind == kind)
            .map(|s| s.device_name.lock().unwrap().clone())
            .unwrap_or_else(|| "Disabled".into())
    }

    /// Write each source's audio for the interval to a WAV in `dir`.
    pub fn export(&self, start_wall: f64, end_wall: f64, dir: &Path) -> AudioExport {
        let mut export = AudioExport::default();
        let sources = self.sources.lock().unwrap().clone();
        for source in sources {
            let (samples, rate) = {
                let ring = source.ring.lock().unwrap();
                (ring.read(start_wall, end_wall), ring.rate)
            };
            let Some(samples) = samples else { continue };
            let path = dir.join(format!("{}.wav", source.kind.label()));
            match write_wav(&path, rate, &samples) {
                Ok(()) => match source.kind {
                    Kind::System => export.system = Some(path),
                    Kind::Microphone => export.microphone = Some(path),
                },
                Err(e) => log::warn!("Could not write {} audio: {e}", source.kind.label()),
            }
        }
        export
    }
}

fn join_source(source: &Arc<Source>) {
    source.stop.store(true, Ordering::SeqCst);
    if let Some(handle) = source.thread.lock().unwrap().take() {
        let _ = handle.join();
    }
}

fn capitalise(text: &str) -> String {
    let mut chars = text.chars();
    chars
        .next()
        .map(|c| c.to_uppercase().collect::<String>() + chars.as_str())
        .unwrap_or_default()
}

pub fn write_wav(path: &Path, rate: u32, samples: &[i16]) -> std::io::Result<()> {
    let mut out = BufWriter::new(File::create(path)?);
    let data_len = (samples.len() * 2) as u32;
    out.write_all(b"RIFF")?;
    out.write_all(&(36 + data_len).to_le_bytes())?;
    out.write_all(b"WAVEfmt ")?;
    out.write_all(&16u32.to_le_bytes())?;
    out.write_all(&1u16.to_le_bytes())?; // PCM
    out.write_all(&2u16.to_le_bytes())?; // stereo
    out.write_all(&rate.to_le_bytes())?;
    out.write_all(&(rate * 4).to_le_bytes())?;
    out.write_all(&4u16.to_le_bytes())?;
    out.write_all(&16u16.to_le_bytes())?;
    out.write_all(b"data")?;
    out.write_all(&data_len.to_le_bytes())?;
    let mut bytes = Vec::with_capacity(samples.len() * 2);
    for sample in samples {
        bytes.extend_from_slice(&sample.to_le_bytes());
    }
    out.write_all(&bytes)?;
    out.flush()
}

// ---------------------------------------------------------------------------
// WASAPI
// ---------------------------------------------------------------------------

#[derive(Clone, Copy)]
struct Format {
    rate: u32,
    channels: usize,
    bits: u16,
    float: bool,
}

struct Stream {
    client: IAudioClient,
    capture: IAudioCaptureClient,
    format: Format,
    name: String,
}

impl Drop for Stream {
    fn drop(&mut self) {
        unsafe {
            let _ = self.client.Stop();
        }
    }
}

fn capture_thread(
    source: Arc<Source>,
    preferred: String,
    ring_seconds: u32,
    ready: mpsc::Sender<Result<String, String>>,
) {
    unsafe {
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
    }
    let mut ready = Some(ready);
    let mut converted: Vec<i16> = Vec::new();
    while !source.stop.load(Ordering::SeqCst) {
        let stream = match unsafe { open_stream(source.kind, &preferred) } {
            Ok(stream) => stream,
            Err(error) => {
                if let Some(tx) = ready.take() {
                    let _ = tx.send(Err(error));
                    return;
                }
                std::thread::sleep(Duration::from_secs(2));
                continue;
            }
        };
        *source.device_name.lock().unwrap() = stream.name.clone();
        {
            let mut ring = source.ring.lock().unwrap();
            if ring.rate != stream.format.rate {
                *ring = Ring::new(stream.format.rate, ring_seconds);
            }
        }
        if let Some(tx) = ready.take() {
            let _ = tx.send(Ok(stream.name.clone()));
        }
        if let Err(error) = unsafe { pump(&source, &stream, &mut converted) } {
            log::warn!("{} audio stream lost ({error}); reopening", source.kind.label());
            drop(stream);
            std::thread::sleep(Duration::from_secs(1));
        }
    }
}

unsafe fn pump(source: &Source, stream: &Stream, converted: &mut Vec<i16>) -> windows::core::Result<()> {
    while !source.stop.load(Ordering::SeqCst) {
        std::thread::sleep(Duration::from_millis(100));
        loop {
            let packet = stream.capture.GetNextPacketSize()?;
            if packet == 0 {
                break;
            }
            let mut data: *mut u8 = std::ptr::null_mut();
            let mut frames = 0u32;
            let mut flags = 0u32;
            let mut qpc = 0u64;
            stream
                .capture
                .GetBuffer(&mut data, &mut frames, &mut flags, None, Some(&mut qpc))?;
            let frames_usize = frames as usize;
            let silent = flags & (AUDCLNT_BUFFERFLAGS_SILENT.0 as u32) != 0 || data.is_null();
            let wall = if qpc > 0 { from_qpc_100ns(qpc) } else { crate::util::now() };
            if silent {
                source.ring.lock().unwrap().push(wall, frames_usize, None);
            } else {
                convert(data, frames_usize, stream.format, converted);
                source.ring.lock().unwrap().push(wall, frames_usize, Some(converted));
            }
            stream.capture.ReleaseBuffer(frames)?;
        }
    }
    Ok(())
}

/// Convert one packet to interleaved stereo i16.
unsafe fn convert(data: *const u8, frames: usize, f: Format, out: &mut Vec<i16>) {
    out.clear();
    out.reserve(frames * 2);
    let ch = f.channels.max(1);
    let sample = |frame: usize, channel: usize| -> i16 {
        let index = frame * ch + channel.min(ch - 1);
        match (f.float, f.bits) {
            (true, 32) => {
                let v = *(data as *const f32).add(index);
                (v.clamp(-1.0, 1.0) * 32767.0) as i16
            }
            (false, 16) => *(data as *const i16).add(index),
            (false, 32) => (*(data as *const i32).add(index) >> 16) as i16,
            (false, 24) => {
                let p = data.add(index * 3);
                i16::from_le_bytes([*p.add(1), *p.add(2)])
            }
            _ => 0,
        }
    };
    for frame in 0..frames {
        out.push(sample(frame, 0));
        out.push(sample(frame, 1));
    }
}

unsafe fn enumerator() -> windows::core::Result<IMMDeviceEnumerator> {
    CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL)
}

unsafe fn friendly_name(device: &IMMDevice) -> String {
    let Ok(store) = device.OpenPropertyStore(STGM_READ) else {
        return "Audio device".into();
    };
    let Ok(value) = store.GetValue(&PKEY_Device_FriendlyName) else {
        return "Audio device".into();
    };
    match PropVariantToStringAlloc(&value) {
        Ok(text) => {
            let name = text.to_string().unwrap_or_default();
            CoTaskMemFree(Some(text.0 as *const _));
            name
        }
        Err(_) => "Audio device".into(),
    }
}

fn clean_device_name(name: &str) -> String {
    name.trim()
        .trim_end_matches("[Loopback]")
        .trim()
        .to_lowercase()
}

unsafe fn find_device(
    enumerator: &IMMDeviceEnumerator,
    flow: EDataFlow,
    preferred: &str,
) -> Option<IMMDevice> {
    let wanted = clean_device_name(preferred);
    if wanted.is_empty() {
        return None;
    }
    let collection = enumerator.EnumAudioEndpoints(flow, DEVICE_STATE_ACTIVE).ok()?;
    let count = collection.GetCount().ok()?;
    let mut partial = None;
    for index in 0..count {
        let Ok(device) = collection.Item(index) else { continue };
        let name = clean_device_name(&friendly_name(&device));
        if name == wanted {
            return Some(device);
        }
        if partial.is_none() && (name.contains(&wanted) || wanted.contains(&name)) {
            partial = Some(device);
        }
    }
    partial
}

unsafe fn open_stream(kind: Kind, preferred: &str) -> Result<Stream, String> {
    let enumerator = enumerator().map_err(|e| e.message())?;
    let flow = if kind == Kind::System { eRender } else { eCapture };
    let device = match find_device(&enumerator, flow, preferred) {
        Some(device) => device,
        None => enumerator
            .GetDefaultAudioEndpoint(flow, eConsole)
            .map_err(|_| match kind {
                Kind::System => "No audio output device was found.".to_string(),
                Kind::Microphone => "No microphone input device was found.".to_string(),
            })?,
    };
    let name = friendly_name(&device);
    let loopback = if kind == Kind::System { AUDCLNT_STREAMFLAGS_LOOPBACK } else { 0 };
    let buffer_100ns = 5_000_000i64; // 500 ms device buffer, drained every 100 ms

    let desired = WAVEFORMATEX {
        wFormatTag: 1,
        nChannels: 2,
        nSamplesPerSec: TARGET_RATE,
        nAvgBytesPerSec: TARGET_RATE * 4,
        nBlockAlign: 4,
        wBitsPerSample: 16,
        cbSize: 0,
    };
    let client: IAudioClient = device.Activate(CLSCTX_ALL, None).map_err(|e| e.message())?;
    let direct = client.Initialize(
        AUDCLNT_SHAREMODE_SHARED,
        loopback
            | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
            | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
        buffer_100ns,
        0,
        &desired,
        None,
    );
    let (client, format) = match direct {
        Ok(()) => (
            client,
            Format {
                rate: TARGET_RATE,
                channels: 2,
                bits: 16,
                float: false,
            },
        ),
        Err(error) => {
            log::info!("Direct 48 kHz PCM capture unavailable ({error}); using the mix format");
            let client: IAudioClient = device.Activate(CLSCTX_ALL, None).map_err(|e| e.message())?;
            let mix = client.GetMixFormat().map_err(|e| e.message())?;
            let format = parse_format(mix);
            let result = client.Initialize(
                AUDCLNT_SHAREMODE_SHARED,
                loopback,
                buffer_100ns,
                0,
                mix,
                None,
            );
            CoTaskMemFree(Some(mix as *const _));
            result.map_err(|e| e.message())?;
            (client, format)
        }
    };
    let capture: IAudioCaptureClient = client.GetService().map_err(|e| e.message())?;
    client.Start().map_err(|e| e.message())?;
    Ok(Stream {
        client,
        capture,
        format,
        name,
    })
}

unsafe fn parse_format(pointer: *const WAVEFORMATEX) -> Format {
    let base = std::ptr::read_unaligned(pointer);
    let tag = base.wFormatTag;
    let mut float = tag == 3;
    if tag == 0xFFFE && base.cbSize >= 22 {
        let ext = std::ptr::read_unaligned(pointer as *const WAVEFORMATEXTENSIBLE);
        let sub = ext.SubFormat;
        float = sub.data1 == 3;
    }
    Format {
        rate: base.nSamplesPerSec,
        channels: usize::from(base.nChannels),
        bits: base.wBitsPerSample,
        float,
    }
}

pub fn list_devices(system: bool) -> Vec<String> {
    let mut names = Vec::new();
    unsafe {
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
        let Ok(enumerator) = enumerator() else { return names };
        let flow = if system { eRender } else { eCapture };
        let Ok(collection) = enumerator.EnumAudioEndpoints(flow, DEVICE_STATE_ACTIVE) else {
            return names;
        };
        let count = collection.GetCount().unwrap_or(0);
        for index in 0..count {
            if let Ok(device) = collection.Item(index) {
                let name = friendly_name(&device);
                if !names.contains(&name) {
                    names.push(name);
                }
            }
        }
    }
    names
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ring_keeps_silence_gaps_aligned() {
        let mut ring = Ring::new(1000, 10);
        let tone = vec![100i16; 200];
        ring.push(0.0, 100, Some(&tone)); // 0.0 - 0.1 s
        ring.push(1.0, 100, Some(&tone)); // gap, then 1.0 - 1.1 s
        let out = ring.read(0.0, 1.1).unwrap();
        assert_eq!(out.len(), 1100 * 2);
        assert_eq!(out[0], 100);
        assert_eq!(out[500 * 2], 0); // silence inside the gap
        assert_eq!(out[1050 * 2], 100); // second packet at its real time
    }

    #[test]
    fn ring_absorbs_jitter() {
        let mut ring = Ring::new(1000, 10);
        let tone = vec![1i16; 200];
        ring.push(0.0, 100, Some(&tone));
        ring.push(0.105, 100, Some(&tone)); // 5 ms late: stays contiguous
        assert_eq!(ring.next_frame, 200);
    }

    #[test]
    fn ring_wraps() {
        let mut ring = Ring::new(100, 10); // 1000 frames
        let tone = vec![7i16; 2 * 600];
        for i in 0..5 {
            ring.push(f64::from(i) * 6.0, 600, Some(&tone));
        }
        assert!(ring.read(0.0, 1.0).is_none());
        assert!(ring.read(23.0, 29.0).is_some());
    }
}
