# inference_system — working rules

One local inference cloud (LiteLLM + engines on tailnet hosts) used by N projects. Design record:
`docs/PLATFORM_PLAN.md`. **Start every session by reading `docs/PROGRESS.md`** (current state + next action)
and keep it updated: commit + push it after every finished unit, and before destructive or long steps.

* Python glue only, engines are black boxes. Don't reinvent what LiteLLM/vLLM/ComfyUI already do.
* Every node runs `hosts/core.yaml` (core models + balancer + gateway); `hosts/<host>/compose.yaml` includes it and adds
  what only that machine has (non-core). `deploy/node.sh` starts and supervises it: never `compose up` by hand. No sudo.
* Tailnet-only. The admin binds to the tailnet IP; its password is set on the first visit.
* Prove every feature against the running server + logs before any UI. One revertable commit per unit to `main`.
* Performance numbers live in the host README next to the hardware, never in docs/.
* `knowledge/` is dormant reference (TTS uk, video workflows). It isn't run, but don't delete it.
