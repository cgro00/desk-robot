# Computer control on Windows

Rocky's computer control was built and tested on a Mac. On Windows (and
Linux) only part of it works today:

| Ask | Mac | Windows / Linux |
|---|---|---|
| "Look up an Arduino on Amazon" (searches) | yes | yes |
| "Open YouTube" (sites and your bookmarks) | yes | yes |
| "Open Spotify" (apps) | yes | not yet |
| "Play Mr. Blue Sky", "pause", "next song", "what's playing?" | yes | not yet |
| "Turn the volume down" | yes | not yet |
| "Run Lights off" (Apple Shortcuts) | yes | no (Mac only) |

Searches and sites open in your default browser on any computer. On
Windows and Linux the server doesn't offer the other abilities to the
language model at all, so he can't try them or claim they worked.

This page is for anyone who wants to add the rest on Windows. **None of
it is tested**: the project has no Windows machine. If you get it
working, please open a pull request so everyone gets it.

## Where the code is

Everything is in [`server/brain/computer.py`](../server/brain/computer.py):

- `MAC = sys.platform == "darwin"` at the top.
- `tools()` at the bottom builds the list of abilities the model sees. The
  apps, music, volume and Shortcuts abilities sit inside `if MAC:`.
- Each of those actions (`open_app`, `media`, `play_music`, `now_playing`,
  `computer_volume`, `run_shortcut`) starts with `self._mac_only()`, then
  does its work inside a `go()` function that runs Mac commands through
  `self._run([...])` (a program with no shell) or `self._osa(...)`
  (AppleScript).

To add Windows, give each action a Windows branch inside its `go()`, let
`_mac_only()` pass on Windows for the actions you've done, and offer those
in `tools()`. The tests in
[`server/tests/test_computer.py`](../server/tests/test_computer.py) swap
`run` for a fake, so you can test the commands you build without a
Windows machine, then try it for real on one.

## Keep the safety rules

Anyone near Rocky (or a TV) can talk to him, so the Mac code follows these
rules. Keep them in the Windows version:

- **Allowlists only.** The model picks a name from your lists; the server
  builds the command. Never put the model's own words into a command.
- **No shell.** Don't build `cmd /c start ...` or PowerShell strings:
  `cmd` treats `&`, `|` and `^` in an app name as commands. Pass an
  argument list to `subprocess`, or use `os.startfile`.
- **Limits and logging.** Call through `self._go(...)` like the Mac code,
  so the switch, the 3-per-question / 8-per-minute limits and the
  dashboard log still apply.

## Ideas, one action at a time

### Volume and play / pause / skip: media keys

Windows has virtual media keys that Spotify and most players respond to,
with no extra packages:

```python
import ctypes

VK = {"play": 0xB3, "pause": 0xB3, "next": 0xB0, "previous": 0xB1,   # play and pause are one toggle key
      "mute": 0xAD, "down": 0xAE, "up": 0xAF}                         # mute is a toggle too

def press(vk: int, times: int = 1) -> None:
    user32 = ctypes.windll.user32
    for _ in range(times):
        user32.keybd_event(vk, 0, 0, 0)   # key down
        user32.keybd_event(vk, 0, 2, 0)   # key up (KEYEVENTF_KEYUP)
```

Limits: one volume key press is a small step (about 2%), so "up" is a few
presses; play and pause can't be told apart, and neither can mute and
unmute. Setting an exact level ("volume to 30") or reading the current one
needs Windows' audio API, for example the third-party `pycaw` package.

### Open an app

Windows has no `open -a Spotify`. A safe way is the app's ID from the Start
menu: in PowerShell, `Get-StartApps` lists every app's `Name` and `AppID`.
Look up the allowed app's `AppID` there, then start it with:

```python
subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app_id])
```

### Play a song by name

The Spotify search (`SpotifySearch`, the key in `server/.env`) already
works on any computer. Instead of the AppleScript line, hand the song's id
to the Spotify app:

```python
os.startfile(uri)   # uri like "spotify:track:...", already checked by SPOTIFY_URI
```

Spotify for Windows may open the song without starting it; a play press
afterwards (above) may be needed. The no-key fallback works the same way
with `"spotify:search:" + urllib.parse.quote(query, safe="")`.

### What's playing

The hardest one. Windows shares what's playing through its media session
API, which Python can read with the third-party
`winrt-Windows.Media.Control` package
(`GlobalSystemMediaTransportControlsSessionManager`). A rougher way is the
Spotify window's title, which shows "Artist - Song" while it plays.

### Shortcuts

Apple Shortcuts doesn't exist on Windows, so leave this one Mac only.

## Linux

The same approach works with Linux tools: `xdg-open` for apps and links,
`playerctl` for play / pause / skip and what's playing, and `wpctl` or
`pactl` for the volume.
