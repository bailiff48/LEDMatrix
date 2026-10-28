"""Tests for src/wifi_setup_service.py against a simulated NetworkManager."""
import json
import socket
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import wifi_setup_service as ws  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeNM:
    """Just enough nmcli / ip / iw behaviour for the service."""

    def __init__(self):
        self.profiles = {}           # name -> dict(type, ssid, psk, ap)
        self.active = None           # profile name active on wlan0
        self.ethernet = False
        self.visible = {}            # ssid -> (signal, psk or None)
        self.stations = 0
        self.calls = []
        self.ap_args = None

    def esc(self, s):
        return s.replace("\\", "\\\\").replace(":", "\\:")

    def __call__(self, args, timeout=30):
        self.calls.append(list(args))
        a = args
        if a[0] == "ip":
            if self.active and not self.profiles[self.active].get("ap"):
                return 0, "3: wlan0    inet 192.168.1.57/24 brd 192.168.1.255 scope global wlan0\n", ""
            if self.active:
                return 0, f"3: wlan0    inet {ws.AP_IP}/24 scope global wlan0\n", ""
            return 0, "", ""
        if a[0] == "iw":
            return 0, "Station aa:bb\n" * self.stations, ""
        assert a[0] == "nmcli", a
        rest = a[1:]
        if rest[:2] == ["radio", "wifi"]:
            return 0, "", ""
        if rest[:3] == ["device", "wifi", "rescan"]:
            return (1, "", "Scanning not allowed while in AP mode") if self.ap_on() else (0, "", "")
        if rest[:3] == ["-t", "-f", "SSID,SIGNAL,SECURITY"]:
            if self.ap_on():
                return 0, "", ""
            lines = [f"{self.esc(s)}:{sig}:{'WPA2' if psk else ''}" for s, (sig, psk) in self.visible.items()]
            return 0, "\n".join(lines) + "\n", ""
        if rest[:5] == ["-t", "-f", "NAME,TYPE,DEVICE", "connection", "show"]:
            rows = []
            if self.active:
                rows.append(f"{self.esc(self.active)}:802-11-wireless:wlan0")
            if self.ethernet:
                rows.append("Wired connection 1:802-3-ethernet:eth0")
            rows.append("lo:loopback:lo")
            return 0, "\n".join(rows) + "\n", ""
        if rest[:4] == ["-t", "-f", "NAME,TYPE", "connection"]:
            rows = [f"{self.esc(n)}:802-11-wireless" for n in self.profiles]
            return 0, "\n".join(rows) + "\n", ""
        if rest[:2] == ["connection", "delete"]:
            name = rest[2]
            if name in self.profiles:
                if self.active == name:
                    self.active = None
                del self.profiles[name]
                return 0, "", ""
            return 10, "", f"Error: unknown connection '{name}'."
        if rest[:2] == ["connection", "add"]:
            kv = dict(zip(rest[2::2], rest[3::2]))
            self.ap_args = kv
            self.profiles[kv["con-name"]] = {"ssid": kv["ssid"], "ap": kv.get("802-11-wireless.mode") == "ap",
                                             "psk": kv.get("wifi-sec.psk"),
                                             "key_mgmt": kv.get("wifi-sec.key-mgmt"),
                                             "hidden": kv.get("802-11-wireless.hidden") == "yes"}
            return 0, "", ""
        if rest[:2] == ["connection", "modify"]:
            name, kv = rest[2], dict(zip(rest[3::2], rest[4::2]))
            if name not in self.profiles:
                return 10, "", "unknown connection"
            if "connection.id" in kv:
                self.profiles[kv["connection.id"]] = self.profiles.pop(name)
                if self.active == name:
                    self.active = kv["connection.id"]
            return 0, "", ""
        if rest[:2] == ["--wait", rest[1]] and rest[2:4] == ["connection", "up"]:
            rest = rest[2:]
        if rest[:2] == ["connection", "up"]:
            name = rest[2]
            p = self.profiles.get(name)
            if not p:
                return 10, "", "unknown connection"
            if p.get("ap"):
                self.active = name
                return 0, "", ""
            vis = self.visible.get(p["ssid"])
            if p.get("psk") and not p.get("key_mgmt") and name != "preconfigured":
                return 4, "", "Error: 802-11-wireless-security.key-mgmt: property is missing."
            if vis and vis[1] == p.get("psk"):
                self.active = name
                return 0, "", ""
            if vis:
                return 4, "", ("Error: Connection activation failed: Secrets were required, "
                               "but not provided.")
            return 4, "", "Error: Connection activation failed: The Wi-Fi network could not be found"
        if rest[:2] == ["connection", "down"]:
            if self.active == rest[2]:
                self.active = None
                return 0, "", ""
            return 10, "", "not active"
        if rest[:3] == ["device", "wifi", "connect"]:
            ssid = rest[3]
            kv = dict(zip(rest[4::2], rest[5::2]))
            pw = kv.get("password")
            if pw:   # what ledpi-dev did right after the hotspot went down
                return 4, "", "Error: 802-11-wireless-security.key-mgmt: property is missing."
            self.profiles[ssid] = {"ssid": ssid, "psk": pw}
            vis = self.visible.get(ssid)
            if not vis:
                return 10, "", f"Error: No network with SSID '{ssid}' found."
            if vis[1] != pw:
                return 4, "", ("Error: Connection activation failed: Secrets were required, "
                               "but not provided.")
            self.active = ssid
            return 0, "Device 'wlan0' successfully activated", ""
        raise AssertionError(f"unexpected nmcli call {args}")

    def ap_on(self):
        return bool(self.active and self.profiles.get(self.active, {}).get("ap"))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def env(tmp_path):
    nm, clock = FakeNM(), Clock()
    svc = ws.WifiSetupService(
        settings={"boot_grace_seconds": 90, "retry_saved_seconds": 300},
        run=nm, clock=clock, sleep=clock.sleep,
        status_file=tmp_path / "wifi_status.json", request_file=tmp_path / "wifi_setup_request",
        state_file=tmp_path / "state.json", dnsmasq_conf=tmp_path / "dnsmasq" / "captive.conf",
        http_port=free_port())
    yield svc, nm, clock, tmp_path
    svc.stop_http()


def panel(tmp_path):
    f = tmp_path / "wifi_status.json"
    return json.loads(f.read_text()) if f.exists() else None


def http(svc, path, data=None, host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{svc.http_port}{path}",
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json", **({"Host": host} if host else {})})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        r = opener.open(req, timeout=5)
        return r.status, r.headers, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


def boot_offline(svc, nm):
    nm.visible = {"HomeNet": (80, "correct-horse"), "Neighbor:5G": (40, "x" * 8), "Cafe": (20, None)}
    nm.profiles["preconfigured"] = {"ssid": "BaileysHouse", "psk": "pw123456"}   # imager profile
    assert not svc.wait_for_network()
    assert svc.enter_setup("no network after boot")


def test_online_at_boot_stays_out_of_setup(env):
    svc, nm, clock, tmp = env
    nm.ethernet = True
    assert svc.wait_for_network()
    assert not any(c[:2] == ["connection", "add"] for c in [x[1:] for x in nm.calls])


def test_offline_boot_starts_hotspot_with_fixed_address(env):
    svc, nm, clock, tmp = env
    boot_offline(svc, nm)
    assert nm.ap_on() and nm.active == ws.AP_PROFILE
    a = nm.ap_args
    assert a["802-11-wireless.mode"] == "ap" and a["ipv4.method"] == "shared"
    assert a["ipv4.addresses"] == "192.168.4.1/24" and a["ssid"] == "TickerSetup"
    assert a["wifi-sec.psk"] == "ledticker" and a["wifi-sec.proto"] == "rsn"
    # scanned BEFORE the hotspot came up; escaped ':' handled
    assert [n["ssid"] for n in svc.networks] == ["HomeNet", "Neighbor:5G", "Cafe"]
    assert svc.networks[2]["secure"] is False
    conf = (tmp / "dnsmasq" / "captive.conf").read_text()
    assert "address=/#/192.168.4.1" in conf
    p = panel(tmp)
    flat = " ".join(" ".join(pg) for pg in p["pages"])
    assert "TickerSetup" in flat and "ledticker" in flat and "192.168.4.1" in flat


def test_setup_page_and_captive_redirects(env):
    svc, nm, clock, tmp = env
    boot_offline(svc, nm)
    code, hdr, body = http(svc, "/generate_204", host="connectivitycheck.gstatic.com")
    assert code == 302 and hdr["Location"] == "http://192.168.4.1/"
    code, hdr, body = http(svc, "/hotspot-detect.html", host="captive.apple.com")
    assert code == 302
    code, hdr, body = http(svc, "/", host="192.168.4.1")
    assert code == 200 and "Connect your ticker to Wi-Fi" in body and "TickerSetup" in body
    code, hdr, body = http(svc, "/api/state", host="192.168.4.1")
    st = json.loads(body)
    assert [n["ssid"] for n in st["networks"]][0] == "HomeNet"
    code, _, body = http(svc, "/api/connect", {"ssid": "HomeNet", "password": "short"}, host="192.168.4.1")
    assert code == 400 and "8 to 63" in body
    code, _, body = http(svc, "/api/connect", {"ssid": "", "password": ""}, host="192.168.4.1")
    assert code == 400


def test_wrong_password_brings_hotspot_back_and_explains(env):
    svc, nm, clock, tmp = env
    boot_offline(svc, nm)
    code, _, _ = http(svc, "/api/connect", {"ssid": "HomeNet", "password": "wrong-pass"}, host="192.168.4.1")
    assert code == 200
    svc.tick()
    assert nm.ap_on(), "hotspot must come back after a failed join"
    assert "HomeNet" not in nm.profiles, "failed profile is removed so NM doesn't keep retrying it"
    assert svc.last_result["ok"] is False and "password" in svc.last_result["error"].lower()
    assert svc.mode == "setup"
    p = panel(tmp)
    assert p["pages"][0][0] == "COULDN'T CONNECT"
    st = json.loads(http(svc, "/api/state", host="192.168.4.1")[2])
    assert st["last_result"]["ssid"] == "HomeNet"


def test_right_password_connects_and_cleans_up(env):
    svc, nm, clock, tmp = env
    boot_offline(svc, nm)
    http(svc, "/api/connect", {"ssid": "HomeNet", "password": "correct-horse"}, host="192.168.4.1")
    svc.tick()
    assert nm.active == "HomeNet" and not nm.ap_on()
    assert ws.AP_PROFILE not in nm.profiles
    assert not (tmp / "dnsmasq" / "captive.conf").exists()
    assert svc.mode == "client" and svc.httpd is None
    p = panel(tmp)
    assert p["pages"][0][0] == "CONNECTED!" and "192.168.1.57:5001" in p["pages"][0][3]
    assert svc.tick() == 10          # client mode idles


def test_open_network_and_hidden_flag(env):
    svc, nm, clock, tmp = env
    boot_offline(svc, nm)
    nm.visible["Hidden1"] = (60, "secret-pass")
    http(svc, "/api/connect", {"ssid": "Hidden1", "password": "secret-pass", "hidden": True}, host="192.168.4.1")
    svc.tick()
    assert nm.active == "Hidden1" and nm.profiles["Hidden1"]["hidden"]
    assert nm.profiles["Hidden1"]["key_mgmt"] == "wpa-psk"


def test_router_back_after_power_cut_leaves_setup(env):
    svc, nm, clock, tmp = env
    boot_offline(svc, nm)
    nm.profiles["preconfigured"] = {"ssid": "HomeNet", "psk": "correct-horse"}
    # Within the retry interval nothing happens
    clock.sleep(100); svc.tick()
    assert nm.ap_on()
    # A phone is using the page: don't pull the hotspot out from under it
    clock.sleep(250); svc.last_http = clock()
    svc.tick()
    assert nm.ap_on()
    # Idle and interval passed: retries saved Wi-Fi and leaves setup
    clock.sleep(400)
    svc.tick()
    assert nm.active == "preconfigured" and svc.mode == "client"
    assert panel(tmp) is None


def test_saved_retry_fails_and_hotspot_returns(env):
    svc, nm, clock, tmp = env
    boot_offline(svc, nm)
    clock.sleep(400)
    svc.tick()
    assert nm.ap_on() and svc.mode == "setup"


def test_web_ui_request_enters_setup(env):
    svc, nm, clock, tmp = env
    nm.ethernet = True
    svc.mode = "client"
    (tmp / "wifi_setup_request").write_text("x")
    svc.tick()
    assert svc.mode == "setup" and nm.ap_on()
    assert not (tmp / "wifi_setup_request").exists()


def test_hotspot_restarted_if_it_drops(env):
    svc, nm, clock, tmp = env
    boot_offline(svc, nm)
    nm.active = None
    svc.tick()
    assert nm.ap_on()


def test_split_terse():
    assert ws.split_terse(r"My\:Net:80:WPA2") == ["My:Net", "80", "WPA2"]
    assert ws.split_terse(r"a\\b:1") == ["a\\b", "1"]


def test_panel_hook_draws_only_fresh_status(tmp_path):
    from PIL import Image, ImageDraw
    from src import wifi_status_display as d
    import time as _t

    class DM:
        def __init__(self):
            self.image = Image.new("RGB", (128, 32)); self.draw = ImageDraw.Draw(self.image); self.updates = 0
        def update_display(self):
            self.updates += 1
    f = tmp_path / "s.json"
    dm = DM()
    assert d.show(dm, f) is False                          # no file
    f.write_text(json.dumps({"pages": [["WIFI SETUP", "hi"]], "timestamp": _t.time(), "ttl": 30}))
    assert d.show(dm, f) is True and dm.updates == 1
    assert dm.image.getbbox() is not None                  # something was drawn
    f.write_text(json.dumps({"pages": [["X"]], "timestamp": _t.time() - 100, "ttl": 30}))
    assert d.show(dm, f) is False                          # stale -> normal rotation resumes


def test_join_sets_security_explicitly_and_keeps_old_profile_on_failure(env):
    svc, nm, clock, tmp = env
    boot_offline(svc, nm)
    nm.profiles["HomeNet"] = {"ssid": "HomeNet", "psk": "old-password", "key_mgmt": "wpa-psk"}
    http(svc, "/api/connect", {"ssid": "HomeNet", "password": "wrong-pass"}, host="192.168.4.1")
    svc.tick()
    assert "HomeNet" in nm.profiles and ws.JOIN_PROFILE not in nm.profiles   # old one untouched
    http(svc, "/api/connect", {"ssid": "HomeNet", "password": "correct-horse"}, host="192.168.4.1")
    svc.tick()
    assert nm.active == "HomeNet" and nm.profiles["HomeNet"]["psk"] == "correct-horse"
    assert nm.profiles["HomeNet"]["key_mgmt"] == "wpa-psk"
    assert ws.JOIN_PROFILE not in nm.profiles


def test_wpa3_only_uses_sae(env):
    svc, nm, clock, tmp = env
    boot_offline(svc, nm)
    svc.networks.append({"ssid": "New6E", "signal": 50, "secure": True, "security": "WPA3"})
    assert svc.key_mgmt_for("New6E") == "sae"
    svc.networks.append({"ssid": "Mixed", "signal": 50, "secure": True, "security": "WPA2 WPA3"})
    assert svc.key_mgmt_for("Mixed") == "wpa-psk"
