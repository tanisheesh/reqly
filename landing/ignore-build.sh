#!/bin/sh
# Vercel "Ignored Build Step" for landing/: exit 0 skips the build, exit 1 runs it.
# Any other exit code fails the deployment, so every git error must end in exit 1.
prev="${VERCEL_GIT_PREVIOUS_SHA:-}"

# Vercel clones shallowly, so the last deployed commit may not be here. Can't tell: build.
if [ -z "$prev" ] || ! git cat-file -e "${prev}^{commit}" 2>/dev/null; then
  echo "Last deployed commit not available; building."
  exit 1
fi

if git diff --quiet "$prev" HEAD -- .; then
  echo "No changes in landing/ since $prev; skipping."
  exit 0
fi
echo "landing/ changed since $prev; building."
exit 1
