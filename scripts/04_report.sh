#!/usr/bin/env bash
# 実機の状態をまとめて表示する（不具合の切り分け用）。パスワード類は出力しない。
# 実行: bash ~/soikaxa/scripts/04_report.sh | tee ~/soikaxa-report.txt
set -uo pipefail
sec() { echo; echo "===== $1 ====="; }
run() { echo "\$ $*"; "$@" 2>&1 | head -"${LINES_MAX:-40}"; }

sec "OS / ハード"
run cat /etc/os-release
run uname -a
run cat /proc/device-tree/model; echo
run python3 --version
run id

sec "起動時間"
run systemd-analyze
LINES_MAX=10 run systemd-analyze blame

sec "温度・電源"
run vcgencmd measure_temp
run vcgencmd get_throttled

sec "Wi-Fi（nmcli の出力形式の確認用）"
run nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status
run nmcli -t -f NAME,TYPE,AUTOCONNECT-PRIORITY connection show
LINES_MAX=8 run nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY,CHAN dev wifi list ifname wlan0 --rescan no
run nmcli -t -f GENERAL.HWADDR device show wlan0
echo "\$ iw list | (valid interface combinations)"
iw list 2>/dev/null | grep -A6 "valid interface combinations"
run iw dev

sec "Bluetooth（bluetoothctl の出力形式の確認用）"
run bluetoothctl --version
run bluetoothctl devices
run bluetoothctl devices Paired
for mac in $(bluetoothctl devices Paired 2>/dev/null | awk '{print $2}' | head -3); do
  LINES_MAX=14 run bluetoothctl info "$mac"
done
echo "\$ wpctl status | (Sinks)"
wpctl status 2>/dev/null | sed -n '/Sinks:/,/Sources:/p' | head -8

sec "LCD / タッチ"
run ls -l /dev/fb0 /dev/fb1
for f in /sys/class/graphics/fb*; do echo "$f: $(cat "$f/name" 2>/dev/null) $(cat "$f/virtual_size" 2>/dev/null)"; done
echo "\$ grep (kms|spi|osoyoo) /boot/firmware/config.txt"
grep -E "kms|spi|osoyoo" /boot/firmware/config.txt
echo "\$ 入力デバイス"
grep -E "^N: Name=|^H: Handlers=" /proc/bus/input/devices | paste - - | head -8

sec "SoiKaxa"
run mpv --version
run "$HOME/soikaxa/.venv/bin/yt-dlp" --version
run "$HOME/soikaxa/.venv/bin/python" -c "import soikaxa, evdev, PIL; print('soikaxa import ok, Pillow', PIL.__version__)"
run systemctl is-enabled "soikaxa@$USER.service"
LINES_MAX=14 run systemctl status "soikaxa@$USER.service" --no-pager
echo "\$ journalctl -u soikaxa@$USER -n 40"
journalctl -u "soikaxa@$USER.service" -n 40 --no-pager 2>&1
run ls -la /var/lib/soikaxa
run cat /var/lib/soikaxa/health.json
run cat /etc/NetworkManager/dnsmasq-shared.d/soikaxa.conf
