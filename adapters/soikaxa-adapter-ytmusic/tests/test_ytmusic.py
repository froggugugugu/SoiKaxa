import os
import stat

import pytest
from soikaxa.adapters.base import AdapterError, AuthRequired, Playlist, SourceAdapter, Track
from soikaxa.adapters.capabilities import AuthCapable, AuthCheckable
from soikaxa_adapter_ytmusic import YTMusicAdapter, create

VID = "dQw4w9WgXcQ"


class FakeYT:
    private: set = set()
    songs = 1

    def __init__(self, auth, fail=None):
        self.auth = auth
        self.fail = fail
        self.playlist_calls = []

    def _maybe_fail(self):
        if self.fail:
            raise self.fail

    def get_library_playlists(self, limit):
        self._maybe_fail()
        return [
            {
                "playlistId": "PL1",
                "title": "Mix",
                "count": "1,234 songs",
                "thumbnails": [{"url": "s"}, {"url": "l"}],
            },
            {"playlistId": "PL2", "title": "Odd", "count": "many"},
            {"title": "no id"},
        ]

    def get_playlist(self, pid, limit):
        self.playlist_calls.append((pid, limit))
        if self.private and pid in self.private and not self.auth:
            raise RuntimeError("404 not found")
        self._maybe_fail()
        return {
            "tracks": [
                {
                    "videoId": VID,
                    "title": "Song",
                    "artists": [{"name": "A"}, {"name": "B"}],
                    "duration_seconds": 200,
                    "thumbnails": [{"url": "t"}],
                },
                {"videoId": None, "title": "gone"},
                {"videoId": "x" * 11, "title": "unavailable", "isAvailable": False},
            ]
        }

    playable = "OK"

    def get_song(self, vid):
        self._maybe_fail()
        return {
            "playabilityStatus": {"status": self.playable},
            "videoDetails": {
                "videoId": vid,
                "title": "Shared",
                "author": "Singer",
                "lengthSeconds": "215",
                "thumbnail": {"thumbnails": [{"url": "s"}, {"url": "big"}]},
            },
        }

    def search(self, q, filter, limit):
        self._maybe_fail()
        self.searched = True
        if filter == "songs":
            return [{"videoId": VID, "title": "Found", "artists": []}] * self.songs
        return [{"browseId": "VLPLabc", "title": "Public"}, {"title": "none"}]


class FakeYDL:
    def __init__(self, opts, info=None, exc=None):
        self.opts, self.info, self.exc = opts, info, exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download):
        assert not download and url.endswith(VID)
        if self.exc:
            raise self.exc
        return self.info


def adapter(tmp_path, yt_fail=None, info=None, exc=None, setup=lambda raw: '{"cookie": "x"}'):
    created = []

    def yt_factory(auth):
        created.append(auth)
        return FakeYT(auth, yt_fail)

    a = YTMusicAdapter(
        tmp_path / "yt", ytmusic_factory=yt_factory, ytdl_factory=lambda o: FakeYDL(o, info, exc), setup=setup
    )
    a.created = created
    return a


def test_protocols_and_entry_point_factory(tmp_path):
    a = create(tmp_path)
    assert isinstance(a, SourceAdapter) and isinstance(a, AuthCapable) and isinstance(a, AuthCheckable)
    assert a.auth_path == tmp_path / "adapters" / "ytmusic" / "browser.json"


def test_register_auth_private_file_and_clear(tmp_path):
    a = adapter(tmp_path)
    assert not a.has_auth()
    a.register_auth("cookie: SAPISID=...")
    assert a.has_auth()
    assert stat.S_IMODE(os.stat(a.auth_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(a.auth_dir).st_mode) == 0o700
    a.clear_auth()
    assert not a.has_auth()
    a.clear_auth()  # 2 回目でも例外にしない


@pytest.mark.parametrize("raw", ["", "x" * 64_001])
def test_register_auth_rejects_bad_size(tmp_path, raw):
    with pytest.raises(AdapterError):
        adapter(tmp_path).register_auth(raw)


def test_register_auth_wraps_parse_error(tmp_path):
    def boom(raw):
        raise ValueError("bad")

    with pytest.raises(AdapterError):
        adapter(tmp_path, setup=boom).register_auth("garbage")


def test_list_playlists_requires_auth(tmp_path):
    with pytest.raises(AuthRequired):
        adapter(tmp_path).list_playlists()


def test_list_playlists_and_client_uses_auth(tmp_path):
    a = adapter(tmp_path)
    a.register_auth("h")
    pls = a.list_playlists()
    assert pls == [
        Playlist(id="PL1", title="Mix", track_count=1234, source="ytmusic", thumbnail="l"),
        Playlist(id="PL2", title="Odd", track_count=None, source="ytmusic"),
    ]
    assert a.created == [str(a.auth_path)]


def test_check_auth_counts_owner_playlists(tmp_path):
    a = adapter(tmp_path)
    with pytest.raises(AuthRequired):
        a.check_auth()  # 未登録
    a.register_auth("h")
    assert a.check_auth() == 2
    assert a.created == [str(a.auth_path)]  # 認証付きのクライアントで確かめる


@pytest.mark.parametrize(
    ("fail", "exc"), [(RuntimeError("HTTP 401"), AuthRequired), (OSError("down"), AdapterError)]
)
def test_check_auth_converts_errors(tmp_path, fail, exc):
    a = adapter(tmp_path, yt_fail=fail)
    a.register_auth("h")
    with pytest.raises(exc):
        a.check_auth()


def test_list_tracks_filters_unavailable(tmp_path):
    tracks = adapter(tmp_path).list_tracks("PL1")
    assert tracks == [
        Track(
            id=VID, title="Song", artist="A, B", duration_sec=200, source="ytmusic", extra={"thumbnail": "t"}
        )
    ]
    with pytest.raises(AdapterError):
        adapter(tmp_path).list_tracks("../etc")


def test_search_songs_and_playlists_anonymous(tmp_path):
    a = adapter(tmp_path)
    res = a.search("  q ")
    assert isinstance(res[0], Track) and res[0].title == "Found"
    assert res[1] == Playlist(id="PLabc", title="Public", source="ytmusic")
    assert a.created == [None]  # 未認証でも検索できる
    assert a.search("") == []
    assert len(a.search("q", limit=1)) == 1


def test_errors_are_converted(tmp_path):
    with pytest.raises(AuthRequired):
        adapter(tmp_path, yt_fail=Exception("HTTP 401 Unauthorized")).search("q")
    with pytest.raises(AdapterError) as e:
        adapter(tmp_path, yt_fail=RuntimeError("boom")).search("q")
    assert not isinstance(e.value, AuthRequired)


def test_resolve_returns_url_headers_and_expiry(tmp_path):
    info = {
        "url": "https://rr.example/videoplayback?expire=1767225600&x=1",
        "http_headers": {"User-Agent": "UA"},
    }
    r = adapter(tmp_path, info=info).resolve(VID)
    assert r.uri == info["url"] and r.headers == {"User-Agent": "UA"} and r.expires_at == 1767225600.0
    r2 = adapter(tmp_path, info={"url": "https://x/?expire=bad"}).resolve(VID)
    assert r2.expires_at is None and r2.headers == {}


@pytest.mark.parametrize("tid", ["", "short", "../../../etc/pass", VID + "x"])
def test_resolve_rejects_invalid_ids(tmp_path, tid):
    with pytest.raises(AdapterError):
        adapter(tmp_path).resolve(tid)


def test_resolve_failures(tmp_path):
    with pytest.raises(AdapterError):
        adapter(tmp_path, exc=RuntimeError("Sign in to confirm")).resolve(VID)
    with pytest.raises(AdapterError):
        adapter(tmp_path, info={}).resolve(VID)


def test_guest_search_never_uses_owner_account(tmp_path):
    a = adapter(tmp_path)
    a.register_auth("h")
    a.search("q")
    assert a.created == [None]  # 認証済みでも、検索は未認証クライアント


def test_search_reserves_room_for_playlists(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeYT, "songs", 30)
    res = adapter(tmp_path).search("q", limit=20)
    assert len(res) == 20
    assert [type(r).__name__ for r in res[-2:]] == ["Track", "Playlist"]  # 曲だけで上限を使い切らない


def test_list_tracks_public_is_anonymous_and_capped(tmp_path):
    a = adapter(tmp_path)
    a.register_auth("h")
    a.list_tracks("PLpublic")
    assert a.created == [None]
    assert a._clients[False].playlist_calls == [("PLpublic", 500)]


def test_list_tracks_owner_playlist_uses_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeYT, "private", {"PL1"})
    a = adapter(tmp_path)
    a.register_auth("h")
    a.list_playlists()  # PL1 がオーナーのものと分かる
    assert a.list_tracks("PL1") and a.created == [str(a.auth_path)]


def test_list_tracks_private_falls_back_to_auth_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeYT, "private", {"PL1"})
    a = adapter(tmp_path)
    a.register_auth("h")
    assert a.list_tracks("PL1")  # 一覧を取っていなくても（自動再生）、未認証で失敗したら認証付きで取る
    assert a.created == [None, str(a.auth_path)]
    b = adapter(tmp_path / "noauth")
    with pytest.raises(AdapterError):
        b.list_tracks("PL1")  # 認証が無ければ非公開は取れない


def test_clear_auth_drops_clients_and_owner_ids(tmp_path):
    a = adapter(tmp_path)
    a.register_auth("h")
    a.list_playlists()
    a.clear_auth()
    assert a._clients == {} and a._owner_playlists == set()


@pytest.mark.parametrize(
    "q",
    [
        f"https://music.youtube.com/watch?v={VID}&si=AbCdEfGh12345678",
        f"https://youtu.be/{VID}?si=AbCdEfGh12345678",
        f"https://www.youtube.com/watch?v={VID}&list=RDAMVM{VID}",
        f"https://m.youtube.com/watch?feature=share&v={VID}",
        f"この曲 https://music.youtube.com/watch?v={VID} 流して",
    ],
)
def test_search_by_share_link_returns_that_song(tmp_path, q):
    """ゲストが自分のアプリで「リンクをコピー」したものを貼ると、その曲 1 件だけを返す。"""
    a = adapter(tmp_path)
    a.register_auth("h")
    res = a.search(q)
    assert res == [
        Track(
            id=VID,
            title="Shared",
            artist="Singer",
            duration_sec=215,
            source="ytmusic",
            extra={"thumbnail": "big"},
        )
    ]
    assert a.created == [None]  # 共有リンクでもオーナーのアカウントは使わない


def test_search_by_share_link_playlist(tmp_path):
    res = adapter(tmp_path).search("https://music.youtube.com/playlist?list=PLabc_123&si=xyz")
    assert res == [Playlist(id="PLabc_123", title="共有されたプレイリスト", source="ytmusic")]


def test_search_by_share_link_unplayable_or_foreign(tmp_path, monkeypatch):
    monkeypatch.setattr(FakeYT, "playable", "UNPLAYABLE")
    assert adapter(tmp_path).search(f"https://youtu.be/{VID}") == []
    # 他サイトの URL や壊れた ID は、ふつうの検索語として扱う
    res = adapter(tmp_path).search(f"https://example.com/watch?v={VID}")
    assert res and res[0].title == "Found"
    res = adapter(tmp_path).search("https://youtu.be/short")
    assert res and res[0].title == "Found"
