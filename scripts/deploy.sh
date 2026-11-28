#!/usr/bin/env bash
# Mac から Pi へ SoiKaxa を送り、インストールまで行う。
#
#   scripts/deploy.sh soi@soikaxa.local                 # 初回: 送信 → install.sh（OS 更新込み）→ 再起動
#   scripts/deploy.sh soi@soikaxa.local --music ~/Development/mp3/Test
#                                                       # 上に加えてフォルダの MP3 を送る（フォルダ名 = プレイリスト名）
#   scripts/deploy.sh soi@soikaxa.local --update        # 2 回目以降: コードと設定を反映（OS 更新なし・数分）。
#                                                       # 起動オプションを変えたときだけ再起動する
#   scripts/deploy.sh soi@soikaxa.local --code-only     # Python / Web / LCD のコードだけ送ってサービスを再起動（数秒）
set -euo pipefail
PI="${1:?使い方: scripts/deploy.sh ユーザー名@soikaxa.local [--music フォルダ] [--update | --code-only]}"
shift
MUSIC="" MODE=full
while [[ $# -gt 0 ]]; do
  case "$1" in
    --music) MUSIC="${2:?--music にはフォルダを指定}"; shift 2 ;;
    --code-only) MODE=code; shift ;;
    --update) MODE=update; shift ;;
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

send_music() {
  [[ -n "$MUSIC" ]] || return 0
  local name
  name="$(basename "$MUSIC")"
  echo "== 曲を送る（プレイリスト「${name}」） =="
  rsync -a --include '*/' --include '*.mp3' --include '*.MP3' --exclude '*' "${MUSIC%/}/" "$PI:/var/lib/soikaxa/music/$name/"
}

if [[ $MODE == code ]]; then
  send_music
  ssh -t "$PI" 'sudo systemctl restart "soikaxa@$USER" && systemctl is-active "soikaxa@$USER"'
  # 設定（スクリプト側の変更）が入っていない実機には --update を案内する
  ssh "$PI" 'test -f ~/.config/wireplumber/bluetooth.lua.d/80-soikaxa-no-logind.lua &&
    grep -q vt.global_cursor_default=0 /boot/firmware/cmdline.txt' ||
    echo "注意: この Pi には最新の設定が入っていません。一度 scripts/deploy.sh $PI --update を実行してください"
  exit 0
fi

if [[ $MODE == update ]]; then
  echo "== コードと設定を反映（OS 更新なし） =="
  ssh -t "$PI" 'sudo bash ~/soikaxa/scripts/install.sh --upgrade'
  send_music
  if ssh "$PI" 'test -f /run/soikaxa-reboot-required'; then
    echo "== 起動オプションを変えたので再起動 =="
    ssh "$PI" 'sudo reboot' || true
    echo "1 分ほどで元の画面に戻ります。"
  else
    ssh -t "$PI" 'sudo systemctl restart "soikaxa@$USER" && systemctl is-active "soikaxa@$USER"'
  fi
  exit 0
fi

echo "== インストール（10〜20 分） =="
ssh -t "$PI" 'sudo bash ~/soikaxa/scripts/install.sh'
send_music

echo "== 再起動 =="
ssh "$PI" 'sudo reboot' || true
echo "1 分ほどで LCD に QR と 6 桁の設定コードが出ます。スマホでその QR を読んでください。"
