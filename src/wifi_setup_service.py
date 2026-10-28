#!/usr/bin/env python3
"""
LED Matrix Wi-Fi setup service  (Oct 2026)

Runs on its own (systemd: ledmatrix-wifi-setup.service, as root) so it does
not depend on the web UI or on the internet. Standard library only.

At boot it waits for a network (Wi-Fi or Ethernet). If none comes up within
`boot_grace_seconds`, it:

  1. scans for nearby Wi-Fi networks (the Pi's chip cannot scan while it is a
     hotspot, so the list is taken first and kept);
  2. starts the "TickerSetup" hotspot at a fixed 192.168.4.1, with DNS for
     every name pointed at the Pi, so phones open the setup page by themselves
     ("Sign in to network");
  3. serves the setup page on port 80 and shows the steps on the LED panel
     (via config/wifi_status.json, read by the display service).

When the person picks a network, the hotspot is taken down and the Pi tries
to join it. On success the panel shows the new address and setup ends. On
failure the hotspot comes back, the panel says so, and the page shows why.

While the hotspot is up and nobody is using it, the Pi retries its saved
Wi-Fi every few minutes, so a router that boots slower than the Pi after a
power cut does not leave the display stuck in setup mode.

The web UI can ask for setup mode by creating config/wifi_setup_request.

Config (optional), config.json -> "wifi_setup":
  enabled (true), ssid ("TickerSetup"), password ("ledticker"; "" = open),
  boot_grace_seconds (90), retry_saved_seconds (300), interface ("wlan0")
"""
import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_DIR / "config"
STATUS_FILE = CONFIG_DIR / "wifi_status.json"        # read by the display service
REQUEST_FILE = CONFIG_DIR / "wifi_setup_request"     # touched by the web UI
STATE_FILE = CONFIG_DIR / "wifi_setup_state.json"    # last result, for the page
DNSMASQ_CONF = Path("/etc/NetworkManager/dnsmasq-shared.d/ledmatrix-captive.conf")

AP_PROFILE = "TickerSetup-AP"
JOIN_PROFILE = "LEDMatrix-Join"   # temporary name while a join is tried
AP_IP = "192.168.4.1"
WEB_UI_PORT = 5001

DEFAULTS = {
    "enabled": True,
    "ssid": "TickerSetup",
    "password": "ledticker",
    "boot_grace_seconds": 90,
    "retry_saved_seconds": 300,
    "interface": "wlan0",
}

logger = logging.getLogger("wifi_setup")


# ---------------------------------------------------------------- helpers

def split_terse(line):
    """Split one line of `nmcli -t` output (':' separated, '\\:' escaped)."""
    fields, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    fields.append("".join(cur))
    return fields


def friendly_error(stderr):
    s = (stderr or "").lower()
    if "secrets were required" in s or ("psk" in s and "invalid" in s):
        return "The password was not accepted."
    if "no network with ssid" in s or "not found" in s or "could not be found" in s:
        return "That network wasn't found. Is it in range, and is the name exact?"
    if "timeout" in s or "timed out" in s:
        return "The network didn't answer in time."
    if "ip configuration could not be reserved" in s or "dhcp" in s:
        return "Joined the network but got no address from the router."
    text = (stderr or "").strip().splitlines()
    return (text[-1] if text else "Unknown error")[:200]


def load_settings():
    settings = dict(DEFAULTS)
    try:
        with open(CONFIG_DIR / "config.json") as f:
            settings.update((json.load(f).get("wifi_setup") or {}))
    except Exception as e:
        logger.info("Using default Wi-Fi setup settings (%s)", e)
    return settings


# ---------------------------------------------------------------- the service

class WifiSetupService:
    def __init__(self, settings=None, run=None, clock=time.time, sleep=time.sleep,
                 status_file=STATUS_FILE, request_file=REQUEST_FILE,
                 state_file=STATE_FILE, dnsmasq_conf=DNSMASQ_CONF,
                 http_port=80):
        self.s = dict(DEFAULTS, **(settings or {}))
        self.iface = self.s["interface"]
        self._run_cmd = run or self._subprocess_run
        self.clock = clock
        self.sleep = sleep
        self.status_file = Path(status_file)
        self.request_file = Path(request_file)
        self.state_file = Path(state_file)
        self.dnsmasq_conf = Path(dnsmasq_conf)
        self.http_port = http_port

        self.mode = "starting"            # starting | client | setup
        self.networks = []                # cached scan: [{ssid, signal, secure}]
        self.connect_queue = queue.Queue()
        self.last_result = self._load_state()
        self.last_http = 0.0              # last time a phone talked to the page
        self.last_retry = 0.0
        self.httpd = None
        self.http_thread = None
        self.lock = threading.Lock()

    # -- shell

    @staticmethod
    def _subprocess_run(args, timeout=30):
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return 124, "", "timed out"
        except FileNotFoundError as e:
            return 127, "", str(e)

    def run(self, args, timeout=30):
        rc, out, err = self._run_cmd(args, timeout=timeout)
        shown = list(args)
        for i, a in enumerate(shown[:-1]):
            if a in ("wifi-sec.psk", "password"):
                shown[i + 1] = "***"
        logger.debug("%s -> %s %s", " ".join(shown), rc, (err or "").strip()[:200])
        return rc, out, err

    # -- state for the page

    def _load_state(self):
        try:
            return json.loads(self.state_file.read_text()).get("last_result")
        except Exception:
            return None

    def _save_result(self, result):
        self.last_result = result
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"last_result": result}))
        except Exception as e:
            logger.warning("Could not save setup state: %s", e)

    # -- LED panel

    def show(self, pages, ttl=30, color=None):
        """pages: list of lists of lines (title first). Refreshed while needed."""
        data = {"pages": pages, "timestamp": self.clock(), "ttl": ttl}
        if color:
            data["color"] = color
        try:
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.status_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            os.replace(tmp, self.status_file)
        except Exception as e:
            logger.warning("Could not write LED status: %s", e)

    def clear_panel(self):
        try:
            self.status_file.unlink()
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Could not clear LED status: %s", e)

    def setup_pages(self):
        ssid, pw = self.s["ssid"], self.s["password"]
        join = ["WIFI SETUP", "On your phone, join", f"Wi-Fi: {ssid}"]
        join.append(f"Password: {pw}" if pw else "(no password)")
        pages = [join, ["WIFI SETUP", "A setup page opens.", "If not, browse to", AP_IP]]
        r = self.last_result
        if r and not r.get("ok") and self.clock() - r.get("at", 0) < 900:
            pages.insert(0, ["COULDN'T CONNECT", f"to {r.get('ssid', '')[:16]}",
                             "Check the password,", f"rejoin {ssid}"])
        return pages

    # -- network state

    def active_connections(self):
        rc, out, _ = self.run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"])
        rows = [split_terse(l) for l in out.splitlines() if l.strip()] if rc == 0 else []
        return [r for r in rows if len(r) >= 3]

    def is_online(self):
        """True if any real Wi-Fi or Ethernet connection is up (not our hotspot)."""
        for name, ctype, dev in self.active_connections():
            if name == AP_PROFILE:
                continue
            if ctype in ("802-11-wireless", "wifi", "802-3-ethernet", "ethernet") and dev:
                return True
        return False

    def ap_active(self):
        return any(r[0] == AP_PROFILE for r in self.active_connections())

    def saved_wifi_profiles(self):
        rc, out, _ = self.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
        if rc != 0:
            return []
        return [r[0] for r in (split_terse(l) for l in out.splitlines() if l.strip())
                if len(r) >= 2 and r[1] in ("802-11-wireless", "wifi") and r[0] != AP_PROFILE]

    def wifi_ip(self):
        rc, out, _ = self.run(["ip", "-4", "-o", "addr", "show", self.iface])
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out or "")
        return m.group(1) if m and m.group(1) != AP_IP else None

    def stations_connected(self):
        """Number of phones joined to the hotspot (0 if it can't be told)."""
        rc, out, _ = self.run(["iw", "dev", self.iface, "station", "dump"])
        return out.count("Station ") if rc == 0 else 0

    def scan(self):
        self.run(["nmcli", "radio", "wifi", "on"])
        self.run(["nmcli", "device", "wifi", "rescan", "ifname", self.iface], timeout=20)
        self.sleep(4)
        rc, out, _ = self.run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi",
                               "list", "ifname", self.iface, "--rescan", "no"], timeout=20)
        best = {}
        for line in (out.splitlines() if rc == 0 else []):
            f = split_terse(line)
            if len(f) < 3 or not f[0] or f[0] == self.s["ssid"]:
                continue
            sig = int(f[1]) if f[1].isdigit() else 0
            if f[0] not in best or sig > best[f[0]]["signal"]:
                best[f[0]] = {"ssid": f[0], "signal": sig, "secure": f[2] not in ("", "--"),
                             "security": f[2]}
        nets = sorted(best.values(), key=lambda n: -n["signal"])
        if nets or not self.networks:
            self.networks = nets
        logger.info("Scan found %d networks", len(nets))
        return nets

    # -- hotspot

    def write_dnsmasq_conf(self):
        try:
            self.dnsmasq_conf.parent.mkdir(parents=True, exist_ok=True)
            self.dnsmasq_conf.write_text(
                "# Written by ledmatrix wifi_setup_service while TickerSetup is up\n"
                f"address=/#/{AP_IP}\n"
                f"dhcp-option=114,http://{AP_IP}/\n")
        except Exception as e:
            logger.warning("Could not write captive-portal DNS config: %s", e)

    def remove_dnsmasq_conf(self):
        try:
            self.dnsmasq_conf.unlink()
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Could not remove captive-portal DNS config: %s", e)

    def start_ap(self):
        ssid, pw = self.s["ssid"], self.s["password"]
        self.write_dnsmasq_conf()
        self.run(["nmcli", "radio", "wifi", "on"])
        self.run(["nmcli", "connection", "delete", AP_PROFILE])
        add = ["nmcli", "connection", "add", "type", "wifi", "ifname", self.iface,
               "con-name", AP_PROFILE, "autoconnect", "no", "ssid", ssid,
               "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
               "ipv4.method", "shared", "ipv4.addresses", f"{AP_IP}/24",
               "ipv6.method", "disabled"]
        if pw:
            # WPA2-only settings that the Pi's Broadcom chip handles reliably
            add += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", pw,
                    "wifi-sec.proto", "rsn", "wifi-sec.pairwise", "ccmp",
                    "wifi-sec.group", "ccmp", "wifi-sec.pmf", "disable"]
        rc, _, err = self.run(add)
        if rc != 0:
            logger.error("Could not create hotspot profile: %s", err.strip())
            return False
        rc, _, err = self.run(["nmcli", "connection", "up", AP_PROFILE], timeout=45)
        if rc != 0:
            logger.error("Could not start hotspot: %s", err.strip())
            return False
        logger.info("Hotspot %s up at %s", ssid, AP_IP)
        return True

    def stop_ap(self, delete=True):
        self.run(["nmcli", "connection", "down", AP_PROFILE], timeout=20)
        if delete:
            self.run(["nmcli", "connection", "delete", AP_PROFILE])
            self.remove_dnsmasq_conf()

    # -- modes

    def enter_setup(self, reason):
        logger.info("Entering setup mode: %s", reason)
        self.mode = "setup"
        self.show(self.setup_pages(), ttl=60)
        self.scan()
        ok = self.start_ap()
        if not ok:
            self.show([["WIFI SETUP", "Couldn't start the", "setup network.", "Retrying..."]], ttl=90)
        self.start_http()
        self.last_retry = self.clock()
        return ok

    def leave_setup(self):
        self.stop_http()
        self.stop_ap(delete=True)
        self.mode = "client"

    def try_saved_networks(self):
        """Hotspot down, try saved Wi-Fi; True if online. Hotspot is NOT restarted here."""
        profiles = self.saved_wifi_profiles()
        if not profiles:
            return False
        logger.info("Retrying saved Wi-Fi: %s", profiles)
        self.stop_ap(delete=False)
        self.sleep(2)
        for name in profiles:
            rc, _, _ = self.run(["nmcli", "connection", "up", name], timeout=40)
            if rc == 0 and self.is_online():
                return True
        return self.is_online()

    def key_mgmt_for(self, ssid):
        """wpa-psk for WPA/WPA2 (and WPA2/WPA3 mixed); sae only for WPA3-only networks."""
        sec = next((n.get("security", "") for n in self.networks if n["ssid"] == ssid), "")
        if "WPA3" in sec and "WPA2" not in sec and "WPA1" not in sec:
            return "sae"
        return "wpa-psk"

    def handle_connect(self, ssid, password, hidden=False):
        """Join `ssid` with a profile built explicitly.

        `nmcli device wifi connect` needs the network in a fresh scan to learn its
        security type, and right after the hotspot goes down there often isn't one
        ("802-11-wireless-security.key-mgmt: property is missing"). So the profile is
        created with the security set from the scan taken before the hotspot came up,
        under a temporary name, and only kept (renamed to the SSID) if it works.
        """
        logger.info("Joining %s (hidden=%s)", ssid, hidden)
        self.show([["CONNECTING", f"to {ssid[:20]}", "", "Please wait..."]], ttl=120)
        self.stop_ap(delete=False)
        self.sleep(2)
        self.run(["nmcli", "device", "wifi", "rescan", "ifname", self.iface], timeout=20)
        self.sleep(3)
        self.run(["nmcli", "connection", "delete", JOIN_PROFILE])
        add = ["nmcli", "connection", "add", "type", "wifi", "ifname", self.iface,
               "con-name", JOIN_PROFILE, "autoconnect", "yes", "ssid", ssid,
               "connection.autoconnect-priority", "10"]
        if hidden:
            add += ["802-11-wireless.hidden", "yes"]
        if password:
            add += ["wifi-sec.key-mgmt", self.key_mgmt_for(ssid), "wifi-sec.psk", password]
        rc, _, err = self.run(add)
        if rc == 0:
            rc, _, err = self.run(["nmcli", "--wait", "50", "connection", "up", JOIN_PROFILE],
                                  timeout=60)
        ip = None
        if rc == 0:
            for _ in range(10):
                ip = self.wifi_ip()
                if ip:
                    break
                self.sleep(2)
        if rc == 0 and ip:
            # Keep it under the network's own name, replacing an older profile of that name.
            if ssid != JOIN_PROFILE and ssid in self.saved_wifi_profiles():
                self.run(["nmcli", "connection", "delete", ssid])
            self.run(["nmcli", "connection", "modify", JOIN_PROFILE, "connection.id", ssid])
            self._save_result({"ok": True, "ssid": ssid, "ip": ip, "at": self.clock()})
            self.leave_setup()
            self.show([["CONNECTED!", f"Wi-Fi: {ssid[:13]}", "Browse to:",
                        f"{ip}:{WEB_UI_PORT}"]], ttl=180, color="green")
            logger.info("Joined %s, address %s", ssid, ip)
            return True
        reason = friendly_error(err) if rc != 0 else "Joined, but got no address from the router."
        logger.warning("Could not join %s: %s", ssid, (err or "").strip())
        self._save_result({"ok": False, "ssid": ssid, "error": reason, "at": self.clock()})
        self.run(["nmcli", "connection", "down", JOIN_PROFILE], timeout=20)
        self.run(["nmcli", "connection", "delete", JOIN_PROFILE])
        self.start_ap()
        self.show(self.setup_pages(), ttl=60)
        return False

    # -- main loop

    def wait_for_network(self):
        deadline = self.clock() + self.s["boot_grace_seconds"]
        self.show([["STARTING UP", "Looking for", "a network..."]], ttl=self.s["boot_grace_seconds"] + 30)
        while self.clock() < deadline:
            if self.is_online():
                return True
            self.sleep(5)
        return self.is_online()

    def tick(self):
        """One pass of the loop. Returns seconds to sleep."""
        if self.mode == "client":
            if self.request_file.exists():
                try:
                    self.request_file.unlink()
                except Exception:
                    pass
                self.enter_setup("requested from the web UI")
                return 2
            return 10

        # setup mode
        try:
            req = self.connect_queue.get_nowait()
        except queue.Empty:
            req = None
        if req:
            self.handle_connect(*req)
            return 2

        if self.request_file.exists():
            try:
                self.request_file.unlink()
            except Exception:
                pass

        now = self.clock()
        if not self.ap_active():
            logger.warning("Hotspot is down; restarting it")
            self.start_ap()

        idle = now - self.last_http > 180 and self.stations_connected() == 0
        if idle and now - self.last_retry >= self.s["retry_saved_seconds"]:
            self.last_retry = now
            self.show([["WIFI SETUP", "Trying saved", "Wi-Fi again..."]], ttl=90)
            if self.try_saved_networks():
                logger.info("Saved Wi-Fi is back; leaving setup mode")
                self.leave_setup()
                self.clear_panel()
                return 10
            self.start_ap()

        self.show(self.setup_pages(), ttl=60)
        return 5

    def run_forever(self):
        if not self.s.get("enabled", True):
            logger.info("Wi-Fi setup disabled in config; exiting")
            self.clear_panel()
            return
        self.remove_dnsmasq_conf()           # never leave DNS hijack behind
        self.run(["nmcli", "connection", "delete", AP_PROFILE])
        self.run(["nmcli", "connection", "delete", JOIN_PROFILE])   # half-finished join
        if self.wait_for_network():
            logger.info("Network is up; no setup needed")
            self.mode = "client"
            self.clear_panel()
        else:
            self.enter_setup("no network after boot")
        while True:
            try:
                self.sleep(self.tick())
            except Exception as e:
                logger.exception("Setup loop error: %s", e)
                self.sleep(10)

    # -- web page

    def state_for_page(self):
        return {"ap_ssid": self.s["ssid"], "networks": self.networks,
                "last_result": self.last_result, "mode": self.mode}

    def request_connect(self, ssid, password, hidden=False):
        ssid = (ssid or "").strip()
        password = password or ""
        if not ssid or len(ssid.encode()) > 32:
            return False, "Pick a network or type its name."
        if password and not (8 <= len(password) <= 63):
            return False, "Wi-Fi passwords are 8 to 63 characters."
        self.connect_queue.put((ssid, password, bool(hidden)))
        return True, "ok"

    def start_http(self):
        if self.httpd:
            return
        svc = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                logger.debug("http %s", fmt % args)

            def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
                data = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                svc.last_http = svc.clock()
                host = (self.headers.get("Host") or "").split(":")[0]
                path = self.path.split("?")[0]
                if path == "/api/state":
                    return self._send(200, json.dumps(svc.state_for_page()), "application/json")
                if host and host != AP_IP and host not in ("localhost", "127.0.0.1"):
                    # Any other site (phone's captive-portal check): send to the page
                    return self._send(302, "", headers={"Location": f"http://{AP_IP}/"})
                if path in ("/", "/index.html", "/wifi-setup"):
                    return self._send(200, SETUP_PAGE.replace("__SSID__", svc.s["ssid"]))
                return self._send(302, "", headers={"Location": f"http://{AP_IP}/"})

            def do_POST(self):
                svc.last_http = svc.clock()
                if self.path.split("?")[0] != "/api/connect":
                    return self._send(404, "not found", "text/plain")
                n = min(int(self.headers.get("Content-Length") or 0), 4096)
                raw = self.rfile.read(n).decode("utf-8", "replace")
                if "json" in (self.headers.get("Content-Type") or ""):
                    try:
                        body = json.loads(raw or "{}")
                    except ValueError:
                        body = {}
                else:
                    body = {k: v[0] for k, v in parse_qs(raw).items()}
                ok, msg = svc.request_connect(body.get("ssid"), body.get("password"),
                                              body.get("hidden") in (True, "1", "true", "on"))
                return self._send(200 if ok else 400,
                                  json.dumps({"ok": ok, "message": msg}), "application/json")

        try:
            self.httpd = ThreadingHTTPServer(("0.0.0.0", self.http_port), Handler)
        except OSError as e:
            logger.error("Could not open the setup page on port %s: %s", self.http_port, e)
            self.httpd = None
            return
        self.httpd.daemon_threads = True
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.http_thread.start()
        logger.info("Setup page listening on port %s", self.http_port)

    def stop_http(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


SETUP_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ticker Wi-Fi setup</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#16181d;--mute:#5d6470;--line:#d9dde3;--acc:#1d6fe0;--bad:#b42318;--good:#137333}
@media (prefers-color-scheme:dark){:root{--bg:#121417;--card:#1c1f24;--ink:#eef0f3;--mute:#a3aab5;--line:#30353d;--acc:#5b9bff;--bad:#ff8a80;--good:#6dd58c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:460px;margin:0 auto;padding:20px 16px 40px}h1{font-size:22px;margin:4px 0 4px}p{margin:6px 0;color:var(--mute)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:14px}
.net{display:flex;align-items:center;gap:10px;width:100%;padding:12px;border:1px solid var(--line);border-radius:10px;background:transparent;color:var(--ink);font:inherit;text-align:left;margin-top:8px;cursor:pointer}
.net.sel{border-color:var(--acc);box-shadow:0 0 0 2px var(--acc) inset}.net .n{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bars{font-size:12px;color:var(--mute)}label{display:block;font-weight:600;margin:12px 0 4px}
input[type=text],input[type=password]{width:100%;padding:12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);color:var(--ink);font:inherit}
.row{display:flex;align-items:center;gap:8px;margin-top:8px;color:var(--mute);font-size:14px}
button.go{width:100%;margin-top:16px;padding:14px;border:0;border-radius:10px;background:var(--acc);color:#fff;font:600 17px system-ui,sans-serif}
.msg{padding:12px;border-radius:10px;margin-top:14px}.bad{background:color-mix(in srgb,var(--bad) 14%,transparent);color:var(--bad)}
.good{background:color-mix(in srgb,var(--good) 14%,transparent);color:var(--good)}.hide{display:none}a{color:var(--acc)}
</style></head><body><main>
<h1>Connect your ticker to Wi-Fi</h1>
<p>Pick your home network and enter its password.</p>
<div id="last" class="msg bad hide"></div>
<form id="f" class="card">
  <div id="nets"></div>
  <button type="button" class="net" id="other"><span class="n">My network isn't listed</span></button>
  <div id="manual" class="hide"><label for="ssid">Network name</label>
    <input type="text" id="ssid" autocomplete="off" autocapitalize="off" spellcheck="false">
    <div class="row"><input type="checkbox" id="hidden"><label for="hidden" style="margin:0;font-weight:400">It's a hidden network</label></div></div>
  <label for="pw">Password</label>
  <input type="password" id="pw" autocomplete="current-password">
  <div class="row"><input type="checkbox" id="show"><label for="show" style="margin:0;font-weight:400">Show password</label></div>
  <div id="err" class="msg bad hide"></div>
  <button class="go" type="submit">Connect</button>
</form>
<div id="done" class="card hide">
  <h1 id="dt">Connecting&hellip;</h1>
  <p>Your phone will drop off <b>__SSID__</b> now. Watch the ticker's display:</p>
  <p><b>"CONNECTED!"</b> means you're done. It also shows the address of the settings page.</p>
  <p><b>"COULDN'T CONNECT"</b> means it didn't work. Rejoin <b>__SSID__</b> in about a minute and this page will say why.</p>
</div>
</main>
<script>
let chosen=null;const $=id=>document.getElementById(id);
function bars(s){return s>=70?"Strong":s>=40?"OK":"Weak"}
function pick(btn,ssid){document.querySelectorAll('.net').forEach(b=>b.classList.remove('sel'));btn.classList.add('sel');chosen=ssid;$('manual').classList.toggle('hide',ssid!==null);if(ssid===null)$('ssid').focus();else $('pw').focus()}
async function load(){try{const r=await fetch('/api/state',{cache:'no-store'});const s=await r.json();
 const box=$('nets');box.innerHTML='';
 if(!s.networks.length){box.innerHTML='<p>No networks were found when the ticker scanned. Use "My network isn\'t listed".</p>'}
 s.networks.forEach(n=>{const b=document.createElement('button');b.type='button';b.className='net';
  b.innerHTML='<span class="n"></span><span class="bars"></span>';b.querySelector('.n').textContent=(n.secure?'\u{1F512} ':'')+n.ssid;
  b.querySelector('.bars').textContent=bars(n.signal);b.onclick=()=>pick(b,n.ssid);box.appendChild(b)});
 const L=s.last_result;if(L&&!L.ok&&Date.now()/1000-L.at<1800){$('last').textContent='Couldn\'t join "'+L.ssid+'": '+L.error;$('last').classList.remove('hide')}
}catch(e){$('nets').innerHTML='<p>Couldn\'t load the network list. Reload this page.</p>'}}
$('other').onclick=()=>pick($('other'),null);
$('show').onchange=e=>{$('pw').type=e.target.checked?'text':'password'};
$('f').onsubmit=async e=>{e.preventDefault();const err=$('err');err.classList.add('hide');
 const ssid=chosen===null?$('ssid').value.trim():chosen;
 if(!ssid){err.textContent='Pick your network first.';err.classList.remove('hide');return}
 const body={ssid,password:$('pw').value,hidden:chosen===null&&$('hidden').checked};
 try{const r=await fetch('/api/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();if(!j.ok){err.textContent=j.message;err.classList.remove('hide');return}}catch(x){}
 $('f').classList.add('hide');$('last').classList.add('hide');$('done').classList.remove('hide');$('dt').textContent='Connecting to '+ssid+'…'};
load();
</script></body></html>
"""


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    WifiSetupService(load_settings()).run_forever()


if __name__ == "__main__":
    main()
