from __future__ import annotations

import queue

import numpy as np
import sounddevice as sd
import soxr

from voicejournal.app.config import AppConfig


TARGET_SAMPLE_RATE = 16_000
CHANNEL_COUNT = 1
BLOCK_SIZE = 512
SAMPLE_DTYPE = "float32"


class AudioStream:
    def __init__(self, config: AppConfig, vad_queue: queue.Queue[np.ndarray]) -> None:
        self._vad_queue = vad_queue
        self._audio_device = config.audio_device
        self._native_rate = float(TARGET_SAMPLE_RATE)
        self._resampler: soxr.ResampleStream | None = None

        try:
            self._stream = self._open_stream(TARGET_SAMPLE_RATE)
            self._native_rate = float(self._stream.samplerate)
            self._maybe_create_resampler()
        except sd.PortAudioError:
            info = self._query_device_info()
            requested_rate = float(info["default_samplerate"])
            self._stream = self._open_stream(requested_rate)
            self._native_rate = float(self._stream.samplerate)
            self._maybe_create_resampler()

    @property
    def native_rate(self) -> float:
        return self._native_rate

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()
        self._stream.close()
        if self._resampler is None:
            return

        tail = self._resampler.resample_chunk(
            np.zeros(0, dtype=SAMPLE_DTYPE),
            last=True,
        )
        if tail.size:
            self._enqueue_chunk(tail)

    def _open_stream(self, samplerate: float):
        return sd.InputStream(
            samplerate=samplerate,
            channels=CHANNEL_COUNT,
            dtype=SAMPLE_DTYPE,
            blocksize=BLOCK_SIZE,
            callback=self._callback,
            device=self._audio_device,
        )

    def _query_device_info(self):
        if self._audio_device is not None:
            return sd.query_devices(self._audio_device, kind="input")

        input_device = self._default_input_device()

        if input_device in (None, -1):
            return sd.query_devices(kind="input")
        return sd.query_devices(input_device, kind="input")

    def _default_input_device(self):
        default_device = sd.default.device
        if default_device is None or isinstance(default_device, (int, str)):
            return default_device

        try:
            return default_device[0]
        except (TypeError, IndexError, KeyError):
            return default_device

    def _maybe_create_resampler(self) -> None:
        if self._native_rate == TARGET_SAMPLE_RATE:
            return
        self._resampler = soxr.ResampleStream(
            self._native_rate,
            TARGET_SAMPLE_RATE,
            CHANNEL_COUNT,
            dtype=SAMPLE_DTYPE,
            quality="HQ",
        )

    def _callback(self, indata, frames, time_info, status) -> None:
        del frames, time_info, status

        chunk = np.asarray(indata[:, 0], dtype=np.float32).copy()
        if self._resampler is not None:
            chunk = self._resampler.resample_chunk(chunk, last=False)
        if chunk.size:
            self._enqueue_chunk(chunk)

    def _enqueue_chunk(self, chunk: np.ndarray) -> None:
        try:
            self._vad_queue.put_nowait(chunk)
        except queue.Full:
            pass