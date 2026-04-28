from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import queue
from types import SimpleNamespace

import numpy as np
import pytest

from voicejournal.app.core import audio as audio_module
from voicejournal.app.core.audio import AudioStream


@dataclass(frozen=True, slots=True)
class _FakeConfig:
    audio_device: int | str | None = None


class _FakePortAudioError(Exception):
    pass


class _FakeInputOutputPair:
    def __init__(self, input_device, output_device) -> None:
        self._values = (input_device, output_device)

    def __getitem__(self, index: int):
        return self._values[index]


class _FakeInputStream:
    def __init__(self, *, samplerate: float, **kwargs) -> None:
        self.kwargs = kwargs
        self.samplerate = samplerate
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class _FakeInputStreamFactory:
    def __init__(
        self,
        *,
        fail_first: bool = False,
        actual_samplerates: list[float] | None = None,
    ) -> None:
        self._fail_first = fail_first
        self._actual_samplerates = list(actual_samplerates or [])
        self.calls: list[dict[str, object]] = []
        self.streams: list[_FakeInputStream] = []

    def __call__(self, **kwargs) -> _FakeInputStream:
        self.calls.append(dict(kwargs))
        if self._fail_first and len(self.calls) == 1:
            raise _FakePortAudioError("unsupported samplerate")
        stream_kwargs = dict(kwargs)
        requested_samplerate = float(stream_kwargs.pop("samplerate"))
        samplerate = (
            self._actual_samplerates.pop(0)
            if self._actual_samplerates
            else requested_samplerate
        )
        stream = _FakeInputStream(samplerate=samplerate, **stream_kwargs)
        self.streams.append(stream)
        return stream


class _FakeResampler:
    def __init__(self, outputs: list[np.ndarray]) -> None:
        self._outputs = outputs
        self.calls: list[tuple[np.ndarray, bool]] = []

    def resample_chunk(self, chunk: np.ndarray, *, last: bool) -> np.ndarray:
        self.calls.append((chunk.copy(), last))
        if self._outputs:
            return self._outputs.pop(0)
        return chunk


class _FakeResamplerFactory:
    def __init__(self, outputs: list[np.ndarray] | None = None) -> None:
        self._outputs = outputs or []
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.instances: list[_FakeResampler] = []

    def __call__(self, *args, **kwargs) -> _FakeResampler:
        self.calls.append((args, kwargs))
        resampler = _FakeResampler(list(self._outputs))
        self.instances.append(resampler)
        return resampler


def _install_audio_fakes(
    monkeypatch,
    *,
    fail_first: bool = False,
    default_device: object = None,
    queried_samplerate: int = 48_000,
    actual_samplerates: list[float] | None = None,
    resampler_outputs: list[np.ndarray] | None = None,
):
    input_stream_factory = _FakeInputStreamFactory(
        fail_first=fail_first,
        actual_samplerates=actual_samplerates,
    )
    query_calls: list[tuple[int | str | None, str | None]] = []

    def query_devices(device=None, kind=None):
        query_calls.append((device, kind))
        return {"default_samplerate": queried_samplerate}

    fake_sounddevice = SimpleNamespace(
        InputStream=input_stream_factory,
        PortAudioError=_FakePortAudioError,
        query_devices=query_devices,
        default=SimpleNamespace(
            device=default_device if default_device is not None else _FakeInputOutputPair(4, 8)
        ),
    )
    resampler_factory = _FakeResamplerFactory(outputs=resampler_outputs)

    monkeypatch.setattr(audio_module, "sd", fake_sounddevice)
    monkeypatch.setattr(
        audio_module,
        "soxr",
        SimpleNamespace(ResampleStream=resampler_factory),
    )
    return input_stream_factory, resampler_factory, query_calls


def test_audio_stream_uses_direct_16khz_capture_when_supported(monkeypatch) -> None:
    input_stream_factory, resampler_factory, query_calls = _install_audio_fakes(monkeypatch)

    audio_stream = AudioStream(_FakeConfig(audio_device=7), queue.Queue())
    audio_stream.start()

    assert audio_stream.native_rate == 16_000
    assert len(input_stream_factory.calls) == 1
    assert input_stream_factory.calls[0]["samplerate"] == 16_000
    assert input_stream_factory.calls[0]["channels"] == 1
    assert input_stream_factory.calls[0]["dtype"] == "float32"
    assert input_stream_factory.calls[0]["blocksize"] == 512
    assert input_stream_factory.calls[0]["device"] == 7
    assert input_stream_factory.streams[0].started is True
    assert query_calls == []
    assert resampler_factory.calls == []


def test_audio_stream_resamples_when_successful_open_reports_off_rate(monkeypatch) -> None:
    input_stream_factory, resampler_factory, query_calls = _install_audio_fakes(
        monkeypatch,
        actual_samplerates=[16_000.5],
    )

    audio_stream = AudioStream(_FakeConfig(audio_device=2), queue.Queue())

    assert audio_stream.native_rate == pytest.approx(16_000.5)
    assert len(input_stream_factory.calls) == 1
    assert query_calls == []
    assert resampler_factory.calls == [
        ((16_000.5, 16_000, 1), {"dtype": "float32", "quality": "HQ"})
    ]


def test_audio_stream_falls_back_to_native_rate_and_resampler(monkeypatch) -> None:
    input_stream_factory, resampler_factory, query_calls = _install_audio_fakes(
        monkeypatch,
        fail_first=True,
        default_device=_FakeInputOutputPair(3, 9),
        queried_samplerate=47_999.5,
        actual_samplerates=[47_999.5],
    )

    audio_stream = AudioStream(_FakeConfig(), queue.Queue())

    assert audio_stream.native_rate == pytest.approx(47_999.5)
    assert [call["samplerate"] for call in input_stream_factory.calls] == [16_000, 47_999.5]
    assert query_calls == [(3, "input")]
    assert resampler_factory.calls == [
        ((47_999.5, 16_000, 1), {"dtype": "float32", "quality": "HQ"})
    ]


def test_audio_stream_fallback_queries_default_input_kind_when_input_slot_is_unset(monkeypatch) -> None:
    input_stream_factory, resampler_factory, query_calls = _install_audio_fakes(
        monkeypatch,
        fail_first=True,
        default_device=_FakeInputOutputPair(-1, 9),
        queried_samplerate=48_000,
        actual_samplerates=[48_000.0],
    )

    audio_stream = AudioStream(_FakeConfig(), queue.Queue())

    assert audio_stream.native_rate == pytest.approx(48_000.0)
    assert [call["samplerate"] for call in input_stream_factory.calls] == [16_000, 48_000]
    assert query_calls == [(None, "input")]
    assert resampler_factory.calls == [
        ((48_000.0, 16_000, 1), {"dtype": "float32", "quality": "HQ"})
    ]


def test_audio_stream_fallback_uses_reopened_actual_rate_for_resampler_decision(monkeypatch) -> None:
    input_stream_factory, resampler_factory, query_calls = _install_audio_fakes(
        monkeypatch,
        fail_first=True,
        default_device=_FakeInputOutputPair(6, 9),
        queried_samplerate=47_999.5,
        actual_samplerates=[16_000.0],
    )

    audio_stream = AudioStream(_FakeConfig(), queue.Queue())

    assert audio_stream.native_rate == pytest.approx(16_000.0)
    assert [call["samplerate"] for call in input_stream_factory.calls] == [16_000, 47_999.5]
    assert query_calls == [(6, "input")]
    assert resampler_factory.calls == []


def test_audio_stream_fallback_queries_configured_string_device_as_input(monkeypatch) -> None:
    input_stream_factory, resampler_factory, query_calls = _install_audio_fakes(
        monkeypatch,
        fail_first=True,
        queried_samplerate=44_100,
        actual_samplerates=[44_100.0],
    )

    audio_stream = AudioStream(_FakeConfig(audio_device="USB Headset"), queue.Queue())

    assert audio_stream.native_rate == pytest.approx(44_100.0)
    assert [call["samplerate"] for call in input_stream_factory.calls] == [16_000, 44_100]
    assert query_calls == [("USB Headset", "input")]
    assert resampler_factory.calls == [
        ((44_100.0, 16_000, 1), {"dtype": "float32", "quality": "HQ"})
    ]


def test_audio_stream_callback_copies_direct_16khz_audio_into_vad_queue(monkeypatch) -> None:
    input_stream_factory, _resampler_factory, _query_calls = _install_audio_fakes(monkeypatch)
    vad_queue: queue.Queue[np.ndarray] = queue.Queue()
    audio_stream = AudioStream(_FakeConfig(audio_device=5), vad_queue)

    indata = np.array([[0.1], [0.2], [0.3]], dtype=np.float32)
    audio_stream._callback(indata, 3, None, None)
    indata[0, 0] = 9.9

    queued = vad_queue.get_nowait()
    assert input_stream_factory.calls[0]["device"] == 5
    assert np.array_equal(queued, np.array([0.1, 0.2, 0.3], dtype=np.float32))


def test_audio_stream_callback_resamples_native_audio_before_queueing(monkeypatch) -> None:
    resampled = np.array([0.4, 0.5], dtype=np.float32)
    _input_stream_factory, resampler_factory, _query_calls = _install_audio_fakes(
        monkeypatch,
        fail_first=True,
        queried_samplerate=48_000,
        resampler_outputs=[resampled],
    )
    vad_queue: queue.Queue[np.ndarray] = queue.Queue()
    audio_stream = AudioStream(_FakeConfig(), vad_queue)

    audio_stream._callback(np.array([[1.0], [2.0]], dtype=np.float32), 2, None, None)

    queued = vad_queue.get_nowait()
    resampler = resampler_factory.instances[0]
    assert np.array_equal(queued, resampled)
    assert len(resampler.calls) == 1
    assert np.array_equal(resampler.calls[0][0], np.array([1.0, 2.0], dtype=np.float32))
    assert resampler.calls[0][1] is False


def test_audio_stream_callback_drops_zero_length_resampler_output(monkeypatch) -> None:
    _input_stream_factory, _resampler_factory, _query_calls = _install_audio_fakes(
        monkeypatch,
        fail_first=True,
        queried_samplerate=48_000,
        resampler_outputs=[np.zeros(0, dtype=np.float32)],
    )
    vad_queue: queue.Queue[np.ndarray] = queue.Queue()
    audio_stream = AudioStream(_FakeConfig(), vad_queue)

    audio_stream._callback(np.array([[1.0], [2.0]], dtype=np.float32), 2, None, None)

    with pytest.raises(queue.Empty):
        vad_queue.get_nowait()


def test_audio_stream_stop_drains_resampler_tail_with_last_true(monkeypatch) -> None:
    tail = np.array([0.6, 0.7], dtype=np.float32)
    input_stream_factory, resampler_factory, _query_calls = _install_audio_fakes(
        monkeypatch,
        fail_first=True,
        queried_samplerate=44_100,
        resampler_outputs=[tail],
    )
    vad_queue: queue.Queue[np.ndarray] = queue.Queue()
    audio_stream = AudioStream(_FakeConfig(audio_device="Built-in Mic"), vad_queue)

    audio_stream.stop()

    queued = vad_queue.get_nowait()
    stream = input_stream_factory.streams[0]
    resampler = resampler_factory.instances[0]
    assert stream.stopped is True
    assert stream.closed is True
    assert np.array_equal(queued, tail)
    assert resampler.calls[0][1] is True
    assert resampler.calls[0][0].dtype == np.float32
    assert resampler.calls[0][0].size == 0