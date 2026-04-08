# daemon-documentation.md

## Title
Daemon documentation and OpenFan Controller integration deep-dive

## Purpose
This document is for **documentation and analysis**, not feature delivery.

Your task is to inspect the daemon codebase and produce a **truthful, end-to-end technical explanation** of how the daemon works today.
The output must be useful to:
- the project owner;
- a future maintainer;
- and especially the **OpenFan Controller developer**, who will care about the real communication model, protocol usage, timing, safety logic, and runtime behaviour.

This is not a marketing summary.
This is not a speculative architecture write-up.
This is not a place to repeat old assumptions as fact.

You must treat this as a **code-first, evidence-led documentation pass**.
If the code contradicts prior notes, the code wins.
If the rationale is not evidenced, say so.
If a behaviour appears planned but not implemented, label it clearly.

---

## Core outcome required
Produce a detailed daemon explanation that answers, at minimum, these questions:

1. **Provide a detailed, end-to-end explanation of how the daemon works, what key design decisions were made, and why they were made.**
2. **Provide extra effort when detailing how the daemon interacts with the OpenFan Controller.**
3. **Specify what standards were used to communicate, how the interactions are made, how often, what safety logic exists, and everything the OpenFan Controller developer would want to know.**

The final output must be useful even to someone who has **not** followed the project week by week.

---

## Context and project expectations you must respect
Carry forward the existing project standards:
- inspect the real implementation first;
- research the chosen technical claims online before stating them as fact;
- do not patch gaps in understanding with guesses;
- distinguish clearly between **implemented**, **inferred**, **planned**, and **unknown**;
- keep explanations maintainable and useful for a future human;
- and prefer meaningful evidence over confident-sounding prose.

This project remains:
- Linux-first;
- structured around a **GUI + daemon** split;
- intended to support truthful state rather than GUI illusions;
- and developed iteratively, so previous docs may contain outdated assumptions.

For this task, your job is to document the daemon **as it actually exists now**.

---

## Truthfulness rules — non-negotiable

### 1. Code is the primary source of truth
Use the real codebase as the primary source.
Prior docs, milestone notes, refinement notes, and chat-history-derived assumptions are secondary.

### 2. Never invent rationale
If you cannot find evidence for *why* a design decision was made, do **not** present a guessed reason as fact.
Instead say one of:
- “Rationale evidenced in code/comments/docs:”
- “Likely rationale, but not explicitly evidenced:”
- “No reliable rationale found in the repo.”

### 3. Separate evidence levels explicitly
Every major claim should be mentally classified as one of:
- **Implemented and evidenced**
- **Strongly inferred from code/tests**
- **Documented intent but not fully implemented**
- **Unknown / not evidenced**

Do not blur these categories.

### 4. Do not answer from memory alone
Do not rely on prior conversation context or old documents unless the current repo confirms them.
Use previous project docs only to help find relevant code paths or known concepts.

### 5. Use primary online sources for standards/protocol claims
If you state that a library, protocol, serial mode, operating-system mechanism, or communication standard works a certain way, verify it against official or primary documentation before stating it as fact.

### 6. Be precise about scope
If something belongs to the GUI, a shared config layer, a test harness, or future scope rather than the daemon runtime itself, say so clearly.

---

## First step: documentation stocktake
Before writing the main daemon explanation, take stock of the project documentation and identify what already exists.

At minimum inspect, where present:
- `CLAUDE.md`
- `plan.md`
- `README*`
- `docs/` or any architecture/documentation folders
- milestone documents (for daemon-related milestones)
- refinement documents relevant to daemon/runtime/IPC/telemetry/safety
- `TelemetryExportSpec.md`
- `discovery_backlog.md`
- `next_tasks.md`
- ADRs / architecture notes
- protocol notes / hardware notes / service notes
- tests that encode behavioural expectations

The stocktake must answer:
- what daemon documentation already exists;
- what is outdated;
- what is missing;
- what is duplicated;
- and which document should become the **canonical** daemon reference.

Do not let the final explanation drift away from the project’s actual docs structure.

---

## Required research workflow

### Phase 1 — Inventory the daemon surface area
Identify and document:
- the daemon entrypoint(s);
- major modules/packages/crates;
- configuration loading paths;
- IPC/API surfaces;
- control-loop logic;
- device-discovery logic;
- sensor input paths;
- fan-output paths;
- safety/override logic;
- persistence/state management;
- telemetry/syslog/export logic if present;
- service integration / startup / shutdown behaviour;
- and test coverage relevant to daemon behaviour.

You must identify the real high-value files and symbols first.

### Phase 2 — Understand execution flow end-to-end
Trace the daemon from:
- process start;
- argument parsing / config load / service init;
- dependency construction;
- runtime loop start;
- sensor acquisition;
- curve/profile evaluation;
- safety checks and overrides;
- write decisions;
- hardware communication;
- status reporting;
- telemetry/logging;
- reload/apply/update flows if present;
- error paths;
- and shutdown/cleanup.

### Phase 3 — Deep-dive the OpenFan Controller interaction
This section requires **extra effort**.
Do not stop at a vague “the daemon communicates over serial”.
You must inspect the exact implementation and describe it in practical technical detail.

### Phase 4 — Verify standards and technical claims online
Before finalising the documentation, verify any claims about:
- serial/USB communication standards;
- OS interfaces;
- crates/libraries used for transport, async runtime, configuration, IPC, syslog, etc.;
- any protocol framing or standard format mentioned in the code;
- and any Linux subsystem behaviour the daemon relies on.

Use official docs first.

### Phase 5 — Produce the canonical documentation set
Write the documentation outputs listed below.
Ensure they match the evidence you found.

---

## Minimum questions you must answer from the codebase
Your documentation must answer these with specificity, not generalities:

### A. Daemon architecture and lifecycle
- What starts first?
- What objects/services are constructed at startup?
- What is long-lived versus request-scoped?
- What concurrency model is used?
- What loops, tasks, threads, timers, or async intervals exist?
- What are the key runtime states?
- How does the daemon recover from transient failures?
- What is persisted across restarts?
- What is recomputed live?

### B. IPC / API / control surface
- How does the GUI talk to the daemon?
- What interface is used (for example Unix socket, HTTP over Unix socket, direct socket protocol, etc.)?
- What commands/endpoints/messages exist?
- What are the expected request/response shapes?
- What error model is used?
- What status/health/freshness surfaces exist?
- How are apply/update/activate flows handled?

### C. Sensor and control logic
- Where do temperature and other inputs come from?
- How are sensors selected/resolved/grouped?
- How often are they polled?
- How is staleness handled?
- How are curves evaluated?
- How are roles/groups/profiles resolved into actual output commands?
- What arbitration or priority logic exists when multiple signals compete?
- What safety overrides exist, and exactly when do they trigger?

### D. OpenFan Controller integration — extra effort required
You must answer, in real technical detail:
- how the daemon discovers and identifies the controller;
- what transport is used;
- what serial/device parameters are configured;
- what library is used to perform communication;
- whether communication is request/response, stream-based, line-based, framed, binary, textual, or mixed;
- what commands/messages are sent;
- what responses are expected;
- what timing/cadence is used for reads and writes;
- whether commands are throttled, deduplicated, queued, coalesced, or retried;
- what happens on timeouts, malformed responses, reconnects, or device disappearance;
- what safety behaviour occurs if the controller stops responding;
- what assumptions the daemon makes about the device;
- how fan channel identity is mapped;
- and what another OpenFan Controller developer would need to implement or validate compatibility.

If possible from the code, include:
- baud rate / parity / stop bits / data bits / flow control;
- newline/framing rules;
- command encoding / units / ranges;
- channel numbering conventions;
- expected ACK/error semantics;
- retry limits / retry deadlines;
- freshness windows / deadlines;
- and any ownership/lease model preventing write conflicts.

### E. Safety logic
Document all meaningful safety logic, including:
- temperature emergency behaviour;
- stale sensor behaviour;
- stale controller status behaviour;
- startup safe state;
- fallback PWM/fan handling;
- invalid config handling;
- guardrails against over-writing or conflicting writes;
- and any “hard-coded” safety rules still present.

Be exact about what is implemented today.

### F. Design decisions and rationale
Identify the key design decisions and, where evidenced, explain why they were made.
Examples may include:
- daemon vs GUI split;
- IPC choice;
- transport choice;
- polling interval design;
- safety-overrides design;
- config persistence choices;
- controller abstraction choices;
- retry/reconnect strategy;
- telemetry/logging choices;
- and testability-driven abstractions.

Again: do **not** invent rationale.

### G. Documentation gaps and risks
Call out:
- where code is hard to reason about;
- where the behaviour is under-documented;
- where tests are weak or absent;
- where the hardware/protocol contract is implicit rather than explicit;
- and what should be documented next.

---

## Required output format from you
Your response/documentation must contain the following sections in order.

### 1. Executive summary
A concise but meaningful overview of what the daemon does, what its architectural shape is, and what the most important design themes are.

### 2. Evidence-based architecture overview
A clear description of major daemon components and how they relate.
Include file paths, module names, and key types/functions where helpful.

### 3. End-to-end runtime flow
Explain the real lifecycle from startup to steady state to shutdown.

### 4. GUI ↔ daemon interaction model
Explain the control/API surface and how the daemon exposes functionality outward.

### 5. Sensors, curves, profiles, and control-loop behaviour
Explain exactly how readings become control outputs.

### 6. OpenFan Controller integration deep-dive
This must be one of the strongest sections in the document.
It should be detailed enough that the hardware developer learns something real from it.

### 7. Safety and failure-handling matrix
Prefer a table here.
For each failure mode or safety concern, describe:
- trigger/condition;
- detection mechanism;
- daemon response;
- user-visible impact;
- and any open gaps.

### 8. Standards, protocols, libraries, and operating-system interfaces used
List the real standards and libraries involved.
State which are evidenced directly in code and which were verified against official docs.

### 9. Key design decisions and evidenced rationale
Prefer a table with columns such as:
- decision;
- where implemented;
- evidence for rationale;
- notes / trade-offs.

### 10. What is implemented vs planned vs unclear
Make this explicit.
Do not let the reader mistake design intent for completed functionality.

### 11. Documentation gaps / recommended next documentation work
Be practical.

### 12. Appendix
Include:
- glossary of important terms;
- message/command summary tables if discoverable;
- timing/poll interval tables;
- config key tables;
- and sequence diagrams if useful.

---

## Required diagrams and tables
Where supported, include diagrams/tables rather than prose alone.
At minimum, provide:

### Diagrams
- startup sequence
- steady-state control loop
- GUI → daemon → hardware interaction path
- one failure/recovery path (for example device disconnect or stale sensor path)

Use Mermaid if the project docs support it. If not, use clean text diagrams.

### Tables
At minimum include:
- daemon components / responsibilities
- IPC surface summary
- controller communication parameters
- safety logic matrix
- configuration/state ownership summary
- known gaps / uncertainties

---

## OpenFan Controller developer focus — extra depth requirements
Assume the reader is the hardware/controller developer and wants to know whether the daemon is a good citizen.
That means you must explicitly document:
- discovery expectations;
- connection assumptions;
- serial settings;
- communication cadence;
- whether writes are bursty or smooth;
- whether the daemon may issue redundant writes;
- whether there is command suppression/coalescing;
- the daemon’s expectations for acknowledgement or response timing;
- reconnect expectations;
- device-loss behaviour;
- whether the daemon assumes exclusive access;
- and what firmware/protocol changes would be risky for compatibility.

If the code does **not** reveal this cleanly, say so and identify the exact gap.

---

## Research rules for online verification
When verifying technical claims online:
- prefer official documentation and primary sources;
- use library/framework docs before blogs;
- use operating-system/vendor docs before forum guesses;
- and verify the exact library or mechanism actually used by the repo.

Examples of acceptable verification targets, if relevant to what the repo actually uses:
- official crate/library docs;
- Linux kernel docs;
- protocol RFCs;
- vendor hardware docs;
- framework/runtime docs.

Do not cite random posts as authoritative if a primary source exists.

---

## Documentation outputs you must update or create
Take stock of the repo first, then update/create the exact docs below.
If a file already exists, update it in place.
If it does not exist, create it.

### A. Canonical daemon architecture document
Create or update:
- `docs/architecture/daemon-end-to-end.md`

This should contain:
- sections 1–11 above;
- the main end-to-end daemon explanation;
- architecture diagrams;
- and the authoritative “implemented vs planned vs unclear” view.

### B. OpenFan Controller integration appendix
Create or update:
- `docs/architecture/openfan-controller-integration.md`

This should contain:
- the deep hardware/protocol/transport section;
- controller-specific timing and message details;
- compatibility notes for the OpenFan Controller developer;
- failure and reconnect behaviour;
- and any protocol uncertainties called out explicitly.

### C. Project root guidance index
Update, if present:
- `CLAUDE.md`

Only add:
- a short pointer to the canonical daemon docs;
- any build/test commands needed to validate daemon behaviour;
- and any lasting project instruction that would help future daemon work.

Do not bloat `CLAUDE.md`.
Keep it concise.

### D. Project plan / next actions
Update only if justified by findings:
- `plan.md`
- `next_tasks.md`
- `discovery_backlog.md`

Use these only for:
- real documentation gaps;
- missing tests;
- protocol ambiguities;
- or follow-on work discovered during the audit.

Do not create busywork entries.

### E. Existing daemon-related docs
Update any existing daemon/IPC/protocol docs that conflict with current reality.
If you supersede something, say so explicitly.

---

## Documentation style requirements
Write for a technical audience.
Use:
- precise language;
- short, concrete paragraphs;
- tables where they improve clarity;
- explicit distinction between fact and inference;
- and file/module/type names where helpful.

Avoid:
- vague summaries;
- ungrounded “best practice” claims;
- hand-wavy architecture prose;
- and repeating the same point across multiple sections.

Make it useful.

---

## What you must inspect in the codebase
At minimum, inspect and trace the real implementation for:
- daemon entrypoint and startup path;
- config schema and persistence;
- IPC/API handlers and status/health endpoints;
- device discovery and serial open path;
- transport wrapper/service abstraction;
- polling loops and timer intervals;
- sensor freshness / health logic;
- curve evaluation and profile application logic;
- safety override logic;
- command/write path to the controller;
- retry/reconnect logic;
- shutdown and cleanup behaviour;
- tests covering daemon/runtime behaviour;
- logs/diagnostics/telemetry hooks;
- and mocks/fakes used by tests.

---

## Strong guidance on “why” explanations
When explaining why design decisions were made, use this order of preference:
1. explicit code comments / docs / ADRs / commit notes;
2. tests that clearly encode intended behaviour;
3. surrounding structure that strongly implies the reason;
4. if still uncertain, say the rationale is not reliably evidenced.

Do not reverse-engineer intent and then present it as settled fact.

---

## Suggested practical method
A strong workflow would look like this:
1. Inventory daemon files, docs, and tests.
2. Identify the startup path and the long-lived runtime objects.
3. Trace one complete “sensor read to controller write” path.
4. Trace one complete “GUI/API request to daemon action” path.
5. Trace one complete failure path.
6. Inspect controller transport details and timing in code.
7. Verify external technical claims against official docs.
8. Draft the documentation with explicit evidence boundaries.
9. Update the canonical docs listed above.
10. Report remaining uncertainties and recommended next documentation work.

---

## Definition of done
This task is complete only when:
- the daemon has been documented end-to-end from real code evidence;
- the OpenFan Controller interaction has been documented in extra depth;
- communication standards/mechanisms/libraries have been verified before being stated as fact;
- implemented vs planned vs unclear is explicit;
- key design decisions are described without invented rationale;
- the canonical daemon docs have been created or updated;
- conflicting/outdated daemon docs have been corrected or flagged;
- and the final output would genuinely help the OpenFan Controller developer understand the daemon’s behaviour and expectations.

---

## Final instruction to Claude
Do not produce a shallow architecture summary.
Do not guess.
Do not let previous notes override the code.

Inspect the daemon codebase thoroughly, verify technical claims before stating them, document the OpenFan Controller interaction with extra care, and leave behind a documentation set that is truthful, practical, and maintainable.
