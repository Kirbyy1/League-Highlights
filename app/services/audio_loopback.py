from __future__ import annotations

import logging
import math
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import os

if os.name == "nt":     # Windows
    import pyaudiowpatch as pyaudio
else:                   # Linux/macOS
    import pyaudio

from app.config import AppConfig

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class AudioChunk:
    """One continuous block of PCM frames in a source-local sample timeline."""

    start_frame: int
    end_frame: int
    data: bytes


@dataclass(slots=True)
class _BufferedSource:
    kind: str
    device_name: str
    device_index: int
    rate: int
    channels: int
    sample_width: int
    chunks: deque[AudioChunk] = field(default_factory=deque)
    stream: object | None = None

    # The original implementation timestamped every callback with time.time().
    # Normal callback scheduling jitter then became tiny gaps/overlaps in the
    # exported WAV, which can sound crackly or robotic. Keep a continuous sample
    # clock instead and map it to wall time once when capture starts.
    anchor_wall_time: float | None = None
    anchor_adc_time: float | None = None
    next_frame: int = 0
    status_warning_count: int = 0


@dataclass(slots=True, frozen=True)
class AudioExport:
    system_path: Path | None = None
    microphone_path: Path | None = None
    system_seconds: float = 0.0
    microphone_seconds: float = 0.0

    @property
    def has_audio(self) -> bool:
        return self.system_path is not None or self.microphone_path is not None

    @property
    def sources(self) -> tuple[str, ...]:
        result: list[str] = []
        if self.system_path is not None:
            result.append("system")
        if self.microphone_path is not None:
            result.append("microphone")
        return tuple(result)


class LoopbackAudioBuffer:
    """Buffers system output and an optional microphone with WASAPI/PyAudio.

    Audio is stored as raw PCM in a sample-accurate ring buffer. System and
    microphone stay separate and are mixed only when a clip is exported.
    """

    def __init__(
        self,
        config: AppConfig,
        max_seconds: int = 75,
        frames_per_buffer: int = 2048,
    ) -> None:
        self.config = config
        self.max_seconds = max_seconds
        # A slightly larger callback buffer lowers the chance of Python callback
        # overruns while still keeping latency comfortably below one video frame
        # segment. It does not change the final audio quality.
        self.frames_per_buffer = frames_per_buffer
        self._lock = threading.RLock()
        self._pyaudio: pyaudio.PyAudio | None = None
        self._system: _BufferedSource | None = None
        self._microphone: _BufferedSource | None = None
        self.last_error: str | None = None
        self.warnings: list[str] = []

    @property
    def running(self) -> bool:
        return any(self._stream_active(source) for source in (self._system, self._microphone))

    @property
    def system_device_name(self) -> str:
        return self._system.device_name if self._system is not None else "Disabled"

    @property
    def microphone_device_name(self) -> str:
        return self._microphone.device_name if self._microphone is not None else "Disabled"

    @property
    def source_summary(self) -> str:
        sources: list[str] = []
        if self._system is not None:
            sources.append("system")
        if self._microphone is not None:
            sources.append("microphone")
        return " + ".join(sources) if sources else "video only"

    def start(self) -> None:
        if self.running:
            return

        self.stop()
        self.last_error = None
        self.warnings.clear()

        requested = int(self.config.system_audio_enabled) + int(self.config.microphone_enabled)
        if requested == 0:
            LOGGER.info("Audio capture is disabled; recording video only")
            return

        manager = pyaudio.PyAudio()
        started = 0
        try:
            if self.config.system_audio_enabled:
                try:
                    device = self._resolve_system_device(manager, self.config.system_audio_device)
                    self._system = self._open_source(manager, "system", device)
                    started += 1
                    LOGGER.info(
                        "Capturing system audio from %s (%s Hz, %s channel%s)",
                        self._system.device_name,
                        self._system.rate,
                        self._system.channels,
                        "s" if self._system.channels != 1 else "",
                    )
                except Exception as exc:
                    message = f"System audio unavailable: {exc}"
                    self.warnings.append(message)
                    LOGGER.exception(message)

            if self.config.microphone_enabled:
                try:
                    device = self._resolve_microphone_device(
                        manager,
                        self.config.microphone_device,
                    )
                    self._microphone = self._open_source(manager, "microphone", device)
                    started += 1
                    LOGGER.info(
                        "Capturing microphone from %s (%s Hz, %s channel%s)",
                        self._microphone.device_name,
                        self._microphone.rate,
                        self._microphone.channels,
                        "s" if self._microphone.channels != 1 else "",
                    )
                except Exception as exc:
                    message = f"Microphone unavailable: {exc}"
                    self.warnings.append(message)
                    LOGGER.exception(message)

            if started == 0:
                details = "; ".join(self.warnings) or "No requested audio device could be opened."
                raise RuntimeError(details)

            self._pyaudio = manager
        except Exception as exc:
            self._close_source(self._system)
            self._close_source(self._microphone)
            self._system = None
            self._microphone = None
            manager.terminate()
            self.last_error = str(exc)
            raise RuntimeError(f"Audio capture failed: {exc}") from exc

    def stop(self) -> None:
        manager = self._pyaudio
        self._pyaudio = None
        system, microphone = self._system, self._microphone
        self._system = None
        self._microphone = None
        self._close_source(system)
        self._close_source(microphone)
        if manager is not None:
            try:
                manager.terminate()
            except Exception:
                LOGGER.exception("Could not terminate PyAudio")

    def write_interval(self, start_time: float, end_time: float, output_dir: Path) -> AudioExport:
        """Write buffered sources for a clip interval as separate WAV files."""
        if end_time <= start_time:
            raise ValueError("Audio interval must have a positive duration")

        output_dir.mkdir(parents=True, exist_ok=True)
        system_path: Path | None = None
        microphone_path: Path | None = None
        system_seconds = 0.0
        microphone_seconds = 0.0

        if self._system is not None:
            candidate = output_dir / "system_audio.wav"
            system_seconds = self._write_source_interval(
                self._system,
                start_time,
                end_time,
                candidate,
            )
            if system_seconds >= 0.25:
                system_path = candidate

        if self._microphone is not None:
            candidate = output_dir / "microphone.wav"
            microphone_seconds = self._write_source_interval(
                self._microphone,
                start_time,
                end_time,
                candidate,
            )
            if microphone_seconds >= 0.25:
                microphone_path = candidate

        return AudioExport(
            system_path=system_path,
            microphone_path=microphone_path,
            system_seconds=system_seconds,
            microphone_seconds=microphone_seconds,
        )

    @classmethod
    def list_system_devices(cls) -> list[str]:
        manager = pyaudio.PyAudio()
        try:
            names: list[str] = []
            for item in manager.get_loopback_device_info_generator():
                name = str(item.get("name", "")).strip()
                if name and name not in names:
                    names.append(name)
            return names
        except Exception:
            LOGGER.exception("Could not enumerate system-audio devices")
            return []
        finally:
            manager.terminate()

    @classmethod
    def list_microphone_devices(cls) -> list[str]:
        manager = pyaudio.PyAudio()
        try:
            names: list[str] = []
            for index in range(manager.get_device_count()):
                item = dict(manager.get_device_info_by_index(index))
                if int(item.get("maxInputChannels", 0) or 0) <= 0:
                    continue
                if bool(item.get("isLoopbackDevice", False)):
                    continue
                name = str(item.get("name", "")).strip()
                if name and name not in names:
                    names.append(name)
            return names
        except Exception:
            LOGGER.exception("Could not enumerate microphone devices")
            return []
        finally:
            manager.terminate()

    def _open_source(
        self,
        manager: pyaudio.PyAudio,
        kind: str,
        device: dict,
    ) -> _BufferedSource:
        rate = int(round(float(device.get("defaultSampleRate", 48_000))))
        max_channels = max(1, int(device.get("maxInputChannels", 1)))
        channels = min(2, max_channels)
        sample_width = pyaudio.get_sample_size(pyaudio.paInt16)
        source = _BufferedSource(
            kind=kind,
            device_name=str(device.get("name", kind.title())),
            device_index=int(device["index"]),
            rate=rate,
            channels=channels,
            sample_width=sample_width,
        )

        def callback(in_data: bytes, frame_count: int, time_info: dict, status_flags: int):
            frame_size = source.sample_width * source.channels
            actual_frames = len(in_data) // frame_size if frame_size > 0 else 0
            if actual_frames <= 0:
                return (None, pyaudio.paContinue)

            wall_now = time.time()
            adc_time = 0.0
            try:
                adc_time = float(time_info.get("input_buffer_adc_time", 0.0) or 0.0)
            except (AttributeError, TypeError, ValueError):
                adc_time = 0.0

            with self._lock:
                if source.anchor_wall_time is None:
                    # The first sample in this callback occurred one buffer duration
                    # before the callback completed. Map that point to wall time.
                    source.anchor_wall_time = wall_now - (actual_frames / source.rate)
                    source.anchor_adc_time = adc_time if adc_time > 0 else None
                    start_frame = 0
                else:
                    start_frame = source.next_frame
                    if source.anchor_adc_time is not None and adc_time > 0:
                        predicted = int(round((adc_time - source.anchor_adc_time) * source.rate))
                        # PortAudio's ADC timestamp identifies the first captured
                        # sample. Accept forward movement to preserve real dropped
                        # samples as silence, but never move backwards and overlap
                        # already-buffered audio. Ignore implausible jumps.
                        maximum_reasonable_gap = int(source.rate * 0.5)
                        if source.next_frame <= predicted <= source.next_frame + maximum_reasonable_gap:
                            start_frame = predicted

                end_frame = start_frame + actual_frames
                source.chunks.append(AudioChunk(start_frame, end_frame, bytes(in_data)))
                source.next_frame = end_frame
                self._trim_source_locked(source)

                if status_flags:
                    source.status_warning_count += 1
                    if source.status_warning_count <= 5 or source.status_warning_count % 100 == 0:
                        LOGGER.warning(
                            "%s audio callback status flags=%s (count=%s)",
                            kind,
                            status_flags,
                            source.status_warning_count,
                        )
            return (None, pyaudio.paContinue)

        stream = manager.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            frames_per_buffer=self.frames_per_buffer,
            input=True,
            input_device_index=source.device_index,
            stream_callback=callback,
        )
        stream.start_stream()
        source.stream = stream
        return source

    @staticmethod
    def _resolve_system_device(manager: pyaudio.PyAudio, preferred_name: str) -> dict:
        devices = [dict(item) for item in manager.get_loopback_device_info_generator()]
        if preferred_name:
            match = LoopbackAudioBuffer._match_device(devices, preferred_name)
            if match is not None:
                return match

        helper = getattr(manager, "get_default_wasapi_loopback", None)
        if callable(helper):
            return dict(helper())

        wasapi = manager.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = dict(manager.get_device_info_by_index(wasapi["defaultOutputDevice"]))
        if speakers.get("isLoopbackDevice"):
            return speakers
        match = next(
            (item for item in devices if str(speakers.get("name", "")) in str(item.get("name", ""))),
            None,
        )
        if match is not None:
            return match
        if devices:
            return devices[0]
        raise RuntimeError("No WASAPI loopback output device was found.")

    @staticmethod
    def _resolve_microphone_device(manager: pyaudio.PyAudio, preferred_name: str) -> dict:
        devices: list[dict] = []
        for index in range(manager.get_device_count()):
            item = dict(manager.get_device_info_by_index(index))
            if int(item.get("maxInputChannels", 0) or 0) <= 0:
                continue
            if bool(item.get("isLoopbackDevice", False)):
                continue
            devices.append(item)

        if preferred_name:
            match = LoopbackAudioBuffer._match_device(devices, preferred_name)
            if match is not None:
                return match

        try:
            default = dict(manager.get_default_input_device_info())
            if int(default.get("maxInputChannels", 0) or 0) > 0:
                return default
        except Exception:
            pass
        if devices:
            return devices[0]
        raise RuntimeError("No microphone input device was found.")

    @staticmethod
    def _match_device(devices: list[dict], preferred_name: str) -> dict | None:
        wanted = preferred_name.strip().casefold()
        exact = next(
            (item for item in devices if str(item.get("name", "")).strip().casefold() == wanted),
            None,
        )
        if exact is not None:
            return exact
        return next(
            (item for item in devices if wanted in str(item.get("name", "")).casefold()),
            None,
        )

    def _write_source_interval(
        self,
        source: _BufferedSource,
        start_time: float,
        end_time: float,
        output_path: Path,
    ) -> float:
        with self._lock:
            chunks = list(source.chunks)
            anchor_wall_time = source.anchor_wall_time

        if anchor_wall_time is None or not chunks:
            return 0.0

        frame_size = source.sample_width * source.channels
        requested_start_frame = math.floor((start_time - anchor_wall_time) * source.rate)
        requested_end_frame = math.ceil((end_time - anchor_wall_time) * source.rate)
        requested_frames = max(1, requested_end_frame - requested_start_frame)
        selected = bytearray(requested_frames * frame_size)
        copied_any = False

        for chunk in chunks:
            overlap_start = max(requested_start_frame, chunk.start_frame)
            overlap_end = min(requested_end_frame, chunk.end_frame)
            if overlap_end <= overlap_start:
                continue

            source_first = overlap_start - chunk.start_frame
            destination_first = overlap_start - requested_start_frame
            frame_count = overlap_end - overlap_start

            source_bytes_start = source_first * frame_size
            source_bytes_end = source_bytes_start + frame_count * frame_size
            destination_bytes_start = destination_first * frame_size
            destination_bytes_end = destination_bytes_start + frame_count * frame_size
            selected[destination_bytes_start:destination_bytes_end] = chunk.data[
                source_bytes_start:source_bytes_end
            ]
            copied_any = True

        if not copied_any:
            return 0.0

        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(source.channels)
            wav.setsampwidth(source.sample_width)
            wav.setframerate(source.rate)
            wav.writeframes(selected)
        return requested_frames / source.rate

    @staticmethod
    def _stream_active(source: _BufferedSource | None) -> bool:
        if source is None or source.stream is None:
            return False
        try:
            return bool(source.stream.is_active())
        except Exception:
            return False

    @staticmethod
    def _close_source(source: _BufferedSource | None) -> None:
        if source is None or source.stream is None:
            return
        try:
            if source.stream.is_active():
                source.stream.stop_stream()
            source.stream.close()
        except Exception:
            LOGGER.exception("Could not close %s audio stream", source.kind)

    def _trim_source_locked(self, source: _BufferedSource) -> None:
        cutoff_frame = source.next_frame - int(self.max_seconds * source.rate)
        while source.chunks and source.chunks[0].end_frame < cutoff_frame:
            source.chunks.popleft()
