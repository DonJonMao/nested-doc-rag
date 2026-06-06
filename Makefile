.PHONY: install test lint show-config

install:
	python -m pip install -e .

test:
	pytest

lint:
	ruff check .

show-config:
	python -m nested_doc_rag.cli show-config --config config/local.example.yaml
