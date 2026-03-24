# Documentation Style

- **Motivation first** - explain why a capability matters before listing flags.
- **Public vs internal** - keep user-facing product docs in `docs/`; move architecture, design, and engineering notes into `dev/`.
- **Code-first examples** - every major concept should include a runnable command example.
- **Real workflows** - prefer notebooks, data analysis, RAG, and agentic workflows over placeholder examples.
- **Compact intros** - start pages with 3-4 short bullets that summarize the key takeaways.
- **Explicit failure guidance** - when describing errors, include “How to fix:” guidance instead of vague troubleshooting.
- **Runtime-first language** - describe `agent-repl` as the notebook runtime authority and the editor as an optional projection client.
