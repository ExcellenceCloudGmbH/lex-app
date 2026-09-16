#!/usr/bin/env bash
# Capture every documentation figure, then render them.
#
#   scripts/capture_figures.sh [--check] [--pac /path/to/process-admin-general-client]
#
# Two repos and four steps sit between a docs page and a picture of the thing
# it describes: start the app, drive it, write a PNG and a JSON of anchor
# boxes, turn the pair into an annotated SVG. Every one of them has an
# environment prerequisite, and every one of them fails differently when the
# prerequisite is missing -- Playwright reports a timeout when Vite is absent,
# the renderer reports a missing shot when the capture never ran, and the
# `lex --help` figure photographs `flex`, the lexical analyser, when the wrong
# `lex` is first on PATH.
#
# So this checks first and says what is missing, rather than starting work that
# will fail in ten minutes with an error about something else.
#
#   --check   report what is and is not available, then stop.
#
# If the prerequisites are not reachable -- most often the private
# @react-admin registry token, which is a repository secret and is on nobody's
# laptop -- run the workflow instead:
#
#   gh workflow run capture_figures.yml --repo ExcellenceCloudGmbH/lex-app
#
# (installed from docs/ci-cd/capture-figures-workflow.yml)
#
# It does the same three steps with the secret available, and uploads both the
# rendered SVGs and the raw shots.
set -euo pipefail

LEX_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PAC="${LEX_PAC_DIR:-$(cd "$LEX_ROOT/.." && pwd)/process-admin-general-client}"
SHOTS="${LEX_DOCSHOT_DIR:-$LEX_ROOT/docs/shots}"
DOCS_IMAGES="${LEX_DOCS_IMAGES:-$(cd "$LEX_ROOT/.." && pwd)/lex-app-docs/content/images}"
CHECK_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=1; shift ;;
    --pac)   PAC="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

missing=0
have() {  # have <label> <test-command...>
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    printf '  ok      %s\n' "$label"
  else
    printf '  MISSING %s\n' "$label"
    missing=$((missing + 1))
  fi
}

echo "Prerequisites"
have "node (Playwright, and Vite for the React app the embeds need)" command -v node
have "the PAC checkout at $PAC" test -d "$PAC/e2e"
have "PAC dependencies installed (node_modules)" test -d "$PAC/node_modules"
have "python3" command -v python3
have "PyYAML (the renderer reads docs/figures.yml)" python3 -c "import yaml"
have "the lex-app CLI on PATH" command -v lex
have "lex-app-docs images directory at $DOCS_IMAGES" test -d "$DOCS_IMAGES"

# `lex` resolving to flex is the trap worth naming: the capture succeeds, the
# figure is of the wrong program's usage message, and the anchors catch it only
# after a confusing minute.
if command -v lex >/dev/null 2>&1 && ! lex --help 2>&1 | head -1 | grep -qi "lex-app"; then
  echo "  WARNING the \`lex\` on PATH is not lex-app (flex, probably) — the CLI figure will photograph the wrong program"
fi

if [ "$missing" -gt 0 ]; then
  echo
  echo "$missing prerequisite(s) missing. Nothing has been run."
  [ "$CHECK_ONLY" -eq 1 ] && exit 0
  exit 1
fi
echo "  all present"
[ "$CHECK_ONLY" -eq 1 ] && exit 0

echo
echo "1/3  Application figures"
(
  cd "$PAC"
  LEX_DOCSHOT_DIR="$SHOTS" npx playwright test docshots.spec.ts --reporter=line
)

echo
echo "2/3  Streamlit figures"
# Its own run, and its own flag: the Streamlit webServer only starts when this
# is set, and booting it for the other spec would cost 90 seconds for nothing.
(
  cd "$PAC"
  LEX_DOCSHOT_STREAMLIT=1 LEX_DOCSHOT_DIR="$SHOTS" \
    npx playwright test docshots-streamlit.spec.ts --reporter=line
)

echo
echo "3/3  Rendering"
cd "$LEX_ROOT"
PYTHONPATH=.github/scripts python3 -m docshots build docs/figures.yml --out "$DOCS_IMAGES"

echo
echo "Done. Figures written to $DOCS_IMAGES"
echo "Next: commit them in lex-app-docs with paths written FROM THE CONTENT ROOT"
echo "      (images/... , never ../../images/... — Quartz adds the climb itself)."
