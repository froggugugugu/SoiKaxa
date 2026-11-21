# 実機セットアップ手順（Raspberry Pi 4B）

SoiKaxa を Raspberry Pi 4B に入れて、音が出るところまで確認する手順。上から順に進める。
各ステップの「確認」が期待どおりでなければ、そこで止めて出力を記録する（最後の「うまくいかないとき」も参照）。

- 作業 PC: macOS（この手順のコマンドは Mac のターミナル前提）
- 所要時間の目安: 準備〜音出し確認まで 2〜3 時間（A2DP 30 分の連続再生テストを除く）
- この手順は開発 PC 上の検証だけで書いている。**実機では未検証**の箇所には「未検証」と書いた

## 0. 用意するもの

| もの | 条件 |
| --- | --- |
| Raspberry Pi 4B | メモリ容量は問わない |
| microSD | 16GB 以上（32GB A1 推奨） |
| 電源 | USB-C 5V/3A（15W 以上） |
| LAN ケーブル | **セットアップ中は有線 LAN で SSH する**（理由は下） |
| Bluetooth スピーカー | A2DP 対応 |
| OSOYOO 3.5 インチ SPI LCD（型番 2022013800） | **最初は付けない**。ステップ 7 で付ける |
| スマホ | 端末 AP への参加と画面操作の確認用 |
| 自宅 Wi-Fi の SSID とパスワード | 5GHz 帯を推奨 |

有線 LAN を使う理由: 途中で Pi の Wi-Fi を AP に切り替える試験をする。Wi-Fi 経由の SSH だと、その瞬間に接続が切れて操作できなくなる。

## 1. バージョン

| 対象 | 指定 |
| --- | --- |
| Raspberry Pi Imager | **2.0.12**（2026-11 時点の最新）。この Mac には 2.0.10 が入っているので更新する |
| OS | **Raspberry Pi OS (Legacy, 64-bit) Lite** = Debian 12 **Bookworm**、**2026-10-06 版**（`2026-10-06-raspios-bookworm-arm64-lite.img.xz`） |
| Python | 3.11（Bookworm 標準。追加インストール不要） |

OS は最新の Trixie ではなく、**Legacy の Bookworm** を選ぶ。設計（PRD）が Bookworm 前提で、Wi-Fi の AP と上流の同時動作は Bookworm での実績報告がある一方、Trixie では不安定という報告があるため。

## 2. Raspberry Pi Imager を更新する

1. <https://www.raspberrypi.com/software/> から macOS 版をダウンロードし、`Raspberry Pi Imager.app` を「アプリケーション」に上書きコピーする
2. 確認:

```bash
defaults read "/Applications/Raspberry Pi Imager.app/Contents/Info.plist" CFBundleShortVersionString
# → v2.0.12
```

2.0.10 のままでも書き込みはできる。ただし 2.0 系の初期には、Lite イメージで事前設定（ホスト名や Wi-Fi）が反映されない不具合報告があるので、更新しておく。

## 3. microSD に OS を書き込む

microSD を Mac に挿し、Imager を起動する。画面は「デバイス → OS → ストレージ → カスタマイズ → 書き込み」の順に進む（項目名は版によって多少違う）。

| 画面 | 選ぶ・入力する内容 |
| --- | --- |
| デバイス | Raspberry Pi 4 |
| OS | **Raspberry Pi OS (other)** → **Raspberry Pi OS (Legacy, 64-bit) Lite**（説明に "Debian Bookworm" とあるもの） |
| ストレージ | 挿した microSD |
| ホスト名 | `soikaxa` |
| ロケール | タイムゾーン `Asia/Tokyo`、キーボード `jp`（US 配列なら `us`） |
| ユーザー | 好きなユーザー名（例 `soi`）とパスワード。以降この名前を使う |
| Wi-Fi | 自宅 Wi-Fi の SSID・パスワード、国 `JP` |
| リモートアクセス（SSH） | 有効。公開鍵認証を選び、下のコマンドで出る 1 行を貼る |
| Raspberry Pi Connect | 使わない（外部サービスに依存しない方針） |

SSH 公開鍵の表示:

```bash
cat ~/.ssh/id_ed25519.pub
```

書き込みと検証が終わったら microSD を取り出す。

## 4. 初回起動と SSH 接続

1. Pi に microSD を挿す。**LCD は付けない**
2. LAN ケーブルをルーターにつなぎ、電源を入れる。初回起動は設定の反映で 1〜2 分かかる
3. Mac から接続する。以降、Mac 側のコマンドは変数 `PI` を使う（`soi` は自分のユーザー名に置き換える）:

```bash
PI=soi@soikaxa.local
ssh $PI
```

`soikaxa.local` が見つからないときは、ルーターの管理画面で Pi の IP アドレスを調べて `PI=soi@192.168.x.x` にする。

4. Pi 上で確認する:

```bash
grep -E "VERSION_CODENAME|PRETTY" /etc/os-release   # → bookworm
uname -m                                             # → aarch64
python3 --version                                    # → Python 3.11.x
id -u                                                # → 1000
nmcli -t -f DEVICE,STATE,CONNECTION device status    # eth0 と wlan0 の両方が connected
```

- `id -u` が 1000 でない場合は止める（サービス定義が UID 1000 を前提にしている）
- `wlan0` が connected でない場合は、Imager の Wi-Fi 設定が入っていない。次で接続する:

```bash
sudo nmcli dev wifi connect "自宅のSSID" password "パスワード"
```

5. OS を更新して再起動する:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

## 5. コードを Pi に送り、基本セットアップを実行する

1. **Mac 側**で、リポジトリを Pi の `~/soikaxa` に送る。置き場所は必ず `~/soikaxa`（スクリプトとサービス定義がこのパスを前提にしている）。Mac の仮想環境（`.venv`）は送らない:

```bash
cd ~/Development/SoiKaxa
rsync -av --delete \
  --exclude .venv --exclude .git --exclude .data --exclude testreport \
  --exclude input --exclude output --exclude .claude --exclude .agents --exclude .codex \
  --exclude __pycache__ --exclude .DS_Store \
  ./ $PI:soikaxa/
```

コードを直したあとの再送も同じコマンドでよい。

2. **Pi 側**で基本セットアップを実行する（mpv・BlueZ・PipeWire などの導入、Python 仮想環境の作成、起動高速化）。10 分前後かかる:

```bash
sudo bash ~/soikaxa/scripts/01_setup.sh
sudo reboot
```

3. 再起動後、Pi で確認する:

```bash
systemd-analyze                     # 起動時間（kernel + userspace）を控える
mpv --version | head -1
~/soikaxa/.venv/bin/yt-dlp --version
wpctl status | head -5              # PipeWire が動いている
```

## 6. フェーズ 1 の技術検証

設計の前提が実機で成り立つかを確かめる。結果は `docs/phase1-results.md`（Mac 側のファイル）に書く。

### 6.1 Bluetooth と再生（検証スクリプト）

```bash
bash ~/soikaxa/scripts/02_verify.sh
```

対話式に進む。各ステップで Enter を押すと実行、`s` でスキップ。

| ステップ | やること | 合格の目安 |
| --- | --- | --- |
| 0 | 起動時間の表示 | 合計 30 秒以内（目標 20 秒） |
| 1 | スピーカーをペアリングモードにして Enter。`bluetoothctl` が開くので `power on` → `scan on` → スピーカーの MAC が出たら `pair XX:XX:...` → `trust XX:XX:...` → `connect XX:XX:...` → `quit` | 「接続中のデバイス」と BT シンクが表示される |
| 2 | テスト音 | スピーカーから 2 秒の音が出る |
| 3 | YouTube Music の曲 URL を貼る | 再生される。余裕があれば 30 分流して途切れを数える（`q` で終了） |
| 4 | **`s` でスキップ**（音源の認証は後で Web 画面から登録する） | — |
| 5 | **`s` でスキップ**（次の 6.2 で別の方法で試す） | — |
| 6 | 説明の表示のみ | — |

ステップ 5 をスキップする理由: スクリプトは `wlan0` に直接 AP を立てるが、NetworkManager は 1 つのデバイスで AP と上流 Wi-Fi を同時に有効にできない。実行すると上流の Wi-Fi が切れる見込み。

### 6.2 AP と上流 Wi-Fi の同時動作（未検証・この手順の最重要確認）

`wlan0` の上に AP 用の仮想インターフェース `uap0` を作り、同時に動くかを確かめる。

```bash
sudo apt install -y iw
iw list | grep -A6 "valid interface combinations"
```

出力に `#{ managed } <= 1, #{ AP } <= 1` のような「managed と AP を 1 つずつ」の組み合わせがあれば、チップは同時動作に対応している。続けて試す:

```bash
# 0) 上流 Wi-Fi の設定を wlan0 専用にする（仮想インターフェース側で使われないようにする）
STA=$(nmcli -t -f NAME,DEVICE con show --active | awk -F: '$2=="wlan0"{print $1}'); echo "STA=$STA"
sudo nmcli con modify "$STA" connection.interface-name wlan0

# 1) 仮想インターフェースを作る
sudo iw dev wlan0 interface add uap0 type __ap
nmcli -t -f DEVICE,TYPE,STATE device status          # uap0 が wifi として見える

# 2) 上流 Wi-Fi のチャンネルを調べる（AP は同じチャンネルにする必要がある）
CH=$(nmcli -t -f ACTIVE,CHAN dev wifi list ifname wlan0 --rescan no | awk -F: '$1=="yes"{print $2; exit}')
BAND=$([ "$CH" -gt 14 ] && echo a || echo bg); echo "channel=$CH band=$BAND"

# 3) テスト用 AP を uap0 に立てる
sudo nmcli con add type wifi ifname uap0 con-name soikaxa-test ssid soikaxa-test \
  802-11-wireless.mode ap 802-11-wireless.band $BAND 802-11-wireless.channel $CH \
  ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled \
  wifi-sec.key-mgmt wpa-psk wifi-sec.psk soikaxa1234
sudo nmcli con up soikaxa-test

# 4) 両方が生きているか
nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device status
ping -c 3 -I wlan0 1.1.1.1
```

確認すること:

| # | 確認 | 合格 |
| --- | --- | --- |
| a | `wlan0` が自宅 Wi-Fi に connected のまま、`uap0` が `soikaxa-test` で connected | 両方 connected |
| b | `ping -I wlan0` が通る | 3 回とも応答 |
| c | スマホで Wi-Fi `soikaxa-test`（パスワード `soikaxa1234`）に入れる | 入れる（「インターネット未接続」と出てよい） |
| d | 6.1 ステップ 3 と同じように曲を流しながら、スマホを AP につないだまま 5 分 | 途切れ・ノイズなし |

d の再生コマンド（別の SSH ウィンドウで）:

```bash
mpv --no-video --ao=pipewire --ytdl-format='bestaudio[ext=webm]/bestaudio' "曲のURL"
```

終わったらテスト用 AP を消す:

```bash
sudo nmcli con delete soikaxa-test
sudo iw dev uap0 del
```

結果による分岐:

| 結果 | 次にやること |
| --- | --- |
| a〜d すべて合格 | ステップ 8.2 で `uap0` を常時使う設定を入れる |
| a か b が不合格（同時に動かない） | 出力を記録して相談する。代替は USB Wi-Fi ドングルを AP 専用にする（PRD のリスク対策） |
| d で音切れ | 上流を 5GHz の Wi-Fi にして再試験。改善しなければ同上 |

### 6.3 結果を記録する

Mac 側の `docs/phase1-results.md` に、起動時間、A2DP の途切れ回数とスピーカー機種、6.2 の a〜d、スマホの機種と OS を書く。

## 7. LCD を取り付ける

1. Pi 上で、ドライバ（デバイスツリーオーバーレイ）を入れる。OSOYOO の公式手順（2025-10-01 以降の Bookworm/Trixie 向け）のうち、デスクトップ用の設定を除いた部分:

```bash
cd ~ && sudo apt install -y unzip
wget https://osoyoo.com/driver/osoyoo35b.zip
unzip -o osoyoo35b.zip
sudo cp "$(find . -maxdepth 3 -name osoyoo35b.dtbo | head -1)" /boot/firmware/overlays/
ls -l /boot/firmware/overlays/osoyoo35b.dtbo
```

2. `/boot/firmware/config.txt` を書き換える（バックアップを取り、KMS を無効にして SPI とオーバーレイを足す）:

```bash
sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.before-lcd
sudo sed -i 's/^dtoverlay=vc4-kms-v3d/#&/' /boot/firmware/config.txt
printf 'dtparam=spi=on\ndtoverlay=osoyoo35b:speed=20000000\n' | sudo tee -a /boot/firmware/config.txt
grep -E "kms|spi|osoyoo" /boot/firmware/config.txt
```

3. 電源を切り、ケーブルを抜いてから LCD を GPIO の 1〜26 番ピンに挿す（1 列ずれに注意）:

```bash
sudo poweroff
```

4. 電源を入れる。ドライバが読まれるまで画面は白い。SSH で確認する:

```bash
ls -l /dev/fb*
for f in /sys/class/graphics/fb*; do echo "$f: $(cat $f/name) $(cat $f/virtual_size)"; done
grep -E "^N: Name=" /proc/bus/input/devices
```

| 確認 | 期待 |
| --- | --- |
| `/dev/fb1` がある | ある（`fb0` は HDMI 用） |
| `fb1` の `virtual_size` | `480,320` |
| 入力デバイスに `ADS7846 Touchscreen` | ある |

- SPI の画面が `fb0` になっている場合（`fb1` が無く、`fb0` が 480,320）: ステップ 8.2 で `SOIKAXA_FB=/dev/fb0` を設定する
- `virtual_size` が `320,480`（縦向き）の場合: 未検証だが、`config.txt` の行を `dtoverlay=osoyoo35b:speed=20000000,rotate=90`（または `270`）に変えて再起動し、`480,320` になる値を探す
- 白いままのとき: 電源を抜き差しする。それでも白ければ `grep` の結果と `dmesg | grep -i -E "spi|ili|fb"` を記録する

## 8. SoiKaxa 本体を入れる

### 8.1 インストール

```bash
sudo bash ~/soikaxa/scripts/03_install.sh
```

Python パッケージ、権限（polkit）、キャプティブポータル用の DNS 設定、systemd の登録を行う。この時点ではまだ起動しない。

画面に試し描きする（「起動中…」と進捗バーが出れば、LCD とアプリの描画は正常）:

```bash
sudo ~/soikaxa/.venv/bin/python -c "
from soikaxa.lcd.fb import Framebuffer
from soikaxa.lcd.screens import render_boot
Framebuffer('/dev/fb1').write(render_boot(0.5))"
```

色がおかしい・上下が逆などの場合は、スマホで画面を撮っておく。

### 8.2 AP 用インターフェースの設定（6.2 が合格した場合）

`uap0` を起動時に作り、SoiKaxa に AP は `uap0` を使うよう伝える（未検証）:

```bash
sudo systemctl enable --now soikaxa-uap0.service
iw dev | grep -A2 uap0

sudo mkdir -p /etc/systemd/system/soikaxa@.service.d
sudo tee /etc/systemd/system/soikaxa@.service.d/ap.conf >/dev/null <<'EOF'
[Unit]
After=soikaxa-uap0.service
Wants=soikaxa-uap0.service

[Service]
Environment=SOIKAXA_AP_IF=uap0
Environment=SOIKAXA_AP_SAME_RADIO=1
EOF
sudo systemctl daemon-reload
```

SPI の画面が `fb0` だった場合は、`[Service]` に `Environment=SOIKAXA_FB=/dev/fb0` も足す。

6.2 が不合格のままこの設定を入れずに起動すると、AP が `wlan0` を占有して上流 Wi-Fi が切れる。有線 LAN をつないでいれば、有線が上流になるので動作確認は続けられる。

### 8.3 ローカルの曲を置く（任意・おすすめ）

インターネットや音源の認証に関係なく音出しを確認できる。フォルダ名がプレイリスト名になる:

```bash
sudo install -d -o $USER -g $USER -m 700 /var/lib/soikaxa /var/lib/soikaxa/music /var/lib/soikaxa/music/Test
```

Mac から MP3 を数曲送る:

```bash
scp ~/Music/なにか/*.mp3 $PI:/var/lib/soikaxa/music/Test/
```

### 8.4 起動する

```bash
sudo systemctl start soikaxa@$USER
systemctl status soikaxa@$USER --no-pager | head -12     # Active: active (running)
journalctl -u soikaxa@$USER -f                           # ログを流しっぱなしにする（Ctrl+C で止める）
```

| 確認 | 期待 |
| --- | --- |
| サービスの状態 | `active (running)` が 1 分以上続く（再起動を繰り返さない） |
| LCD | 「起動中…」のあと、QR と「設定コード（6 桁）」が出る |
| スマホの Wi-Fi 一覧 | `soikaxa-XXXX` が見える |

`active (running)` にならない場合は「うまくいかないとき」の 1 を見る。

## 9. 初回設定と動作確認

スマホで行う。**Web 画面は端末 AP に参加したスマホからだけ開ける**（自宅 Wi-Fi 側の PC からは 403 になる。仕様）。

1. スマホのカメラで LCD の QR を読み、`soikaxa-XXXX` に接続する
2. 画面が自動で開く。開かなければブラウザで `http://10.42.0.1/` を開く
3. 「オーナーはこちら」→ **設定コード**（LCD に出ている 6 桁）と、新しい **PIN（6〜8 桁の数字）** を入力して「設定」
4. 「Wi-Fi」タブ: Imager で入れた自宅 Wi-Fi はすでに使われているので、追加登録は不要。別の Wi-Fi を試すなら「スキャン」から登録する
5. 「スピーカー」タブ: スピーカーをペアリングモードにして「スキャン」→「接続」
6. 「再生」タブ: 「マイプレイリスト」に 8.3 のフォルダ（`Test`）が出るので「再生」
7. 音源を YouTube Music にする場合: 「音源」タブで認証ヘッダを貼って「登録」→ 音源を `ytmusic` に切り替え →「再生」タブでプレイリストを選ぶ。認証ヘッダは、PC のブラウザで music.youtube.com にログインし、開発者ツールの Network で `browse` などの POST リクエストの Request Headers をコピーしたもの

確認チェックリスト（結果を ○ × で控える）:

| # | 確認 | 期待 |
| --- | --- | --- |
| 1 | QR を読むと画面が自動で開く（iPhone / Android それぞれ） | 開く |
| 2 | 設定コードなし、または違うコードでは PIN を設定できない | 「本体の画面に出ている設定コードを…」と出る |
| 3 | スピーカーから音が出る | 出る |
| 4 | LCD に曲名・次の曲・QR が出て、レコードが回る | 出る。回転がカクつかない |
| 5 | 画面タップ（0.3 秒以上）で一時停止/再開、1.5 秒長押しで次の曲 | 動く |
| 6 | 10 秒長押しで設定用 QR が全面に出る | 出る（30 秒で戻る） |
| 7 | 別のスマホ（ゲスト）で QR → 検索 →「追加」 | LCD の「次:」に曲名と名前が出る |
| 8 | 曲が終わると次の曲に進む | 進む（2 曲以上続けて確認） |
| 9 | 再生中にスピーカーの電源を切って入れ直す | 30 秒程度で再接続する |
| 10 | 電源を抜いて挿し直す | 自動で起動し、前回のプレイリストが流れる。電源投入から音が出るまでの秒数を控える |
| 11 | `vcgencmd measure_temp` | 再生 10 分後に 70℃ 未満 |
| 12 | 自宅 Wi-Fi 側の PC から `http://soikaxa.local/` | 403（開けないのが正しい） |

## 10. 結果を共有する

Pi で次を実行し、できたファイルの中身を貼る（パスワード類は含まれない）:

```bash
bash ~/soikaxa/scripts/04_report.sh | tee ~/soikaxa-report.txt
```

Mac に持ってくる場合:

```bash
scp $PI:soikaxa-report.txt ~/Desktop/
```

あわせて、ステップ 9 のチェックリストの ○ × と、気になった挙動（画面の写真があると早い）を伝える。

## うまくいかないとき

### 1. サービスが起動しない・再起動を繰り返す

```bash
journalctl -u soikaxa@$USER -n 60 --no-pager
```

`Permission denied` や `Read-only file system` が出ている場合は、サンドボックス設定（未検証）が原因の可能性がある。一時的に外して切り分ける:

```bash
sudo tee /etc/systemd/system/soikaxa@.service.d/nosandbox.conf >/dev/null <<'EOF'
[Service]
NoNewPrivileges=no
ProtectSystem=no
ProtectHome=no
PrivateTmp=no
ProtectKernelTunables=no
ProtectKernelModules=no
ProtectControlGroups=no
RestrictSUIDSGID=no
LockPersonality=no
RestrictAddressFamilies=
CapabilityBoundingSet=~
EOF
sudo systemctl daemon-reload && sudo systemctl restart soikaxa@$USER
```

これで動いた場合は、外す前のログを共有する（どの制限が原因かを特定して直す）。

### 2. 症状別

| 症状 | 見るところ |
| --- | --- |
| `soikaxa.local` に SSH できない | ルーターで IP を調べて IP で接続。Imager の設定が反映されていなければ書き込みからやり直す |
| `03_install.sh` が evdev のビルドで失敗 | エラー全文を記録。`sudo apt install -y python3-dev build-essential` が入っているか確認 |
| AP（`soikaxa-XXXX`）が見えない | `nmcli -t -f DEVICE,STATE,CONNECTION device status` と `journalctl -u NetworkManager -n 40 --no-pager` |
| AP に入れるが画面が開かない | スマホで `http://10.42.0.1/` を直接開く。開けば自動表示だけの問題（機種と OS を記録） |
| 起動後に SSH が切れた | AP が `wlan0` を占有した。有線 LAN で入り直し、8.2 の設定を確認する |
| 音が出ない | `wpctl status` の Sinks にスピーカーがあるか。`bluetoothctl info <MAC>` が `Connected: yes` か |
| 曲が 1 曲目で止まる・進まない | `journalctl -u soikaxa@$USER -f` を流しながら再現し、ログを共有 |
| LCD が白い | ステップ 7 の確認コマンドの出力と `dmesg \| grep -i -E "spi\|ili\|fb"` |
| LCD に文字化け（□） | `fc-list \| grep -i noto \| head -3`（`fonts-noto-cjk` は `01_setup.sh` が入れる） |
| PIN を忘れた・ロックされた | ロックは本体を 10 秒長押しで解除。PIN を忘れた場合は `sudo systemctl stop soikaxa@$USER && rm /var/lib/soikaxa/settings.json && sudo systemctl start soikaxa@$USER`（設定が初期化され、新しい設定コードが LCD に出る） |

### 3. やり直す

| やり直したい範囲 | 方法 |
| --- | --- |
| コードだけ更新 | ステップ 5 の `rsync` → `sudo systemctl restart soikaxa@$USER` |
| アプリの設定を初期化 | Web の「設定」→「初期化」、または上の `settings.json` 削除 |
| LCD 設定を戻す | `sudo cp /boot/firmware/config.txt.before-lcd /boot/firmware/config.txt && sudo reboot` |
| 全部 | ステップ 3 の書き込みからやり直す（30 分程度） |

## 参考にした情報

- Raspberry Pi OS とイメージ一覧: <https://www.raspberrypi.com/software/operating-systems/>
- Raspberry Pi Imager 2.0: <https://www.raspberrypi.com/news/a-new-raspberry-pi-imager/>
- OSOYOO 3.5 インチ SPI LCD（Bookworm/Trixie、2025-10-01 以降）: <https://osoyoo.com/?p=61051>
- 仮想 AP インターフェースと Bookworm / Trixie の報告: <https://github.com/raspberrypi/linux/issues/7092>
