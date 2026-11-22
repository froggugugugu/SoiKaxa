#!/usr/bin/env bash
# GitHub Releases の最新版に更新する。オーナー画面の「アップグレード」→ soikaxa-upgrade@<ユーザー>.service が
# root で実行する（03_install.sh が /usr/local/lib/soikaxa/upgrade.sh に root 所有でコピーしたもの）。
# 手動で実行する場合: sudo /usr/local/lib/soikaxa/upgrade.sh <ユーザー名>
#
# 進み具合は /var/lib/soikaxa-upgrade/upgrade.json に書く（オーナー画面が読む）。root だけが書ける場所にし、
# デーモン（データ領域に書ける）がシンボリックリンクを仕込んで root に別のファイルを書かせられないようにする。
# 更新前のコードを ~/soikaxa.prev に残し、新しい版が起動しなければそこへ戻す。
set -Eeuo pipefail
STATUS_DIR="${SOIKAXA_UPGRADE_DIR:-/var/lib/soikaxa-upgrade}"
STATUS="$STATUS_DIR/upgrade.json"
REPO="${SOIKAXA_RELEASE_REPO:-froggugugugu/SoiKaxa}"
FROM="" TO="" APPLIED=0 WORK=""

status() { # <state> <message>  state: running / done / failed / rolled_back
  local tmp
  tmp="$(mktemp "$STATUS_DIR/.upgrade.XXXXXX")"
  jq -n --arg s "$1" --arg m "$2" --arg f "${FROM}" --arg t "${TO}" --argjson at "$(date +%s)" \
    '{state: $s, message: $m, from: $f, to: $t, at: $at}' >"$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "$STATUS"
  echo "[$1] $2"
}
fail() { status failed "$1"; exit 1; }

install -d -m 0755 "$STATUS_DIR"
# 前回が running のまま終わっていれば（電源断・タイムアウト）、~/soikaxa は途中までしか入っていない
PREV_STATE="$(jq -r '.state // ""' "$STATUS" 2>/dev/null || true)"
trap '[[ -n "$WORK" ]] && rm -rf "$WORK"' EXIT

USER_NAME="${1:-}"
[[ -n "$USER_NAME" ]] || fail "ユーザー名が指定されていません"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6 || true)"
ROOT="$HOME_DIR/soikaxa"
PREV="$HOME_DIR/soikaxa.prev"
SERVICE="soikaxa@$USER_NAME.service"
[[ -n "$HOME_DIR" && -d "$ROOT" ]] || fail "$USER_NAME の ~/soikaxa がありません"
# root で rsync --delete するので、リンク先（/etc など）を消さないようシンボリックリンクは拒む
[[ ! -L "$ROOT" && ! -L "$PREV" ]] || fail "ホームの soikaxa か soikaxa.prev がシンボリックリンクです"
WORK="$(mktemp -d)"

install_tree() { SUDO_USER="$USER_NAME" bash "$ROOT/scripts/install.sh" --upgrade; }

healthy() { # 起動して（Type=notify なので READY=1 まで待つ）、20 秒落ちずに動き続けるか
  systemctl restart "$SERVICE" || return 1
  local n0
  n0="$(systemctl show -p NRestarts --value "$SERVICE")"
  sleep 20
  systemctl is-active --quiet "$SERVICE" && [[ "$(systemctl show -p NRestarts --value "$SERVICE")" == "$n0" ]]
}

rollback() { # <理由>
  status running "元のバージョンに戻しています"
  rsync -ac --delete --exclude .venv "$PREV/" "$ROOT/"
  install_tree || true # 依存の入れ直しに失敗しても（ネットが無いなど）、元のコードで起動を試す
  if healthy; then
    status rolled_back "$1。元のバージョン（v${FROM}）に戻しました"
  else
    status failed "$1。元に戻しても起動しません。手順書の「うまくいかないとき」を見てください"
  fi
}

on_error() { # <理由>
  trap - ERR TERM INT HUP
  set +e # 戻す処理は途中で失敗しても最後まで進め、結果を status に残す
  if [[ $APPLIED == 1 ]]; then
    rollback "$1"
  else
    status failed "$1。インターネット接続を確かめてください"
  fi
  exit 1
}
trap 'on_error "更新中にエラーが起きました（upgrade.sh $LINENO 行目）"' ERR
trap 'on_error "更新が中断されました"' TERM INT HUP

FROM="$(runuser -u "$USER_NAME" -- "$ROOT/.venv/bin/python" -c 'from importlib.metadata import version; print(version("soikaxa"))')"
status running "最新のリリースを確認しています"
curl -fsSL --max-time 30 -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/$REPO/releases/latest" >"$WORK/release.json"
TAG="$(jq -r .tag_name "$WORK/release.json")"
[[ "${TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "リリースのタグ（${TAG}）が vX.Y.Z の形ではありません"
TO="${TAG#v}"
if [[ "$(printf '%s\n' "${FROM}" "${TO}" | sort -V | tail -1)" == "${FROM}" ]]; then
  status "done" "すでに最新です（v${FROM}）"
  exit 0
fi

status running "v${TO} をダウンロードしています"
curl -fsSL --max-time 300 -o "$WORK/src.tar.gz" "https://github.com/$REPO/archive/refs/tags/${TAG}.tar.gz"
mkdir "$WORK/src"
tar -xzf "$WORK/src.tar.gz" -C "$WORK/src" --strip-components=1
grep -q "^version = \"${TO}\"" "$WORK/src/core/pyproject.toml" && [[ -f "$WORK/src/scripts/install.sh" ]] ||
  fail "v${TO} のファイルが正しくありません（core/pyproject.toml の version とタグが違う）"

status running "v${TO} を入れています（数分かかります。音楽はいったん止まります）"
if [[ "$PREV_STATE" == running && -d "$PREV" ]]; then
  echo "前回の更新が途中で止まっていたので、~/soikaxa.prev（その前の正常な版）を残したまま進めます"
else
  rsync -ac --delete --exclude .venv "$ROOT/" "$PREV/"
fi
APPLIED=1
rsync -ac --delete --exclude .venv \
  --exclude .git --exclude .github --exclude .claude --exclude .agents --exclude .codex \
  --exclude input --exclude output --exclude testreport \
  "$WORK/src/" "$ROOT/"
chown -R "$USER_NAME:$USER_NAME" "$ROOT" "$PREV"
install_tree

status running "再起動しています"
if healthy; then
  status "done" "v${TO} に更新しました"
else
  rollback "v${TO} が起動しませんでした"
  exit 1
fi
