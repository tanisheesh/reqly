#!/bin/sh
# Vercel build for reqly.tanisheesh.in (project root directory: landing/).
# Output: public/ = the landing page at / and the docs site at /docs/.
set -eu

rm -rf public
mkdir -p public
cp index.html public/

PY="$(command -v python3 || command -v python)"
echo "Using $PY ($("$PY" --version 2>&1))"

# The docs tools go into /tmp, not the project; no virtualenv needed.
"$PY" -m pip install --quiet --disable-pip-version-check --target /tmp/reqly-docs-deps -r requirements-docs.txt
PYTHONPATH=/tmp/reqly-docs-deps "$PY" -m mkdocs build --strict --site-dir public/docs
