import time

import pytest
from PIL import Image
from soikaxa.lcd import fb, record, screens, theme
from soikaxa.lcd.qr import make_qr


def test_rgb565_known_values():
    img = Image.new("RGB", (4, 1))
    for i, c in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]):
        img.putpixel((i, 0), c)
    assert fb.to_rgb565(img).hex() == "00f8e0071f00ffff"


def test_framebuffer_full_and_region_writes(tmp_path):
    path = tmp_path / "fb"
    f = fb.Framebuffer(path, width=4, height=3)
    f.write(Image.new("RGB", (4, 3), (0, 0, 0)))
    f.write(Image.new("RGB", (2, 2), (255, 255, 255)), xy=(1, 1))
    data = path.read_bytes()
    assert len(data) == 4 * 3 * 2
    row1 = data[8:16]
    assert row1 == b"\x00\x00" + b"\xff\xff" * 2 + b"\x00\x00"
    assert f.available
    with pytest.raises(ValueError):
        f.write(Image.new("RGB", (3, 1)), xy=(2, 0))


def test_qr_size_and_contrast():
    q = make_qr("WIFI:T:WPA;S:soikaxa-ABCD;P:abcdefghjk;;", 140)
    assert q.size == (140, 140)
    colors = {c for _, c in q.getcolors(10000)}
    assert colors <= {(0, 0, 0), (255, 255, 255)}


def test_fit_text_truncates():
    f = theme.font(16)
    long = "とても長い曲名" * 20
    out = screens.fit_text(long, f, 200)
    assert out.endswith("…") and f.getlength(out) <= 200
    assert screens.fit_text("short", f, 200) == "short"


@pytest.mark.parametrize(
    "img",
    [
        lambda: screens.render_boot(0.5),
        lambda: screens.render_boot(2.0),
        lambda: screens.render_setup("WIFI:T:WPA;S:x;P:y;;", "http://10.42.0.1/"),
        lambda: screens.render_error("Wi-Fi に繋がりません", "WIFI:T:WPA;S:x;P:y;;"),
        lambda: screens.render_setup_full("WIFI:T:WPA;S:x;P:y;;"),
        lambda: screens.render_now_playing(
            screens.NowPlayingView(
                title="曲",
                artist="A",
                position=30,
                duration=200,
                next_title="次の曲",
                next_added_by="Aさん",
                guest_qr="WIFI:T:WPA;S:x;P:y;;",
                paused=True,
                notice="音源側の変更で停止中",
            )
        ),
        lambda: screens.render_now_playing(screens.NowPlayingView(title="")),
    ],
)
def test_screens_render_480x320(img):
    im = img()
    assert im.size == (480, 320) and im.mode == "RGB"
    assert im.getbbox() is not None


def test_pattern_cover_deterministic():
    a = record.pattern_cover("playlist A")
    assert a.tobytes() == record.pattern_cover("playlist A").tobytes()
    assert a.tobytes() != record.pattern_cover("playlist B").tobytes()


def test_record_frames_72_rgb565_and_cycle():
    t0 = time.perf_counter()
    rf = record.RecordFrames.build(Image.new("RGB", (300, 300), (200, 10, 10)), "x")
    elapsed = time.perf_counter() - t0
    assert len(rf.frames) == 72
    assert all(len(f) == 200 * 200 * 2 for f in rf.frames)
    assert rf.frames[0] != rf.frames[1]  # 回転している
    first = rf.next()
    for _ in range(71):
        rf.next()
    assert rf.current == first
    assert elapsed < 10  # 開発 PC で十分速い（Pi では生成中は静止画）


def test_font_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SOIKAXA_FONT", str(tmp_path / "missing.ttf"))
    theme.find_font_path()  # 存在しなければ候補探索に戻る
    bogus = tmp_path / "bogus.ttf"
    bogus.write_bytes(b"not a font")
    monkeypatch.setenv("SOIKAXA_FONT", str(bogus))
    monkeypatch.setattr(theme, "FONT_CANDIDATES", ())
    theme.font.cache_clear()
    assert theme.font(12) is not None  # 壊れたフォントでも既定にフォールバック
    theme.font.cache_clear()


def test_qr_is_cached():
    assert make_qr("WIFI:T:WPA;S:a;P:bbbbbbbb;;", 140) is make_qr("WIFI:T:WPA;S:a;P:bbbbbbbb;;", 140)


def test_setup_screens_show_code():
    qr = "WIFI:T:WPA;S:x;P:y;;"
    for render in (
        lambda code: screens.render_setup(qr, "http://10.42.0.1/", code),
        lambda code: screens.render_setup_full(qr, code=code),
        lambda code: screens.render_error("エラー", qr, code),
    ):
        assert render("123456").tobytes() != render(None).tobytes()
