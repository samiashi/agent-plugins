.PHONY: check lint test validate fix codex-skills

check: lint test validate ## Run everything CI runs, except actionlint

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run python scripts/check_plugins.py check --base origin/main
	uv run zizmor --offline .

test:
	python3 -m unittest discover -s tests -t .

validate:
	npx --no-install claude plugin validate .
	for plugin in plugins/*/; do npx --no-install claude plugin validate "$$plugin"; done

fix:
	uv run ruff check --fix .
	uv run ruff format .

codex-skills: ## Regenerate every plugin's Codex skills after editing skills/
	for script in plugins/*/scripts/build_codex_skills.py; do python3 "$$script"; done
