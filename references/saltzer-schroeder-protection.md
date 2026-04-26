# Saltzer and Schroeder Protection Principles

## Source

- Jerome H. Saltzer and Michael D. Schroeder, "The Protection of Information in Computer Systems," *Proceedings of the IEEE*, 63(9), 1975.
- Working web sources used during setup:
  - <https://web.mit.edu/Saltzer/www/publications/protection/>
  - <https://web.mit.edu/Saltzer/www/publications/protection/Basic.html>

## Source Context

- The paper is broader than the usual summary of a few security slogans. It is a tutorial treatment of protection and authentication in multi-user computing systems.
- The authors distinguish privacy, security, protection, and authentication rather than collapsing them into one term.
- Their core contribution for design work is a set of engineering principles meant to reduce flaws in systems that cannot be proven perfect.

## Key Ideas

- Economy of mechanism: keep the design as simple and small as possible, especially where flaws would be hard to notice during normal use.
- Fail-safe defaults: decide access by permission, not by trying to enumerate every exclusion.
- Complete mediation: check authority at each relevant access, including unusual lifecycle paths such as initialization, recovery, shutdown, and maintenance.
- Open design: do not depend on secrecy of the mechanism; protect the keys, permissions, or credentials instead.
- Separation of privilege: where feasible, require multiple conditions or authorities for particularly sensitive actions.
- Least privilege: every program and user should operate with only the privilege needed for the job, reducing damage and reducing risky interactions.
- Least common mechanism: minimize shared mechanism and shared state because shared structures create extra coupling and possible information paths.
- Psychological acceptability: the mechanism should be usable enough that people apply it correctly as a matter of routine.

## Practical Caveats

- The authors explicitly present these principles as warnings or heuristics, not absolute laws.
- The paper repeatedly warns against false confidence: narrow, elegant protection mechanisms can still leave important gaps if the broader system is ignored.
- Dynamic authorization and revocation are called out as especially difficult in real systems.
- This source is about computer protection systems, not product architecture in general, so its transfer to app and agent design should stay at the level of engineering heuristics.

## Why It Matters Beyond Security

- Economy of mechanism maps cleanly to minimizing code surface, tool surface, and unnecessary branching complexity.
- Least privilege maps to narrow permissions, narrow dependency assumptions, and narrow operational scope.
- Least common mechanism maps to skepticism about hidden globals, broad shared state, and central services that every part of the system must trust.
- Open design supports reviewability: a system should stay intelligible even when the mechanism is visible.
- Psychological acceptability is relevant to developer workflows and user-facing flows alike because awkward controls get bypassed.