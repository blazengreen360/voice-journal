---
name: context7-library-research
description: 'Use for external library, framework, dependency, packaging, or API research. Context7-first workflow for PySide6, PyInstaller, llama-cpp-python, faster-whisper, kokoro-onnx, soxr, onnxruntime, platformdirs, sqlite, and other third-party docs where current behavior matters.'
---

# Context7 Library Research

## When to Use

- Any task depends on external library behavior or API contracts.
- A version-specific compatibility question matters.
- Packaging, platform behavior, or runtime integration depends on upstream documentation.
- The code or design mentions a third-party library and correctness depends on current docs.

## Default Rule

Always use Context7 MCP Server first for third-party documentation.

Do not rely on model memory for library behavior when Context7 can answer the question.

## Procedure

1. Identify the exact libraries involved.
2. Resolve the library ID with Context7.
3. Fetch focused docs for the specific topic or API surface.
4. Extract only the contract that matters for the current task.
5. Compare the docs to the repo’s assumptions, code, or design docs.
6. Implement or recommend changes based on the documented contract.

## Output Requirements

- Name the library and the topic researched.
- Summarize the relevant contract briefly.
- Call out version-sensitive behavior when it matters.
- State whether the repo’s current assumption matches or conflicts with the docs.

## Notes For VoiceJournal

This project is especially sensitive to external-doc correctness in these areas:

- PySide6 toolbar and window behavior
- PyInstaller packaging and bundled assets
- llama-cpp-python compatibility
- faster-whisper and local model runtime behavior
- kokoro-onnx TTS API behavior
- soxr resampling semantics
- onnxruntime and Silero ONNX expectations
- platformdirs storage paths

If Context7 does not cover the needed detail, say so explicitly and then fall back to official upstream docs.