# soikaxa-adapter-ytmusic

SoiKaxa 用 YouTube Music ソースアダプタ（別リポジトリとして公開する前提の雛形）。

- `ytmusicapi` でプレイリスト一覧・公開コンテンツ検索
- `yt-dlp` で `resolve(track_id)` → 音声ストリーム URL（期限付き。`expires_at` を返しコアが再解決する）
- 認証は ytmusicapi の browser 方式。`~/.config/soikaxa/ytmusic/browser.json` に保存。**コミットしない**

## 免責
自分の YouTube アカウントで、個人利用の範囲で、自己責任で使うためのものです。
YouTube の利用規約に抵触する可能性があります。配布・販売用途には使わないでください。

## 実装メモ
- `resolve`: `yt_dlp.YoutubeDL({"format": "bestaudio[ext=webm]/bestaudio", "quiet": True}).extract_info(url, download=False)` → `info["url"]` と `info.get("http_headers")`
- `search`: `YTMusic.search(query, filter="playlists" | "songs")`。公開コンテンツのみ
- yt-dlp の更新は systemd timer（週次）。失敗時は前バージョンを維持
- 例外は `soikaxa.adapters.base.AdapterError` / `AuthRequired` に変換する
