#!/usr/bin/env bash
# フェーズ1 検証: 一つずつ対話的に確認する
# 実行: bash 02_verify.sh  (sudo 不要。AP作成のみ sudo を内部で使う)
set -uo pipefail
VENV="$HOME/soikaxa/.venv"
PY="$VENV/bin/python"

step() { echo; echo "=============================="; echo "$1"; echo "=============================="; }
ask()  { read -r -p "$1 [Enter で続行 / s でスキップ] " a; [[ "${a:-}" == "s" ]]; }

step "0. 起動時間"
systemd-analyze || true
echo "--- 遅いサービス上位10 ---"
systemd-analyze blame 2>/dev/null | head -10 || true

step "1. Bluetooth スピーカーのペアリング"
echo "スピーカーをペアリングモードにしてから Enter。"
if ! ask "準備できたら"; then
  echo "bluetoothctl を起動します。順に:  power on / agent on / scan on / (MACが出たら) pair XX / trust XX / connect XX / quit"
  bluetoothctl
fi
echo "--- 接続中のデバイス ---"
bluetoothctl devices Connected 2>/dev/null || bluetoothctl info | head -5
echo "--- PipeWire の BT シンク ---"
wpctl status 2>/dev/null | grep -iA3 "sinks" || echo "wpctl が見つからない: PipeWire が user セッションで動いていない可能性。loginctl enable-linger $USER を確認"
echo "注意: SSH でログイン中はつながっても、ログアウト中は br-connection-profile-unavailable になることがある。"
echo "      ~/.config/wireplumber/ に 01_setup.sh の設定（80-soikaxa-*）があるか確認する"

step "2. A2DP 再生スパイク (ローカル音源→次に YouTube)"
if ! ask "テスト音を鳴らします"; then
  "$PY" - <<'EOF'
import math, struct, wave
with wave.open('/tmp/tone.wav','w') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
    w.writeframes(b''.join(struct.pack('<h', int(12000*math.sin(2*math.pi*440*i/44100))) for i in range(44100*2)))
EOF
  mpv --no-video --ao=pipewire /tmp/tone.wav && echo "OK: スピーカーから 2 秒の音が出ていれば A2DP 経路は通っています"
fi

step "3. yt-dlp → mpv で YouTube Music の曲を再生"
echo "yt-dlp: $(yt-dlp --version 2>/dev/null || echo 未導入)"
read -r -p "テスト用の YouTube Music の曲URL (例 https://music.youtube.com/watch?v=xxxx): " URL
if [[ -n "${URL:-}" ]]; then
  echo "30 分流して途切れを観察する場合は Ctrl+C せず放置。終了は q"
  time mpv --no-video --ao=pipewire --ytdl-format='bestaudio[ext=webm]/bestaudio' \
      --cache=yes --demuxer-max-bytes=50MiB --demuxer-readahead-secs=20 "$URL"
fi

step "4. ytmusicapi 認証とプレイリスト取得"
if [[ ! -f "$HOME/soikaxa/browser.json" ]]; then
  echo "ブラウザで music.youtube.com にログイン → 開発者ツール → Network → 任意の POST リクエスト(browse 等)の"
  echo "Request Headers をコピーし、以下に貼り付ける (ytmusicapi の browser 方式)"
  if ! ask "続行"; then
    (cd "$HOME/soikaxa" && "$VENV/bin/ytmusicapi" browser)
  fi
fi
if [[ -f "$HOME/soikaxa/browser.json" ]]; then
  "$PY" - <<'EOF'
from ytmusicapi import YTMusic
import os
yt = YTMusic(os.path.expanduser('~/soikaxa/browser.json'))
pls = yt.get_library_playlists(limit=20)
print(f"プレイリスト {len(pls)} 件")
for p in pls[:10]:
    print(" -", p.get('title'), p.get('playlistId'))
r = yt.search("lo-fi", filter="playlists", limit=3)
print("公開検索:", [x.get('title') for x in r])
EOF
fi

step "5. STA + AP 同時動作 (NetworkManager)"
echo "現在の接続:"; nmcli -t -f NAME,TYPE,DEVICE con show --active
if ! ask "テスト AP 'soikaxa-test' を wlan0 に追加します"; then
  sudo nmcli con add type wifi ifname wlan0 con-name soikaxa-ap ssid soikaxa-test \
      mode ap 802-11-wireless.band bg ipv4.method shared ipv6.method disabled \
      wifi-sec.key-mgmt wpa-psk wifi-sec.psk "soikaxa1234" 2>/dev/null || echo "(既に存在)"
  # STA と同じチャンネルでないと同時動作できない。STA のチャンネルを拾って合わせる
  CH=$(nmcli -t -f ACTIVE,CHAN dev wifi | awk -F: '$1=="yes"{print $2; exit}')
  if [[ -n "${CH:-}" ]]; then
    sudo nmcli con modify soikaxa-ap 802-11-wireless.channel "$CH"
    [[ "$CH" -gt 14 ]] && sudo nmcli con modify soikaxa-ap 802-11-wireless.band a
    echo "AP チャンネルを STA に合わせて $CH に設定"
  fi
  sudo nmcli con up soikaxa-ap && echo "OK: AP 起動。iPhone で 'soikaxa-test' (pw soikaxa1234) が見えるか確認。"
  echo "同時に STA が生きているか:"; nmcli -t -f NAME,TYPE,DEVICE con show --active
  echo "AP に入った端末から  http://10.42.0.1  へ到達できるか、後で Web アプリを立てて確認する"
  echo "AP を止める:  sudo nmcli con down soikaxa-ap"
fi

step "6. 再生中に AP が同居しても途切れないか"
echo "手順3 の mpv を流しながら 手順5 の AP に iPhone をつなぎ、5 分間ブラウザで 10.42.0.1 を叩き続ける。"
echo "途切れ・ノイズが出たら記録。 → PRD フェーズ1 ②の判定"
echo
echo "すべて通れば フェーズ1 ①② 合格。③ 起動時間は手順0 の systemd-analyze を見る。"
