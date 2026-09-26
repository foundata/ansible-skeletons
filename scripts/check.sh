#!/usr/bin/env sh
# Run the repository checks from any working directory.

set -u

script_dir="$(CDPATH='' cd -- "$(dirname -- "${0}")" && pwd)" || exit 1
cd "${script_dir}/.." || exit 1
exec uv run --locked python -m scripts.check "$@"
