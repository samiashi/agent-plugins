.PHONY: check lint test validate fix codex-skills

check: lint test validate ## Run everything CI runs, except actionlint

lint:
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked python scripts/check_plugins.py check --base origin/main
	uv run --locked python scripts/check_release_age.py check --base origin/main
	uv run --locked zizmor --offline .

test:
	uv run --locked python -m unittest discover -s tests -t .

validate:
	npx --no-install claude plugin validate .
	for plugin in plugins/*/; do npx --no-install claude plugin validate "$$plugin"; done

fix:
	uv run --locked ruff check --fix .
	uv run --locked ruff format .

codex-skills: ## Regenerate every plugin's Codex skills after editing skills/
	for script in plugins/*/scripts/build_codex_skills.py; do python3 "$$script"; done
