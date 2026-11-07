"""Computer control: the switch, the allowlists, the URLs it builds, the limits, and shortcut confirmation.
Nothing really opens: a fake runner records the commands."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from brain import computer, settings, thinking
from brain.computer import Computer, NotDone


class Fake:
    def __init__(self, replies=None):
        self.cmds, self.urls, self.replies = [], [], replies or {}

    def run(self, argv, timeout):
        self.cmds.append(argv)
        script = argv[-1] if argv[0] == "osascript" else None
        for needle, reply in self.replies.items():
            if script and needle in script:
                return reply() if callable(reply) else reply
        return 0, "", ""

    def open(self, url):
        computer.check_url(url)
        self.urls.append(url)


def make(replies=None, spotify=None, **cfg):
    f = Fake(replies)
    c = Computer(run=f.run, open_url=f.open, spotify=spotify)
    c.configure({"control": True, "apps": computer.DEFAULT_APPS, "bookmarks": "", "shortcuts": "",
                 "confirm_shortcuts": True, **cfg})
    c.new_turn()
    return c, f


class ComputerTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(computer, "MAC", True)
        p.start()
        self.addCleanup(p.stop)

    def test_off_by_default_and_refuses_everything(self):
        self.assertFalse(settings.defaults()["computer"]["control"])
        c, f = make(control=False)
        for fn, args in ((c.search_web, {"site": "amazon", "query": "arduino"}), (c.open_website, {"name": "youtube"}),
                         (c.open_app, {"name": "Spotify"}), (c.media, {"action": "play"}),
                         (c.computer_volume, {"change": "up"})):
            with self.assertRaises(NotDone, msg=fn.__name__):
                fn(args)
        self.assertEqual((f.cmds, f.urls), ([], []))
        self.assertIn("switched off", c.switched_off({}))

    def test_search_builds_the_url(self):
        c, f = make()
        c.search_web({"site": "amazon", "query": "arduino uno & <case>"})
        self.assertEqual(f.urls, ["https://www.amazon.com/s?k=arduino+uno+%26+%3Ccase%3E"])
        c.search_web({"site": "Google", "query": "a\x00b\nc  d" + "x" * 500})
        self.assertTrue(f.urls[1].startswith("https://www.google.com/search?q=a+b+c+dxxx"))
        self.assertLessEqual(len(f.urls[1]), len("https://www.google.com/search?q=") + 220)

    def test_search_only_known_sites(self):
        c, f = make()
        for site in ("evil.com", "https://evil.com/?q={}", "javascript"):
            with self.assertRaises(NotDone):
                c.search_web({"site": site, "query": "x"})
        with self.assertRaises(NotDone):
            c.search_web({"site": "google", "query": "   "})
        self.assertEqual(f.urls, [])

    def test_websites_and_bookmarks(self):
        c, f = make(bookmarks="My GitHub = https://github.com/me\nShop=http://example.com/a?b=1")
        c.open_website({"name": "youtube"})
        c.open_website({"name": "my github"})
        c.new_turn()
        c.open_website({"name": "SHOP"})
        self.assertEqual(f.urls, ["https://www.youtube.com", "https://github.com/me", "http://example.com/a?b=1"])
        with self.assertRaises(NotDone):
            c.open_website({"name": "https://evil.com"})

    def test_bad_bookmarks_are_refused(self):
        for text in ("x = javascript:alert(1)", "x = file:///etc/passwd", "x = data:text/html,hi", "x = https://",
                     "no equals sign https://a.com", "x = https://a.com/has space", "= https://a.com",
                     "x = //a.com", "x = smb://a.com"):
            with self.assertRaises(ValueError, msg=text):
                computer.parse_bookmarks(text)
        with tempfile.TemporaryDirectory() as d, patch.object(settings, "PATH", Path(d) / "s.json"):
            with self.assertRaises(ValueError):
                settings.update({"computer": {"bookmarks": "x = javascript:alert(1)"}})
            s = settings.update({"computer": {"control": True, "bookmarks": "Docs = https://docs.example.com"}})
            self.assertTrue(settings.load()["computer"]["control"])     # remembered
            self.assertEqual(s["computer"]["bookmarks"], "Docs = https://docs.example.com")

    def test_apps_only_from_the_list(self):
        c, f = make(apps="Spotify, Google Chrome")
        c.open_app({"name": "spotify"})
        c.open_app({"name": "google chrome"})
        self.assertEqual(f.cmds, [["open", "-a", "Spotify"], ["open", "-a", "Google Chrome"]])
        for name in ("Terminal", "Spotify; rm -rf ~", "../../bin/sh", "", "-n"):
            with self.assertRaises(NotDone, msg=name):
                c.open_app({"name": name})
        self.assertEqual(len(f.cmds), 2)

    def test_app_that_fails_to_open(self):
        f = Fake()
        f.run = lambda argv, t: (1, "", "Unable to find application")
        c = Computer(run=f.run, open_url=f.open)
        c.configure({"control": True, "apps": "Ghost"})
        c.new_turn()
        with self.assertRaises(NotDone):
            c.open_app({"name": "Ghost"})

    def test_limits_per_question_and_per_minute(self):
        c, f = make()
        for _ in range(computer.PER_TURN):
            c.open_website({"name": "google"})
        with self.assertRaises(NotDone):
            c.open_website({"name": "google"})
        for _ in range(computer.PER_MINUTE - computer.PER_TURN):
            c.new_turn()
            c.open_website({"name": "google"})
        c.new_turn()
        with self.assertRaises(NotDone):
            c.open_website({"name": "google"})
        self.assertEqual(len(f.urls), computer.PER_MINUTE)

    def test_shortcut_waits_for_a_yes_next_turn(self):
        c, f = make(shortcuts="Lights off, Focus")
        with self.assertRaises(NotDone):
            c.run_shortcut({"name": "lights off"})
        with self.assertRaises(NotDone):          # calling again in the same reply doesn't count as a yes
            c.run_shortcut({"name": "lights off"})
        self.assertEqual(f.cmds, [])
        c.said("Run Lights off?")
        c.new_turn()
        c.run_shortcut({"name": "lights off"})
        self.assertEqual(f.cmds, [["shortcuts", "run", "Lights off"]])
        c.new_turn()                              # used up: the next run asks again
        with self.assertRaises(NotDone):
            c.run_shortcut({"name": "Lights off"})

    def test_shortcut_needs_the_human_to_say_yes(self):
        c, f = make(shortcuts="Lights off")
        with self.assertRaises(NotDone):
            c.run_shortcut({"name": "Lights off"})
        c.said("Run Lights off?")
        for heard in ("no, don't", "what's the weather?", "Run lights off and also dim them"):
            c.new_turn(heard)                     # the model calling again isn't enough: the words must be a yes
            with self.assertRaises(NotDone):
                c.run_shortcut({"name": "Lights off"})
            c.said("Okay. Run Lights off, then?")
        self.assertEqual(f.cmds, [])
        c.new_turn("Yes, go ahead.")
        c.run_shortcut({"name": "Lights off"})
        self.assertEqual(f.cmds, [["shortcuts", "run", "Lights off"]])

    def test_shortcut_yes_only_counts_if_he_named_it(self):
        # a personality's prompt words the question: "Want a fun fact?" must not turn the yes into a shortcut
        c, f = make(shortcuts="Lights off")
        with self.assertRaises(NotDone):
            c.run_shortcut({"name": "Lights off"})
        c.said("Want a fun fact?")
        c.new_turn("Yes!")
        with self.assertRaises(NotDone):
            c.run_shortcut({"name": "Lights off"})
        self.assertEqual(f.cmds, [])

    def test_shortcut_yes_is_for_that_one_and_expires(self):
        c, f = make(shortcuts="Lights off, Focus")
        with self.assertRaises(NotDone):
            c.run_shortcut({"name": "Lights off"})
        c.said("Run Lights off?")
        c.new_turn()
        with self.assertRaises(NotDone):          # a different shortcut needs its own yes
            c.run_shortcut({"name": "Focus"})
        c.new_turn()
        with patch.object(computer.time, "monotonic", return_value=time.monotonic() + computer.CONFIRM_WINDOW_S + 1):
            with self.assertRaises(NotDone):
                c.run_shortcut({"name": "Focus"})
        self.assertEqual(f.cmds, [])
        for name in ("Delete everything", ""):
            with self.assertRaises(NotDone):
                c.run_shortcut({"name": name})

    def test_shortcut_without_confirmation(self):
        c, f = make(shortcuts="Focus", confirm_shortcuts=False)
        c.run_shortcut({"name": "focus"})
        self.assertEqual(f.cmds, [["shortcuts", "run", "Focus"]])

    def test_media_picks_the_playing_app(self):
        c, f = make(replies={'"Spotify" is running': (0, "true\n", ""), '"Music" is running': (0, "true\n", ""),
                             '"Spotify" to player state': (0, "paused\n", ""),
                             '"Music" to player state': (0, "playing\n", "")})
        self.assertIn("Music", c.media({"action": "pause"}))
        self.assertEqual(f.cmds[-1], ["osascript", "-e", 'tell application "Music" to pause'])
        c2, _ = make(replies={"is running": (0, "false\n", "")})
        with self.assertRaises(NotDone):
            c2.media({"action": "play"})
        with self.assertRaises(NotDone):
            c.media({"action": "delete library"})

    def test_volume(self):
        c, f = make(replies={"output volume of": (0, "45\n", "")})
        c.computer_volume({"change": "up"})
        self.assertIn(["osascript", "-e", "set volume output volume 55"], f.cmds)
        c.computer_volume({"level": 250})
        self.assertIn(["osascript", "-e", "set volume output volume 100 without output muted"], f.cmds)
        c.computer_volume({"change": "mute"})
        with self.assertRaises(NotDone):
            c.computer_volume({"level": "loud; do shell script \"x\""})

    def test_volume_on_an_output_without_one(self):
        c, f = make(replies={"output volume of": (0, "missing value\n", "")})
        for args in ({"change": "up"}, {"level": 30}, {"change": "mute"}):
            with self.assertRaisesRegex(NotDone, "doesn't let apps", msg=args):
                c.computer_volume(args)
        self.assertFalse([cmd for cmd in f.cmds if "set volume" in cmd[-1]])

    def test_automation_permission_message(self):
        c, _ = make(replies={'"Spotify" is running': (0, "true\n", ""), '"Music" is running': (0, "false\n", ""),
                             "player state": (1, "", "Not authorized to send Apple events to Spotify. (-1743)")})
        with self.assertRaisesRegex(NotDone, "Automation"):
            c.media({"action": "play"})

    def test_tools_follow_the_switch_and_lists(self):
        c, _ = make(control=False)
        self.assertEqual([t["function"]["name"] for t in computer.tools(c)], ["use_computer"])
        c, _ = make(bookmarks="My GitHub = https://github.com/me", apps="Spotify", shortcuts="Focus")
        tools = {t["function"]["name"]: t["function"] for t in computer.tools(c)}
        self.assertEqual(set(tools), {"search_web", "open_website", "open_app", "media", "computer_volume", "run_shortcut",
                                     "play_music", "now_playing"})
        self.assertIn("My GitHub", tools["open_website"]["parameters"]["properties"]["name"]["enum"])
        self.assertEqual(tools["open_app"]["parameters"]["properties"]["name"]["enum"], ["Spotify"])
        c, _ = make()
        self.assertNotIn("run_shortcut", {t["function"]["name"] for t in computer.tools(c)})

    def test_status_reports_the_last_action(self):
        c, _ = make()
        self.assertIsNone(c.status()["last"])
        c.search_web({"site": "amazon", "query": "arduino"})
        self.assertEqual(c.status()["last"][0], 'searched amazon for "arduino"')


# ── Spotify: search with the app key, play what it finds ──

TRACK = "spotify:track:" + "A" * 22
KEYS = {"SPOTIFY_CLIENT_ID": "id", "SPOTIFY_CLIENT_SECRET": "secret", "SPOTIFY_MARKET": "US"}


class FakeSpotify:
    """Spotify's two web addresses: the token and the search."""
    def __init__(self, items=None, search_status=200, token_status=200):
        self.items = items if items is not None else [{"uri": TRACK, "name": "Mr. Blue Sky",
                                                       "artists": [{"name": "Electric Light Orchestra"}]}]
        self.search_status, self.token_status = search_status, token_status
        self.tokens = self.searches = 0
        self.last_url = None

    def fetch(self, url, data, headers):
        if url.startswith(computer.SpotifySearch.TOKEN_URL):
            self.tokens += 1
            assert headers["Authorization"].startswith("Basic ")
            return self.token_status, {"access_token": f"t{self.tokens}", "expires_in": 3600}
        self.searches += 1
        self.last_url = url
        status = self.search_status if not callable(self.search_status) else self.search_status()
        return status, {"tracks": {"items": self.items}, "artists": {"items": self.items}}


SPOTIFY_ON = {'"Spotify" is running': (0, "true\n", "")}


class SpotifyTests(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(computer, "MAC", True), patch.dict(os.environ, KEYS)):
            p.start()
            self.addCleanup(p.stop)

    def make(self, fake, replies=SPOTIFY_ON, **cfg):
        return make(replies=replies, spotify=computer.SpotifySearch(fetch=fake.fetch), **cfg)

    def test_finds_and_plays_a_song(self):
        sp = FakeSpotify()
        c, f = self.make(sp)
        self.assertEqual(c.play_music({"query": "mr blue sky", "kind": "song"}),
                         'Playing "Mr. Blue Sky" by Electric Light Orchestra on Spotify.')
        self.assertEqual(f.cmds[-1], ["osascript", "-e", f'tell application "Spotify" to play track "{TRACK}"'])
        self.assertIn("type=track", sp.last_url)
        self.assertIn("q=mr+blue+sky", sp.last_url)
        self.assertIn("market=US", sp.last_url)
        c.new_turn()
        c.play_music({"query": "elo", "kind": "artist"})
        self.assertIn("type=artist", sp.last_url)
        self.assertEqual(sp.tokens, 1)          # the token is kept for the next search

    def test_a_stale_token_is_renewed_once(self):
        answers = iter([401, 200])
        sp = FakeSpotify(search_status=lambda: next(answers))
        c, f = self.make(sp)
        c.play_music({"query": "x", "kind": "song"})
        self.assertEqual((sp.tokens, sp.searches), (2, 2))

    def test_an_odd_answer_never_reaches_applescript(self):
        for uri in ('spotify:track:abc" & do shell script "say hi', "spotify:track:short", "https://evil.com",
                    "spotify:episode:" + "A" * 22):
            sp = FakeSpotify(items=[{"uri": uri, "name": "x"}])
            c, f = self.make(sp)
            with self.assertRaises(NotDone, msg=uri):
                c.play_music({"query": "x", "kind": "song"})
            self.assertFalse([cmd for cmd in f.cmds if "play track" in cmd[-1]], uri)

    def test_without_a_key_opens_spotify_search(self):
        sp = FakeSpotify()
        with patch.dict(os.environ, {"SPOTIFY_CLIENT_ID": "", "SPOTIFY_CLIENT_SECRET": ""}):
            c, f = self.make(sp)
            with self.assertRaisesRegex(NotDone, "no Spotify key"):
                c.play_music({"query": "mr blue sky & co", "kind": "song"})
        self.assertEqual(f.cmds[-1], ["open", "spotify:search:mr%20blue%20sky%20%26%20co"])
        self.assertEqual(sp.searches, 0)

    def test_refusals_explain_and_fall_back(self):
        for sp, why in ((FakeSpotify(search_status=403), "Premium"), (FakeSpotify(token_status=400), "check SPOTIFY"),
                        (FakeSpotify(items=[]), "found no song"), (FakeSpotify(search_status=429), "too many")):
            c, f = self.make(sp)
            with self.assertRaisesRegex(NotDone, why):
                c.play_music({"query": "x", "kind": "song"})
            self.assertEqual(f.cmds[-1][:1], ["open"])   # Spotify's search, for the human to pick

    def test_switched_off_asks_spotify_nothing(self):
        sp = FakeSpotify()
        c, f = self.make(sp, control=False)
        with self.assertRaises(NotDone):
            c.play_music({"query": "x", "kind": "song"})
        self.assertEqual((sp.tokens, sp.searches, f.cmds), (0, 0, []))
        for args in ({"query": "", "kind": "song"}, {"query": "x", "kind": "podcast"}):
            with self.assertRaises(NotDone):
                self.make(sp)[0].play_music(args)

    def test_opens_spotify_first_if_needed(self):
        state = iter(["false", "false", "true"])
        c, f = self.make(FakeSpotify(), replies={'"Spotify" is running': lambda: (0, next(state, "true"), "")})
        with patch.object(computer.time, "sleep"):
            c.play_music({"query": "x", "kind": "song"})
        self.assertIn(["open", "-a", "Spotify"], f.cmds)
        self.assertIn("play track", f.cmds[-1][-1])

    def test_now_playing(self):
        c, f = self.make(FakeSpotify(), replies={'"Spotify" is running': (0, "true\n", ""),
                                                 '"Music" is running': (0, "false\n", ""),
                                                 "player state": (0, "paused\n", ""),
                                                 "name of current track": (0, "Perfect\nYung Pinch\nPerfect - Single\n", "")})
        self.assertEqual(c.now_playing({}), 'Spotify: paused - "Perfect" by Yung Pinch (album Perfect - Single)')
        c, _ = self.make(FakeSpotify(), replies={"is running": (0, "false\n", "")})
        self.assertEqual(c.now_playing({}), "Neither Spotify nor Music is open.")

    def test_tools(self):
        c, _ = self.make(FakeSpotify())
        names = {t["function"]["name"] for t in computer.tools(c)}
        self.assertTrue({"play_music", "now_playing"} <= names)
        self.assertIn("play_music", computer.QUICK)
        self.assertNotIn("now_playing", computer.QUICK)   # he has to hear the answer to say it


# ── The brain: a done computer action ends the reply without a second model call ──

def _chunk(content=None, call=None):
    tool_calls = None
    if call is not None:
        tool_calls = [SimpleNamespace(index=0, id="call_1", function=SimpleNamespace(name=call[0], arguments=call[1]))]
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))])


class FakeStream(list):
    def close(self):
        pass


class FakeClient:
    def __init__(self, rounds):
        self.rounds, self.calls = list(rounds), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        return FakeStream(self.rounds.pop(0))


class BrainTests(unittest.TestCase):
    def brain(self, rounds, action):
        b = thinking.RobotBrain({"search_web": action, "look": lambda a: ("ok", None)},
                                extra_tools=lambda: [{"type": "function", "function": {"name": "search_web"}}],
                                quick={"search_web"})
        b.client = FakeClient(rounds)
        return b

    def test_quick_action_after_his_line_is_one_call(self):
        done = []
        b = self.brain([[_chunk("[happy] Opening Amazon. "), _chunk(call=("search_web", '{"site":"amazon","query":"arduino"}'))]],
                       lambda a: (done.append(a) or "opened", None))
        said = list(b.reply("look up an arduino on amazon"))
        self.assertEqual(said, ["Opening Amazon."])
        self.assertEqual(len(b.client.calls), 1)
        self.assertEqual(done, [{"site": "amazon", "query": "arduino"}])
        self.assertEqual([t["function"]["name"] for t in b.client.calls[0]["tools"]][-1], "search_web")
        self.assertEqual([m["role"] for m in b.history], ["user", "assistant", "tool", "assistant"])

    def test_failed_or_silent_action_asks_the_model_again(self):
        def refuse(a):
            raise NotDone("switched off")
        b = self.brain([[_chunk("Opening. "), _chunk(call=("search_web", "{}"))], [_chunk("Cannot. Switch is off.")]], refuse)
        self.assertEqual(list(b.reply("search")), ["Opening.", "Cannot.", "Switch is off."])
        self.assertEqual(len(b.client.calls), 2)
        b = self.brain([[_chunk(call=("search_web", "{}"))], [_chunk("Done, human.")]], lambda a: ("opened", None))
        self.assertEqual(list(b.reply("search")), ["Done, human."])
        self.assertEqual(len(b.client.calls), 2)

    def test_an_action_after_the_sentence_limit_still_runs(self):
        done = []
        words = [_chunk("Amaze! "), _chunk("Good song. "), _chunk("Very good. "), _chunk("Extra words. "),
                 _chunk(call=("search_web", '{"q": 1}'))]
        b = self.brain([words], lambda a: (done.append(a) or "ok", None))
        with patch.object(thinking.config, "REPLY_MAX_SENTENCES", 3):
            said = list(b.reply("play it"))
        self.assertEqual(said, ["Amaze!", "Good song.", "Very good."])
        self.assertEqual(done, [{"q": 1}])
        self.assertEqual(len(b.client.calls), 1)

    def test_head_turn_without_a_picture_ends_after_his_line(self):
        # "turn your head left": look with see false moves the head, takes no picture, and says it's done
        moved = []
        b = thinking.RobotBrain({"look": lambda a: (moved.append(a) or "Head is now at pan -40.", None, True)})
        b.client = FakeClient([[_chunk("[neutral] Turning left. "), _chunk(call=("look", '{"direction":"left","see":false}'))]])
        self.assertEqual(list(b.reply("turn your head left")), ["Turning left."])
        self.assertEqual(moved, [{"direction": "left", "see": False}])
        self.assertEqual(len(b.client.calls), 1)                 # no second call: nothing to describe
        self.assertFalse(any(m["role"] == "user" and "Camera view" in str(m["content"]) for m in b.history))
        # with nothing said before the move, he gets one more call to say where his head went
        b = thinking.RobotBrain({"look": lambda a: ("Head is now at pan -40.", None, True)})
        b.client = FakeClient([[_chunk(call=("look", '{"direction":"left","see":false}'))], [_chunk("[neutral] Head left.")]])
        self.assertEqual(list(b.reply("turn your head left")), ["Head left."])
        self.assertEqual(len(b.client.calls), 2)

    def test_look_tool_asks_whether_to_see(self):
        look = next(t for t in thinking.TOOLS if t["function"]["name"] == "look")["function"]["parameters"]
        self.assertIn("see", look["properties"])
        self.assertIn("see", look["required"])

    def test_titles_dont_end_a_sentence(self):
        b = self.brain([[_chunk("Amaze! Playing Mr. Blue Sky now. Dr. Rocky approves.")]], lambda a: ("ok", None))
        self.assertEqual(list(b.reply("play mr blue sky")), ["Amaze!", "Playing Mr. Blue Sky now.", "Dr. Rocky approves."])


if __name__ == "__main__":
    unittest.main()
