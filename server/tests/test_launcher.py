"""The launcher's start / stop / restart (launcher.py): two restarts at once must not start two brains. No real
brain: the process is faked."""

import threading
import time
import unittest
from unittest import mock

import launcher


class FakeProc:
    started = []

    def __init__(self, *args, **kwargs):
        self.code = None
        self.ended = threading.Event()
        self.stdin = mock.Mock()
        self.stdout = iter(())
        FakeProc.started.append(self)
        time.sleep(0.05)   # starting takes a moment: the window two restarts used to slip through together

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        if timeout is None:            # the launcher's reader thread: until it's stopped
            self.ended.wait()
        elif self.code is None:        # stop() after "quit": it quits
            self.end(0)
        return self.code

    def end(self, code):
        self.code = code
        self.ended.set()

    def terminate(self):
        self.end(-15)

    kill = terminate


class RestartTests(unittest.TestCase):
    def setUp(self):
        FakeProc.started = []
        for target, name, value in ((launcher.subprocess, "Popen", FakeProc),
                                    (launcher.Brain, "external", lambda self: False)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_two_restarts_at_once_leave_one_brain_running(self):
        b = launcher.Brain()
        b.start()
        threads = [threading.Thread(target=b.restart) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        running = [p for p in FakeProc.started if p.poll() is None]
        self.assertEqual(len(running), 1)
        self.assertIs(running[0], b.proc)   # the one it can stop is the one running
        self.assertEqual(len(FakeProc.started), 3)   # the first, then one per restart, one after the other

    def test_two_starts_at_once_start_one(self):
        b = launcher.Brain()
        results = []
        threads = [threading.Thread(target=lambda: results.append(b.start())) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["already running", "started"])
        self.assertEqual(len(FakeProc.started), 1)


if __name__ == "__main__":
    unittest.main()
