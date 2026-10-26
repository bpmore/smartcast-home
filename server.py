#!/usr/bin/env python3
"""Clean local web remote + custom home screen for Vizio SmartCast TVs.

A thin Flask wrapper over pyvizio's sync API. Holds the auth token server-side,
exposes a small JSON API, and serves the single-page remote in static/.
Run: python server.py  (reads creds from .env)
"""
import os
import random
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, request, send_from_directory
from pyvizio import Vizio


def _load_env():
    env = {}
    p = Path(__file__).parent / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, val = line.split("=", 1)
                env[k.strip()] = val.strip()
    return env


ENV = _load_env()
IP = ENV.get("VIZIO_IP", "192.168.1.50:7345")  # set in .env
AUTH = ENV.get("VIZIO_AUTH", "")
DEVICE_TYPE = ENV.get("VIZIO_DEVICE_TYPE", "tv")

# Quick-launch favorites, in display order (only those the TV reports are shown).
FAVORITE_APPS = [
    "YouTube", "YouTube TV", "Tubi", "Prime Video", "Apple TV+",
    "Pluto TV", "iHeartRadio",
]

# --- Roku (ECP over HTTP :8060) ---
ROKU_BASE = "http://" + ENV.get("ROKU_IP", "192.168.1.51:8060")  # set in .env
ROKU_INPUT = "HDMI-2"  # which Vizio input the Roku is on
ROKU_FAVORITES = [  # display order; matched loosely against installed channels
    "YouTube", "YouTube TV", "Vevo", "Apple TV", "Pluto TV", "Tubi",
]

# --- Custom home screen (Conjure web launcher) ---
# The TV renders an arbitrary page via PUT /app/launch {APP_ID:17, NS:4, MESSAGE:url}.
LAUNCHER_URL = ENV.get("LAUNCHER_URL", "http://192.168.1.60:8750/home.html")  # this host, set in .env
WEATHER_LAT = float(ENV.get("WEATHER_LAT", "40.7128"))  # set in .env
WEATHER_LON = float(ENV.get("WEATHER_LON", "-74.0060"))  # set in .env
WEATHER_TZ = ENV.get("WEATHER_TZ", "America/New_York")  # set in .env
STOCK_HOME_APP = "SmartCast Home"  # what get_current_app returns on stock home

app = Flask(__name__, static_folder="static", static_url_path="")
_lock = threading.Lock()  # pyvizio client isn't meant for concurrent use
_client = Vizio("pyvizio-remote", IP, "Vizio Web Remote", AUTH, DEVICE_TYPE)
_apps_cache = None


def tv(fn, *a, retries=2, **kw):
    """Call a pyvizio method, retrying transient Wi-Fi drops.

    pyvizio returns None (not an exception) when it can't reach the TV, so we
    retry on both None and exceptions. Callers that expect None/False as a valid
    value (e.g. get_power_state when the TV is off) should read the result, not
    assume failure — None here means "still unreachable after retries".
    """
    last_exc = None
    for attempt in range(retries + 1):
        try:
            with _lock:
                res = getattr(_client, fn)(*a, **kw)
            if res is not None:
                return res
        except Exception as e:
            last_exc = e
        if attempt < retries:
            time.sleep(0.4)
    if last_exc:
        raise last_exc
    return None


@app.get("/api/state")
def state():
    # power: None = unreachable, False = off (but reachable), True = on
    try:
        power = tv("get_power_state")
    except Exception as e:
        return jsonify({"ok": True, "reachable": False, "power": None,
                        "input": None, "app": None, "volume": None,
                        "max_volume": 100, "muted": False, "inputs": [],
                        "error": str(e)})
    if power is None:
        return jsonify({"ok": True, "reachable": False, "power": None,
                        "input": None, "app": None, "volume": None,
                        "max_volume": 100, "muted": False, "inputs": []})
    on = bool(power)
    try:
        inputs = tv("get_inputs_list") or []
        inputs = [{"name": i.name, "label": i.meta_name or i.name} for i in inputs]
    except Exception:
        inputs = []
    def safe(fn, default=None):
        try:
            return tv(fn, retries=1)
        except Exception:
            return default
    return jsonify({
        "ok": True,
        "reachable": True,
        "power": on,
        "input": safe("get_current_input") if on else None,
        "app": safe("get_current_app") if on else None,
        "volume": safe("get_current_volume") if on else None,
        "max_volume": safe("get_max_volume", 100) or 100,
        "muted": bool(safe("is_muted", False)) if on else False,
        "inputs": inputs,
    })


@app.get("/api/apps")
def apps():
    global _apps_cache
    try:
        if _apps_cache is None:
            full = tv("get_apps_list") or []
            favs = [a for a in FAVORITE_APPS if a in full]
            _apps_cache = {"favorites": favs, "all": sorted(full)}
        return jsonify({"ok": True, **_apps_cache})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@app.post("/api/power")
def power():
    tv("pow_toggle")
    return jsonify({"ok": True})


@app.post("/api/input")
def set_input():
    name = (request.json or {}).get("name")
    if not name:
        return jsonify({"ok": False, "error": "missing name"}), 400
    tv("set_input", name)
    return jsonify({"ok": True})


@app.post("/api/volume")
def volume():
    data = request.json or {}
    direction = data.get("dir")
    amount = int(data.get("amount", 1))
    if direction == "up":
        tv("vol_up", amount)
    elif direction == "down":
        tv("vol_down", amount)
    else:
        return jsonify({"ok": False, "error": "dir must be up/down"}), 400
    return jsonify({"ok": True})


@app.post("/api/mute")
def mute():
    tv("mute_toggle")
    return jsonify({"ok": True})


@app.post("/api/key")
def key():
    k = (request.json or {}).get("key")
    if not k:
        return jsonify({"ok": False, "error": "missing key"}), 400
    tv("remote", k)
    return jsonify({"ok": True})


@app.post("/api/launch")
def launch():
    name = (request.json or {}).get("name")
    if not name:
        return jsonify({"ok": False, "error": "missing name"}), 400
    tv("launch_app", name)
    return jsonify({"ok": True})


# ---------- Roku endpoints ----------
def roku(method, path, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.request(method, ROKU_BASE + path, timeout=4)
            return r
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(0.4)
    raise last


def _short(name):
    """Trim store-style channel names for tidy buttons: cut at a separator."""
    for sep in (" - ", " – ", " — ", ": ", " | "):
        if sep in name:
            name = name.split(sep, 1)[0]
    return name.strip()


def _roku_apps():
    xml = roku("GET", "/query/apps").text
    root = ET.fromstring(xml)
    apps = []
    for e in root.findall("app"):
        name = (e.text or "").strip()
        apps.append({"id": e.get("id"), "name": name, "label": _short(name)})
    return apps


def _match(term, apps):
    t = term.lower()
    for a in apps:
        if a["name"].lower() == t:
            return a
    for a in apps:
        if a["name"].lower().startswith(t):
            return a
    for a in apps:
        if t in a["name"].lower():
            return a
    return None


@app.get("/api/roku/state")
def roku_state():
    try:
        xml = roku("GET", "/query/active-app").text
        root = ET.fromstring(xml)
        el = root.find("app")
        return jsonify({"ok": True, "reachable": True,
                        "app": (el.text or "").strip() if el is not None else None,
                        "app_id": el.get("id") if el is not None else None})
    except Exception as e:
        return jsonify({"ok": True, "reachable": False, "app": None, "error": str(e)})


@app.get("/api/roku/apps")
def roku_apps():
    try:
        apps = _roku_apps()
        favs, seen = [], set()
        for term in ROKU_FAVORITES:
            m = _match(term, apps)
            if m and m["id"] not in seen:
                favs.append(m)
                seen.add(m["id"])
        return jsonify({"ok": True, "favorites": favs,
                        "all": sorted(apps, key=lambda a: a["name"].lower())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@app.post("/api/roku/key")
def roku_key():
    k = (request.json or {}).get("key")
    if not k:
        return jsonify({"ok": False, "error": "missing key"}), 400
    r = roku("POST", "/keypress/" + k)
    return jsonify({"ok": r.status_code in (200, 202), "code": r.status_code})


@app.post("/api/roku/launch")
def roku_launch():
    app_id = (request.json or {}).get("id")
    if not app_id:
        return jsonify({"ok": False, "error": "missing id"}), 400
    r = roku("POST", "/launch/" + str(app_id))
    return jsonify({"ok": r.status_code in (200, 202), "code": r.status_code})


@app.post("/api/roku/switch")
def roku_switch():
    """Flip the Vizio to the Roku's input, then nudge the Roku awake."""
    tv("set_input", ROKU_INPUT)
    try:
        roku("POST", "/keypress/Home", retries=1)
    except Exception:
        pass
    return jsonify({"ok": True})


@app.post("/api/roku/open")
def roku_open():
    """Flip the Vizio to the Roku input AND launch a specific Roku channel by name
    (used for apps that are deprecated/broken on SmartCast, e.g. YouTube TV)."""
    name = (request.json or {}).get("name")
    if not name:
        return jsonify({"ok": False, "error": "missing name"}), 400
    try:
        match = _match(name, _roku_apps())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502
    if not match:
        return jsonify({"ok": False, "error": f"'{name}' not installed on Roku"}), 404
    tv("set_input", ROKU_INPUT)
    r = roku("POST", "/launch/" + str(match["id"]))
    return jsonify({"ok": r.status_code in (200, 202), "launched": match["name"]})


# ---------- Custom home screen: weather, launcher control, watcher ----------
WMO = {
    0: ("☀️", "Clear"), 1: ("🌤️", "Mostly clear"), 2: ("⛅", "Partly cloudy"),
    3: ("☁️", "Overcast"), 45: ("🌫️", "Fog"), 48: ("🌫️", "Rime fog"),
    51: ("🌦️", "Light drizzle"), 53: ("🌦️", "Drizzle"), 55: ("🌧️", "Heavy drizzle"),
    61: ("🌦️", "Light rain"), 63: ("🌧️", "Rain"), 65: ("🌧️", "Heavy rain"),
    66: ("🌧️", "Freezing rain"), 67: ("🌧️", "Freezing rain"),
    71: ("🌨️", "Light snow"), 73: ("🌨️", "Snow"), 75: ("❄️", "Heavy snow"),
    77: ("🌨️", "Snow grains"), 80: ("🌦️", "Showers"), 81: ("🌧️", "Showers"),
    82: ("⛈️", "Violent showers"), 85: ("🌨️", "Snow showers"), 86: ("❄️", "Snow showers"),
    95: ("⛈️", "Thunderstorm"), 96: ("⛈️", "Thunderstorm"), 99: ("⛈️", "Hailstorm"),
}
_weather_cache = {"t": 0, "data": None}


@app.get("/api/time")
def api_time():
    """Authoritative time from Tank (the TV's own clock/timezone is unreliable)."""
    import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo(WEATHER_TZ))
    except Exception:
        now = datetime.datetime.now()
    return jsonify({"ok": True,
                    "time": now.strftime("%-I:%M %p"),
                    "date": now.strftime("%A, %B %-d")})


_quote_cache = {"t": 0, "data": None}


@app.get("/api/quote")
def quote():
    now = time.time()
    if _quote_cache["data"] and now - _quote_cache["t"] < 300:
        return jsonify(_quote_cache["data"])
    try:
        r = requests.get("https://zenquotes.io/api/random", timeout=8)
        item = r.json()[0]
        data = {"ok": True, "text": item.get("q", "").strip(),
                "author": item.get("a", "").strip()}
        if data["text"]:
            _quote_cache.update(t=now, data=data)
            return jsonify(data)
    except Exception:
        pass
    return jsonify({"ok": True,
                    "text": "The best way to predict the future is to invent it.",
                    "author": "Alan Kay"})


@app.get("/api/weather")
def weather():
    now = time.time()
    if _weather_cache["data"] and now - _weather_cache["t"] < 600:
        return jsonify(_weather_cache["data"])
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=6, params={
            "latitude": WEATHER_LAT, "longitude": WEATHER_LON,
            "current": "temperature_2m,weather_code,apparent_temperature",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code",
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
            "timezone": WEATHER_TZ, "forecast_days": 3,
        })
        j = r.json()
        cur = j.get("current", {})
        code = int(cur.get("weather_code", 0))
        icon, text = WMO.get(code, ("•", ""))
        daily = j.get("daily", {})
        days = []
        import datetime
        for i, d in enumerate(daily.get("time", [])[:3]):
            dc = int(daily["weather_code"][i])
            di, _ = WMO.get(dc, ("•", ""))
            name = "Today" if i == 0 else datetime.date.fromisoformat(d).strftime("%a")
            days.append({"day": name, "icon": di,
                         "hi": round(daily["temperature_2m_max"][i]),
                         "lo": round(daily["temperature_2m_min"][i])})
        data = {"ok": True, "temp": round(cur.get("temperature_2m", 0)),
                "feels": round(cur.get("apparent_temperature", 0)),
                "icon": icon, "text": text, "city": ENV.get("WEATHER_CITY", ""), "days": days}
        _weather_cache.update(t=now, data=data)
        return jsonify(data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# full-replacement watcher state
_takeover = {"enabled": True, "paused": False, "last_launch": 0.0, "last_power": None}


def launch_home():
    try:
        with _lock:
            _client.launch_app_config("Home", "17", 4, LAUNCHER_URL)
        _takeover["last_launch"] = time.time()
        return True
    except Exception:
        return False


@app.get("/api/launcher/status")
def launcher_status():
    return jsonify({"ok": True, "enabled": _takeover["enabled"],
                    "paused": _takeover["paused"], "url": LAUNCHER_URL})


@app.post("/api/launcher/home")
def launcher_home():
    return jsonify({"ok": launch_home()})


@app.post("/api/launcher/pause")
def launcher_pause():
    _takeover["paused"] = True
    return jsonify({"ok": True, "paused": True})


@app.post("/api/launcher/resume")
def launcher_resume():
    _takeover["paused"] = False
    return jsonify({"ok": True, "paused": False})


def _watcher():
    """Bounce the TV to our home screen on power-on and whenever it lands on stock home."""
    while True:
        time.sleep(3)
        if not _takeover["enabled"] or _takeover["paused"]:
            continue
        try:
            with _lock:
                power = _client.get_power_state()
        except Exception:
            power = None
        try:
            if power:
                edge = _takeover["last_power"] is False  # real off->on only (None = unknown)
                app_name = None
                try:
                    with _lock:
                        app_name = _client.get_current_app()
                except Exception:
                    app_name = None
                on_stock_home = (app_name == STOCK_HOME_APP)
                if (edge or on_stock_home) and (time.time() - _takeover["last_launch"] > 8):
                    launch_home()
            _takeover["last_power"] = power
        except Exception:
            pass


# Background images: Tank pools themed photos from Pexels (key in .env) and
# serves a random one same-origin so the TV's Cobalt webview loads it reliably.
# Falls back to Picsum (random), then a bundled local image, if Pexels is off.
BG_SOURCE = ENV.get("BG_SOURCE", "https://picsum.photos/1920/1080")
PEXELS_KEY = ENV.get("PEXELS_KEY", "")
BG_THEMES = [t.strip() for t in ENV.get("BG_THEMES", "").split("|") if t.strip()] or [
    "nature landscape", "forest", "mountains", "ocean sunset", "waterfall",
    "beach", "tropical beach", "california beach", "US coastline",
    "Seattle skyline", "New York City skyline", "Portland Oregon skyline",
    "Boston skyline", "New Orleans skyline", "Los Angeles skyline",
    "San Diego skyline", "Chicago skyline", "cityscape at night",
]
_bg_cache = {}                       # token -> (bytes, content_type)
_bg_pool = {"t": 0, "urls": []}      # pooled image URLs across all themes
_bg_lock = threading.Lock()


def _refresh_bg_pool():
    urls = []
    for q in BG_THEMES:
        try:
            r = requests.get("https://api.pexels.com/v1/search",
                             headers={"Authorization": PEXELS_KEY},
                             params={"query": q, "orientation": "landscape",
                                     "per_page": 30, "page": random.randint(1, 4)},
                             timeout=10)
            for p in r.json().get("photos", []):
                u = p.get("src", {}).get("large2x") or p.get("src", {}).get("large")
                if u:
                    urls.append(u)
        except Exception:
            pass
    return urls


def _bg_pool_urls():
    if not PEXELS_KEY:
        return []
    if not _bg_pool["urls"] or time.time() - _bg_pool["t"] > 6 * 3600:
        with _bg_lock:
            if not _bg_pool["urls"] or time.time() - _bg_pool["t"] > 6 * 3600:
                u = _refresh_bg_pool()
                if u:
                    _bg_pool.update(t=time.time(), urls=u)
    return _bg_pool["urls"]


@app.get("/bg/next")
def bg_next():
    token = request.args.get("t", "")
    if token in _bg_cache:
        data, ct = _bg_cache[token]
        return Response(data, mimetype=ct)
    pool = _bg_pool_urls()
    src = random.choice(pool) if pool else BG_SOURCE
    try:
        r = requests.get(src, timeout=12)
        r.raise_for_status()
        data = r.content
        ct = r.headers.get("Content-Type", "image/jpeg")
        _bg_cache[token] = (data, ct)
        while len(_bg_cache) > 4:
            _bg_cache.pop(next(iter(_bg_cache)))
        return Response(data, mimetype=ct)
    except Exception:
        return send_from_directory(os.path.join(app.static_folder, "bg"), "bg1.jpg")


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    print(f"Vizio remote -> {IP}  (auth {'set' if AUTH else 'MISSING'})")
    print(f"Home-screen takeover watcher ON -> {LAUNCHER_URL}")
    print("Open http://localhost:8750  (or http://<this-mac-ip>:8750 from your phone)")
    threading.Thread(target=_watcher, daemon=True).start()
    threading.Thread(target=_bg_pool_urls, daemon=True).start()  # warm the image pool
    app.run(host="0.0.0.0", port=8750, threaded=True)
