# Prompt Loop

**Editor-driven** - Prompt cells start from the notebook UI today. A human creates the prompt in VS Code or Cursor, then the agent answers from the CLI.

**Notebook-as-conversation** - The prompt and the agent response both live in the notebook, so the conversation is visible and executable.

**Not the minimal path** - Use this when a human intentionally wants a notebook conversation. It is separate from the normal `new` + `ix` workflow.

## How It Works

1. A human clicks **Ask Agent** in the notebook toolbar.
2. The notebook gets a markdown cell with `agent-repl` prompt metadata.
3. The agent discovers that prompt from the CLI.
4. The agent responds with code.
5. The response cell is inserted and executed.

## List Prompts

```bash
agent-repl prompts notebooks/demo.ipynb
```

Typical response:

```json
{
  "prompts": [
    {
      "cell_id": "abc123",
      "cell_type": "markdown",
      "source": "clean this dataframe and show the final shape"
    }
  ]
}
```

## Respond to a Prompt

```bash
agent-repl respond notebooks/demo.ipynb --to abc123 -s 'df = df.dropna()\nprint(df.shape)'
```

`respond` currently performs three sequential operations:

- marks the prompt in progress
- inserts a response cell
- executes that response cell, then marks the prompt answered

This is an editor-assisted flow today, not the same headless core path as `new`, `ix`, `edit`, or `exec`.

## When to Use This

Use the prompt loop when:

- a human is actively in the notebook UI
- the notebook should act like a conversational workspace
- the response should be visible as a real notebook cell

Use the normal command workflow instead when:

- the agent is working headlessly
- there is no human-created prompt cell
- you just want to do notebook work from the CLI

## Current Scope

Prompt cells are an editor-assisted capability today. They are not the same as the headless core notebook workflow.

That means:

- the human creates prompts from the editor
- the agent answers from the CLI
- the notebook remains the shared conversation surface

## Next Steps

- [Getting Started](/Users/giladrubin/python_workspace/agent-repl/docs/getting-started.md)
- [Command Reference](/Users/giladrubin/python_workspace/agent-repl/docs/commands.md)
