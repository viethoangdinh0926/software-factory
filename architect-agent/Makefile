# Architect Agent — build UI into backend and run the service
#
# Usage:
#   make            # install + build + run
#   make deploy     # same as default
#   make run        # run backend (expects UI already built)
#   make build      # build React UI into backend static/
#   make install    # install UI + Python deps
#   make clean      # remove build artifacts and local venv caches

AGENT_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
UI_DIR     := $(AGENT_ROOT)/ui
BACKEND_DIR := $(AGENT_ROOT)/backend
ENV_FILE   := $(AGENT_ROOT)/.env

.PHONY: all deploy install install-ui install-backend build run stop clean env help

all: deploy

help:
	@echo "Targets:"
	@echo "  make deploy   Install deps, build UI, run architect-agent"
	@echo "  make install  Install UI (npm) and backend (uv) dependencies"
	@echo "  make build    Build React UI into backend static/"
	@echo "  make run      Start the backend service"
	@echo "  make env      Create .env from .env.example if missing"
	@echo "  make clean    Remove UI/backend build artifacts"

deploy: env install build run

env:
	@if [ ! -f "$(ENV_FILE)" ]; then \
		cp "$(AGENT_ROOT)/.env.example" "$(ENV_FILE)"; \
		echo "Created $(ENV_FILE) from .env.example"; \
	else \
		echo "Using existing $(ENV_FILE)"; \
	fi

install: install-ui install-backend

install-ui:
	cd "$(UI_DIR)" && npm install

install-backend:
	cd "$(BACKEND_DIR)" && uv sync

build:
	cd "$(UI_DIR)" && npm run build

run:
	cd "$(BACKEND_DIR)" && uv run architect-agent

clean:
	rm -rf "$(UI_DIR)/node_modules" "$(UI_DIR)/dist"
	rm -rf "$(BACKEND_DIR)/src/architect_agent/static"
	rm -rf "$(BACKEND_DIR)/.venv"
	@echo "Cleaned UI node_modules/dist, backend static/, and backend .venv"
