# Repository Instructions

These rules apply to agents editing this repository. Prefer evidence, narrow scope, and observable outcomes over speculative flexibility.

## Sources of truth

- Read the active ticket or spec before changing code.
- Use `CONTEXT.md` for domain vocabulary and read relevant ADRs under `docs/adr/`.
- For the target Agentic MVP, start at `docs/agentic_mvp/README.md`. Data shape is owned by `contracts.md`, execution order by `agent_loop.md`, character facts by `characters.md`, and module material by `module_reference.md`.
- Existing source and tests describe current runtime behavior. Target documents describe intended behavior and must not be presented as already implemented.
- Old top-level design documents are migration references, not authority for new Agentic MVP work.
- Surface conflicts with accepted ADRs instead of silently overriding them.

## Think before coding

- Inspect the ticket, relevant documents, code, tests, and current worktree before deciding.
- State assumptions and meaningful tradeoffs explicitly.
- Make conservative low-risk, reversible decisions independently and record them in the result.
- Ask before choices that materially change scope, authority, public contracts, persistent data, external systems, or irreversible state.
- Point out a materially simpler solution when one exists.

## Simplicity and scope

- Build the smallest complete vertical slice that satisfies the ticket and its acceptance criteria.
- Do not add features, provider frameworks, configuration, or abstractions without an accepted requirement or an established seam that needs them.
- An abstraction is justified when it protects a real boundary, removes meaningful duplication, or is required by the accepted architecture.
- Do not remove required validation, failure handling, recovery, persistence guarantees, traceability, or tests merely to reduce line count.

## Surgical changes

- Every changed line must trace to the active ticket, spec, ADR, or migration exit condition.
- Preserve unrelated user changes and work with overlapping dirty-worktree edits. Do not reset, revert, or rewrite unrelated work.
- Match the local Python style, while following accepted Agentic MVP decisions when they intentionally replace legacy rule-driven patterns.
- Remove only dead paths made obsolete by the active migration slice or by your own changes.
- Do not commit unless the user asks or the active workflow explicitly requires a commit.

## Reliability boundaries

- Treat provider responses, tool arguments, JSON/schema data, timeouts, and recovery state as untrusted boundaries.
- Preserve the authority split defined by the Agentic MVP contracts: Harness owns COC mechanics and persistence; GM owns fictional causality and canon.
- Respect atomic commit and incomplete-turn recovery semantics. Never create a hidden reroll or silently discard committed mechanics.
- Never store or expose API keys, hidden reasoning, or player-invisible diagnostics in game records or normal output.

## Python and comments

- Install Python packages with `uv pip install`, targeting the project environment rather than the global interpreter.
- Report the actual interpreter or environment used for verification.
- Add concise Chinese comments or docstrings only for non-obvious invariants, trust boundaries, atomic operations, or recovery ordering. Do not narrate self-explanatory code.

## Verification

- Translate requirements into observable acceptance conditions before implementation.
- Prefer behavior tests at the highest stable seam, with independent expected values and relevant counterexamples.
- Run focused tests while working and the full baseline at the end: `PYTHONPATH=src python3 -m unittest discover -s tests`.
- Deterministic fake-adapter tests and real DeepSeek tests are separate evidence lanes; neither substitutes for the other.
- Report commands run, important evidence, the interpreter used, and any residual unverified risk.

## Agent skills

### Issue tracker

Issues and specs use the local Markdown tracker under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

The tracker uses the five default triage role strings. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository using root `CONTEXT.md` and `docs/adr/`. See `docs/agents/domain.md`.
