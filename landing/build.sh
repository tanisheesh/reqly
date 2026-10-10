#!/bin/sh
# Vercel build for reqly.tanisheesh.in (project root directory: landing/).
# Output: public/ = the landing page at / and the docs site at /docs/.
set -eu

rm -rf public
mkdir -p public
cp index.html public/

python3 -m venv /tmp/reqly-docs-venv
/tmp/reqly-docs-venv/bin/pip install --quiet --disable-pip-version-check -r requirements-docs.txt
/tmp/reqly-docs-venv/bin/mkdocs build --strict --site-dir public/docs
