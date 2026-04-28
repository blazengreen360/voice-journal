from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import io
import math
import queue
import threading
import wave

import numpy as np
import onnxruntime as ort


FRAME_SIZE = 512
CONTEXT_SIZE = 64
SAMPLE_RATE = 16_000
NEGATIVE_THRESHOLD_OFFSET = 0.15


@dataclass(frozen=True, slots=True)
class VADParams:
    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 100
    speech_pad_ms: int = 30

    def __post_init__(self) -> None:
        if not 0.1 <= self.threshold <= 0.9:
            raise ValueError("VAD threshold must be between 0.1 and 0.9.")
        if self.min_speech_duration_ms < 0:
            raise ValueError("min_speech_duration_ms must be non-negative.")
        if self.min_silence_duration_ms < 0:
            raise ValueError("min_silence_duration_ms must be non-negative.")
        if self.speech_pad_ms < 0:
            raise ValueError("speech_pad_ms must be non-negative.")

    @property
    def negative_threshold(self) -> float:
        return max(self.threshold - NEGATIVE_THRESHOLD_OFFSET, 0.01)

    @property
    def min_speech_samples(self) -> int:
        return int(SAMPLE_RATE * self.min_speech_duration_ms / 1000)

    @property
    def min_silence_samples(self) -> int:
        return int(SAMPLE_RATE * self.min_silence_duration_ms / 1000)

    @property
    def speech_pad_frames(self) -> int:
        if self.speech_pad_ms == 0:
            return 0
        speech_pad_samples = SAMPLE_RATE * self.speech_pad_ms / 1000
        return max(1, math.ceil(speech_pad_samples / FRAME_SIZE))


class VADWorker(threading.Thread):
    def __init__(
        self,
        vad_queue: queue.Queue[np.ndarray],
        on_speech_end,
        params: VADParams,
        ort_session: ort.InferenceSession,
        *,
        on_error=None,
        poll_interval: float = 0.1,
    ) -> None:
        super().__init__(daemon=True)
        self._q = vad_queue
        self._on_speech_end = on_speech_end
        self._params = params
        self._session = ort_session
        self._on_error = on_error
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._muted = False
        self._sr_tensor = np.array([SAMPLE_RATE], dtype=np.int64)
        self._padding_frames: deque[np.ndarray] = deque(maxlen=self._params.speech_pad_frames)
        self._reset_runtime_state()
        self._reset_segment_state()

    def reset_for_new_session(self) -> None:
        with self._lock:
            self._reset_runtime_state()
            self._reset_segment_state()

    def set_muted(self, muted: bool) -> None:
        with self._lock:
            self._muted = muted
            if muted:
                # Drop partial user audio so assistant playback cannot leak into the next turn.
                self._reset_runtime_state()
                self._reset_segment_state()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                chunk = self._q.get(timeout=self._poll_interval)
            except queue.Empty:
                continue

            try:
                self._process_chunk(chunk)
            except Exception as exc:
                if self._on_error is None:
                    continue
                try:
                    self._on_error(exc)
                except Exception:
                    continue

    def _process_chunk(self, chunk: np.ndarray) -> None:
        samples = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return

        completed_segments: list[io.BytesIO] = []
        with self._lock:
            if self._muted:
                return

            self._ring = np.concatenate([self._ring, samples])
            while self._ring.size >= FRAME_SIZE:
                frame = self._ring[:FRAME_SIZE].copy()
                self._ring = self._ring[FRAME_SIZE:]
                probability = self._infer(frame)
                segment = self._drive_state_machine(probability, frame)
                if segment is not None:
                    completed_segments.append(segment)

        for segment in completed_segments:
            self._on_speech_end(segment)

    def _infer(self, frame: np.ndarray) -> float:
        if frame.shape != (FRAME_SIZE,):
            raise ValueError(f"Expected a {FRAME_SIZE}-sample frame, got shape {frame.shape!r}.")

        model_input = np.concatenate(
            [self._context, frame.reshape(1, -1).astype(np.float32, copy=False)],
            axis=1,
        )
        output, new_state = self._session.run(
            None,
            {
                "input": model_input,
                "state": self._state,
                "sr": self._sr_tensor,
            },
        )

        speech_probability = np.asarray(output, dtype=np.float32)
        next_state = np.asarray(new_state, dtype=np.float32)
        if speech_probability.shape != (1, 1):
            raise ValueError(
                "Silero VAD output must have shape (1, 1); "
                f"got {speech_probability.shape!r}."
            )
        if next_state.shape != (2, 1, 128):
            raise ValueError(
                "Silero VAD state output must have shape (2, 1, 128); "
                f"got {next_state.shape!r}."
            )

        self._state = next_state.copy()
        self._context = model_input[:, -CONTEXT_SIZE:].copy()
        return float(speech_probability[0, 0])

    def _drive_state_machine(self, probability: float, frame: np.ndarray) -> io.BytesIO | None:
        self._current_sample += FRAME_SIZE
        frame_copy = frame.copy()

        if probability >= self._params.threshold:
            if self._temp_end:
                self._speech_frames.extend(self._trailing_frames)
                self._trailing_frames.clear()
            self._temp_end = 0
            if not self._triggered:
                self._triggered = True
                self._speech_frames = list(self._padding_frames)
                self._padding_frames.clear()
            self._speech_frames.append(frame_copy)
            return None

        if self._triggered and probability >= self._params.negative_threshold:
            if self._temp_end:
                self._speech_frames.extend(self._trailing_frames)
                self._trailing_frames.clear()
            self._temp_end = 0
            self._speech_frames.append(frame_copy)
            return None

        if not self._triggered:
            self._padding_frames.append(frame_copy)
            return None

        if self._temp_end == 0:
            self._temp_end = self._current_sample
        self._trailing_frames.append(frame_copy)
        if self._current_sample - self._temp_end < self._params.min_silence_samples:
            return None

        pad_frames = min(len(self._trailing_frames), self._params.speech_pad_frames)
        segment = self._finalize_speech(self._trailing_frames[:pad_frames])
        self._padding_frames.clear()
        return segment

    def _finalize_speech(self, trailing_frames: list[np.ndarray] | None = None) -> io.BytesIO | None:
        frames = list(self._speech_frames)
        if trailing_frames:
            frames.extend(trailing_frames)
        speech = np.concatenate(frames) if frames else np.zeros(0, dtype=np.float32)
        self._reset_segment_state(clear_current_sample=False)
        if speech.size < self._params.min_speech_samples:
            return None
        return self._encode_wav(speech)

    def _encode_wav(self, samples: np.ndarray) -> io.BytesIO:
        clipped = np.clip(samples, -1.0, 1.0)
        pcm = (clipped * np.iinfo(np.int16).max).astype("<i2", copy=False)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(pcm.tobytes())
        buffer.seek(0)
        return buffer

    def _reset_runtime_state(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, CONTEXT_SIZE), dtype=np.float32)
        self._ring = np.zeros(0, dtype=np.float32)

    def _reset_segment_state(self, *, clear_current_sample: bool = True) -> None:
        self._triggered = False
        self._temp_end = 0
        self._speech_frames: list[np.ndarray] = []
        self._trailing_frames: list[np.ndarray] = []
        self._padding_frames.clear()
        if clear_current_sample:
            self._current_sample = 0