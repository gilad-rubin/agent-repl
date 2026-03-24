.PHONY: package package-cli package-ext install-dev verify-install install-ext clean

VERSION := $(shell python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")

# Build both artifacts for offline deployment
package: package-cli package-ext
	@echo ""
	@echo "Offline artifacts ready:"
	@ls -1 dist/*.whl extension/*.vsix
	@echo ""
	@echo "Install on air-gapped machine:"
	@echo "  pip install dist/agent_repl-$(VERSION)-py3-none-any.whl"
	@echo "  code --install-extension extension/agent-repl-$(VERSION).vsix"

# Python wheel (includes requests dep)
package-cli:
	uv build --wheel

# Install the current checkout as the globally available CLI tool.
install-dev:
	uv tool install . --reinstall

# Verify the globally installed CLI exposes the current command surface.
verify-install:
	@echo "agent-repl version:"
	@if ! agent-repl --version; then \
		echo ""; \
		echo "installed CLI is stale or missing --version; run 'make install-dev'"; \
		exit 1; \
	fi
	@echo ""
	@echo "checking v2 command availability..."
	@if ! agent-repl v2 --help >/dev/null; then \
		echo "installed CLI is missing v2; run 'make install-dev'"; \
		exit 1; \
	fi
	@echo "installed CLI exposes v2"

# VS Code extension (.vsix)
package-ext:
	cd extension && npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-$(VERSION).vsix

# Build and reinstall the VS Code extension into the local editor.
install-ext: package-ext
	code --install-extension extension/agent-repl-$(VERSION).vsix --force
	@echo ""
	@echo "If a VS Code window is already open, reload or reopen that window."
	@echo "Then run: agent-repl reload --pretty"
	@echo "and confirm extension_root points at giladrubin.agent-repl-$(VERSION)."
