#!/usr/bin/env bash
# SoiKaxa を Pi 4B（Raspberry Pi OS Lite 64bit / Bookworm）に入れる。何度実行してもよい。
# 実行: sudo bash ~/soikaxa/scripts/install.sh      （Mac からは scripts/deploy.sh が呼ぶ）
# 内容: OS 更新 → 基本セットアップ（01_setup.sh）→ LCD ドライバ → 本体（03_install.sh）
#       → AP 用 uap0 → 上流 Wi-Fi を 2.4GHz に固定 → 曲フォルダ。最後に再起動が必要。
# --upgrade: ソフトウェア更新（upgrade.sh）から呼ぶ。OS 更新と Wi-Fi の固定を省き、再起動を求めない
set -euo pipefail
UPGRADE=0
[[ "${1:-}" == --upgrade ]] && UPGRADE=1
if [[ $EUID -ne 0 ]]; then echo "sudo で実行してください"; exit 1; fi
USER_NAME="${SUDO_USER:-pi}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
BOOT_CFG=/boot/firmware/config.txt

say() { echo; echo "######## $* ########"; }

# --- 前提の確認（サービス定義が ~/soikaxa と UID 1000 を前提にしている） ---
[[ "$ROOT" == "$HOME_DIR/soikaxa" ]] || { echo "置き場所は $HOME_DIR/soikaxa にしてください（今: ${ROOT}）"; exit 1; }
[[ "$(id -u "$USER_NAME")" == 1000 ]] || { echo "ユーザー $USER_NAME の UID が 1000 ではありません。Imager で作った最初のユーザーで実行してください"; exit 1; }
grep -q 'VERSION_CODENAME=bookworm' /etc/os-release || echo "注意: Bookworm ではありません（Legacy の Bookworm Lite を推奨）"

if [[ $UPGRADE == 0 ]]; then
  say "1/6 OS の更新"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get full-upgrade -y
fi

say "2/6 基本セットアップ（mpv・Bluetooth・PipeWire・Python・起動高速化）"
bash "$ROOT/scripts/01_setup.sh"

say "3/6 LCD ドライバ（OSOYOO 3.5 インチ）"
if [[ ! -f /boot/firmware/overlays/osoyoo35b.dtbo ]]; then
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/osoyoo35b.zip" https://osoyoo.com/driver/osoyoo35b.zip
  apt-get install -y --no-install-recommends unzip
  unzip -oq "$tmp/osoyoo35b.zip" -d "$tmp"
  cp "$(find "$tmp" -name osoyoo35b.dtbo | head -1)" /boot/firmware/overlays/
  rm -rf "$tmp"
fi
cp -n "$BOOT_CFG" "$BOOT_CFG.before-lcd" || true
sed -i 's/^dtoverlay=vc4-kms-v3d/#&/' "$BOOT_CFG"
grep -q '^dtparam=spi=on' "$BOOT_CFG" || echo 'dtparam=spi=on' >> "$BOOT_CFG"
grep -q '^dtoverlay=osoyoo35b' "$BOOT_CFG" || echo 'dtoverlay=osoyoo35b:speed=20000000' >> "$BOOT_CFG"

say "4/6 SoiKaxa 本体（Python パッケージ・権限・ポータル DNS・systemd）"
bash "$ROOT/scripts/03_install.sh"

say "5/6 端末 AP を uap0 に分ける（上流 Wi-Fi と同時に使う）"
systemctl enable soikaxa-uap0.service
mkdir -p /etc/systemd/system/soikaxa@.service.d
cat > /etc/systemd/system/soikaxa@.service.d/ap.conf <<'EOF'
# install.sh が作成。AP を wlan0 上の仮想 IF uap0 に立てる
[Unit]
After=soikaxa-uap0.service
Wants=soikaxa-uap0.service

[Service]
Environment=SOIKAXA_AP_IF=uap0
Environment=SOIKAXA_AP_SAME_RADIO=1
EOF
systemctl daemon-reload
# 上流 Wi-Fi は 2.4GHz に限る（AP は上流と同じチャンネルでしか動かず、5GHz の DFS では立たない）。
# Imager で入れた接続など、wlan0 用の既存プロファイルを固定する。SoiKaxa の Web 画面から入れたものは自動で固定される。
# 2.4GHz で見えない SSID は固定すると繋がらなくなるので、警告だけにする。
if [[ $UPGRADE == 0 ]]; then  # 更新時は触らない（再スキャンで端末 AP が一瞬切れるため）
  nmcli -t -f SSID,CHAN dev wifi list ifname wlan0 --rescan yes >/tmp/soikaxa-scan.txt 2>/dev/null || true
  nmcli -t -f NAME,TYPE con show | awk -F: '$2=="802-11-wireless"{print $1}' | while read -r con; do
    [[ "$con" == soikaxa-* ]] && continue
    mode="$(nmcli -g 802-11-wireless.mode con show "$con" 2>/dev/null || true)"
    [[ "$mode" == ap ]] && continue
    ssid="$(nmcli -g 802-11-wireless.ssid con show "$con" 2>/dev/null || true)"
    nmcli con modify "$con" connection.interface-name wlan0
    if awk -F: -v s="$ssid" '$1==s && $2+0<=14 {f=1} END{exit !f}' /tmp/soikaxa-scan.txt; then
      nmcli con modify "$con" 802-11-wireless.band bg
      echo "  ${con}（${ssid}）: 2.4GHz に固定"
    else
      echo "  注意: ${con}（${ssid}）は 2.4GHz で見つかりません。5GHz 専用だと端末 AP が立たないことがあります"
    fi
  done
fi

say "6/6 曲フォルダ"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 700 /var/lib/soikaxa /var/lib/soikaxa/music

[[ $UPGRADE == 1 ]] && exit 0
echo
echo "インストール完了。再起動すると SoiKaxa が自動で起動します:  sudo reboot"
echo "（LCD に「起動中…」→ QR と 6 桁の設定コードが出れば成功）"
