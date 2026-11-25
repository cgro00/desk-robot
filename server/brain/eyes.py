"""Eyes: a camera stream (the robot's, or this computer's), and a live-view
page for the browser.

The robot sends one JPEG per binary frame (type byte 0x02). We keep only the
newest. Settings > Vision can instead run this computer's camera through
`LocalCamera`, which pushes frames here the same way. A tiny HTTP server
(standard library, its own thread) serves:

  /          the dashboard page (brain/dashboard.html): what Rocky sees and
             hears, plus controls for his head, face, voice and sleep. The
             launcher (server/launcher.py, port 8766) serves the same page with
             settings and a Start button, and passes the live parts through
             to this server
  /stream    multipart MJPEG — the newest frame, pushed as it changes
  /frame     the newest frame as a plain JPEG
  /status    JSON: fps, frame age, chip temperature, last transcript, and
             whatever main.py adds through `state_provider`
  /api/<x>   POST, JSON body: a control action, handed to `command_handler`
             (main.py). Only answers requests that carry the X-Rocky-Console
             header, which a cross-site page can't add without a CORS
             preflight we never grant.
"""

from __future__ import annotations

import contextlib
import ipaddress
import json
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config

_ROTATE_CODES = {90: "ROTATE_90_CLOCKWISE", 180: "ROTATE_180", 270: "ROTATE_90_COUNTERCLOCKWISE"}


def rotate_jpeg(jpeg: bytes, deg: int, mirror: bool = False) -> bytes:
    """Turn a JPEG `deg` degrees clockwise (0 / 90 / 180 / 270), then mirror it left-right if asked. The camera sits
    on its side in the V2 head and the sensor can only flip, not turn, so frames are turned here. Returns the input
    unchanged for 0 without mirror, or on any error."""
    code = _ROTATE_CODES.get(int(deg) % 360)
    if (code is None and not mirror) or not jpeg:
        return jpeg
    import cv2           # only needed when rotating (the tracker imports it anyway)
    import numpy as np
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return jpeg
    if code is not None:
        img = cv2.rotate(img, getattr(cv2, code))
    if mirror:
        img = cv2.flip(img, 1)
    ok, out = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return out.tobytes() if ok else jpeg


# The Vision settings' picture sizes, as pixels.
_SIZES = {"svga": (800, 600), "xga": (1024, 768), "uxga": (1600, 1200)}


def _cv2_backend() -> int:
    """The system's own camera API, so this camera works wherever the server runs: AVFoundation on
    macOS, DirectShow on Windows, V4L2 on Linux (cv2's auto-probe is noisy and slow when nothing is there)."""
    import cv2

    name = {"darwin": "CAP_AVFOUNDATION", "win32": "CAP_DSHOW"}.get(sys.platform, "CAP_V4L2")
    return getattr(cv2, name, 0)   # CAP_ANY if the wheel predates the constant


class LocalCamera:
    """This computer's camera as a frame source, for running the server on its
    own (dashboard: Settings > Vision > "This computer's camera"). Pushes JPEGs
    into Eyes the way the robot's stream frames do, and takes the sharp stills
    the robot would. Works on macOS, Windows and Linux: cv2 picks the system's
    camera API (AVFoundation / DirectShow / V4L2). cv2 is only touched while
    running, so this file stays importable without OpenCV."""

    def __init__(self, eyes: "Eyes") -> None:
        self.eyes = eyes
        self.index = None            # None = the system default camera
        self.device_name = ""
        self.error: str | None = None
        self.busy = False            # a head move is taking its picture: wait before re-grabbing
        self._capture = None
        self._jpeg: bytes = b""      # newest full-size frame (a snapshot re-encodes this)
        self._at = 0.0               # time.time() of that frame
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._want_high = False      # a sharp grab was asked for while the loop was at stream size

    @property
    def running(self) -> bool:
        return self._thread is not None and not self._stop.is_set()

    # ── the loop ────────────────────────────────────────────────────────────
    def start(self) -> str:
        """Open the camera and start pushing frames. Raises RuntimeError with a friendly
        reason when there is no camera to open (nothing is connected yet, no permission)."""
        import cv2

        cap = cv2.VideoCapture(self.index if self.index is not None else 0, _cv2_backend())
        if not cap.isOpened():
            raise RuntimeError("no camera could be opened (is one plugged in"
                               + (", and is this app allowed to use the camera: System Settings > Privacy "
                                  "& Security > Camera?)" if sys.platform == "darwin" else "?)"))
        self._capture = cap
        self.device_name = f"camera {self.index if self.index is not None else 0}"
        self.error = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self.device_name

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._capture is not None:
            with contextlib.suppress(Exception):
                self._capture.release()
            self._capture = None
        self._jpeg = b""

    def _run(self) -> None:
        import cv2

        self._last_frame = time.time()
        high = (1280, 720)
        while not self._stop.is_set():
            want = high if (self._want_high or not config.CAMERA_STREAM_SIZE) else \
                _SIZES.get(config.CAMERA_STREAM_SIZE, (640, 480))
            if self._capture.get(cv2.CAP_PROP_FRAME_WIDTH) != want[0]:
                self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, want[0])
                self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, want[1])
            ok, frame = self._capture.read()
            if not ok:
                # A phone-as-webcam or Continuity Camera device can take a couple of seconds to hand over its
                # first frame: only say something once the stream really has gone quiet.
                if time.time() - self._last_frame > 5.0:
                    self.error = "the camera stopped sending frames"
                if self._stop.wait(1.0):
                    break
                continue
            self._last_frame = time.time()
            self._want_high = False
            now = time.time()
            full = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            small = cv2.resize(full, (320, 240), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 90])
            if ok:
                self.eyes.push_frame(buf.tobytes(), "mac")
            ok, big = cv2.imencode(".jpg", full, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if ok:
                self._jpeg, self._at = big.tobytes(), now
            self._stop.wait(0.1)   # ~10 fps, like the robot's stream

    # ── snapshots ───────────────────────────────────────────────────────────
    def take_snapshot(self, size_key: str, since: float) -> bytes:
        """The first full-size frame taken after `since`, at the settings' picture size ("" = as grabbed).
        Runs in main's thread pool — cv2.read blocks, so never on the event loop."""
        self._want_high = True
        deadline = time.time() + 2.5
        while self._at < since and time.time() < deadline:
            time.sleep(0.05)
        if self._at < since or not self._jpeg:
            return b""
        jpeg = self._jpeg
        want = _SIZES.get(size_key)
        if want is not None:
            import cv2
            import numpy as np
            img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                ok, out = cv2.imencode(".jpg", cv2.resize(img, want, interpolation=cv2.INTER_AREA),
                                       [cv2.IMWRITE_JPEG_QUALITY, 92])
                if ok:
                    jpeg = out.tobytes()
        return jpeg



class Eyes:
    def __init__(self) -> None:
        self.jpeg: bytes = b""          # newest raw frame from the robot
        self.frame_at = 0.0
        self.frame_seq = 0
        self.display: bytes = b""       # what the browser shows (annotated if a tracker runs)
        self.display_seq = 0
        self.has_annotator = False       # set by the tracker; then it publishes frames
        self.tracking_info: dict = {}
        self.frames = 0
        self._fps_window: list[float] = []
        self.temperature: float | None = None
        self.last_heard = ""
        self.last_said = ""
        # main.py plugs in: extra /status fields, and the handler for /api/<action>.
        self.state_provider: Callable[[], dict] | None = None
        self.command_handler: Callable[[str, dict], dict] | None = None
        self._cond = threading.Condition()

    # ── robot → server ──────────────────────────────────────────────────────
    def push_frame(self, jpeg: bytes, source: str = "robot") -> None:
        # The robot's camera sits on its side in the V2 head (CAMERA_ROTATE); this computer's camera has its own
        # turn setting, since a laptop's lens is already upright.
        deg, mirror = ((config.CAMERA_LOCAL_ROTATE, config.CAMERA_LOCAL_MIRROR) if source == "mac"
                       else (config.CAMERA_ROTATE, config.CAMERA_MIRROR))
        jpeg = rotate_jpeg(jpeg, deg, mirror)
        now = time.time()
        with self._cond:
            self.jpeg = jpeg
            self.frame_at = now
            self.frame_seq += 1
            self.frames += 1
            self._fps_window.append(now)
            self._fps_window = [t for t in self._fps_window if now - t < 2.0]
            if not self.has_annotator:
                self.display = jpeg
                self.display_seq = self.frame_seq
            self._cond.notify_all()

    def publish(self, jpeg: bytes) -> None:
        """A frame for the browser (the tracker's annotated copy)."""
        if not jpeg:
            return
        with self._cond:
            self.display = jpeg
            self.display_seq += 1
            self._cond.notify_all()

    def wait_for_display(self, seen_seq: int, timeout: float = 1.0) -> int:
        with self._cond:
            self._cond.wait_for(lambda: self.display_seq != seen_seq, timeout=timeout)
            return self.display_seq

    def latest(self, max_age: float = 2.0) -> bytes | None:
        """The newest frame if it's recent enough to be worth showing the model."""
        if self.jpeg and time.time() - self.frame_at <= max_age:
            return self.jpeg
        return None

    def fps(self) -> float:
        return len(self._fps_window) / 2.0

    def wait_for_new(self, seen_seq: int, timeout: float = 1.0) -> int:
        with self._cond:
            self._cond.wait_for(lambda: self.frame_seq != seen_seq, timeout=timeout)
            return self.frame_seq

    # ── browser ─────────────────────────────────────────────────────────────
    def serve(self, port: int, bind: str = "127.0.0.1") -> None:
        eyes = self
        page = (Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
                .replace("{name}", config.ROBOT_NAME).encode())
        local_hosts = ("localhost", "127.0.0.1", "::1", bind.lower())

        class Handler(BaseHTTPRequestHandler):
            server_version = "desk-robot"  # don't advertise the Python version
            sys_version = ""

            def log_message(self, *args) -> None:  # keep the console quiet
                pass

            def _local(self) -> bool:
                # A malicious web page can point its own domain at 127.0.0.1
                # (DNS rebinding) and read a localhost server. Only answer
                # requests addressed to us by a local name.
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()   # "[::1]:8767" too
                if host not in local_hosts:
                    self._reply(403, "text/plain", b"forbidden")
                    return False
                # Another site's page making the browser fetch us (an <img>, a script) is refused; following a
                # link to the page is fine.
                if (self.headers.get("Sec-Fetch-Site") == "cross-site"
                        and self.headers.get("Sec-Fetch-Dest", "document") != "document"):
                    self._reply(403, "text/plain", b"forbidden")
                    return False
                return True

            def _from_this_computer(self) -> bool:
                # The controls answer this computer only, even if LIVE_VIEW_BIND opens the page to the LAN:
                # the header and Origin checks stop browsers, not a script on another machine.
                try:
                    ip = ipaddress.ip_address(self.client_address[0].split("%")[0])
                except ValueError:
                    return False
                return ip.is_loopback or bool(getattr(ip, "ipv4_mapped", None) and ip.ipv4_mapped.is_loopback)

            def do_POST(self) -> None:
                if not self._local():
                    return
                if not self._from_this_computer():
                    self._reply(403, "application/json", b'{"error": "controls answer this computer only"}')
                    return
                # Controls: a page on another site could still POST here
                # (browsers allow simple cross-site POSTs), so demand a custom
                # header — that turns it into a preflighted request, and we
                # never answer preflights. Belt and braces: check Origin too.
                origin = (self.headers.get("Origin") or "").lower()
                origin_host = origin.split("://", 1)[-1].split(":")[0].strip("[]")
                if self.headers.get("X-Rocky-Console") != "1" or (origin and origin_host not in local_hosts):
                    self._reply(403, "application/json", b'{"error": "not the console"}')
                    return
                if not self.path.startswith("/api/") or eyes.command_handler is None:
                    self._reply(404, "application/json", b'{"error": "no such control"}')
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 <= length <= 64 * 1024:
                        raise ValueError("bad content length")
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(payload, dict):
                        raise ValueError("body must be a JSON object")
                except (ValueError, json.JSONDecodeError, RecursionError) as e:
                    self._reply(400, "application/json", json.dumps({"error": f"bad request: {e}"}).encode())
                    return
                action = self.path[len("/api/"):]
                try:
                    result = eyes.command_handler(action, payload)
                except ValueError as e:  # the control said no
                    self._reply(400, "application/json", json.dumps({"error": str(e)}).encode())
                    return
                except Exception as e:  # something broke inside the server
                    self._reply(500, "application/json", json.dumps({"error": f"{type(e).__name__}: {e}"}).encode())
                    return
                self._reply(200, "application/json", json.dumps({"ok": True, **(result or {})}).encode())

            def do_GET(self) -> None:
                if not self._local():
                    return
                if self.path == "/":
                    self._reply(200, "text/html; charset=utf-8", page)
                elif self.path == "/frame":
                    if eyes.jpeg:
                        self._reply(200, "image/jpeg", eyes.jpeg)
                    else:
                        self._reply(503, "text/plain", b"no frame yet")
                elif self.path == "/status":
                    st = {
                        "fps": round(eyes.fps(), 1),
                        "frame_age_s": round(time.time() - eyes.frame_at, 1) if eyes.frame_at else None,
                        "frames": eyes.frames,
                        "temperature_c": eyes.temperature,
                        "last_heard": eyes.last_heard,
                        "last_said": eyes.last_said,
                        **eyes.tracking_info,
                    }
                    if eyes.state_provider is not None:
                        try:
                            st.update(eyes.state_provider())
                        except Exception as e:  # never let a status bug kill the page
                            st["state_error"] = str(e)
                    self._reply(200, "application/json", json.dumps(st).encode())
                elif self.path == "/stream":
                    self._stream()
                else:
                    self._reply(404, "text/plain", b"not found")

            def _reply(self, code: int, ctype: str, body: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")  # robot-supplied bytes stay images
                self.send_header("X-Frame-Options", "DENY")                  # no framing (clickjacking)
                self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def _stream(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                seq = -1
                try:
                    while True:
                        seq = eyes.wait_for_display(seq, timeout=1.0)
                        frame = eyes.display
                        if not frame:
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode())
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer((bind, port), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()

