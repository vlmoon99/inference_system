# <host name> — <hardware in one line>

What this folder must contain when the host is real:

| File | Purpose |
|---|---|
| `host.yaml` | what runs where; validated by `make host-validate` |
| `up.sh` / `down.sh` | bring the services up/down on the box (or `install.sh` + systemd units) |
| `worker.env.example` | the four service URLs + queue settings for `ads-worker` on this box |
| `README.md` | this file: install steps, weights, measured numbers, quirks |

Steps to add a machine class:

1. Copy `_template/` to `hosts/<name>/`, edit `host.yaml`, run `make host-validate`.
2. Write the install: engines pinned (ComfyUI commit, vLLM tag, llama.cpp build),
   weights with exact repo paths, one env file, systemd units or an `up.sh`.
3. Run `python -m inference.conformance --host <name> --at <ip>` from any tailnet
   machine. PASS = supported hardware. Flip `status: built`.
4. Register the box on Admin → Executors, put the token in `worker.env`, start
   `ads-worker`. It claims only what its services advertise.
5. Record what you measured (seconds per unit, peak memory) in this README —
   numbers live next to the hardware, never in `docs/`.

Engine choices per vendor are listed in `inference/README.md`.
