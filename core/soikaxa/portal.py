"""キャプティブポータル: OS の接続検知 URL を検出し、端末の Web 画面へ誘導する。"""

from __future__ import annotations

from .net import AP_IP

PORTAL_URL = f"http://{AP_IP}/"

# iOS/macOS, Android, Windows, Firefox 等の接続検知パス
DETECTION_PATHS = frozenset(
    {
        "/hotspot-detect.html",
        "/library/test/success.html",
        "/generate_204",
        "/gen_204",
        "/connecttest.txt",
        "/ncsi.txt",
        "/redirect",
        "/success.txt",
        "/canonical.html",
    }
)

LOCAL_HOSTS = frozenset({AP_IP, "soikaxa.local", "localhost", "127.0.0.1", "testserver"})


def _strip_port(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_detection_request(host: str | None, path: str, extra_hosts: frozenset[str] = frozenset()) -> bool:
    """接続検知か、端末以外のホスト宛てなら True（= ポータルへ 302）。"""
    if path in DETECTION_PATHS:
        return True
    if not host:
        return False
    return _strip_port(host) not in (LOCAL_HOSTS | extra_hosts)


def dnsmasq_config(ap_ip: str = AP_IP) -> str:
    """NetworkManager の shared モード用 dnsmasq 追加設定。

    配置先: /etc/NetworkManager/dnsmasq-shared.d/soikaxa.conf
    すべての名前を端末に解決させ、OS の接続検知をポータルに向ける。
    """
    return f"# SoiKaxa captive portal (generated)\naddress=/#/{ap_ip}\nno-resolv\ndomain-needed\nbogus-priv\n"


if __name__ == "__main__":  # pragma: no cover - `python -m soikaxa.portal > soikaxa.conf`
    import sys

    sys.stdout.write(dnsmasq_config())
