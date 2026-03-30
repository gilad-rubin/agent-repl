# Onboarding

**One product, three entry points** - every user should start from the same workspace-scoped runtime, then add the surfaces they actually need.

**CLI is the base install** - the CLI is the required foundation for every path, including MCP and editor workflows.

**Decision first** - onboarding should ask what the user wants to do, then produce the smallest possible setup flow.

**Human-friendly and agent-friendly** - every step should work both as a manual checklist and as a short sequence that a coding agent can execute and verify interactively.

## How To Use This Page

There are two equally valid ways to follow this guide:

- **Manual setup** - copy the commands yourself, verify each result, then continue
- **Agent-assisted setup** - ask your coding agent to follow this page step by step, report each result, and stop only when it needs you to confirm an editor choice or paste MCP config into another tool

When we improve onboarding further, we should preserve both modes instead of optimizing only for one.

## Recommended Install Paths

Use this decision table first:

| User goal | What to install | First command |
|---|---|---|
| Create and run notebooks from a terminal or agent | CLI only | `agent-repl new analysis.ipynb` |
| Use the Agent REPL canvas inside VS Code or Cursor | CLI + extension | `agent-repl open analysis.ipynb` |
| Connect Claude Desktop, VS Code MCP, Cursor MCP, or another MCP client | CLI + public MCP onboarding flow | `agent-repl mcp setup` |
| Preview the canvas in a browser during QA or development | CLI + extension checkout | `agent-repl open analysis.ipynb --target browser` |

The browser canvas is a useful preview and debugging surface, but it should not be presented as a primary onboarding path for most new users.

## Installation Options

The onboarding flow should explicitly support multiple installation methods instead of assuming `uv`.

| Install method | When to use it | Command |
|---|---|---|
| `uv tool install` | Best default for isolated CLI installs | `uv tool install /path/to/agent-repl --reinstall` |
| `pipx install` | Good when the user already uses `pipx` for Python CLIs | `pipx install /path/to/agent-repl` |
| `pip install` in a virtualenv | Good when the user wants the CLI tied to a project environment | `python -m pip install /path/to/agent-repl` |
| editable dev install | Best for contributors working from a checkout | `python -m pip install -e .` |

Recommended preference order:

1. `uv tool install`
2. `pipx install`
3. `pip install` inside a virtual environment

We should not make `uv` feel mandatory if the product itself does not require it.

## Canonical New-User Flow

### 1. Install Prerequisites

- Python 3.10+
- one installer: `uv`, `pipx`, or `pip`
- Optional: VS Code, Cursor, or another editor built on the VS Code extension model

### 2. Install the CLI

Choose one:

Preferred isolated install with `uv`:

```bash
uv tool install /path/to/agent-repl --reinstall
```

If `pipx` is the user's normal CLI manager:

```bash
pipx install /path/to/agent-repl
```

If the user wants a project-local install inside an activated virtualenv:

```bash
python -m pip install /path/to/agent-repl
```

If the user is developing from this checkout:

```bash
python -m pip install -e .
```

Verify:

```bash
agent-repl --version
agent-repl --help
```

Agent-friendly instruction:

- install using the first available preferred method
- report which installer was used
- stop if `agent-repl --version` fails

### 3. Verify the Headless Runtime

This is the most important smoke test:

```bash
agent-repl new tmp/install-check.ipynb
agent-repl ix tmp/install-check.ipynb -s 'print("agent-repl is working")'
```

If this passes, the shared runtime path is working.

Agent-friendly instruction:

- run both commands
- report success only if notebook creation and execution both succeed
- if kernel selection fails, try the workspace `.venv` first, then fall back to an explicit Python path

### 4. Choose an Optional Surface

#### Option A: CLI Only

Stay here if the user wants agent-driven or terminal-first notebook work.

Typical next commands:

```bash
agent-repl new analysis.ipynb
agent-repl ix analysis.ipynb -s 'x = 2\nx * 3'
agent-repl open analysis.ipynb
```

#### Option B: VS Code or Cursor Canvas

Install the extension when the user wants:

- the Agent REPL canvas UI
- live notebook projection while the agent works
- prompt-cell workflows

If the user is developing this repo itself, prefer the workspace checkout in an Extension Development Host instead of testing an installed copy first:

```bash
agent-repl editor dev --editor vscode
```

That path compiles the repo extension, opens VS Code against the workspace checkout, and avoids installed-extension drift during normal development.

Current install flow:

```bash
cd extension
npm install
npm run compile
npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-0.3.0.vsix
code --install-extension agent-repl-0.3.0.vsix --force
```

If the user is on Cursor, install the same `.vsix` from Cursor's command palette.

If the user is on Windsurf, the likely path is the same `.vsix`-style install flow if its current build supports VS Code-compatible extensions, but we should verify that against the current product before documenting it as guaranteed.

Then verify:

```bash
agent-repl open tmp/install-check.ipynb
```

Expected result:

- the notebook opens in the Agent REPL canvas
- persisted cells and outputs are visible
- future runs reuse the shared runtime when it is still alive

Agent-friendly instruction:

- detect whether `code` is available before assuming CLI-driven install
- if editor CLI installation is unavailable, build the `.vsix` and hand the user the exact file path plus the one editor action they need to perform
- after installation, run the notebook open command and ask the user to confirm the canvas opened

#### Option C: MCP Client

Install this when the user wants to connect `agent-repl` to an external MCP client.

MCP now has a public onboarding surface. Users should not need to discover daemon internals or wire `/mcp/mcp` manually from runtime metadata.

Use these public commands:

```bash
agent-repl mcp setup
agent-repl mcp status
agent-repl mcp config
agent-repl mcp smoke-test
```

Recommended path:

1. Run `agent-repl mcp setup`
2. Paste the emitted `mcpServers` block into the target client
3. Run `agent-repl mcp smoke-test`

What `setup` gives the user:

- the canonical public endpoint at `/mcp`
- the compatibility alias at `/mcp/mcp`
- the auth header value
- a ready-to-paste `mcpServers` JSON block

What the other commands are for:

- `agent-repl mcp status` shows the current endpoint, token header, and daemon details
- `agent-repl mcp config` prints only the config block when that is all the user needs
- `agent-repl mcp smoke-test` performs a real MCP round-trip and verifies the public surface

Quick bootstrap and verify:

```bash
agent-repl mcp setup
agent-repl mcp smoke-test
```

Agent-friendly instruction:

- run `agent-repl mcp setup`
- capture the emitted JSON block exactly
- prefer the canonical `/mcp` endpoint
- either paste it into the target MCP client config automatically when the environment allows that, or present it to the user with a single clear next action
- use `agent-repl mcp status` if the user wants diagnostics without reformatting config
- run `agent-repl mcp smoke-test` after configuration and report whether the round-trip succeeded

#### Option D: Browser Canvas

Use this for preview, QA, or renderer work rather than as the default onboarding story.

```bash
cd extension
npm run preview:webview
```

Then open `http://127.0.0.1:4173/preview.html`.

Agent-friendly instruction:

- start the preview server
- report the preview URL
- if the agent has browser control, open the page and confirm the notebook explorer appears

## Product Positioning During Onboarding

The current product story is easiest to understand when it is explained in this order:

1. `agent-repl` is a workspace-scoped notebook runtime.
2. The CLI is the required base install.
3. MCP is a configuration layer on top of that runtime.
4. The extension is an optional projection UI on top of the same runtime.
5. The browser canvas is a useful preview surface, not the main install target.

That ordering avoids making new users think they need every surface on day one.

## Friction In The Current Flow

- The install story is split across `README.md`, `docs/installation.md`, `docs/getting-started.md`, and `docs/mcp.md`.
- The docs still read as if `uv` is the default install path, even though many users will expect `pipx` or `pip`.
- Extension install is still a manual VSIX packaging flow instead of a marketplace or one-command install path.
- MCP onboarding is much better now that there is a public `agent-repl mcp` command group, but it is still presented separately from the main install decision instead of as part of one guided path.
- `agent-repl setup` and `agent-repl doctor` now exist, but they still need a more interactive wizard-style UX if we want true first-run guidance.
- The current setup story is more agent-executable than before, but we can still improve how it guides editor and extension installation decisions.
- "Open notebooks in the Agent REPL canvas by default" is true for `agent-repl open`, but not yet a clearly guided editor-level default for arbitrary `.ipynb` opens.

## Shipped Onboarding Commands

These commands now exist:

```bash
agent-repl setup
agent-repl doctor
agent-repl editor configure --default-canvas
agent-repl editor dev --editor vscode
```

Recommended current usage:

- `agent-repl setup --smoke-test` for post-install onboarding
- `agent-repl setup --with-mcp` when the user wants MCP immediately
- `agent-repl doctor --probe-mcp` for diagnostics
- `agent-repl editor configure --default-canvas` to make the canvas the workspace default
- `agent-repl editor dev --editor vscode` for contributors working from the repo checkout

## Remaining Improvements

### 1. Make `setup` More Interactive

The current `setup` command should grow into a more guided wizard that:

- verify Python and at least one supported installer
- support `uv`, `pipx`, and `pip` install paths
- install or reinstall the CLI
- detect whether the workspace has a `.venv`
- ask whether the user wants CLI only, editor, MCP, or all three
- optionally build and install the extension
- optionally print or write the MCP config block using the public `agent-repl mcp config` surface
- run one verification flow at the end
- produce machine-readable status output so an external coding agent can drive it safely

### 2. Expand `doctor`

The current `doctor` command should report:

- CLI version and path
- installation method when detectable
- workspace runtime status
- kernel availability
- extension availability
- repo-versus-installed extension build drift when the workspace contains an `extension/` checkout
- whether the extension can launch the CLI
- MCP endpoint and smoke-test result

### 3. Expand Editor Configuration

`agent-repl editor configure --default-canvas` now handles the workspace-scoped default. The next step is to offer workspace versus user scope explicitly.

`agent-repl editor dev` is now the preferred integration loop for contributors. The next step there is to make editor choice and workspace reuse feel more wizard-like inside `setup`.

For VS Code, it should offer to update workspace or user settings so `*.ipynb` opens in the Agent REPL canvas by default. VS Code officially supports custom editors and `workbench.editorAssociations`, so this is a viable guided path.

For Cursor, the likely model is the same because it inherits much of the VS Code settings model.

For Windsurf, we should verify the current compatibility story before promising fully automatic settings updates.

### 4. Ship Ready-Made Settings Snippets

Even before automation exists, we should document a copy-paste settings block for VS Code-family editors:

```json
{
  "workbench.editorAssociations": {
    "*.ipynb": "agent-repl.canvasEditor"
  }
}
```

This should be framed as an opt-in preference, not an automatic override.

### 5. Add One Install Page With "Choose Your Path"

Keep the current docs, but add a single landing page that asks:

- Do you want CLI only?
- Do you want the canvas in an editor?
- Do you want MCP?
- Which installer do you prefer: `uv`, `pipx`, or `pip`?

Each answer should expand into one short command block and one verification block.

### 6. Publish The Extension Through A Real Distribution Channel

The current VSIX packaging flow is fine for development, but it adds unnecessary friction for new users. Publishing through the VS Code Marketplace, Open VSX, or both would remove a full step from onboarding.

### 7. Offer A First-Run Sample Notebook

After successful setup, offer:

```bash
agent-repl new --open examples/welcome.ipynb
```

That notebook should demonstrate:

- one markdown cell
- one code cell
- one prompt-cell example
- one MCP-oriented explanation of how the same runtime is reused

### 8. Make Onboarding Even More Agent-Executable

Every setup command should be designed so a coding agent can run it safely and narrate progress.

That means:

- each step has one command block
- each step has a clear success check
- each step has a fallback when the preferred path fails
- each step identifies whether it is safe for an agent to do automatically or whether the user must confirm something in an editor UI
- commands that are intended for automation should support structured output where possible

## Suggested Near-Term Rollout

### Phase 1: Docs

- add this onboarding page
- link to it from `README.md` and the docs summary
- reduce repetition between install, getting started, and MCP pages

### Phase 2: Verification

- add a single end-to-end `agent-repl verify` command
- expand `agent-repl doctor`
- make the verification commands emit concise structured output for agent-driven setup flows

### Phase 3: Guided Setup

- add installer selection to the setup flow
- expand `agent-repl setup`
- expand `agent-repl editor configure --default-canvas`
- add optional MCP config writing for supported clients

## References

- [Installation](/Users/giladrubin/python_workspace/agent-repl/docs/installation.md)
- [Getting Started](/Users/giladrubin/python_workspace/agent-repl/docs/getting-started.md)
- [MCP](/Users/giladrubin/python_workspace/agent-repl/docs/mcp.md)
- [Command Reference](/Users/giladrubin/python_workspace/agent-repl/docs/commands.md)
