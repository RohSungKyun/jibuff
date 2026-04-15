#!/usr/bin/env bash
# jibuff installer
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/RohSungKyun/jibuff/main/scripts/install.sh | bash
#   curl -fsSL ... | bash -s -- --extras rtc,mcp

set -euo pipefail

REPO="RohSungKyun/jibuff"
PACKAGE="jibuff"
MIN_PYTHON="3.12"

# ── helpers ────────────────────────────────────────────────────────────────────

info()  { printf '\033[0;34m[jibuff]\033[0m %s\n' "$*"; }
ok()    { printf '\033[0;32m[jibuff]\033[0m %s\n' "$*"; }
err()   { printf '\033[0;31m[jibuff]\033[0m %s\n' "$*" >&2; exit 1; }

# ── parse args ─────────────────────────────────────────────────────────────────

EXTRAS=""
for arg in "$@"; do
  case "$arg" in
    --extras=*) EXTRAS="${arg#--extras=}" ;;
    --extras)   shift; EXTRAS="$1" ;;
    --help|-h)
      echo "Usage: install.sh [--extras rtc,mcp]"
      echo ""
      echo "Extras:"
      echo "  rtc   Playwright-based RTC validators"
      echo "  mcp   MCP stdio server support"
      echo "  all   Everything above"
      exit 0 ;;
  esac
done

# ── python check ───────────────────────────────────────────────────────────────

PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
    # compare version
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    req_major=$(echo "$MIN_PYTHON" | cut -d. -f1)
    req_minor=$(echo "$MIN_PYTHON" | cut -d. -f2)
    if [ "$major" -gt "$req_major" ] || { [ "$major" -eq "$req_major" ] && [ "$minor" -ge "$req_minor" ]; }; then
      PYTHON="$cmd"
      break
    fi
  fi
done

[ -z "$PYTHON" ] && err "Python $MIN_PYTHON+ is required. Install from https://python.org"
info "Using $($PYTHON --version)"

# ── pip check ──────────────────────────────────────────────────────────────────

"$PYTHON" -m pip --version &>/dev/null || err "pip not found. Run: $PYTHON -m ensurepip"

# ── install ────────────────────────────────────────────────────────────────────

if [ -n "$EXTRAS" ]; then
  INSTALL_TARGET="${PACKAGE}[${EXTRAS}]"
else
  INSTALL_TARGET="$PACKAGE"
fi

info "Installing $INSTALL_TARGET ..."
"$PYTHON" -m pip install --quiet --upgrade "$INSTALL_TARGET"

# ── verify ─────────────────────────────────────────────────────────────────────

if command -v jb &>/dev/null; then
  ok "Installed successfully!"
  echo ""
  echo "  jb interview \"your idea\"   — clarify requirements"
  echo "  jb run --mode quick        — run the loop"
  echo "  jb run --mode rtc          — run with RTC validators"
  echo "  jb status                  — check loop state"
  echo "  jb mcp serve               — start MCP stdio server"
  echo ""
  ok "Run 'jb --help' to get started."
else
  ok "Package installed. 'jb' not in PATH yet."
  echo ""
  echo "  Add this to your shell profile:"
  echo "    export PATH=\"\$($PYTHON -m site --user-base)/bin:\$PATH\""
  echo ""
  echo "  Then reload: source ~/.zshrc  (or ~/.bashrc)"
fi
