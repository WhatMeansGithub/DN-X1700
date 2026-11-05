#!/usr/bin/env bash
# Publishes site/ (the web tool) to the gh-pages branch, which GitHub Pages serves.
# Fallback for when GitHub Actions is unavailable (the workflow in .github/workflows/pages.yml does the same job, with checks).
# Only the three page files are published. gh-pages is force-pushed: it is a build output, never edit it by hand.
#
#   tools/publish_site.sh        run from anywhere inside the repository, after committing your changes to site/
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
if ! git -C "$ROOT" diff --quiet HEAD -- site; then
  echo "site/ has uncommitted changes. Commit them first so what is published matches the repository." >&2
  exit 1
fi

REMOTE=$(git -C "$ROOT" remote get-url origin)
NAME=$(git -C "$ROOT" config user.name)
EMAIL=$(git -C "$ROOT" config user.email)
SRC=$(git -C "$ROOT" rev-parse --short HEAD)

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
for f in index.html patcher.js patches.js; do cp "$ROOT/site/$f" "$TMP/$f"; done
touch "$TMP/.nojekyll"                                   # serve the files as they are, no Jekyll processing

git -C "$TMP" init -q -b gh-pages
git -C "$TMP" config user.name "$NAME"
git -C "$TMP" config user.email "$EMAIL"
git -C "$TMP" add -A
git -C "$TMP" commit -q -m "Publish site from master $SRC"
git -C "$TMP" push -q --force "$REMOTE" gh-pages
echo "Published site/ from master $SRC to the gh-pages branch."
