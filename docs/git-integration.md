# Git Integration

Jupyter notebooks are JSON files with embedded outputs, execution counts, and volatile metadata. A single re-run can change hundreds of lines without touching any code. agent-repl solves this with two commands: `clean` for manual stripping and `git-setup` for automatic filtering on commit.

## The Problem

A notebook diff after re-execution:

```diff
   "execution_count": null,
+  "execution_count": 3,
   "outputs": [
+    {
+      "output_type": "execute_result",
+      "data": {
+        "text/plain": "<MyModel at 0x7f3b2c4a>",
+        "text/html": "<div style=\"...200 lines of HTML...\">",
+        "application/vnd.google.colaboratory.intrinsic+json": {"type": "string"}
+      },
+      "execution_count": 3,
+      "metadata": {}
+    }
   ]
```

The code didn't change. The diff is noise -- execution counts, object addresses, Colab metadata, and rendered HTML.

## `agent-repl clean`: Strip Outputs

`clean` produces a stripped version of the notebook on stdout. It removes outputs, execution counts, and unstable metadata:

```bash
agent-repl clean analysis.ipynb > analysis-clean.ipynb
```

What gets removed:

| Field | Before | After |
|-------|--------|-------|
| `outputs` | `[{...execution results...}]` | `[]` |
| `execution_count` | `3` | `null` |
| Cell metadata | `{"collapsed": true, "scrolled": false}` | `{}` |
| Top-level metadata | Unsorted keys | Sorted keys (deterministic) |

What stays: all cell source code, cell types, cell IDs, and `agent-repl` namespace metadata (like tags).

### Preserving agent-repl Tags

If you've tagged cells with directives, those survive cleaning:

```python
#| agent-tags: setup, critical
import pandas as pd
```

The cell's `metadata.agent-repl.tags` is preserved. All other cell metadata is stripped.

## `agent-repl git-setup`: Automatic Filtering

Run this once per repository to configure a git clean filter. Every `git add` of an `.ipynb` file will automatically pass through `agent-repl clean`.

```bash
agent-repl git-setup
```

Output:

```json
{
  "gitattributes": ".gitattributes",
  "git_config_clean": "git config filter.agent-repl-clean.clean 'agent-repl clean %f'",
  "git_config_smudge": "git config filter.agent-repl-clean.smudge cat"
}
```

The command creates (or appends to) `.gitattributes`:

```
*.ipynb filter=agent-repl-clean
```

Then run the two git config commands it prints. The `clean` filter runs `agent-repl clean` on every staged notebook. The `smudge` filter is `cat` -- notebooks are checked out as-is.

After setup, `git diff` shows only source code changes, never output noise.

## Output Normalization on Save

Even when you keep outputs (no `clean`), agent-repl normalizes them automatically every time a notebook is saved through the CLI. This removes artifacts that cause spurious diffs:

- **ANSI escape codes** stripped: `\x1b[31mError\x1b[0m` becomes `Error`
- **Object addresses** cleaned: `<Model at 0x7f3b2c>` becomes `<Model>`
- **Colab metadata** removed: the `application/vnd.google.colaboratory.intrinsic+json` key is dropped
- **Base64 trailing whitespace** trimmed

This normalization is idempotent and runs on every save through `exec`, `ix`, `run-all`, and `edit` commands. You don't need to opt in.

## Recommended Workflow

For teams committing notebooks to version control:

```bash
# 1. One-time setup
agent-repl git-setup
git config filter.agent-repl-clean.clean 'agent-repl clean %f'
git config filter.agent-repl-clean.smudge cat

# 2. Work normally -- execute cells, iterate
agent-repl ix analysis.ipynb -s 'df.describe()'

# 3. Commit -- outputs are stripped automatically by the git filter
git add analysis.ipynb
git commit -m "add summary statistics"
```

If you prefer manual control (no git filter), strip before committing:

```bash
# Manual: pipe clean output to the file, then commit
agent-repl clean analysis.ipynb > analysis-clean.ipynb
mv analysis-clean.ipynb analysis.ipynb
git add analysis.ipynb
git commit -m "add summary statistics"
```

Either way, the committed notebook has clean diffs: no outputs, no execution counts, no volatile metadata.

## Next Steps

- **Output filtering for agents**: [Output Filtering](output-filtering.md) -- how CLI output is filtered separately from the notebook file
- **Cell tagging**: See `#| agent-tags:` directives in the SKILL.md for tagging cells that survive `clean`
- **Full command reference**: `agent-repl clean --help` and `agent-repl git-setup --help`
