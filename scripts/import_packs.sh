#!/usr/bin/env bash
# Import purchased Synty archives: verify (SHA-256 dedupe + CRC), store the original,
# extract safely and index with synty-inventory.
#
#   scripts/import_packs.sh ~/Downloads/POLYGON_*.zip [--pack polygon-city]
#   SYNTY_MCP_CONFIG=config.yaml scripts/import_packs.sh /mnt/usb/synty/*.unitypackage
#
# In Docker:  docker compose exec synty-mcp synty-mcp import /data/inbox/<file>.zip
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
bin="${SYNTY_MCP_BIN:-$here/.venv/bin/synty-mcp}"
[ -x "$bin" ] || bin="synty-mcp"
if [ "$#" -eq 0 ]; then
  echo "usage: $0 ARCHIVE... [--pack SLUG]" >&2
  exit 2
fi
files=(); pack=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --pack) pack=(--pack "$2"); shift 2 ;;
    *) files+=("$1"); shift ;;
  esac
done
exec "$bin" ${SYNTY_MCP_CONFIG:+--config "$SYNTY_MCP_CONFIG"} import "${pack[@]}" "${files[@]}"
