import time
from dataclasses import replace

import pytest
from PIL import Image
from soikaxa.lcd import fb, record, screens, theme
from soikaxa.lcd.qr import make_qr


def test_rgb565_known_values():
    img = Image.new("RGB", (4, 1))
    for i, c in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]):
        img.putpixel((i, 0), c)
    assert fb.to_rgb565(img).hex() == "00f8e0071f00ffff"


def _fake_sysfs(root, sizes):
    for name, size in sizes.items():
        d = root / name
        d.mkdir(parents=True)
        (d / "virtual_size").write_text(size + "\n")


def test_find_lcd_fb_picks_480x320(tmp_path):
    _fake_sysfs(tmp_path, {"fb0": "480,320", "fb1": "1920,1080"})
    assert fb.find_lcd_fb(tmp_path) == "/dev/fb0"


def test_find_lcd_fb_falls_back_to_fb1(tmp_path):
    _fake_sysfs(tmp_path, {"fb0": "1920,1080"})
    assert fb.find_lcd_fb(tmp_path) == "/dev/fb1"
    assert fb.find_lcd_fb(tmp_path / "missing") == "/dev/fb1"


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


def test_region_write_is_one_full_width_write(tmp_path, monkeypatch):
    """部分書き込みも、行ごとではなく行の範囲をまとめて 1 回で書く（途中で転送されて上下に割れない）。"""
    path = tmp_path / "fb"
    f = fb.Framebuffer(path, width=4, height=3)
    f.write(Image.new("RGB", (4, 3), (0, 0, 0)))
    calls = []
    orig = fb.os.pwrite

    def rec(fd, b, off):
        calls.append((len(b), off))
        return orig(fd, b, off)

    monkeypatch.setattr(fb.os, "pwrite", rec)
    f.write(Image.new("RGB", (2, 2), (255, 255, 255)), xy=(1, 1))
    assert calls == [(2 * 4 * 2, 1 * 4 * 2)]


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


def test_record_cover_fills_disc_with_thin_rim():
    """ジャケットは円のほぼ全面。黒縁は細く、回転が分かるマゼンタの弧は縁の中に置く。"""
    disc = record.record_disc(Image.new("RGB", (50, 50), (255, 0, 0)), "s")
    c = record.DIAMETER // 2
    assert disc.getpixel((c + record.COVER_DIAMETER // 2 - 4, c)) == (255, 0, 0)  # 外寄りまでジャケット
    assert record.DIAMETER - record.COVER_DIAMETER <= 24  # 縁は片側 12px 以下
    rim = {
        disc.getpixel((c + x, c - y))
        for x in range(c)
        for y in range(c)
        if (x * x + y * y) ** 0.5 > record.COVER_DIAMETER / 2 + 1 and (x * x + y * y) ** 0.5 < c - 2
    }
    assert theme.MAGENTA in rim


def test_now_playing_with_queue_rows_replaces_record():
    rows = (
        screens.QueueRow("追加された曲", "＋ Aさん", highlight=True),
        screens.QueueRow("プレイリストの曲", "歌手"),
    )
    base = screens.NowPlayingView(title="曲")
    with_list = screens.render_now_playing(replace(base, queue_rows=rows))
    with_disc = screens.render_now_playing(base)
    assert with_list.size == (480, 320)
    left = (0, 0, 230, 320)
    assert with_list.crop(left).tobytes() != with_disc.crop(left).tobytes()
    colors = {c for _, c in with_list.crop(left).getcolors(100000)}
    assert theme.MAGENTA in colors  # 追加された曲を強調


def test_mode_button_and_icon_follow_mode():
    base = screens.NowPlayingView(title="曲", guest_qr="WIFI:T:WPA;S:x;P:y;;")
    box = screens.MODE_BUTTON
    crops = [
        screens.render_now_playing(replace(base, mode=m)).crop(box).tobytes()
        for m in ("disc", "list", "auto")
    ]
    assert len(set(crops)) == 3  # ラベルとアイコンがモードごとに違う
    assert box[2] <= 480 and box[3] <= 320 and box[2] - box[0] >= 44 and box[3] - box[1] >= 44


LONG_TITLE = "とても長い曲名のテスト — Extended Version (Live at Somewhere 2026)"


def test_marquee_offset_pauses_then_scrolls_and_loops():
    """長い曲名は先頭で止まってから左へ流れ、一周したらまた止まる。収まる曲名は動かない。"""
    assert screens.marquee_offset("短い曲", 10.0) == 0
    assert screens.overflows(LONG_TITLE)
    pause, speed = screens.MARQUEE_PAUSE_SEC, screens.MARQUEE_SPEED
    assert screens.marquee_offset(LONG_TITLE, 0.0) == 0
    assert screens.marquee_offset(LONG_TITLE, pause - 0.1) == 0
    assert screens.marquee_offset(LONG_TITLE, pause + 1.0) == int(speed)
    span = theme.font(screens.TITLE.size).getlength(LONG_TITLE) + screens.MARQUEE_GAP
    cycle = pause + span / speed
    assert screens.marquee_offset(LONG_TITLE, cycle + 0.5) == 0  # 2 周目の先頭で止まる


def test_render_title_scrolls_long_title_without_ellipsis():
    x0, y0, x1, y1 = screens.TITLE.box
    short = screens.render_strip("短い曲")
    assert short.size == (x1 - x0, y1 - y0)
    a, b = screens.render_strip(LONG_TITLE, 0), screens.render_strip(LONG_TITLE, 40)
    assert a.tobytes() != b.tobytes()
    span = int(theme.font(screens.TITLE.size).getlength(LONG_TITLE)) + screens.MARQUEE_GAP
    # 一周ずらすと最初と同じ絵になる（つなぎ目がない）
    assert screens.render_strip(LONG_TITLE, span).tobytes() == a.tobytes()


def test_now_playing_draws_title_strip_at_offset():
    v = screens.NowPlayingView(title=LONG_TITLE, guest_qr="X")
    img = screens.render_now_playing(replace(v, title_offset=40))
    strip = img.crop(screens.TITLE.box)
    assert strip.tobytes() == screens.render_strip(LONG_TITLE, 40).tobytes()


def test_artist_line_scrolls_like_title():
    """アーティスト名も、はみ出すときは同じように流す。待機中の URL はシアンのまま。"""
    long = "The Very Long Artist Name Orchestra feat. Someone Else"
    assert screens.overflows(long, screens.ARTIST) and not screens.overflows("YOASOBI", screens.ARTIST)
    assert screens.marquee_offset(long, screens.MARQUEE_PAUSE_SEC + 1.0, screens.ARTIST) > 0
    v = screens.NowPlayingView(title="曲", artist=long, guest_qr="X", artist_offset=30)
    img = screens.render_now_playing(v)
    expect = screens.render_strip(long, 30, screens.ARTIST, theme.MUTED)
    assert img.crop(screens.ARTIST.box).tobytes() == expect.tobytes()
    assert screens.artist_line(replace(v, hint="http://10.42.0.1/#owner"))[1] == theme.CYAN


def test_title_and_artist_strips_do_not_overlap():
    assert screens.TITLE.box[3] <= screens.ARTIST.box[1]
