"""The room's memory: one sample per poll, kept for KEEP_DAYS, saved to
data/history.json so a restart doesn't lose the graphs.

Only numbers and timestamps are stored. Nothing leaves this computer.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

KEEP_DAYS = 7


class History:
    def __init__(self, path: Path | None, keep_s: float = KEEP_DAYS * 86400) -> None:
        self.path = path
        self.keep_s = keep_s
        self.samples: list[dict] = []
        self.source = ""
        self._lock = threading.Lock()
        if path is not None:
            self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as e:
            # Keep the broken file for inspection rather than overwrite it.
            broken = self.path.with_suffix(".broken.json")
            os.replace(self.path, broken)
            print(f"(history unreadable ({e}); moved it to {broken.name} and started fresh)")
            return
        if isinstance(data, dict) and isinstance(data.get("samples"), list):
            self.source = str(data.get("source", ""))
            self.samples = [s for s in data["samples"]
                            if isinstance(s, dict) and isinstance(s.get("at"), (int, float))
                            and isinstance(s.get("values"), dict)]
            self.samples.sort(key=lambda s: s["at"])

    def add(self, at: float, values: dict, source: str) -> None:
        with self._lock:
            if self.source and source != self.source and self.samples and self.path is not None:
                # Don't mix two sources in one graph: archive the old history.
                archive = self.path.with_name(f"history-{int(self.samples[-1]['at'])}.json")
                self._save_to(archive)
                print(f"(source changed from {self.source!r} to {source!r}; old history archived as {archive.name})")
                self.samples = []
            self.source = source
            if self.samples and at <= self.samples[-1]["at"]:
                return
            self.samples.append({"at": at, "values": values})
            cutoff = at - self.keep_s
            if self.samples[0]["at"] < cutoff:
                self.samples = [s for s in self.samples if s["at"] >= cutoff]
            if self.path is not None:
                self._save_to(self.path)

    def since(self, t: float) -> list[dict]:
        with self._lock:
            return [s for s in self.samples if s["at"] >= t]

    def _save_to(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schemaVersion": 1, "source": self.source, "samples": self.samples},
                                  separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash never leaves half a file
