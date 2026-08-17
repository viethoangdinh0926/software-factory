# Orchestrator Agent — build UI into backend and run the service
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
# Read SSL_VERIFY from .env file (default to true for secure mode)
SSL_VERIFY ?= $(shell grep "^SSL_VERIFY=" "$(ENV_FILE)" 2>/dev/null | cut -d'=' -f2)
SSL_VERIFY ?= true

export PATH := $(HOME)/.local/bin:$(PATH)

.PHONY: all deploy install install-ui install-backend build run stop clean check-env help ensure-uv

all: deploy

help:
	@echo "Targets:"
	@echo "  make deploy   Install deps, build UI, run orchestrator-agent"
	@echo "  make install  Install UI (npm) and backend (uv) dependencies"
	@echo "  make build    Build React UI into backend static/"
	@echo "  make run      Start the backend service"
	@echo "  make check-env  Check that .env exists (required for deploy)"
	@echo "  make clean    Remove UI/backend build artifacts"
	@echo ""
	@echo "Options:"
	@echo "  SSL_VERIFY=false  Ignore SSL certificate verification (set in .env or env var)"

deploy: check-env ensure-uv install build run

check-env:
	@if [ ! -f "$(ENV_FILE)" ]; then \
		echo "Error: $(ENV_FILE) does not exist. Please copy .env.example to .env and configure it." >&2; \
		exit 1; \
	fi
	@echo "Using existing $(ENV_FILE)"

install: ensure-uv install-ui install-backend

ensure-uv:
	@command -v uv >/dev/null 2>&1 || { \
		echo "uv not found, installing..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
		echo "uv installed successfully"; \
	}

install-ui:
	cd "$(UI_DIR)" && npm install

install-backend:
	@if [ "$(SSL_VERIFY)" = "false" ]; then \
		cd "$(BACKEND_DIR)" && uv sync --allow-insecure-host files.pythonhosted.org; \
	else \
		cd "$(BACKEND_DIR)" && uv sync; \
	fi

build:
	cd "$(UI_DIR)" && npm run build

run:
	cd "$(BACKEND_DIR)" && uv run orchestrator-agent

clean:
	rm -rf "$(UI_DIR)/node_modules" "$(UI_DIR)/dist"
	rm -rf "$(BACKEND_DIR)/src/orchestrator_agent/static"
	rm -rf "$(BACKEND_DIR)/.venv"
	@echo "Cleaned UI node_modules/dist, backend static/, and backend .venv"
