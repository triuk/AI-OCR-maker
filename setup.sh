#!/usr/bin/env bash
# Prepares AI-OCR-maker without sudo: a pinned uv, a pinned Python 3.12, the locked dependencies
# and the hash-checked tessdata_best models. Safe to run repeatedly; every step skips work already done.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

OCRMAKER_UV_VERSION=0.13.6
OCRMAKER_UV_SHA256=f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645
OCRMAKER_UV_ARCHIVE=uv-x86_64-unknown-linux-gnu

for ocrmaker_option in "$@"; do
  case "$ocrmaker_option" in
    *) echo "Použití: ./setup.sh" >&2; exit 2;;
  esac
done
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo "AI-OCR-maker podporuje jen Linux na x86_64." >&2
  exit 1
fi

# uv lives outside the checkout so that every clone and worktree shares one copy;
# Python and packages then come from uv's default shared caches.
ocrmaker_tools="${XDG_DATA_HOME:-$HOME/.local/share}/ai-ocr-maker/tools"
ocrmaker_uv="$ocrmaker_tools/uv-$OCRMAKER_UV_VERSION/uv"
if [[ ! -x $ocrmaker_uv ]]; then
  echo "Stahuji uv $OCRMAKER_UV_VERSION…"
  mkdir -p "$ocrmaker_tools"
  ocrmaker_download="$(mktemp -d "$ocrmaker_tools/.uv-download.XXXXXX")"
  trap 'rm -rf -- "$ocrmaker_download"' EXIT
  curl -fsSL --retry 3 -o "$ocrmaker_download/uv.tar.gz" \
    "https://github.com/astral-sh/uv/releases/download/$OCRMAKER_UV_VERSION/$OCRMAKER_UV_ARCHIVE.tar.gz"
  if ! echo "$OCRMAKER_UV_SHA256  $ocrmaker_download/uv.tar.gz" | sha256sum --check --status; then
    echo "Stažený uv nemá očekávaný otisk SHA-256, instalaci přerušuji." >&2
    exit 1
  fi
  tar -xzf "$ocrmaker_download/uv.tar.gz" -C "$ocrmaker_download"
  mkdir -p "$ocrmaker_download/uv-$OCRMAKER_UV_VERSION"
  mv "$ocrmaker_download/$OCRMAKER_UV_ARCHIVE/uv" "$ocrmaker_download/$OCRMAKER_UV_ARCHIVE/uvx" \
    "$ocrmaker_download/uv-$OCRMAKER_UV_VERSION/"
  # A concurrent setup may have finished first; its copy is identical.
  mv -T "$ocrmaker_download/uv-$OCRMAKER_UV_VERSION" "$ocrmaker_tools/uv-$OCRMAKER_UV_VERSION" 2>/dev/null || true
fi

# .python-version pins the interpreter and uv.lock pins every package with hashes.
UV_PYTHON_DOWNLOADS=automatic "$ocrmaker_uv" sync --locked
.venv/bin/python -m ocrmaker.tessdata
echo "Připraveno. Aplikaci spustíte pomocí ./start.sh"
