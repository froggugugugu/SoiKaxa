#!/usr/bin/env bash
# フェーズ 2 以降: SoiKaxa 本体を venv に入れ、systemd / polkit / dnsmasq を設定する。
# 実行: sudo bash ~/soikaxa/scripts/03_install.sh   （01_setup.sh の後）
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "sudo で実行してください"; exit 1; fi
USER_NAME="${SUDO_USER:-pi}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
ROOT="$HOME_DIR/soikaxa"
VENV="$ROOT/.venv"

echo "== [0/5] ビルドに必要なパッケージ（evdev のコンパイル用）と iw =="
apt-get update
apt-get install -y --no-install-recommends python3-dev build-essential iw

echo "== [1/5] Python パッケージ（コア + アダプタ） =="
sudo -u "$USER_NAME" "$VENV/bin/pip" install --quiet -e "$ROOT/core[pi]"
if [[ -d "$ROOT/adapters/soikaxa-adapter-ytmusic" ]]; then
  sudo -u "$USER_NAME" "$VENV/bin/pip" install --quiet -e "$ROOT/adapters/soikaxa-adapter-ytmusic"
fi
chmod +x "$ROOT/scripts/update_ytdlp.sh"

echo "== [2/5] 権限（NetworkManager / 更新ユニット / 入力デバイス / fb） =="
groupadd -f soikaxa
usermod -aG soikaxa,video,input "$USER_NAME"
# 49-polkit-pkla-compat.rules より先に評価させるため 10 番（理由はルールファイルの冒頭）
rm -f /etc/polkit-1/rules.d/50-soikaxa.rules
install -m 0644 "$ROOT/systemd/10-soikaxa.rules" /etc/polkit-1/rules.d/10-soikaxa.rules

echo "== [3/5] キャプティブポータル DNS =="
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
"$VENV/bin/python" -m soikaxa.portal > /etc/NetworkManager/dnsmasq-shared.d/soikaxa.conf

echo "== [4/5] systemd =="
# ソフトウェア更新は root で動くので、ホーム（ユーザーが書ける場所）ではなく root 所有のコピーを実行させる。
# 実行中の自分自身を書き換えないよう、別名で置いてから rename する
install -d -m 0755 /usr/local/lib/soikaxa
install -m 0755 "$ROOT/scripts/upgrade.sh" /usr/local/lib/soikaxa/upgrade.sh.new
mv -f /usr/local/lib/soikaxa/upgrade.sh.new /usr/local/lib/soikaxa/upgrade.sh
install -m 0644 "$ROOT"/systemd/soikaxa*.service "$ROOT"/systemd/soikaxa*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable "soikaxa@$USER_NAME.service" "soikaxa-update@$USER_NAME.timer" "soikaxa-health@$USER_NAME.timer"

echo "== [5/5] 完了 =="
echo "起動: sudo systemctl start soikaxa@$USER_NAME   ログ: journalctl -u soikaxa@$USER_NAME -f"
