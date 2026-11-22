#!/usr/bin/env bash
# Mac から Pi へ SoiKaxa を送り、インストールまで行う。
#
#   scripts/deploy.sh soi@soikaxa.local                 # 初回: 送信 → install.sh → 再起動
#   scripts/deploy.sh soi@soikaxa.local --music ~/Development/mp3/Test
#                                                       # 上に加えてフォルダの MP3 を送る（フォルダ名 = プレイリスト名）
#   scripts/deploy.sh soi@soikaxa.local --code-only     # 2 回目以降: コードだけ送ってサービスを再起動
set -euo pipefail
PI="${1:?使い方: scripts/deploy.sh ユーザー名@soikaxa.local [--music フォルダ] [--code-only]}"
shift
MUSIC="" CODE_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --music) MUSIC="${2:?--music にはフォルダを指定}"; shift 2 ;;
    --code-only) CODE_ONLY=1; shift ;;
    *) echo "不明なオプション: $1"; exit 1 ;;
  esac
done
cd "$(dirname "$0")/.."

echo "== コードを送る（$PI:~/soikaxa） =="
rsync -a --delete \
  --exclude .venv --exclude .git --exclude .data --exclude testreport \
  --exclude input --exclude output --exclude .claude --exclude .agents --exclude .codex \
  --exclude __pycache__ --exclude .DS_Store --exclude .pytest_cache --exclude .mypy_cache --exclude .ruff_cache \
  ./ "$PI":soikaxa/

if [[ $CODE_ONLY == 1 ]]; then
  ssh -t "$PI" 'sudo systemctl restart "soikaxa@$USER" && systemctl is-active "soikaxa@$USER"'
  exit 0
fi

echo "== インストール（10〜20 分） =="
ssh -t "$PI" 'sudo bash ~/soikaxa/scripts/install.sh'

if [[ -n "$MUSIC" ]]; then
  name="$(basename "$MUSIC")"
  echo "== 曲を送る（プレイリスト「$name」） =="
  rsync -a --include '*/' --include '*.mp3' --include '*.MP3' --exclude '*' "${MUSIC%/}/" "$PI:/var/lib/soikaxa/music/$name/"
fi

echo "== 再起動 =="
ssh "$PI" 'sudo reboot' || true
echo "1 分ほどで LCD に QR と 6 桁の設定コードが出ます。スマホでその QR を読んでください。"
