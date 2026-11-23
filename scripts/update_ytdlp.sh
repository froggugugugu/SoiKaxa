#!/usr/bin/env bash
# yt-dlp を nightly（プレリリース）へ更新し、動かなければ前版へ戻す。
# 使い方: update_ytdlp.sh <venv パス>
set -euo pipefail
VENV="${1:?venv path required}"
PIP="$VENV/bin/pip"
YTDLP="$VENV/bin/yt-dlp"

before="$("$YTDLP" --version 2>/dev/null || echo none)"
if "$PIP" install --quiet --upgrade --pre "yt-dlp[default]" && "$YTDLP" --version >/dev/null 2>&1; then
  echo "yt-dlp: $before -> $("$YTDLP" --version)"
  exit 0
fi
echo "yt-dlp update failed; restoring $before" >&2
if [[ "$before" != none ]]; then
  "$PIP" install --quiet "yt-dlp==$before"
fi
exit 1
