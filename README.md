# smartcast-home

Turn an aging **Vizio SmartCast** TV into a fast, clean, ad-light display with a **custom home screen** it boots into, plus a phone-friendly **web remote** — all from a small always-on host on your LAN. No rooting, no firmware mods; everything runs over the TV's authenticated local API.

![status](https://img.shields.io/badge/status-works%20on%20real%20hardware-brightgreen)

## What you get

- **Custom home screen** rendered on the TV itself (via SmartCast's "Conjure" web-app launcher): a large clock, local weather + 3-day forecast, and app tiles you navigate with the **physical remote's d-pad**. A rotating, themed background (Pexels or Picsum). Optionally a daily/rotating quote.
- **Full-replacement takeover**: a watcher re-launches your home screen on power-on and whenever the TV lands on stock SmartCast — so you rarely see Vizio's ad-filled home. Pause/resume anytime from the remote.
- **Web remote** (`/`): power, volume, inputs, SmartCast apps, plus a connected **Roku** (d-pad, media keys, channel launch). Works on your phone; add it to your home screen.
- **Route apps to a Roku**: SmartCast's own apps get deprecated on old TVs (e.g. "device no longer supported"). Tiles can launch on a connected Roku instead, over HDMI.

## How it works

SmartCast's Conjure runtime can display an arbitrary web page as an "app":

```
PUT https://<tv>:7345/app/launch
{"VALUE": {"APP_ID": "17", "NAME_SPACE": 4, "MESSAGE": "http://<host>:8750/home.html"}}
```

This project serves that page (and a small JSON API) from a Flask app on an **always-on host** on your LAN (a Mac mini, a Pi, an old laptop — anything that stays on). The TV loads the page same-origin from the host, so there are no cross-origin/TLS headaches on the TV's old browser engine. App tiles can't launch native apps directly from the page (engine restrictions), so they call back to the host, which issues the real launch.

> Inspired by [free-my-tv](https://github.com/simbasjungles/free-my-tv) (which targets Android TV / webOS / Tizen) and [Horizon for Vizio](https://github.com/Ali-Al-Anbari/horizon-vizio) (the Conjure launch mechanism). Built on [pyvizio](https://github.com/vkorn/pyvizio).

## Requirements

- A **Vizio SmartCast** TV (tested on a 2017 E-series; the Conjure web launcher must be present — see Caveats).
- An **always-on host** on the same LAN with **Python 3.9+**.
- Optional: a **Roku** connected via HDMI (for app tiles), a free **Pexels** API key (for themed backgrounds).

## Setup

```bash
git clone https://github.com/<you>/smartcast-home.git
cd smartcast-home
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 1. Pair with the TV (one time)

```bash
pyvizio --ip=<TV_IP>:7345 --device_type=tv pair
# a PIN appears on the TV; note the "Challenge token" printed
pyvizio --ip=<TV_IP>:7345 --device_type=tv pair-finish \
        --ch_type=1 --token=<challenge token> --pin=<PIN on TV>
# prints your Authorization token
```

Find the TV's IP from your router, or `pyvizio --ip=0 discover`.

### 2. Configure

```bash
cp .env.example .env
# edit .env: VIZIO_IP, VIZIO_AUTH, LAUNCHER_URL (this host's IP),
# WEATHER_LAT/LON/TZ, and optionally ROKU_IP and PEXELS_KEY
```

`LAUNCHER_URL` must be **this host's** LAN IP + port, e.g. `http://192.168.1.60:8750/home.html`.

### 3. Run

```bash
python server.py
```

- Phone remote: `http://<host>:8750`
- In the remote, tap **Launch Home Screen** to put the custom screen on the TV (or just turn the TV on — the watcher does it).

### 4. (Recommended) Keep it running

See [`deploy/`](deploy/) for a macOS **launchd** agent and a Linux **systemd** unit so it starts at boot and restarts itself.

## Roku (optional)

If a Roku is on an HDMI input, set `ROKU_IP` and enable its local control:
**Settings → System → Advanced system settings → Control by mobile apps → Network access → Permissive.**
Then app tiles typed as `roku-app` in `static/home.html` flip the TV to the Roku's input and launch the channel.

## Caveats (real-world, learned the hard way)

- **Conjure engine (Cobalt) is limited.** No `position: fixed`, no `inset` shorthand, and no flexbox `gap` (use margins; CSS grid `gap` does work). The page is written around these.
- **The web launcher may not exist on every model.** `APP_ID 17 / NAMESPACE 4` is the web-app launcher on the models tested, but the app map is chipset-specific. Test with a trivial page first.
- **Pressing Home returns to stock SmartCast** — the takeover watcher bounces back to your screen within a few seconds (this is the "full replacement"). Pause it from the remote to use stock SmartCast.
- **Set the TV to Quick Start**, not Eco: Eco drops the network in standby, so the remote can't see or wake it.
- **TV clock is unreliable** — the home screen pulls time from the host instead.
- **Deprecated apps**: route the heavy hitters (YouTube TV, etc.) to a Roku.

## Security

`.env` holds your TV auth token and Pexels key and is gitignored — keep it that way. The server binds to your LAN; don't port-forward `8750` to the internet. The TV connection is TLS without certificate verification (SmartCast uses a self-signed cert), so treat your LAN as the trust boundary.

## Credits

[free-my-tv](https://github.com/simbasjungles/free-my-tv) · [horizon-vizio](https://github.com/Ali-Al-Anbari/horizon-vizio) · [pyvizio](https://github.com/vkorn/pyvizio) · weather by [Open-Meteo](https://open-meteo.com) · photos by [Pexels](https://www.pexels.com) / [Picsum](https://picsum.photos) · quotes by [ZenQuotes](https://zenquotes.io).

## License

MIT
