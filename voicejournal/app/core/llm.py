from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import threading
from typing import TypedDict

from voicejournal.app.model_sources import ModelSource


LLM_CONTEXT_SIZE = 4096
QUESTION_MAX_TOKENS = 96
PROSE_MAX_TOKENS = 2048
METADATA_MAX_TOKENS = 256
QUESTION_TEMPERATURE = 0.35
PROSE_TEMPERATURE = 0.2
METADATA_TEMPERATURE = 0.0

QUESTION_SYSTEM_PROMPT = (
    "You are VoiceJournal's follow-up question assistant. "
    "Sound calm, thoughtful, and warmly conversational. "
    'Reply as valid JSON with keys "next_question", "summarize", and optional '
    '"summarize_probability". Use summarize=true only when the session should move '
    "to summary now. Ask exactly one concise question that feels natural to answer out loud. "
    "Ground it in the user's latest turn, vary the phrasing across turns, and invite reflection, "
    "emotion, meaning, people, place, or what still lingers. Avoid generic repeats like "
    '"What happened next?" or "Tell me more about that." unless chronology is truly the best next step. '
    "Keep next_question to one sentence and ideally under 14 words."
)

PROSE_SYSTEM_PROMPT = (
    "You are VoiceJournal's journal summarizer. Turn the user's spoken answers "
    "into a concise first-person journal entry as HTML only. Summarize what "
    "happened and how it felt. Do not critique, evaluate, coach, mention the "
    "conversation, or write a review of the entry. Return clean paragraphs "
    "without markdown fences or explanations."
)

METADATA_SYSTEM_PROMPT = (
    "You are VoiceJournal's metadata assistant. "
    'Reply as valid JSON with keys "title", "mood", and "tags" where tags is an array of strings.'
)

SUMMARIZE_TRUE_PATTERN = re.compile(r'"?summarize"?\s*[:=]\s*true\b', re.IGNORECASE)


class LLMMessage(TypedDict):
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class QuestionResult:
    next_question: str
    summarize: bool
    summarize_probability: float | None = None


@dataclass(frozen=True, slots=True)
class MetadataResult:
    title: str
    mood: str | None = None
    tags: list[str] = field(default_factory=list)


class LLMEngine:
    def __init__(self, llama) -> None:
        self._llama = llama
        self._lock = threading.Lock()

    @classmethod
    def from_model_path(
        cls,
        model_path: Path,
        *,
        llama_factory: Callable[..., object] | None = None,
        n_ctx: int = LLM_CONTEXT_SIZE,
        verbose: bool = False,
    ) -> LLMEngine:
        if llama_factory is None:
            from llama_cpp import Llama

            llama_factory = Llama
        llama = llama_factory(str(model_path), n_ctx=n_ctx, verbose=verbose)
        return cls(llama)

    def question(self, messages: Sequence[LLMMessage]) -> QuestionResult:
        with self._lock:
            response = self._llama.create_chat_completion(
                messages=self._with_system_prompt(QUESTION_SYSTEM_PROMPT, messages),
                response_format={"type": "json_object"},
                temperature=QUESTION_TEMPERATURE,
                max_tokens=QUESTION_MAX_TOKENS,
                stream=False,
            )
        content = self._response_content(response)
        return self.parse_question(content)

    def stream_prose(self, messages: Sequence[LLMMessage]) -> Iterator[str]:
        def iterator() -> Iterator[str]:
            with self._lock:
                stream = self._llama.create_chat_completion(
                    messages=self._with_system_prompt(PROSE_SYSTEM_PROMPT, messages),
                    temperature=PROSE_TEMPERATURE,
                    max_tokens=PROSE_MAX_TOKENS,
                    stream=True,
                )
                yield from self._stream_content(stream)

        return iterator()

    def stream_metadata(self, messages: Sequence[LLMMessage]) -> Iterator[str]:
        def iterator() -> Iterator[str]:
            with self._lock:
                stream = self._llama.create_chat_completion(
                    messages=self._with_system_prompt(METADATA_SYSTEM_PROMPT, messages),
                    response_format={"type": "json_object"},
                    temperature=METADATA_TEMPERATURE,
                    max_tokens=METADATA_MAX_TOKENS,
                    stream=True,
                )
                yield from self._stream_content(stream)

        return iterator()

    def parse_question(self, raw_text: str) -> QuestionResult:
        summarize_hint = self._has_summarize_hint(raw_text)
        try:
            payload = self._parse_json_object(raw_text)
        except ValueError:
            if summarize_hint:
                return QuestionResult(next_question="", summarize=True)
            raise
        next_question = str(payload.get("next_question", "")).strip()
        summarize = self._coerce_bool(payload.get("summarize", False))
        summarize_probability = payload.get("summarize_probability")
        probability_value = None
        if summarize_probability not in (None, ""):
            try:
                probability_value = float(summarize_probability)
            except (TypeError, ValueError):
                probability_value = None
        return QuestionResult(
            next_question=next_question,
            summarize=summarize,
            summarize_probability=probability_value,
        )

    def parse_metadata(self, raw_text: str) -> MetadataResult:
        payload = self._parse_json_object(raw_text)
        title = str(payload.get("title", "")).strip()
        mood_value = payload.get("mood")
        mood = None if mood_value in (None, "") else str(mood_value).strip()
        tags_value = payload.get("tags", [])
        if isinstance(tags_value, str):
            tags = [tag.strip() for tag in tags_value.split(",") if tag.strip()]
        elif isinstance(tags_value, Sequence):
            tags = [str(tag).strip() for tag in tags_value if str(tag).strip()]
        else:
            raise ValueError("Metadata tags must be a list or comma-separated string.")
        return MetadataResult(title=title, mood=mood, tags=tags)

    def _with_system_prompt(
        self,
        system_prompt: str,
        messages: Sequence[LLMMessage],
    ) -> list[LLMMessage]:
        return [{"role": "system", "content": system_prompt}, *list(messages)]

    def _response_content(self, response) -> str:
        return str(response["choices"][0]["message"]["content"])

    def _stream_content(self, stream) -> Iterator[str]:
        for chunk in stream:
            delta = chunk["choices"][0].get("delta", {})
            content = delta.get("content")
            if content:
                yield str(content)

    def _parse_json_object(self, raw_text: str) -> dict[str, object]:
        json_text = self._extract_first_json_object(raw_text)
        payload = json.loads(json_text)
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object response from the LLM.")
        return payload

    def _has_summarize_hint(self, raw_text: str) -> bool:
        lowered = raw_text.lower()
        start = lowered.find("{")
        if start < 0:
            return "summarize" in lowered

        prefix_has_summarize_word = "summarize" in lowered[:start]

        try:
            json_text = self._extract_first_json_object(raw_text)
        except ValueError:
            return prefix_has_summarize_word or bool(SUMMARIZE_TRUE_PATTERN.search(raw_text))

        end = raw_text.find(json_text) + len(json_text)
        return bool(SUMMARIZE_TRUE_PATTERN.search(raw_text[end:]))

    def _coerce_bool(self, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no", ""}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return False

    def _extract_first_json_object(self, raw_text: str) -> str:
        start = raw_text.find("{")
        if start < 0:
            raise ValueError("LLM response did not contain a JSON object.")

        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(raw_text)):
            char = raw_text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return raw_text[start : index + 1]

        raise ValueError("LLM response contained an unterminated JSON object.")


def load_llm_engine(
    source: ModelSource,
    model_path: Path,
    *,
    llama_factory: Callable[..., object] | None = None,
    n_ctx: int = LLM_CONTEXT_SIZE,
) -> LLMEngine:
    if not model_path.is_file():
        raise FileNotFoundError(f"{source.display} is not installed at {model_path}")

    return LLMEngine.from_model_path(model_path, llama_factory=llama_factory, n_ctx=n_ctx)


__all__ = [
    "LLM_CONTEXT_SIZE",
    "LLMMessage",
    "LLMEngine",
    "MetadataResult",
    "QuestionResult",
    "load_llm_engine",
]