# Subagents and models

- **Opus is the orchestrator.** The main session plans, briefs, coordinates, verifies and reviews. Subagents do the implementing.
- **Sonnet is the default implementer** (`model: "sonnet"`).
- **Haiku for very well-defined work** (`model: "haiku"`): a mechanical edit, a fixed checklist, a run-and-report job. Its brief must stand alone: the files, the rules, the exact checks to run. Check its results more closely.
- **An Opus subagent only when quality is paramount or the piece is critical.**
- A resumed agent keeps its own model, so don't resume an Opus agent for new work.

# How we build

- **Simpler is better.** Pick the smallest design that does the job. No speculative abstractions, options or layers.
- **Clear separation of concerns.** Each module, file and function has one job, and you can say what it is in one sentence.
- **Minimal linkage.** Parts know as little about each other as they can. They talk through small, explicit interfaces, never through each other's internals or shared state.
- **Dependencies flow one way.** No cycles. Lower-level code never imports or calls back into the code above it.
- **No assumptions: ask first.** When a requirement, fact or design choice is unclear, ask in chat before acting. Don't fill the gap with a plausible guess.
