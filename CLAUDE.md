# Rocky (desk-robot fork, desktop mode)

## Face window (`face-window/`)

Rust floating face, started by `server/launcher.py` when `FACE_WINDOW=1` in `server/.env`. After changing it, rebuild and hot-swap the running window (no launcher restart needed):

```bash
cd face-window && cargo build --release && curl -X POST -H "X-Rocky-Console: 1" localhost:8766/launcher/face/restart
```

Changes to `server/launcher.py` itself still need a launcher restart.

## Tests

```bash
cd server && .venv/bin/python -m unittest discover -s tests
```
