---
name: explore-plan-verify
description: 'Use for multi-file features, refactors, non-trivial bugs, architectural changes, spikes, or any task where Anthropic best practice says explore first, then plan, then code, then verify.'
---

# Explore Plan Verify

## When to Use

- The task spans multiple files.
- The correct implementation path is not yet obvious.
- A refactor or bug fix needs careful scoping.
- The work has meaningful regression risk.
- The task needs explicit verification criteria.

## Workflow

1. Explore

- Read only enough to identify the owning code path.
- Gather the smallest set of facts needed to avoid solving the wrong problem.
- If research will be verbose, isolate it with subagents or focused searches.

2. Plan

- State the target behavior.
- Identify the narrowest slice that can be changed safely.
- Define the verification step before editing when possible.

3. Implement

- Make the smallest production-quality change.
- Keep edits aligned with existing patterns.
- Avoid unrelated cleanup unless it blocks the task.

4. Verify

- Prefer focused tests or targeted commands.
- Use compile, lint, or typecheck if behavior-scoped tests are unavailable.
- Use diff-only review only when no executable validation exists.

## Verification Standard

Anthropic’s highest-leverage rule applies here:

- give the agent a way to verify its work

That means every non-trivial task should end with at least one concrete validation step whenever the environment makes it possible.

## Output Requirements

- What was explored
- What plan was chosen
- What changed
- How it was verified
- Any remaining risk or next step