# Issue tracker: Local Markdown

Issues and specs for this repository live as Markdown files under `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`.
- The spec is `.scratch/<feature-slug>/spec.md`.
- Implementation tickets are separate files under `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01` in dependency order.
- Each issue records triage state in a `Status:` line and blocking dependencies in a `Blocked by:` line.
- Append discussion history under a `## Comments` heading rather than rewriting earlier decisions.

When a skill says to publish to the issue tracker, create or update the corresponding file under `.scratch/<feature-slug>/`. When a skill says to fetch a spec or ticket, read the referenced Markdown file in full.

For wayfinding work, use `.scratch/<effort>/map.md` plus one child file per decision ticket under `.scratch/<effort>/issues/`; claim, block, and resolve tickets using their `Status:` and `Blocked by:` fields.
