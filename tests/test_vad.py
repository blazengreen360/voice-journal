import hashlib
import io
import queue
import threading
import time
import wave

import numpy as np
import onnxruntime as ort
import pytest

from voicejournal.app.bundled_assets import SILERO_VAD_SHA256, bundled_silero_vad_model
from voicejournal.app.core.vad import CONTEXT_SIZE, FRAME_SIZE, SAMPLE_RATE, VADParams, VADWorker


class _FakeOrtSession:
    def __init__(
        self,
        *,
        probabilities: list[float] | None = None,
        state_values: list[float] | None = None,
        outputs: list[tuple[np.ndarray, np.ndarray]] | None = None,
        gate: threading.Event | None = None,
    ) -> None:
        self._probabilities = list(probabilities or [])
        self._state_values = list(state_values or [])
        self._outputs = list(outputs or [])
        self._gate = gate
        self.calls: list[dict[str, np.ndarray]] = []

    def run(self, output_names, inputs):
        del output_names
        if self._gate is not None:
            self._gate.set()
        copied_inputs = {
            key: np.array(value, copy=True)
            for key, value in inputs.items()
        }
        self.calls.append(copied_inputs)
        if self._outputs:
            output, state = self._outputs.pop(0)
            return np.array(output, copy=True), np.array(state, copy=True)

        probability = self._probabilities.pop(0) if self._probabilities else 0.0
        state_value = self._state_values.pop(0) if self._state_values else float(len(self.calls))
        return (
            np.array([[probability]], dtype=np.float32),
            np.full((2, 1, 128), state_value, dtype=np.float32),
        )


def _read_wav(buffer: io.BytesIO) -> tuple[tuple[int, int, int], np.ndarray]:
    with wave.open(buffer, "rb") as wav_file:
        params = (
            wav_file.getnchannels(),
            wav_file.getsampwidth(),
            wav_file.getframerate(),
        )
        frames = wav_file.readframes(wav_file.getnframes())
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / np.iinfo(np.int16).max
    return params, samples


def _wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for predicate to become true.")


def test_bundled_silero_model_is_present_and_pinned() -> None:
    model = bundled_silero_vad_model()

    assert model.is_file()
    assert hashlib.sha256(model.read_bytes()).hexdigest() == SILERO_VAD_SHA256


def test_vad_worker_aggregates_arbitrary_chunks_into_exact_frames() -> None:
    session = _FakeOrtSession()
    worker = VADWorker(queue.Queue(), lambda _: None, VADParams(), session)

    worker._process_chunk(np.ones(300, dtype=np.float32))
    worker._process_chunk(np.ones(212, dtype=np.float32))

    assert len(session.calls) == 1
    assert session.calls[0]["input"].shape == (1, CONTEXT_SIZE + FRAME_SIZE)
    assert worker._ring.size == 0


def test_vad_worker_persists_context_and_state_between_frames() -> None:
    session = _FakeOrtSession(probabilities=[0.1, 0.2], state_values=[3.0, 7.0])
    worker = VADWorker(queue.Queue(), lambda _: None, VADParams(), session)
    first_frame = np.linspace(-0.5, 0.5, FRAME_SIZE, dtype=np.float32)
    second_frame = np.linspace(0.5, -0.5, FRAME_SIZE, dtype=np.float32)

    worker._process_chunk(first_frame)
    worker._process_chunk(second_frame)

    assert np.array_equal(session.calls[0]["input"][0, :CONTEXT_SIZE], np.zeros(CONTEXT_SIZE, dtype=np.float32))
    assert np.array_equal(session.calls[1]["input"][0, :CONTEXT_SIZE], first_frame[-CONTEXT_SIZE:])
    assert np.all(session.calls[1]["state"] == np.float32(3.0))


def test_vad_worker_reset_for_new_session_clears_state_context_and_ring() -> None:
    session = _FakeOrtSession(state_values=[5.0, 9.0])
    worker = VADWorker(queue.Queue(), lambda _: None, VADParams(), session)

    worker._process_chunk(np.ones(FRAME_SIZE + 100, dtype=np.float32))
    assert len(session.calls) == 1
    assert worker._ring.size == 100
    assert np.all(worker._state == np.float32(5.0))

    worker.reset_for_new_session()
    worker._process_chunk(np.ones(FRAME_SIZE, dtype=np.float32))

    assert np.all(worker._state == np.float32(9.0))
    assert np.array_equal(session.calls[1]["input"][0, :CONTEXT_SIZE], np.zeros(CONTEXT_SIZE, dtype=np.float32))
    assert np.array_equal(session.calls[1]["state"], np.zeros((2, 1, 128), dtype=np.float32))


def test_vad_worker_reset_for_new_session_clears_inflight_segment() -> None:
    emitted: list[io.BytesIO] = []
    params = VADParams(min_speech_duration_ms=0)
    probabilities = [0.8, 0.8] + [0.0] * 5
    session = _FakeOrtSession(probabilities=probabilities)
    worker = VADWorker(queue.Queue(), emitted.append, params, session)

    worker._process_chunk(np.full(FRAME_SIZE, 0.2, dtype=np.float32))
    worker._process_chunk(np.full(FRAME_SIZE, 0.2, dtype=np.float32))
    worker.reset_for_new_session()

    for _ in range(5):
        worker._process_chunk(np.zeros(FRAME_SIZE, dtype=np.float32))

    assert emitted == []


def test_vad_worker_muting_drops_buffered_partial_audio() -> None:
    session = _FakeOrtSession()
    worker = VADWorker(queue.Queue(), lambda _: None, VADParams(), session)

    worker._process_chunk(np.ones(300, dtype=np.float32))
    worker.set_muted(True)
    worker._process_chunk(np.ones(400, dtype=np.float32))
    worker.set_muted(False)
    worker._process_chunk(np.ones(212, dtype=np.float32))

    assert session.calls == []
    assert worker._ring.size == 212


def test_vad_worker_thread_consumes_queue_until_stopped() -> None:
    gate = threading.Event()
    session = _FakeOrtSession(gate=gate)
    vad_queue: queue.Queue[np.ndarray] = queue.Queue()
    worker = VADWorker(vad_queue, lambda _: None, VADParams(), session, poll_interval=0.01)

    worker.start()
    vad_queue.put(np.ones(FRAME_SIZE, dtype=np.float32))

    assert gate.wait(timeout=1.0)

    worker.stop()
    worker.join(timeout=1.0)
    assert len(session.calls) == 1
    assert not worker.is_alive()


def test_vad_worker_thread_survives_inference_failure() -> None:
    class _FlakyOrtSession:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, output_names, inputs):
            del output_names, inputs
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("session exploded")
            return (
                np.array([[0.0]], dtype=np.float32),
                np.zeros((2, 1, 128), dtype=np.float32),
            )

    errors: list[str] = []
    session = _FlakyOrtSession()
    vad_queue: queue.Queue[np.ndarray] = queue.Queue()
    worker = VADWorker(
        vad_queue,
        lambda _: None,
        VADParams(),
        session,
        on_error=lambda exc: errors.append(str(exc)),
        poll_interval=0.01,
    )

    worker.start()
    vad_queue.put(np.ones(FRAME_SIZE, dtype=np.float32))
    _wait_until(lambda: errors == ["session exploded"])
    vad_queue.put(np.ones(FRAME_SIZE, dtype=np.float32))
    _wait_until(lambda: session.calls == 2)

    worker.stop()
    worker.join(timeout=1.0)

    assert errors == ["session exploded"]
    assert not worker.is_alive()


def test_vad_worker_thread_survives_speech_end_callback_failure() -> None:
    callback_calls = 0
    errors: list[str] = []
    emitted: list[io.BytesIO] = []

    def on_speech_end(segment: io.BytesIO) -> None:
        nonlocal callback_calls
        callback_calls += 1
        if callback_calls == 1:
            raise RuntimeError("callback exploded")
        emitted.append(segment)

    probabilities = ([0.8] * 8 + [0.1] * 5) * 2
    session = _FakeOrtSession(probabilities=probabilities)
    vad_queue: queue.Queue[np.ndarray] = queue.Queue()
    worker = VADWorker(
        vad_queue,
        on_speech_end,
        VADParams(),
        session,
        on_error=lambda exc: errors.append(str(exc)),
        poll_interval=0.01,
    )

    worker.start()
    for _ in probabilities:
        vad_queue.put(np.full(FRAME_SIZE, 0.25, dtype=np.float32))

    _wait_until(lambda: callback_calls == 2)

    worker.stop()
    worker.join(timeout=1.0)

    assert errors == ["callback exploded"]
    assert len(emitted) == 1
    assert not worker.is_alive()


def test_vad_worker_emits_speech_end_wav_after_sustained_speech_then_silence() -> None:
    emitted: list[io.BytesIO] = []
    probabilities = [0.8] * 8 + [0.1] * 5
    session = _FakeOrtSession(probabilities=probabilities)
    worker = VADWorker(queue.Queue(), emitted.append, VADParams(), session)
    frame = np.full(FRAME_SIZE, 0.25, dtype=np.float32)

    for _ in probabilities:
        worker._process_chunk(frame)

    assert len(emitted) == 1
    wav_params, wav_samples = _read_wav(emitted[0])
    assert wav_params == (1, 2, SAMPLE_RATE)
    assert wav_samples.size >= FRAME_SIZE * 8
    assert np.max(np.abs(wav_samples)) > 0.0


def test_vad_worker_threshold_floor_still_allows_speech_end() -> None:
    emitted: list[io.BytesIO] = []
    probabilities = [0.9] * 8 + [0.0] * 5
    session = _FakeOrtSession(probabilities=probabilities)
    worker = VADWorker(queue.Queue(), emitted.append, VADParams(threshold=0.1), session)

    for _ in probabilities:
        worker._process_chunk(np.full(FRAME_SIZE, 0.2, dtype=np.float32))

    assert len(emitted) == 1


def test_vad_worker_zero_pad_keeps_only_speech_frames() -> None:
    emitted: list[io.BytesIO] = []
    params = VADParams(min_speech_duration_ms=0, speech_pad_ms=0)
    probabilities = [0.0, 0.8, 0.8] + [0.0] * 5
    session = _FakeOrtSession(probabilities=probabilities)
    worker = VADWorker(queue.Queue(), emitted.append, params, session)
    frame_values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]

    for value in frame_values:
        worker._process_chunk(np.full(FRAME_SIZE, value, dtype=np.float32))

    assert len(emitted) == 1
    _wav_params, wav_samples = _read_wav(emitted[0])
    frame_means = [
        float(np.mean(wav_samples[index : index + FRAME_SIZE]))
        for index in range(0, wav_samples.size, FRAME_SIZE)
    ]
    assert len(frame_means) == 2
    assert frame_means == pytest.approx([0.2, 0.3], abs=0.02)


def test_bundled_silero_model_runs_with_expected_tensor_contract() -> None:
    session = ort.InferenceSession(
        str(bundled_silero_vad_model()),
        providers=["CPUExecutionProvider"],
    )
    worker = VADWorker(queue.Queue(), lambda _: None, VADParams(), session)

    probability = worker._infer(np.zeros(FRAME_SIZE, dtype=np.float32))

    assert 0.0 <= probability <= 1.0
    assert worker._state.shape == (2, 1, 128)
    assert worker._context.shape == (1, CONTEXT_SIZE)
