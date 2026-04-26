# VoiceJournal Wireframe Review Pass 2

## Purpose

This document is a second review pass on the current VoiceJournal wireframe direction.
It is self-contained and is intended to answer two questions:

1. What still needs improvement before implementation begins?
2. What should the next enhancement iteration change to make the app feel clearer, calmer, and more premium?

Scope covered in this pass:

- Home
- Session
- Review
- Entry Viewer
- Settings
- Photo Lightbox
- Cross-platform desktop fit for macOS, Windows, and Linux

## Product Intent

VoiceJournal is a private desktop journaling app centered on speaking, reflection, and returning to past entries.
The experience should feel premium in a quiet way:

- calm rather than flashy
- clear rather than technical
- native to each operating system rather than custom for its own sake
- supportive of reflection, browsing, and revision without making the user manage system complexity

## What Is Already Working

- The overall flow is strong: Home to Session to Review to Entry Viewer to Settings.
- The product has a clear emotional direction. It is quieter and more reflective than a typical utility app.
- Home already treats the archive as important, which is correct for a journaling product.
- Session has a strong visual center and avoids looking like a chat app.
- Review and Entry Viewer both move toward an editorial reading experience rather than a control-heavy tool.
- The design is increasingly user-centered and has reduced a large amount of visible implementation jargon.

## Findings

### 1. High — The end of the spoken session still needs a clearer finish path

The Session screen is visually focused, but it still does not communicate the transition from conversation to draft strongly enough.
The user can see how to keep talking, but the path to “I am done, take me to the written version” is not obvious enough.

Why this matters:

- voice-first products fail when users do not know how to complete a task
- journaling sessions should end with confidence, not uncertainty
- users should not need to infer how Review is reached

Required change:

- Add a dedicated, always-visible secondary action in Session for ending the conversation and moving into Review.
- The action should use plain language such as `Finish Entry` or `Go to Draft`.
- It should live in the main interface, not in a hidden menu.

### 2. High — Archive context is still too fragile when moving across screens

Home now supports month and year stepping, which is the right direction, but the archive experience still needs stronger continuity.
If a user opens an entry from a selected day, the app should remember that context when they return.

Why this matters:

- a journal is an archive product, not only a capture product
- losing month or day context breaks the feeling of browsing a personal timeline
- returning from Entry Viewer should feel like stepping back into the same place, not restarting the search

Required change:

- Preserve selected month, selected day, and list scroll position when entering and leaving Entry Viewer.
- Make the return action read like a contextual back action, such as `Back to April` or `Back to Apr 24`.
- Keep the full month view visible as the primary archive surface.

### 3. Medium — Calendar cells do not yet tell enough of the archive story

The month grid is readable, but the cells currently communicate only light presence, not meaning.
For days with multiple entries, photos, or more important moments, the user needs a slightly richer signal.

Why this matters:

- journals are often revisited by memory and rhythm, not only by exact date
- a month view becomes more valuable when it hints at density and importance
- a premium archive should feel informative at a glance without becoming noisy

Required change:

- Keep the clean month grid.
- Add a subtle count or stacked marker for days with multiple entries.
- Reserve stronger emphasis for the selected day.
- Avoid turning the calendar into a heatmap dashboard.

### 4. Medium — Session header controls still compete with the main emotional focus

The center of the Session screen is working, but the header still carries enough activity to pull attention upward.
The result is not cluttered, but it can be calmer.

Why this matters:

- speaking and listening need a stable focal area
- premium voice interfaces should reduce peripheral decision-making during the active moment
- infrequent controls should not visually compete with the primary task

Required change:

- Keep close/cancel available.
- De-emphasize voice selection during the live session, or move it into a quieter overflow.
- Use the bottom action area for the choices that matter in the moment: add photo, finish entry, or leave.

### 5. Medium — Review needs a clearer distinction between “refining” and “ready to keep”

The Review screen is close, but the primary action language is still too static for the moment.
When the user is editing, the button should feel like a save action. When the draft is still settling, the language can be softer.

Why this matters:

- journaling is emotionally sensitive; users need assurance about what happens next
- strong editorial layouts still need decisive completion language
- one generic button label is unlikely to fit every review state

Required change:

- Use state-based user language:
  - `Save Entry` when the draft is ready
  - `Finish and Save` when the app still has a little work left
- Keep the secondary path visible for users who want to continue editing.

### 6. Medium — The photo experience is better, but it still needs stronger hierarchy

Moving photo add into the main Session UI was the right correction.
The next improvement is to make photos feel like a deliberate part of the entry instead of a side attachment.

Why this matters:

- photos often anchor memory better than text snippets
- the app should support a reflective record, not just a transcript with extras
- better photo hierarchy improves the perceived quality of both Review and Entry Viewer

Required change:

- Treat photos as a small visual shelf in Review.
- Allow captioning or ordering without crowding the writing area.
- In Entry Viewer, show the photos as a structured gallery rather than a loose strip when more than two images exist.

### 7. Medium — The lightbox still explains behavior instead of embodying it

The lightbox layout is strong, but persistent instructional copy at the bottom weakens the premium feel.
Premium interfaces usually show the controls and trust the interaction model.

Why this matters:

- instructional chrome makes the app feel less finished
- users should discover the experience from layout and affordances, not from a permanent help sentence
- a calm lightbox should feel immersive, not tutorial-like

Required change:

- Remove persistent behavior instructions from the lightbox.
- Keep close, previous, and next visually obvious.
- Put any extra actions in a quiet overflow or contextual menu.

### 8. Medium — Settings needs a stronger readiness summary

The Settings screen is much more approachable now, but it still reads like a list of subsystems rather than the setup state of one app.

Why this matters:

- people want to know whether the app is ready, not just what components exist
- setup should feel finite and reassuring
- a premium product reduces interpretation work

Required change:

- Add a summary card at the top such as `VoiceJournal is ready` or `Two downloads still in progress`.
- Keep detailed rows below that summary.
- Make it easy to understand what needs attention first.

### 9. Low — Screen-specific platform fit needs one more explicit pass

The platform direction is correct at a high level, but screen-by-screen differences are still slightly under-defined.
That is manageable now, but it will become expensive if left vague until implementation.

Why this matters:

- macOS, Windows, and Linux should share one product, not one identical chrome layout
- toolbar density and search placement should be locked before engineering starts
- premium fit comes from many small native decisions, not one big styling pass

Required change:

- Lock Home, Review, and Viewer toolbar layouts separately for each OS family.
- Keep the body content invariant.
- Allow chrome, spacing, and emphasis to adapt by platform.

## Enhancement Iteration

### Iteration Theme

Calm Archive, Clear Finish.

This iteration should improve two specific qualities:

- make long-term browsing feel more grounded and continuous
- make entry completion feel more confident and more visible

### Experience Goals

1. A user can move through months and years without losing their place.
2. A user always knows how to finish a spoken entry.
3. A user never sees internal implementation language in the main app experience.
4. The archive, draft, and viewer each feel intentionally different in tone.
5. The app keeps its reflective character while becoming easier to navigate.

## Proposed Screen Changes

### Home

Keep:

- full month view as the main archive surface
- simple month and year stepping
- entry list below the calendar

Enhance:

- Add richer day indicators for multiple entries.
- Add a selected-day summary row directly below the calendar before the full list.
- Preserve the selected day when returning from Viewer.
- Make the year stepping feel calm and deliberate rather than like a utility control.

Target result:

- Home should feel like opening a personal archive, not filtering a database.

### Session

Keep:

- strong centered focal point
- inline Add Photo action
- restrained overall layout

Enhance:

- Add a dedicated `Finish Entry` action in the main UI.
- Reduce emphasis on infrequent controls in the header.
- Let the bottom action zone carry the decisions users make in the moment.
- Keep copy emotionally supportive and brief.

Target result:

- Session should feel guided, not mysterious.

### Review

Keep:

- editorial page feeling
- large writing surface
- compact metadata

Enhance:

- Use state-based primary action copy.
- Introduce a clearer separation between text editing and photo handling.
- Give tags an overflow pattern that still feels elegant and readable.
- Keep the screen’s emotional tone closer to “shaping a journal page” than “completing a form.”

Target result:

- Review should feel like a final polishing table, not a staging area.

### Entry Viewer

Keep:

- quiet reading-first layout
- previous/next affordances
- simple metadata presentation

Enhance:

- Make the back action contextual to the month or day the user came from.
- Preserve scroll and archive context on return.
- Upgrade the photo section into a more deliberate gallery when entries have several images.
- Keep navigation visible without overpowering the writing.

Target result:

- Viewer should feel like returning to a finished journal page in a living archive.

### Settings

Keep:

- plain-language capability labels
- clear privacy reassurance
- grouped setup areas

Enhance:

- Add a top-level readiness summary.
- Reduce the feeling of separate technical subsystems.
- Surface the next recommended action first.
- Keep detailed download or storage information secondary.

Target result:

- Settings should feel like a calm setup companion, not a diagnostics console.

### Photo Lightbox

Keep:

- large central image
- generous left and right hit areas
- quiet close control

Enhance:

- Remove instructional caption copy.
- Rely on visible controls and layout for discoverability.
- Keep secondary actions hidden until needed.

Target result:

- Lightbox should feel immersive and premium.

## Copy Direction For The Next Iteration

User-facing copy should continue to follow these rules:

- describe the user’s goal, not the system’s internal state
- prefer calm verbs: `Finish`, `Keep going`, `Save`, `Back`, `Add Photo`
- avoid engineering words in the main UI
- only mention background work when the user needs to act on it
- keep privacy language simple and credible

Examples:

- good: `Finish Entry`
- good: `VoiceJournal is ready`
- good: `Back to April`
- avoid: `state machine`
- avoid: `verification required`
- avoid: `queued`
- avoid: `interrupting overlay`

## Recommended Decisions To Lock Next

1. Session gets a visible `Finish Entry` action.
2. Home preserves archive context when navigating into and out of Viewer.
3. Calendar cells can show multiple-entry density without becoming a data dashboard.
4. Review primary action uses state-based plain language.
5. Lightbox loses permanent instructional text.
6. Settings gets a single readiness summary at the top.
7. Screen-level toolbar patterns are locked per OS family before implementation.

## Acceptance Checklist For The Next Wireframe Revision

- A user can browse multiple years while staying anchored in a full month view.
- A user can open an entry and return to the same month, day, and list position.
- A user can tell how to end a voice session without guesswork.
- Review has a clear primary completion action in plain language.
- The lightbox no longer reads like a tutorial.
- Settings communicates overall readiness before details.
- No visible screen copy exposes internal application mechanics.

## Recommendation

The current direction is strong enough to continue, but it should not be treated as final yet.
One more enhancement iteration is justified before implementation starts.

That iteration should focus on:

- archive continuity
- session completion clarity
- calmer platform-specific chrome
- stronger photo hierarchy
- fully user-centered completion language

If those adjustments are made, the design will be materially closer to a premium desktop product that feels considered on macOS, Windows, and Linux.