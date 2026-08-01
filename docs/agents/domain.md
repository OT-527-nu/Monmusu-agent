# Domain Docs

This is a single-context repository.

## Before working

- Read root `CONTEXT.md` for the project's domain vocabulary.
- Read the ADRs under `docs/adr/` that affect the active ticket.
- For Agentic MVP work, read `docs/agentic_mvp/README.md` and follow its documented authority order.
- Read current source and tests to distinguish implemented behavior from target design.

If a document is absent, proceed without proposing it pre-emptively. Domain modeling creates or changes domain documents only when a real terminology or decision gap is being resolved.

## Vocabulary and conflicts

- Use the terms defined in `CONTEXT.md` in tickets, tests, code-facing descriptions, and design proposals.
- Avoid synonyms that the glossary explicitly rejects.
- If work contradicts an accepted ADR, surface the conflict and obtain a new decision rather than silently overriding it.
- Do not treat legacy top-level design documents as authority for new Agentic MVP behavior; they remain migration references.
