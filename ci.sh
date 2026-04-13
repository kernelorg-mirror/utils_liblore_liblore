#!/usr/bin/env sh

set -eu

uv run mypy .
uv run pytest --durations=0
