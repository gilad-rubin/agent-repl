# MCP Dos and Don'ts

This document defines the MCP design rules for `agent-repl`.

It is not a generic MCP article. It is the project-specific guidance for how our MCP surface should behave.

Read this alongside:

- [Agreed V1 Architecture](design/agreed-v1-architecture.md)
- [Founding Architecture](design/founding-architecture.md)

## Architectural Reference

For this project, the MCP surface is not allowed to invent its own product model.

It should be treated as an agent-facing projection of the architecture defined in
[Agreed V1 Architecture](design/agreed-v1-architecture.md).

In particular, agents using MCP should assume:

- UI is optional
- the daemon is the only notebook and execution authority
- UI, CLI, and MCP share the same mutation path
- UI, CLI, and MCP share the same execution queue and runtime model
- multiple notebooks in a workspace are first-class
- JupyterLab owns notebook behavior in the UI, while MCP operates on daemon-owned notebook state

If an MCP tool design conflicts with those assumptions, the architecture wins and the tool design should change.

## Goal

The MCP surface should let agents operate effectively on the daemon-owned notebook system without:

- depending on UI state
- exposing accidental internal architecture
- forcing the model to compose low-level notebook operations from scratch
- creating a second execution or mutation path

## Core Rule

MCP tools are a client of the daemon, not a special subsystem.

That means:

- the daemon remains authoritative
- MCP uses the same notebook mutation path as the UI and CLI
- MCP uses the same execution queue and runtime path as the UI and CLI
- MCP tools should describe user outcomes, not leaked implementation seams

## Do

### 1. Expose Outcome-Oriented Notebook Tools

Prefer tools that match what an agent is trying to achieve.

Good examples:

- observe notebook state
- edit notebook cells
- execute a cell
- run all cells
- interrupt execution
- restart the kernel
- inspect runtime status
- create or restore a checkpoint
- find notebooks in the workspace

Avoid forcing the model to assemble common tasks from tiny primitives when a higher-level tool would be clearer.

### 2. Keep The Default MCP Surface Small

The default agent-facing surface should be compact and notebook-aware.

For v1, prefer a small set of bundles or grouped capabilities such as:

- notebook-observe
- notebook-edit
- notebook-execute
- notebook-runtime
- workspace-files
- checkpoint

The raw internal tool set can still exist, but it should not be the default operating mode for agents.

### 3. Keep Notebook Identity Stable

Tools should prefer stable cell identity over positional indexing.

If indexes are accepted for convenience, they should be treated as a thin compatibility layer at the tool boundary, not as the core identity model.

Stable cell IDs should remain the canonical reference for:

- edits
- execution requests
- output inspection
- attribution

### 4. Route Through The Same Core Paths As UI And CLI

Every MCP tool that mutates notebook state or executes code should call the same underlying core path used by the UI and CLI.

This is a hard requirement.

If an MCP tool needs a custom path that the UI and CLI do not use, that is a design smell and should be justified explicitly.

### 5. Document Inputs And Outputs For Models

Tool docstrings and schemas are part of the prompt the model reads.

Every tool should make clear:

- what it does
- when to use it
- what the important arguments mean
- what the response shape looks like
- what error cases the model should expect

For notebooks, this is especially important for:

- cell references
- path semantics
- queue behavior
- execution status
- checkpoint restore behavior

### 6. Mark Mutating Tools Clearly

Use tool annotations to distinguish read-only tools from mutating or destructive tools.

At minimum:

- read-only tools should be marked read-only
- mutating tools should not be marked read-only
- destructive tools should be marked destructive when appropriate

This helps clients make better approval and confirmation choices.

### 7. Return Useful Error Detail

Do not hide the actual cause of failure behind short generic messages.

Bad:

- "Bad Request"
- "Internal Server Error"
- "Conflict"

Better:

- the exact validation failure
- the conflicting cell or notebook state
- the queue or runtime condition that blocked the action
- the recovery hint the caller needs

Models self-correct better when the response explains what actually went wrong.

### 8. Prefer Compact, Token-Efficient Read Responses

Tool responses should be concise, but not so compressed that they become ambiguous.

For notebook reads:

- return only the fields needed for the task
- avoid giant repeated payloads when a summary would do
- prefer structured summaries over dumping full notebook JSON by default
- support richer detail only when needed

This is especially important for outputs, activity history, and workspace search results.

### 9. Keep UI Context Advisory, Not Authoritative

If the daemon tracks active human context such as:

- active notebook
- active cell
- nearby cells
- recent execution

that context may be exposed to MCP tools as supplemental information.

It must never replace daemon-owned notebook truth.

MCP tools should not depend on an active UI widget existing.

### 10. Treat All Agent Input As Untrusted

Every string provided by an LLM should be treated like untrusted user input.

Validate at the tool boundary.

Be especially careful with:

- file paths
- shell commands
- kernel or runtime identifiers
- notebook paths
- checkpoint names

## Don't

### 1. Don't Expose UI-Owned Commands As The Primary Tool Model

Avoid tools whose real meaning is "click this JupyterLab button" or "operate on the active visible notebook."

Bad examples for our architecture:

- tools that require the active notebook widget
- tools that require the selected file editor
- tools that depend on UI focus or selection to resolve the target document

That is how NBI works, and it is exactly what we are trying not to build.

### 2. Don't Leak Projection-Specific APIs Into The Main MCP Story

Projection-specific operations should not be part of the default tool vocabulary for agents.

In particular, avoid making tools like "project visible notebook state" or "execute visible cell" part of the primary MCP mental model.

Those are transitional or implementation-specific seams, not durable product capabilities.

### 3. Don't Expose A Giant Flat Tool Catalog By Default

A giant list of tiny notebook tools usually makes agent behavior worse.

It forces the model to:

- decide between many overlapping tools
- reconstruct higher-level workflows itself
- spend more tokens understanding the tool surface than solving the task

Prefer grouped, task-shaped tools over dozens of near-primitives.

### 4. Don't Build A Separate MCP Execution Path

MCP should not get a special execution route.

If the UI submits a cell execution and an MCP agent submits a cell execution in the same notebook, both requests must enter the same queue and runtime model.

### 5. Don't Make The Model Compose Notebook Search, Edit, And Execute Entirely From Raw Primitives

The model should not have to:

- fetch all cells
- locate a target manually
- calculate a positional edit plan
- issue multiple tiny mutation calls
- then separately figure out how to run and inspect the result

for common workflows.

The MCP surface should help it operate at the level of intent.

### 6. Don't Force The UI To Exist

If a tool cannot work without a UI already being open, it is probably the wrong tool for this project.

The daemon must remain fully usable from CLI and MCP in headless mode.

### 7. Don't Mix Notebook Product Capabilities With Admin And Dev Plumbing

Keep notebook-facing tools separate from:

- setup flows
- doctor and diagnostics
- editor development helpers
- reload and hot-swap operations
- experimental branch or review internals

Those may exist, but they should not be the default product-facing MCP story.

### 8. Don't Over-Specify Future Collaboration Mechanics In V1

Do not make the default MCP surface depend on:

- branch workflows
- review approval states
- rich ownership and lease semantics

unless the product actually needs them.

For v1, basic sessions, notebook operations, runtime state, and checkpoints are enough.

## Recommended V1 MCP Capability Set

This is the recommended default product-facing set for v1.

### notebook-observe

Should support:

- get notebook summary
- get cells and outputs
- inspect specific cell state
- inspect queue and runtime state
- search notebook content

### notebook-edit

Should support:

- create notebook
- insert cell
- replace cell source
- delete cell
- move cell
- change cell type
- clear outputs

### notebook-execute

Should support:

- execute cell
- run all
- interrupt
- restart
- restart and run all

### notebook-runtime

Should support:

- inspect runtime state
- inspect queue state
- list kernels
- select kernel

### workspace-files

Should support:

- list notebooks
- find notebooks
- read nearby workspace files
- edit nearby workspace files when needed

### checkpoint

Should support:

- create checkpoint
- list checkpoints
- restore checkpoint

## When To Add More Tools

Add a new tool only if at least one of these is true:

- it represents a real product capability we intend to support
- it significantly reduces agent failure or tool thrash
- it avoids forcing the model to compose the same multi-step flow repeatedly

Do not add tools merely because the internal core has a function for them.

## Review Checklist

Before adding or changing an MCP tool, check:

- Does it operate without any UI being open?
- Does it use the same core path as UI and CLI?
- Is it outcome-oriented rather than implementation-oriented?
- Does it preserve stable cell identity?
- Is it documented for model consumption?
- Is the response concise but useful?
- Does it leak a transitional seam that should stay internal?

If the answer to the last question is yes, the tool should probably not be part of the default MCP surface.
