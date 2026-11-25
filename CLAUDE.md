# Rocky (desktop mode)

## Face window (`face-window/`)

Rust floating face, started by `server/launcher.py` when `FACE_WINDOW=1` in `server/.env`. After changing it, rebuild and hot-swap the running window (no launcher restart needed):

```bash
cd face-window && cargo build --release && curl -X POST -H "X-Rocky-Console: 1" localhost:8766/launcher/face/restart
```

Changes to `server/launcher.py` itself still need a launcher restart.

The face drawing lives once, in `face-window/core/src/face.rs`. The dashboard uses it as `server/brain/face.wasm` (committed, so the server needs no Rust); after changing `face-window/core/`, rebuild and copy it:

```bash
cd face-window && cargo build -p rocky-face-core --target wasm32-unknown-unknown --profile wasm && cp target/wasm32-unknown-unknown/wasm/rocky_face_core.wasm ../server/brain/face.wasm
```

## Tests

```bash
cd server && .venv/bin/python -m unittest discover -s tests
```
