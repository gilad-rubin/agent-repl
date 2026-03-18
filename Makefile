.PHONY: package package-cli package-ext clean

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

# VS Code extension (.vsix)
package-ext:
	cd extension && npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-$(VERSION).vsix
