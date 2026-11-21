#!/usr/bin/env bash
# フェーズ1セットアップ：Pi 4B / Raspberry Pi OS Lite 64bit (Bookworm)
# 実行: sudo bash 01_setup.sh
# 内容: 必要パッケージ導入、yt-dlp/ytmusicapi、不要サービス停止、起動高速化
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "sudo で実行してください"; exit 1; fi
USER_NAME="${SUDO_USER:-pi}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
BOOT_CFG=/boot/firmware/config.txt
BOOT_CMD=/boot/firmware/cmdline.txt

echo "== [1/6] apt =="
apt-get update
apt-get install -y --no-install-recommends \
  mpv \
  bluez pipewire pipewire-audio wireplumber libspa-0.2-bluetooth \
  network-manager dnsmasq-base \
  python3 python3-venv python3-pip python3-pil python3-numpy \
  fonts-noto-cjk \
  git curl jq

echo "== [2/6] Python venv (ytmusicapi, yt-dlp, FastAPI, qrcode) =="
VENV="$HOME_DIR/soikaxa/.venv"
sudo -u "$USER_NAME" mkdir -p "$HOME_DIR/soikaxa"
sudo -u "$USER_NAME" python3 -m venv "$VENV"
sudo -u "$USER_NAME" "$VENV/bin/pip" install --upgrade pip
sudo -u "$USER_NAME" "$VENV/bin/pip" install \
  yt-dlp ytmusicapi fastapi "uvicorn[standard]" qrcode pillow python-mpv-jsonipc gpiozero
# mpv から yt-dlp を見つけられるよう PATH に出す
ln -sf "$VENV/bin/yt-dlp" /usr/local/bin/yt-dlp

echo "== [3/6] 不要サービス停止（起動短縮） =="
# NetworkManager を使うので dhcpcd は不要。残りは起動を遅くするだけのもの。
for s in dhcpcd triggerhappy ModemManager apt-daily.timer apt-daily-upgrade.timer \
         man-db.timer rpi-eeprom-update NetworkManager-wait-online \
         systemd-timesyncd keyboard-setup cups cups-browsed; do
  systemctl disable --now "$s" 2>/dev/null || true
done
systemctl mask systemd-networkd-wait-online.service 2>/dev/null || true
# 時刻は NetworkManager 経由で chrony/timesyncd を後追い起動させる（起動をブロックさせない）
systemctl enable systemd-timesyncd 2>/dev/null || true
# 必要なものは有効化
systemctl enable NetworkManager bluetooth avahi-daemon

echo "== [4/6] Bluetooth: A2DP Source 設定 =="
# PipeWire をユーザーサービスとして自動起動（ログイン不要にするため linger）
loginctl enable-linger "$USER_NAME"
# BlueZ: 起動時に自動で電源ON、A2DP source を有効
mkdir -p /etc/bluetooth
if ! grep -q '^AutoEnable=true' /etc/bluetooth/main.conf 2>/dev/null; then
  sed -i 's/^#\?AutoEnable=.*/AutoEnable=true/' /etc/bluetooth/main.conf || true
  grep -q 'AutoEnable=true' /etc/bluetooth/main.conf || printf '\n[Policy]\nAutoEnable=true\n' >> /etc/bluetooth/main.conf
fi
# 自動再接続を早める
grep -q '^ReconnectAttempts' /etc/bluetooth/main.conf || printf 'ReconnectAttempts=7\nReconnectIntervals=1,2,4,8,16,32,64\n' >> /etc/bluetooth/main.conf
usermod -aG bluetooth,audio,gpio,spi,i2c "$USER_NAME" || true

echo "== [5/6] 起動高速化: config.txt / cmdline.txt =="
cp -n "$BOOT_CFG" "$BOOT_CFG.bak" || true
cp -n "$BOOT_CMD" "$BOOT_CMD.bak" || true
add_cfg() { grep -q "^$1" "$BOOT_CFG" || echo "$1" >> "$BOOT_CFG"; }
add_cfg "disable_splash=1"        # 虹色スプラッシュを出さない
add_cfg "boot_delay=0"            # ブートローダの待ちを0に
add_cfg "initial_turbo=30"        # 起動後30秒は最大クロック
add_cfg "dtparam=audio=off"       # 内蔵アナログ音声は使わない
add_cfg "camera_auto_detect=0"
add_cfg "display_auto_detect=0"   # DSI(7インチ)の自動検出を切る。3.5インチSPIは別途オーバーレイで有効化
# cmdline: 起動ログを静かに、ファイルシステムチェック待ちを減らす
if ! grep -q 'quiet' "$BOOT_CMD"; then
  sed -i 's/$/ quiet loglevel=3 fastboot noswap/' "$BOOT_CMD"
fi
# /boot/firmware/cmdline.txt に splash があれば除去
sed -i 's/ splash//' "$BOOT_CMD"

echo "== [6/6] Wi-Fi: 国コード・省電力OFF =="
raspi-config nonint do_wifi_country JP || true
cat > /etc/NetworkManager/conf.d/wifi-powersave-off.conf <<'EOF'
[connection]
wifi.powersave = 2
EOF
# mDNS 名 (後で正式名に変える)
hostnamectl set-hostname soikaxa || true
sed -i 's/127.0.1.1.*/127.0.1.1\tsoikaxa/' /etc/hosts || true

echo
echo "完了。再起動してください: sudo reboot"
echo "再起動後:  systemd-analyze && systemd-analyze blame | head -20  で起動時間を確認"
