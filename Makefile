# Software Factory — deploy and tear down every agent
#
# Usage:
#   make deploy      # env + install + build + start all agents
#   make teardown    # stop all agents
#   make start       # start agents that are not already running
#   make status      # show pid / health for each agent
#   make logs        # tail combined agent logs
#
# Agents are started in the background. Per-agent `make deploy` still runs
# a single service in the foreground.

FACTORY_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
RUN_DIR      := $(FACTORY_ROOT)/.run
START_TIMEOUT ?= 30

export PATH := $(HOME)/.local/bin:$(PATH)

# Add a directory here when a new agent package lands (must have a Makefile).
AGENTS := architect-agent orchestrator-agent

# Downstream first so A2A peers are listening before upstream starts.
START_ORDER := orchestrator-agent architect-agent
STOP_ORDER  := architect-agent orchestrator-agent

architect-agent_BIN  := architect-agent
architect-agent_HOST ?= 127.0.0.1
architect-agent_PORT ?= 8080

orchestrator-agent_BIN  := orchestrator-agent
orchestrator-agent_HOST ?= 127.0.0.1
orchestrator-agent_PORT ?= 8090

# Architect delivers design packages to the orchestrator when the factory is up.
export ORCHESTRATOR_AGENT_URL ?= http://127.0.0.1:$(orchestrator-agent_PORT)

.PHONY: all deploy teardown down stop start restart status logs help env install build clean

all: deploy

help:
	@echo "Software Factory"
	@echo ""
	@echo "  make deploy     Create .env files, install, build UIs, start all agents"
	@echo "  make teardown   Stop all agents (aliases: stop, down)"
	@echo "  make start      Start agents in the background (skip if already healthy)"
	@echo "  make restart    Tear down, then start"
	@echo "  make status     Pid and /healthz for each agent"
	@echo "  make logs       Tail logs under .run/"
	@echo "  make install    Install UI + Python deps for every agent"
	@echo "  make build      Build every agent UI into its backend static/"
	@echo "  make clean      Tear down, then remove agent build artifacts"
	@echo ""
	@echo "Agents: $(AGENTS)"
	@echo "  architect-agent     http://127.0.0.1:$(architect-agent_PORT)/"
	@echo "  orchestrator-agent  http://127.0.0.1:$(orchestrator-agent_PORT)/"

deploy: env install build start

teardown: stop
down: stop

restart: stop start

env:
	@for agent in $(AGENTS); do \
		$(MAKE) --no-print-directory -C "$(FACTORY_ROOT)/$$agent" env; \
	done

install:
	@for agent in $(AGENTS); do \
		$(MAKE) --no-print-directory -C "$(FACTORY_ROOT)/$$agent" install; \
	done

build:
	@for agent in $(AGENTS); do \
		$(MAKE) --no-print-directory -C "$(FACTORY_ROOT)/$$agent" build; \
	done

start: env
	@mkdir -p "$(RUN_DIR)"
	@for agent in $(START_ORDER); do \
		$(MAKE) --no-print-directory -C "$(FACTORY_ROOT)" _start AGENT="$$agent" || exit 1; \
	done
	@$(MAKE) --no-print-directory -C "$(FACTORY_ROOT)" status

stop:
	@for agent in $(STOP_ORDER); do \
		$(MAKE) --no-print-directory -C "$(FACTORY_ROOT)" _stop AGENT="$$agent"; \
	done

status:
	@for agent in $(AGENTS); do \
		$(MAKE) --no-print-directory -C "$(FACTORY_ROOT)" _status AGENT="$$agent"; \
	done

logs:
	@mkdir -p "$(RUN_DIR)"
	@touch $(addprefix $(RUN_DIR)/,$(addsuffix .log,$(AGENTS)))
	@echo "Tailing $(RUN_DIR)/*.log (Ctrl-C to stop)"
	@tail -n 50 -F $(addprefix $(RUN_DIR)/,$(addsuffix .log,$(AGENTS)))

clean: stop
	@for agent in $(AGENTS); do \
		$(MAKE) --no-print-directory -C "$(FACTORY_ROOT)/$$agent" clean; \
	done
	@rm -rf "$(RUN_DIR)"
	@echo "Removed $(RUN_DIR)"

# --- internal per-agent helpers (AGENT=...) --------------------------------

.PHONY: _start _stop _status

_start:
	@if [ -z "$(AGENT)" ]; then echo "AGENT is required" >&2; exit 2; fi
	@bin="$($(AGENT)_BIN)"; \
	pid_file="$(RUN_DIR)/$(AGENT).pid"; \
	log_file="$(RUN_DIR)/$(AGENT).log"; \
	env_file="$(FACTORY_ROOT)/$(AGENT)/.env"; \
	host="$$(awk -F= '/^HOST=/{print $$2}' "$$env_file" 2>/dev/null | tail -1)"; \
	port="$$(awk -F= '/^PORT=/{print $$2}' "$$env_file" 2>/dev/null | tail -1)"; \
	host="$${host:-$($(AGENT)_HOST)}"; \
	port="$${port:-$($(AGENT)_PORT)}"; \
	health="http://$$host:$$port/healthz"; \
	mkdir -p "$(RUN_DIR)"; \
	if curl -sf "$$health" >/dev/null 2>&1; then \
		echo "$$bin already healthy at $$health"; \
		exit 0; \
	fi; \
	if [ -f "$$pid_file" ] && kill -0 "$$(cat "$$pid_file")" 2>/dev/null; then \
		echo "$$bin pid $$(cat "$$pid_file") is up but not healthy; restarting"; \
		$(MAKE) --no-print-directory -C "$(FACTORY_ROOT)" _stop AGENT="$(AGENT)"; \
	fi; \
	echo "Starting $$bin on $$host:$$port ..."; \
	cd "$(FACTORY_ROOT)/$(AGENT)/backend" || exit 1; \
	nohup uv run "$$bin" >> "$$log_file" 2>&1 < /dev/null & \
	echo $$! > "$$pid_file"; \
	i=0; \
	while [ $$i -lt $(START_TIMEOUT) ]; do \
		if curl -sf "$$health" >/dev/null 2>&1; then \
			echo "$$bin healthy at $$health (pid $$(cat "$$pid_file"))"; \
			exit 0; \
		fi; \
		if ! kill -0 "$$(cat "$$pid_file")" 2>/dev/null; then \
			echo "$$bin exited during startup; last log lines:" >&2; \
			tail -n 40 "$$log_file" >&2 || true; \
			rm -f "$$pid_file"; \
			exit 1; \
		fi; \
		i=$$((i+1)); \
		sleep 1; \
	done; \
	echo "$$bin did not become healthy within $(START_TIMEOUT)s; see $$log_file" >&2; \
	tail -n 40 "$$log_file" >&2 || true; \
	exit 1

_stop:
	@if [ -z "$(AGENT)" ]; then echo "AGENT is required" >&2; exit 2; fi
	@bin="$($(AGENT)_BIN)"; \
	pid_file="$(RUN_DIR)/$(AGENT).pid"; \
	env_file="$(FACTORY_ROOT)/$(AGENT)/.env"; \
	port="$$(awk -F= '/^PORT=/{print $$2}' "$$env_file" 2>/dev/null | tail -1)"; \
	port="$${port:-$($(AGENT)_PORT)}"; \
	if [ -f "$$pid_file" ]; then \
		pid=$$(cat "$$pid_file"); \
		if kill -0 "$$pid" 2>/dev/null; then \
			echo "Stopping $$bin (pid $$pid) ..."; \
			pids="$$pid"; \
			for p in $$(pgrep -P "$$pid" || true); do \
				pids="$$pids $$p $$(pgrep -P "$$p" || true)"; \
			done; \
			kill $$pids 2>/dev/null || true; \
			j=0; \
			while [ $$j -lt 20 ] && kill -0 "$$pid" 2>/dev/null; do \
				sleep 0.2; \
				j=$$((j+1)); \
			done; \
			if kill -0 "$$pid" 2>/dev/null; then \
				kill -9 $$pids 2>/dev/null || true; \
			fi; \
		fi; \
		rm -f "$$pid_file"; \
	fi; \
	if command -v fuser >/dev/null 2>&1; then \
		fuser -k "$$port/tcp" >/dev/null 2>&1 || true; \
	fi; \
	echo "$$bin stopped"

_status:
	@if [ -z "$(AGENT)" ]; then echo "AGENT is required" >&2; exit 2; fi
	@bin="$($(AGENT)_BIN)"; \
	pid_file="$(RUN_DIR)/$(AGENT).pid"; \
	env_file="$(FACTORY_ROOT)/$(AGENT)/.env"; \
	host="$$(awk -F= '/^HOST=/{print $$2}' "$$env_file" 2>/dev/null | tail -1)"; \
	port="$$(awk -F= '/^PORT=/{print $$2}' "$$env_file" 2>/dev/null | tail -1)"; \
	host="$${host:-$($(AGENT)_HOST)}"; \
	port="$${port:-$($(AGENT)_PORT)}"; \
	health="http://$$host:$$port/healthz"; \
	if [ -f "$$pid_file" ] && kill -0 "$$(cat "$$pid_file")" 2>/dev/null; then \
		pid=$$(cat "$$pid_file"); \
		if curl -sf "$$health" >/dev/null 2>&1; then \
			echo "$$bin  running  pid=$$pid  $$health"; \
		else \
			echo "$$bin  running  pid=$$pid  $$health (not healthy)"; \
		fi; \
	else \
		if curl -sf "$$health" >/dev/null 2>&1; then \
			echo "$$bin  running  (no pid file)  $$health"; \
		else \
			echo "$$bin  stopped"; \
		fi; \
	fi
