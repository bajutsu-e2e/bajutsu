.PHONY: test lint typecheck check

test:
	uv run pytest -q

lint:
	uv run ruff check .

typecheck:
	uv run mypy bajutsu

check: lint typecheck test

# Sample-app build / E2E targets live with their demos:
#   make -C demos/features sample-gen|sample-build|e2e|ui-test   (app/sample)
#   make -C demos/record   sample2-gen|sample2-build             (app/sample2)
