# FocusFlow Agent Project Knowledge

This is the curated entry point for BMAD and TEA. It records how to resolve this repository's specifications and evidence; it does not replace the source documents.

## Source Authority

Choose the source according to the decision being made.

| Decision | Authoritative source | Handling rule |
| --- | --- | --- |
| Accepted FocusFlow timeline behavior | `docs/research-timeline-ui/SPEC.md` | Preserve `SCH-001`, `SCH-002`, `SCH-003`, and `CAP-1` through `CAP-5` until an explicit spec change replaces them. |
| Experience, visual design, and architecture for that timeline | `docs/research-timeline-ui/EXPERIENCE.md`, `DESIGN.md`, `ARCHITECTURE.md` | Change the owning document in the same work as the behavior it describes. |
| What was actually executed for that slice | `docs/research-timeline-ui/EVIDENCE.md` and current tests | Evidence is historical. A later test run replaces it only when the command is recorded again. |
| Parent product intent and the full `SCH` ID set | SYA-UI product analysis, not a file in this checkout | Cite the ID and whether this repository accepts or defers it. Do not copy the parent PRD here to win a conflict. |
| HTTP shape for the host and the desktop app | `api.openapi.json` | Contract changes require the service, static UI, Electron spawn path, and tests to move together. |
| Runtime identity, startup, capabilities, events, and permissions | `manifest.json` | `id` stays `scheduler`, `runtime` stays `local-http`, and the entry stays `bin/scheduler-server`. |
| How the parent repository locates this module | `module.json` | `sourceAnchors` name parent paths. Do not rewrite them into this checkout's relative paths unless the parent host contract changes. |
| Current implementation behavior | `tests/`, current code, and a newly recorded evidence note | Unittest success proves the in-process service. It does not prove a packaged app or a live model call. |
| Generated planning material | `_bmad-output/` | Use for work in progress, never as the current source of truth. |

When sources conflict:

1. Classify the disputed claim as intent, requirement, interface, packaging, or observed behavior.
2. Verify the source that owns that class of claim.
3. Surface the contradiction with paths and evidence; do not silently blend the files.
4. Update the owning canonical document and its tests in the same change when feasible.
5. Keep unresolved product decisions explicit in the PR rather than guessing.

## Product Contract

FocusFlow turns long-horizon goals into stages and a next step. Several topics can run at once in the desktop. The core journey is: confirm the routine, create a plan, see it on a shared calendar, select a stage, and mark the current checklist item in progress or done.

The accepted timeline spec covers:

| ID | Scope |
| --- | --- |
| `SCH-001` | Input surface for a long-horizon goal |
| `SCH-002` | Existing decomposition, left on its current contract |
| `SCH-003` | Timeline visualization of first-level children |
| `CAP-1` | Create a plan through `POST /api/v1/tasks/init` |
| `CAP-2` | Root is the current topic; first-level children are milestones |
| `CAP-3` | Select a milestone, then a subtask, then update a leaf |
| `CAP-4` | Timeline and month navigation, with progress from completed leaves |
| `CAP-5` | Search within the current topic |
| `CAP-6` | Append another topic; revise only that topic's unfinished branches |
| `CAP-7` | Place each topic across its own deadline inside one shared work period |
| `CAP-8` | Suggest a new routine from actual work outside the period; the user decides |
| `CAP-9` | Suggest today's grain from completion friction or flow; the user decides |

`SCH-004` through `SCH-009` remain outside the original FocusFlow baseline. The parent-approved 2026-10-19 design-journeys spec adds source-level multi-project persistence, saved dates and preferences, plus explicit preview/confirm for the parent React workspace. Packaged delivery remains unverified. A status label is a testable claim, not a planning shortcut.

## Architecture Invariants

- The static UI in `app/static/` uses same-origin fetch and WebSocket only.
- `desktop/main.cjs` spawns `bin/scheduler-server` and waits on `/health`. The UI does not choose or hardcode the port.
- Renderer code does not read `.env`, Node, native APIs, or local files.
- Service layering stays `app/api/` to `app/services/` to `app/models/` and `app/agents/voyage/`, with `app/core/` owning config, the event bus, the memory store, and the only LLM boundary.
- Voyage names the loop: Navigator decomposes, Helm records human execution, Sextant checks and replans. The human is the executor.
- The store persists projects and preferences when `SYA_SCHEDULER_DATA_DIR` is provided by the host; standalone runs without it remain ephemeral. Task list/get span all projects; legacy tree/timeline routes still use the active project. Restart tests establish source persistence, not packaged delivery.
- This repository is the implementation behind SYA-UI's `function/scheduler` gitlink. A commit here is not shipped in the parent app until that pointer is updated and reviewed.

## Interface Contract

Root contract files are `README.md`, `api.openapi.json`, `manifest.json`, `module.json`, and `config.example.json`.

Every OpenAPI operation needs an `operationId`; method keys are lowercase and `TRACE` is forbidden. Responses that the host consumes use the documented `FunctionResponse` envelope where that schema applies. A manifest-declared SSE path must remain an OpenAPI GET `text/event-stream` operation.

These routes are part of the current contract: `GET /`, `GET /health`, `POST /api/v1/tasks/init`, `PATCH /api/v1/tasks/{id}/status`, `GET /api/v1/tasks/tree`, `WS /ws/tree`, `/api/scheduler/*`, and `GET /manifest`.

## Evidence Levels

Report the strongest level actually executed:

1. Static: framework verification, OpenAPI and manifest parsing, `npm ci`, and `node --check` on the Electron shell.
2. Service/source: `npm test` Python unittest discovery, including the empty-key sample plan.
3. Desktop dev: `npm run app:dev` exercised on a running window.
4. Packaged Electron: `npm run app:dist:mac` output installed or launched.
5. OS and provider: signed identity, macOS permissions, and a live model call with a real key.

Do not use a lower level to close acceptance criteria that require a higher level. Record a missing API key, skipped package build, or unsigned local app as a gap rather than a pass.

## Current Reconciliation Risks

- Parent PRD status text can still describe scheduler progress differently from this checkout and from `docs/research-timeline-ui/EVIDENCE.md`.
- `module.json` anchors point at `function/scheduler/` and parent renderer files that do not exist in this working tree.
- Browser or unittest coverage does not establish packaged Electron, signing, or live GLM behavior.
- `.github/workflows/ci.yml` can build a macOS artifact on push. `.github/workflows/pr-quality.yml` must stay read-only and credential-free.
- Milestone dates are not stored. The desktop schedules leaves from the saved daily hours and work period, skipping meals, then shows day, week, and month views. Today's grain is a display overlay, not a second decomposition.

See `docs/agent-sdlc.md` for workflow usage and GitHub quality-gate operation.
