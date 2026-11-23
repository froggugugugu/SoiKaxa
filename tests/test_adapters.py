from soikaxa.adapters.base import AdapterError
from soikaxa.adapters.local_mp3 import LocalAdapter
from soikaxa.adapters.registry import load_adapters


class EP:
    def __init__(self, name, obj):
        self.name, self._obj = name, obj

    def load(self):
        if isinstance(self._obj, Exception):
            raise self._obj
        return self._obj


class Good:
    name = "good"

    def __init__(self, d):
        self.d = d

    def list_playlists(self):
        return []

    def list_tracks(self, pid):
        return []

    def search(self, q, limit=20):
        return []

    def resolve(self, tid):
        raise AdapterError


def test_local_always_present_and_entry_points_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("SOIKAXA_MUSIC_DIR", str(tmp_path / "m"))
    eps = [EP("good", Good), EP("broken", ImportError("x")), EP("local", Good), EP("bad", lambda d: object())]
    ads = load_adapters(tmp_path, eps=lambda: eps)
    assert set(ads) == {"local", "good"}
    assert isinstance(ads["local"], LocalAdapter) and ads["local"].root == tmp_path / "m"
    assert ads["good"].d == tmp_path


def test_real_entry_point_registers_ytmusic(tmp_path, monkeypatch):
    monkeypatch.delenv("SOIKAXA_MUSIC_DIR", raising=False)
    ads = load_adapters(tmp_path)
    assert "ytmusic" in ads and ads["local"].root == tmp_path / "music"


def test_local_adapter_reference_behaviour(tmp_path):
    root = tmp_path / "music"
    (root / "Party").mkdir(parents=True)
    (root / "Party" / "01 intro.mp3").write_bytes(b"")
    (root / "Party" / "notes.txt").write_text("x")
    a = LocalAdapter(root)
    assert [p.track_count for p in a.list_playlists()] == [1]
    tracks = a.list_tracks("Party")
    assert tracks[0].id == "Party/01 intro.mp3"
    assert a.resolve(tracks[0].id).uri.endswith("01 intro.mp3")
    assert a.search("intro")[0].title == "01 intro"
    assert a.search("party")[0].title == "Party"
    for bad in ["../../etc/passwd", "Party/none.mp3"]:
        try:
            a.resolve(bad)
            raise AssertionError
        except AdapterError:
            pass
    assert LocalAdapter(tmp_path / "none").list_playlists() == []
