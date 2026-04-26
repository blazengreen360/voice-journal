# Google Code Review Principles

## Source

- Google Engineering Practices, "The Standard of Code Review."
- Google Engineering Practices, "What to look for in a code review."
- Working web sources used during setup:
  - <https://google.github.io/eng-practices/review/reviewer/standard.html>
  - <https://google.github.io/eng-practices/review/reviewer/looking-for.html>

## Source Context

- These docs are operational reviewer guidance, not abstract philosophy.
- They are a good fit for the VoiceJournal reviewer agent because they focus on how to judge a change, how to ground comments, and how to balance rigor with delivery progress.
- The strongest idea in the material is that review exists to improve code health over time, not to win arguments or demand perfection.

## Key Ideas

- Technical facts and data overrule opinions and personal preferences.
- The goal of review is to improve overall code health over time.
- Approve once a change is a clear net improvement, even if it is not perfect.
- Review every assigned line in context rather than treating the diff as isolated text.
- Look explicitly at design, functionality, complexity, tests, documentation, and context.

## Practical Caveats

- These practices come from Google's workflow and should be adapted rather than copied mechanically.
- "Approve when it improves code health" does not justify accepting regressions or under-validated risky changes.
- Reviewer speed matters, but not at the expense of missing real defects.

## How VoiceJournal Reviewer Applies It

- Ground findings in evidence from code, docs, or executed checks rather than taste.
- Prioritize defects, regression risk, and code-health decline over polish.
- State review scope clearly when only part of a change or one concern area was examined.