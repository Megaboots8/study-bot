# Repo Overview

**Project:** agent-init
**Language:** JavaScript (Node.js)

## Purpose

Provides the `agent-init` CLI and companion markdown templates that
scaffold agent guidance files into any repository.

## Directory structure

- `bin/` — CLI entry point (`agent-init.js`)
- `templates/` — markdown templates copied into target repos
- `test/` — smoke test suite
- `docs/` — design spec and implementation plan

## Key entry points

- `bin/agent-init.js` — CLI script
- `templates/AGENTS.md` — router template
- `templates/.agents/` — skill and topic templates
