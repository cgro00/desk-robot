"""Computer: lets Rocky do small, safe things on this computer when you ask him to.

  search_web       a search on a fixed list of sites (the URL is built here, never by the model)
  open_website     a site's home page, or one of your bookmarks (settings: computer.bookmarks)
  open_app         an app from your allowed list (settings: computer.apps)
  media            play / pause / next / previous in Spotify or Music
  play_music       find a song / artist / album / playlist on Spotify and play it (Spotify's search needs a free
                   developer key in server/.env: SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET; without one he opens
                   Spotify's search for you to pick)
  now_playing      what Spotify or Music is playing (or has paused) right now
  computer_volume  this computer's sound: a level, up, down, mute, unmute
  run_shortcut     an Apple Shortcut from your allowed list (settings: computer.shortcuts), after a spoken yes

Safety, because anyone (or any TV) that talks near Rocky can ask:
  - Off until you switch it on in the dashboard (computer.control); the choice is remembered.
  - Allowlists only. The model picks from names; this module builds every URL and command. Nothing types, clicks,
    logs in, buys, deletes or runs a shell.
  - At most PER_TURN actions per question and PER_MINUTE a minute, so a looping video can't open fifty tabs.
  - Shortcuts can do anything a shortcut does, so each one waits for a yes in a later turn (computer.confirm_shortcuts),
    and only after he has said the shortcut's name out loud while asking.
  - Every action is printed to the server log and shown on the dashboard.

The model's tool definitions come from tools(); main.py hands ACTIONS to the brain. Standard library only (settings.py
imports this to check bookmarks, and the launcher imports settings).
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

MAC = sys.platform == "darwin"
PER_TURN = 3          # actions in one reply
PER_MINUTE = 8        # actions in any 60 s
CONFIRM_WINDOW_S = 90.0

# Searches: the query is URL-encoded into a fixed https address.
SEARCH = {
    "google":    "https://www.google.com/search?q={}",
    "images":    "https://www.google.com/search?tbm=isch&q={}",
    "amazon":    "https://www.amazon.com/s?k={}",
    "youtube":   "https://www.youtube.com/results?search_query={}",
    "maps":      "https://www.google.com/maps/search/{}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={}",
    "github":    "https://github.com/search?q={}",
    "ebay":      "https://www.ebay.com/sch/i.html?_nkw={}",
    "reddit":    "https://www.reddit.com/search/?q={}",
}
# Home pages he can open by name, besides your bookmarks.
SITES = {
    "google": "https://www.google.com", "amazon": "https://www.amazon.com", "youtube": "https://www.youtube.com",
    "maps": "https://www.google.com/maps", "wikipedia": "https://en.wikipedia.org", "github": "https://github.com",
    "ebay": "https://www.ebay.com", "reddit": "https://www.reddit.com", "gmail": "https://mail.google.com",
}
DEFAULT_APPS = "Safari, Google Chrome, Spotify, Music, Notes, Calendar, Reminders, Maps, Calculator, Clock, Weather, Photos"
MEDIA_APPS = ("Spotify", "Music")
_MEDIA_VERB = {"play": "play", "pause": "pause", "next": "next track", "previous": "previous track"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MUSIC_KINDS = {"song": "track", "artist": "artist", "album": "album", "playlist": "playlist"}
# A confirming answer starts with one of these ("Yes, run it", "Sure.", "Go ahead").
YES = re.compile(r"^\W*(yes|yeah|yep|yup|yah|ya|sure|ok|okay|alright|all right|affirmative|absolutely|definitely|"
                 r"correct|confirm|confirmed|do it|go ahead|go for it|run it|please do)\b", re.IGNORECASE)
SPOTIFY_URI = re.compile(r"^spotify:(track|album|artist|playlist):[A-Za-z0-9]{22}$")
SPOTIFY_START_S = 12.0   # how long to wait for Spotify to open before giving up


class NotDone(Exception):
    """The action was refused or failed; the message goes back to the model."""


# ── Settings text → lists ────────────────────────────────────────────────────

def split_names(text: str) -> list[str]:
    """ "Spotify, Notes\nCalendar" -> ["Spotify", "Notes", "Calendar"] (no blanks, no duplicates, max 40 chars)."""
    out: list[str] = []
    for part in re.split(r"[,\n]", text or ""):
        name = _CONTROL.sub("", part).strip()
        if name and len(name) <= 40 and _key(name) not in {_key(n) for n in out}:
            out.append(name)
    return out


def parse_bookmarks(text: str) -> dict[str, str]:
    """ "Name = https://..." per line -> {name: url}. Raises ValueError naming the bad line. Only http(s) addresses
    with a host: no javascript:, file:, data: or app links."""
    out: dict[str, str] = {}
    for n, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        name, sep, url = line.partition("=")
        name, url = name.strip(), url.strip()
        if not sep or not name or not url:
            raise ValueError(f"bookmark line {n}: write it as  Name = https://example.com")
        if len(name) > 40:
            raise ValueError(f"bookmark line {n}: the name is longer than 40 characters")
        out[name] = check_url(url, f"bookmark line {n}")
    return out


def check_url(url: str, where: str = "address") -> str:
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or _CONTROL.search(url) or " " in url or len(url) > 500:
        raise ValueError(f"{where}: only web addresses starting with https:// (or http://)")
    return url


def _key(name: str) -> str:
    """How names are compared: case, spaces and punctuation don't matter ("google chrome" = "Google Chrome")."""
    return re.sub(r"[^a-z0-9]", "", name.casefold())


# ── The controller ───────────────────────────────────────────────────────────

class Computer:
    def __init__(self, run=None, open_url=None, spotify=None) -> None:
        # run(argv, timeout) -> (returncode, stdout, stderr); open_url(url). Tests swap these for fakes.
        self._run = run or _run
        self._open_url = open_url or _open_url
        self.spotify = spotify or SpotifySearch()
        self.lock = threading.Lock()
        self.enabled = False
        self.apps: list[str] = split_names(DEFAULT_APPS)
        self.bookmarks: dict[str, str] = {}
        self.shortcuts: list[str] = []
        self.confirm_shortcuts = True
        self.turn = 0
        self._turn_count = 0
        self._times: list[float] = []
        # (shortcut, turn asked, when, whether he said its name out loud in that turn's reply)
        self._pending: tuple[str, int, float, bool] | None = None
        self._heard: str | None = None                          # the human's words this turn (None: not known)
        self.last: tuple[str, float] | None = None              # (what happened, when): for the dashboard

    def configure(self, s: dict) -> None:
        """Apply settings["computer"]."""
        with self.lock:
            was = self.enabled
            self.enabled = bool(s.get("control"))
            self.apps = split_names(s.get("apps", DEFAULT_APPS))
            try:
                self.bookmarks = parse_bookmarks(s.get("bookmarks", ""))
            except ValueError:
                self.bookmarks = {}
            self.shortcuts = split_names(s.get("shortcuts", ""))
            self.confirm_shortcuts = bool(s.get("confirm_shortcuts", True))
            if not self.enabled:
                self._pending = None
        if was != self.enabled:
            print(f"computer control {'ON' if self.enabled else 'off'}")

    def new_turn(self, heard: str | None = None) -> None:
        """The human asked something: a fresh per-question budget (and a yes can now count). `heard` is what they
        said; a shortcut waiting for a yes only runs if it starts with one."""
        with self.lock:
            self.turn += 1
            self._turn_count = 0
            self._heard = heard

    def said(self, text: str) -> None:
        """What he said out loud in this turn's reply (main.py, once it's spoken). A shortcut waiting for a yes
        only counts as asked if its name was in it: the model words the question, so without this a personality's
        prompt could ask "Want a fun fact?" and take the yes for the shortcut."""
        with self.lock:
            p = self._pending
            if p is not None and p[1] == self.turn and _key(p[0]) in _key(text or ""):
                self._pending = (p[0], p[1], p[2], True)

    def status(self) -> dict:
        return {"on": self.enabled, "last": list(self.last) if self.last else None}

    # ── the abilities (called from the brain's worker thread) ──

    def search_web(self, args: dict) -> str:
        site = str(args.get("site") or "google").casefold()
        if site not in SEARCH:
            raise NotDone(f"can only search: {', '.join(SEARCH)}")
        query = re.sub(r"\s+", " ", _CONTROL.sub(" ", str(args.get("query") or ""))).strip()[:200]
        if not query:
            raise NotDone("say what to search for")
        url = SEARCH[site].format(urllib.parse.quote_plus(query))
        self._go(f"searched {site} for \"{query}\"", lambda: self._open_url(url))
        return f"Opened {site} results for \"{query}\" on your human's screen."

    def open_website(self, args: dict) -> str:
        name = str(args.get("name") or "")
        sites = {**SITES, **self.bookmarks}
        match = next((n for n in sites if _key(n) == _key(name)), None)
        if match is None:
            raise NotDone(f"not one of the allowed websites ({', '.join(sites)}); your human can add bookmarks in the "
                          "dashboard settings")
        url = sites[match]
        self._go(f"opened {match}", lambda: self._open_url(url))
        return f"Opened {match} on your human's screen."

    def open_app(self, args: dict) -> str:
        self._mac_only()
        name = str(args.get("name") or "")
        match = next((a for a in self.apps if _key(a) == _key(name)), None)
        if match is None:
            raise NotDone(f"{name or 'that'} is not on the allowed apps list; your human can add it in the dashboard "
                          "settings")

        def go() -> None:
            code, _, err = self._run(["open", "-a", match], 10)
            if code != 0:
                raise NotDone(f"{match} didn't open (is it installed?)")
        self._go(f"opened {match}", go)
        return f"Opened {match}."

    def media(self, args: dict) -> str:
        self._mac_only()
        action = str(args.get("action") or "")
        if action not in _MEDIA_VERB:
            raise NotDone("action must be play, pause, next or previous")
        result: list[str] = []

        def go() -> None:
            running = [a for a in MEDIA_APPS if self._osa(f'application "{a}" is running') == "true"]
            if not running:
                raise NotDone("neither Spotify nor Music is open (open_app can start one)")
            # the one that's playing, else the first that's open
            app = next((a for a in running if self._osa(f'tell application "{a}" to player state as string') == "playing"),
                       running[0])
            self._osa(f'tell application "{app}" to {_MEDIA_VERB[action]}')
            result.append(app)
        self._go(f"music: {action}", go)
        return f"Done: {action} in {result[0]}." if result else f"Done: {action}."

    def play_music(self, args: dict) -> str:
        self._mac_only()
        query = re.sub(r"\s+", " ", _CONTROL.sub(" ", str(args.get("query") or ""))).strip()[:120]
        kind = str(args.get("kind") or "song")
        if not query:
            raise NotDone("say what to play")
        if kind not in MUSIC_KINDS:
            raise NotDone("kind must be song, artist, album or playlist")
        with self.lock:
            self._allowed()   # before asking Spotify anything
        try:
            uri, label = self.spotify.find(query, MUSIC_KINDS[kind])
        except NotDone as why:
            # No key, or Spotify said no: open Spotify's own search so the human can pick, and say why.
            def search() -> None:
                self._spotify_ready()
                self._run(["open", "spotify:search:" + urllib.parse.quote(query, safe="")], 10)
            self._go(f"searched Spotify for \"{query}\"", search)
            raise NotDone(f"couldn't play it directly ({why}), so Spotify's search for \"{query}\" is open on the "
                          "screen for your human to pick")
        if not SPOTIFY_URI.match(uri):   # it goes into an AppleScript line: only a well-formed Spotify id may
            raise NotDone("Spotify sent back something odd; try again")

        def go() -> None:
            self._spotify_ready()
            self._osa(f'tell application "Spotify" to play track "{uri}"')
        self._go(f"playing {label} on Spotify", go)
        return f"Playing {label} on Spotify."

    def now_playing(self, args: dict) -> str:
        self._mac_only()
        found: list[str] = []

        def go() -> None:
            running = [a for a in MEDIA_APPS if self._osa(f'application "{a}" is running') == "true"]
            if not running:
                found.append("Neither Spotify nor Music is open.")
                return
            for app in running:
                state = self._osa(f'tell application "{app}" to player state as string')
                if state == "stopped":
                    found.append(f"{app} is open but nothing is playing.")
                    continue
                info = self._osa(f'tell application "{app}" to (name of current track) & "\n" & (artist of current track)'
                                 f' & "\n" & (album of current track)').split("\n")
                name, artist, album = (info + ["", "", ""])[:3]
                found.append(f"{app}: {state} - \"{name}\" by {artist or 'unknown'}" + (f" (album {album})" if album else ""))
        self._go("checked what's playing", go)
        return " ".join(found)

    def computer_volume(self, args: dict) -> str:
        self._mac_only()
        change = str(args.get("change") or "")
        level = args.get("level")

        def go() -> None:
            self._current_volume()   # raises if this output's volume can't be set (an audio interface, HDMI)
            if change == "mute":
                self._osa("set volume output muted true")
            elif change == "unmute":
                self._osa("set volume output muted false")
            elif change in ("up", "down"):
                now = self._current_volume()
                self._osa(f"set volume output volume {max(0, min(100, now + (10 if change == 'up' else -10)))}")
            elif level is not None:
                try:
                    v = int(round(float(level)))
                except (TypeError, ValueError):
                    raise NotDone("level must be a number from 0 to 100")
                self._osa(f"set volume output volume {max(0, min(100, v))} without output muted")
            else:
                raise NotDone("give a level (0-100) or a change: up, down, mute, unmute")
        what = change or f"{level}%"
        self._go(f"computer volume: {what}", go)
        try:
            return f"Computer volume {what}, now {self._current_volume()}%."
        except NotDone:
            return f"Computer volume {what}."

    def run_shortcut(self, args: dict) -> str:
        self._mac_only()
        name = str(args.get("name") or "")
        match = next((s for s in self.shortcuts if _key(s) == _key(name)), None)
        if match is None:
            raise NotDone(f"{name or 'that'} is not on the allowed shortcuts list; your human can add it in the "
                          "dashboard settings")
        if self.confirm_shortcuts:
            with self.lock:
                p = self._pending
                # The yes is checked here, not left to the model: words from a TV or a sign the camera saw
                # can't confirm a shortcut.
                ok = (p is not None and p[0] == match and p[1] < self.turn and p[3]
                      and time.monotonic() - p[2] <= CONFIRM_WINDOW_S
                      and (self._heard is None or bool(YES.match(self._heard))))
                if not ok:
                    self._pending = (match, self.turn, time.monotonic(), False)
            if not ok:
                raise NotDone(f"not run yet. Ask your human: \"Run {match}?\" (say its name: a yes only counts if "
                              "they heard which shortcut). Only call run_shortcut again after they say yes in their "
                              "next message.")
            with self.lock:
                self._pending = None

        def go() -> None:
            # Started, not awaited: a shortcut can take a while. Errors that come back fast are reported.
            code, _, err = self._run(["shortcuts", "run", match], 5)
            if code not in (0, None):
                raise NotDone(f"the shortcut {match} failed: {err.strip()[:120] or 'no reason given'}")
        self._go(f"ran shortcut {match}", go)
        return f"Ran the shortcut \"{match}\"."

    def switched_off(self, args: dict) -> str:
        if self.enabled:
            return "Computer control is on now: use the computer abilities you have."
        return ("Computer control is switched off. Tell your human they can turn on \"Let Rocky use my computer\" in "
                "the dashboard settings.")

    # ── helpers ──

    def _spotify_ready(self) -> None:
        """Open Spotify if it isn't, and wait until it answers."""
        if self._osa('application "Spotify" is running') == "true":
            return
        code, _, _ = self._run(["open", "-a", "Spotify"], 10)
        if code not in (0, None):
            raise NotDone("Spotify isn't installed on this computer")
        end = time.monotonic() + SPOTIFY_START_S
        while time.monotonic() < end:
            time.sleep(0.5)
            if self._osa('application "Spotify" is running') == "true":
                time.sleep(1.5)   # running isn't quite ready to play
                return
        raise NotDone("Spotify took too long to open")

    def _mac_only(self) -> None:
        if not MAC:
            raise NotDone("that only works on a Mac")

    def _current_volume(self) -> int:
        now = self._osa("output volume of (get volume settings)")
        if not now.isdigit():   # "missing value": the output (an audio interface, HDMI) has no volume macOS can set
            raise NotDone("this computer's sound output doesn't let apps change its volume (it's set on the device)")
        return int(now)

    def _osa(self, script: str) -> str:
        """Run a fixed AppleScript line (built only from this module's own strings and numbers)."""
        code, out, err = self._run(["osascript", "-e", script], 8)
        if code != 0:
            if "-1743" in err or "not allowed" in err.lower():
                raise NotDone("macOS hasn't given the server permission to control that app yet (System Settings > "
                              "Privacy & Security > Automation)")
            raise NotDone(f"that didn't work: {err.strip()[:120]}")
        return out.strip()

    def _allowed(self) -> None:
        """Raise unless the switch is on and the limits leave room. Call with the lock held."""
        if not self.enabled:
            raise NotDone("computer control is switched off in the dashboard settings")
        now = time.monotonic()
        self._times = [t for t in self._times if now - t < 60.0]
        if self._turn_count >= PER_TURN:
            raise NotDone(f"that's {PER_TURN} things on the computer for one question; ask again for more")
        if len(self._times) >= PER_MINUTE:
            raise NotDone("too many computer actions in the last minute; wait a moment")

    def _go(self, what: str, do) -> None:
        """Check the switch and the limits, do it, and log it."""
        with self.lock:
            self._allowed()
            now = time.monotonic()
            self._turn_count += 1
            self._times.append(now)
        do()
        self.last = (what, time.time())
        print(f"  computer: {what}")


class SpotifySearch:
    """Spotify's search, with an app key (client credentials: no Spotify login). Since 2026 Spotify only gives these
    keys to Premium accounts. find() returns (spotify URI, "Song by Artist") or raises NotDone saying why."""
    TOKEN_URL = "https://accounts.spotify.com/api/token"
    SEARCH_URL = "https://api.spotify.com/v1/search"

    def __init__(self, fetch=None) -> None:
        self._fetch = fetch or _fetch_json   # fetch(url, data|None, headers) -> (status, json). Tests swap it.
        self._token: tuple[str, float] | None = None
        self.lock = threading.Lock()

    @staticmethod
    def keys() -> tuple[str, str]:
        return os.environ.get("SPOTIFY_CLIENT_ID", "").strip(), os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()

    def configured(self) -> bool:
        return all(self.keys())

    def find(self, query: str, kind: str) -> tuple[str, str]:
        if not self.configured():
            raise NotDone("no Spotify key in server/.env")
        params = {"q": query, "type": kind, "limit": 5}
        market = os.environ.get("SPOTIFY_MARKET", "US").strip()
        if market:
            params["market"] = market
        status, data = self._fetch(f"{self.SEARCH_URL}?{urllib.parse.urlencode(params)}", None,
                                   {"Authorization": f"Bearer {self._get_token()}"})
        if status == 401:   # the token ran out early: one fresh try
            with self.lock:
                self._token = None
            status, data = self._fetch(f"{self.SEARCH_URL}?{urllib.parse.urlencode(params)}", None,
                                       {"Authorization": f"Bearer {self._get_token()}"})
        if status == 403:
            raise NotDone("Spotify refused the search (Spotify keys need the account owner to have Premium)")
        if status == 429:
            raise NotDone("Spotify says too many searches; wait a minute")
        if status != 200 or not isinstance(data, dict):
            raise NotDone(f"Spotify's search answered {status}")
        items = [i for i in ((data.get(kind + "s") or {}).get("items") or []) if isinstance(i, dict) and i.get("uri")]
        if not items:
            raise NotDone(f"Spotify found no {'song' if kind == 'track' else kind} called \"{query}\"")
        best = items[0]
        name = str(best.get("name") or query)
        by = best.get("artists") or ([best["owner"]] if isinstance(best.get("owner"), dict) else [])
        who = ", ".join(str(a.get("name") or a.get("display_name") or "") for a in by[:2] if isinstance(a, dict)).strip(", ")
        return str(best["uri"]), (f"\"{name}\" by {who}" if who and kind != "artist" else f"\"{name}\"")

    def _get_token(self) -> str:
        with self.lock:
            if self._token and time.monotonic() < self._token[1]:
                return self._token[0]
            cid, secret = self.keys()
            basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
            status, data = self._fetch(self.TOKEN_URL, b"grant_type=client_credentials",
                                       {"Authorization": f"Basic {basic}",
                                        "Content-Type": "application/x-www-form-urlencoded"})
            if status in (400, 401) or not isinstance(data, dict) or not data.get("access_token"):
                raise NotDone("Spotify didn't accept the key in server/.env (check SPOTIFY_CLIENT_ID and "
                              "SPOTIFY_CLIENT_SECRET)" if status in (400, 401) else f"Spotify's login answered {status}")
            self._token = (str(data["access_token"]), time.monotonic() + float(data.get("expires_in", 3600)) - 60)
            return self._token[0]


def _fetch_json(url: str, data: bytes | None, headers: dict) -> tuple[int, object]:
    ctx = None
    try:
        import ssl
        import certifi  # in the brain's venv; Python.org builds on macOS have no system roots
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "desk-robot", **headers})
    try:
        with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"null")
        except ValueError:
            body = None
        return e.code, body
    except (OSError, ValueError) as e:
        raise NotDone(f"couldn't reach Spotify: {e}")


def _run(argv: list[str], timeout: float) -> tuple[int | None, str, str]:
    """Run a command with no shell. Still running after `timeout` = left running, returncode None."""
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", ""
    return p.returncode, out, err


def _open_url(url: str) -> None:
    check_url(url)
    if MAC:
        code, _, err = _run(["open", url], 10)
        if code not in (0, None):
            raise NotDone(f"the browser didn't open: {err.strip()[:120]}")
    elif not webbrowser.open(url):
        raise NotDone("couldn't open a browser on this computer")


# ── What the model sees ──────────────────────────────────────────────────────

_SAY = " Say one short line as you call it (like \"Opening Amazon.\"); that's all, no need to wait for a result."


def tools(c: Computer) -> list[dict]:
    """Tool definitions for the model. Switched off: one small tool, so he can tell you how to switch it on instead
    of pretending."""
    if not c.enabled:
        return [_fn("use_computer", "Call this whenever your human asks you to do something on their computer "
                    "(look something up online, open a website or app, music, volume). You can, but the switch for it "
                    "is off right now. Call it before saying anything; the result says how they turn it on.",
                    {"what": {"type": "string", "description": "what they asked for"}}, [])]
    sites = list({**SITES, **c.bookmarks})
    out = [
        _fn("search_web", "Search the web on your human's computer screen, when they ask you to look something up, "
            "find or show something online." + _SAY,
            {"site": {"type": "string", "enum": list(SEARCH), "description": "google unless they name a site"},
             "query": {"type": "string", "description": "the words to search for"}}, ["site", "query"]),
        _fn("open_website", "Open a website's home page on your human's computer." + _SAY,
            {"name": {"type": "string", "enum": sites}}, ["name"]),
    ]
    if MAC:
        if c.apps:
            out.append(_fn("open_app", "Open an app on your human's computer." + _SAY,
                           {"name": {"type": "string", "enum": c.apps}}, ["name"]))
        out += [
            _fn("play_music", "Find music on Spotify and play it on your human's computer: a song, an artist, an "
                "album or a playlist." + _SAY,
                {"query": {"type": "string", "description": "what to search for, like \"Mr. Blue Sky ELO\""},
                 "kind": {"type": "string", "enum": list(MUSIC_KINDS)}}, ["query", "kind"]),
            _fn("now_playing", "Find out what music is playing or paused on your human's computer (Spotify or Music).",
                {}, []),
            _fn("media", "Play, pause or skip the music on your human's computer (Spotify or Music)." + _SAY,
                {"action": {"type": "string", "enum": list(_MEDIA_VERB)}}, ["action"]),
            _fn("computer_volume", "Change the sound volume of your human's computer (not your own speaker)." + _SAY,
                {"change": {"type": "string", "enum": ["up", "down", "mute", "unmute"]},
                 "level": {"type": "number", "description": "0-100, instead of a change"}}, []),
        ]
        if c.shortcuts:
            out.append(_fn("run_shortcut", "Run one of your human's Apple Shortcuts. "
                           + ("The first call only asks: you then ask your human to confirm and call again only after "
                              "they say yes." if c.confirm_shortcuts else _SAY.strip()),
                           {"name": {"type": "string", "enum": c.shortcuts}}, ["name"]))
    return out


def _fn(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": props,
                                                            "required": required}}}


controller = Computer()
ACTIONS = {
    "search_web": controller.search_web,
    "open_website": controller.open_website,
    "open_app": controller.open_app,
    "media": controller.media,
    "computer_volume": controller.computer_volume,
    "run_shortcut": controller.run_shortcut,
    "play_music": controller.play_music,
    "now_playing": controller.now_playing,
    "use_computer": controller.switched_off,
}
# Done once they succeed and he has said his line: no second call to the model.
# (now_playing isn't: he has to hear the answer to say it.)
QUICK = {"search_web", "open_website", "open_app", "media", "computer_volume", "run_shortcut", "play_music"}
