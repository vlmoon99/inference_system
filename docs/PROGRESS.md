# Migration progress log

The single source of truth for "where are we" in the platform migration
([PLATFORM_PLAN.md](PLATFORM_PLAN.md)). Any session, human or Claude, starts by reading
**Current state** and **Next action**, then continues from there.

Rules for whoever works on it:
* Update this file and commit + push it **after every finished unit**, and **before** any destructive
  or long operation (write "STARTED: …" first, so an interrupted step is visible).
* Never mark something done that wasn't verified. Say what's verified and how.
* An interrupted step is re-checked against the machine (`docker ps`, `systemctl --user`, `git status`)
  before it is resumed. Most steps are written so that re-running them is safe.

## Current state

| Step | What | Status |
|---|---|---|
| 0 | push old git, move TTS weights, port knowledge | in progress |
| 1 | stop + clean old system (owner approves deletion list) | not started |
| 2 | inference v1 on dgx-spark, `smoke.sh`, tag `inf-v1` | not started |
| 3 | dgx-spark-2 replica + node-agent, tag `inf-v1.1` | not started |
| 4 | BoostContent backend, `smoke.sh`, tag `bc-v1` | not started |
| 5 | BoostContent admin, tag `bc-admin-v1` | not started |

## Next action

Step 0c: port the knowledge from `advertisment_system` into `knowledge/` (TTS uk chain code + docs, ComfyUI
workflows + weights.lock, host READMEs with measured numbers, adapters needed for v1 as a starting point).

## Log (newest last)

* 2026-10-09: plan settled with the owner in a grilling session (PLATFORM_PLAN.md v1). Progress log created.
  Owner said "go", so steps 0→5 run straight through; only step 1's deletion waits for approval.
* 2026-10-09 0a DONE: advertisment_system main (331775c) + ai/0a8090de, ai/9668cb32, ai/c5034952 pushed;
  verified with `git fetch` (main == origin/main, the 3 branches exist on origin). The other 4 ai/* were already merged.
* 2026-10-09 0b DONE: `advertisment_system/data/models` (7.7 GB, 119 files) moved to `weights/tts/` (gitignored).
  Sources in `weights/weights.lock`, per-file hashes in `weights/tts.sha256` (not committed).
