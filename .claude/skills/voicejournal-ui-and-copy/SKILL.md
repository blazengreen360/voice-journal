---
name: voicejournal-ui-and-copy
description: 'Use for VoiceJournal screen work, PySide6 UI behavior, premium desktop UX fit, archive continuity, session finish-path design, photo hierarchy, settings readiness, and plain-language user copy.'
---

# VoiceJournal UI And Copy

## When to Use

- Editing or implementing Home, Session, Review, Entry Viewer, Settings, or Lightbox.
- Writing or revising user-facing copy.
- Translating wireframes into PySide6 behavior.
- Checking that the UI matches the current wireframe decisions.

## Current Product Rules

- Keep the full month view as the emotional center of the archive.
- Preserve selected month, selected day, and list position when opening and closing entries.
- Session must have a visible `Finish Entry` path in the main UI.
- `Add Photo` stays inline, visible, and non-interruptive.
- Review primary actions must use plain language tied to the user’s goal.
- Photos should read as part of the journal page, not as side attachments.
- Settings should lead with readiness before subsystem detail.
- Lightbox should be quiet and immersive, without tutorial-style persistent instructions.

## Copy Rules

- Talk about the user’s goal, not internal state.
- Avoid implementation jargon in the main UI.
- Prefer calm action words such as `Finish`, `Save`, `Back`, and `Add Photo`.
- Keep privacy copy simple and credible.

## Platform Rules

- Respect native behavior on macOS, Windows, and Linux.
- Keep body structure consistent across platforms.
- Let toolbar density, spacing, and chrome emphasis adapt by OS family.

## Output Requirements

- State which screen or UI surface changed.
- State which pass-3 decision the change supports.
- Call out any copy that was simplified to remove internal jargon.
- Verify behavior with the narrowest feasible UI or logic check.