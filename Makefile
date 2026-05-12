.PHONY: test build verify

build:
	npm run build

test:
	python -m compileall app tests
	pytest -q

verify: build test
