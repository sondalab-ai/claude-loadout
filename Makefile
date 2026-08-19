.PHONY: help install dev test uninstall

help:
	@echo "make install   - (re)install the CLI from this repo via pipx (--force)"
	@echo "make dev       - editable install with dev extras (pytest) into a local venv"
	@echo "make test      - run the test suite with pytest"
	@echo "make uninstall - run scripts/uninstall.sh"

install:
	pipx install . --force

dev:
	pip install -e ".[dev]"

test:
	python3 -m pytest

uninstall:
	bash scripts/uninstall.sh
