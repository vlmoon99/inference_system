# dgx-spark-2 → connect to the ad system

For whoever runs this box (the owner or the agent on it). Goal: this box renders every
image and video for the ad system with **all its models warm**, and the primary Spark
(dgx-spark, 100.64.0.1) hands it the work. Background and numbers: `README.md` here;
the layout across both boxes: `../dgx-spark/README.md` § Two-Spark layout.

| Box | Keeps warm |
|---|---|
| dgx-spark-2 (this box) | Qwen-Image-2512 + Edit-2511 in ComfyUI :8188 → gen-image :8102 · LTX-2.5 in ComfyUI :8189 → gen-video :8104 |
| dgx-spark | LLM, embeddings, translation gate, all voices, avatar, API, web, Postgres, every ads-worker |

You run **no worker** here. A worker on dgx-spark (executor `dgx-spark-2`, already
registered there) claims the jobs and calls this box's two adapters over the tailnet.
Connecting = this box stays healthy and warm; dgx-spark then switches its render mode
to this box.

## 1. Get your local fixes into the repo first

Your working tree has two install fixes that are not committed yet: the torchaudio
placeholder files and the add-on Python packages in `comfyui.Dockerfile`. Without them
a fresh install of this box crashes. Commit them before pulling:

```bash
cd ~/Documents/dev/advertisment_system
git status                     # expect only those two fixes; no secrets, no weights
git add -A inference/hosts/dgx-spark-2 && git commit -m "fix(dgx-spark-2): <what the two fixes are and why>"
git pull --rebase              # brings the keep-warm fix (3a7a5fe) and the dgx-spark side
git push origin main
```

Follow the repo's `CLAUDE.md` commit discipline (one revertable commit, the message says
what changed and why). If the rebase conflicts in `install.sh`, keep both sides: your
fixes and the two-ComfyUI / dropcache steps.

## 2. Apply keep-warm

```bash
# the image ComfyUI must be named ads-comfyui on :8188 (docker rename <name> ads-comfyui if not)
bash inference/hosts/dgx-spark-2/install.sh     # adds ads-comfyui-vid :8189, reinstalls the units
docker restart ads-comfyui                      # drops the LTX copy the image ComfyUI still holds
systemctl --user restart gen-image gen-video    # warm-up: 2512 + Edit on :8188, LTX on :8189
```

install.sh also enables the `ads-dropcache` **user** timer (no sudo): `systemctl --user list-timers ads-dropcache.timer`.

Why: on GB10 the page cache counts as used GPU memory. Without the timer, ComfyUI saw
20 GB free while 85 GB were available, and evicted a warm model to load another.

## 3. Prove it is warm

Wait for both warm-ups (`journalctl --user -u gen-image -u gen-video -f` until both
print `[keepwarm] warm in …`), then:

```bash
for p in 8188 8189; do curl -s localhost:$p/system_stats | python3 -c \
  "import json,sys; d=json.load(sys.stdin)['devices'][0]; print($p, 'torch GB', round(d['torch_vram_total']/2**30,1), 'cuda free GB', round(d['vram_free']/2**30,1))"; done
free -g
for i in 1 2 3; do .venv/bin/python -m inference.conformance --host dgx-spark-2 | grep generate; done
```

Pass criteria:

* :8188 holds ~45–50 GB, :8189 ~22–28 GB, `free` "available" ≥ 25 GB.
* Every image after the first round renders in seconds. Every video stays well under the
  cold ~150 s, and an image right after a video stays fast (that switch is what used to
  cost 175 s).
* `curl -s localhost:8102/health` and `:8104/health` both report `"loaded":true`.

If :8188 settles below 40 GB, set `KEEP_WARM_MIN_GB` in `units/gen-image.service` to what
it really holds minus 5 (same for `18` in `gen-video.service`). Otherwise keep-warm
re-renders every 2 minutes. Then re-run `install.sh`.

## 4. Report

* Fill README step 0, the step 3 measurement table and a new dated entry in the
  **Status log** with the numbers above. Commit, push.
* Tell the owner: **"dgx-spark-2 is warm — ready for cutover"**.

You can SSH to dgx-spark. A read-only look is fine:

```bash
ssh server@100.64.0.1 'cd ~/Documents/dev/advertisment_system && make offload-status'
```

The cutover itself (`make offload-render HOST=dgx-spark-2`) runs **on dgx-spark** and
belongs to the owner or the agent there. It restarts the customer-facing API, so do not
run it from here.

## 5. After cutover: rules for this box

Customers' images and videos run here from then on.

* **Maintenance:** anything that stops ComfyUI or the adapters (`down.sh`, a restart, a
  reinstall, a reboot) makes dgx-spark's watchdog fall back to rendering on dgx-spark
  after ~3 min. It switches back ~5 min after this box is healthy again. A planned outage
  longer than a few minutes: ask the owner to run `make onload` on dgx-spark first.
* **Never** add a model to these ComfyUIs without measuring the peak first. earlyoom kills
  ComfyUI above ~118 GB.
* No sysctl tuning, no `docker system prune -a`.
* **Phase 2** (later, owner's call): the LTX text encoder on the GPU instead of the CPU
  saves 55–65 s per new video prompt. README § Memory budget says when it fits and how to
  add it without touching dgx-spark's workflows.
