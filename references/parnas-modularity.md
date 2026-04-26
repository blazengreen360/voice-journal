# Parnas and Information Hiding

## Source

- D. L. Parnas, "On the Criteria To Be Used in Decomposing Systems into Modules," *Communications of the ACM*, 15(12), 1972.
- Working web source used during setup: <https://fermatslibrary.com/s/on-the-criteria-to-be-used-in-decomposing-systems-into-modules>

## Source Context

- Parnas uses the KWIC indexing example to compare two competing decompositions of the same system.
- One decomposition follows major processing steps; the other decomposes around hidden design decisions.
- The paper is influential because it turns modularity from a vague cleanliness goal into a concrete rule for where boundaries should go.

## Key Ideas

- The main modularity benefits are changeability, independent development, and comprehensibility.
- A good decomposition begins with the design decisions that are difficult or likely to change, not with a flowchart of processing steps.
- Each module should hide one such decision from the others.
- Interfaces should reveal as little as possible about internal representation and sequencing.
- When an interface reveals more than necessary, it restricts future implementations and turns avoidable changes into system-wide edits.
- Clean decomposition and hierarchical structure are both valuable, but they are not the same thing.

## Practical Caveats

- The paper does not argue that runtime phases are unimportant; it argues they are a poor primary basis for modular boundaries.
- Parnas explicitly notes that a conceptually better decomposition can require a different implementation strategy to preserve performance.
- This source should not be used to justify speculative abstraction. The key move is to isolate real volatile decisions, not invent layers for their own sake.
- Over-specification is itself a design error. If an interface prescribes unnecessary order, structure, or representation, it shrinks the space of future solutions.

## How VoiceJournal Agents Apply It

- Prefer boundaries that hide storage layout, sequencing, packaging details, and platform-specific behavior.
- Flag plans or implementations that leak likely-to-change details across modules.
- Treat over-specified interfaces as a design smell because they reduce future flexibility.
- Prefer plans that explain which decision each boundary is intended to isolate.
- Favor abstractions that reduce the blast radius of expected change, especially around storage, models, packaging, and UI flow.
- Be skeptical of decompositions that simply mirror screen flow or execution order when the real volatility lies elsewhere.