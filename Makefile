.PHONY: all build build-ui dev test seed

all: build

build: build-ui

build-ui:
	bash scripts/dev.sh build-ui

dev:
	bash scripts/dev.sh

test:
	bash scripts/dev.sh test

seed:
	bash scripts/dev.sh seed
