# knowledge/: dormant reference from advertisment_system

Copied on 2026-10-09 from `advertisment_system` (github.com/vlmoon99/advertisment_system @ 331775c). **Nothing
here runs.** It's the reference for re-enabling models we already fought with. Code still imports the old
`ads` package in places; treat it as a recipe, not a library.

| Folder | What | Start with |
|---|---|---|
| `tts-uk/` | The Ukrainian voice stack: RAD-TTS++ `mykyta` (engine of record, explicit наголос), stress chain (`normalize.py`, `verbalize.py`, accentor), smoothing/`polish.py`, StyleTTS2-uk, robinhad sidecar, plus the backend side (`backend/tts.py`, `tts_qa.py`, `tts_improve.py`) | `UKRAINIAN_VOICE_IMPROVEMENT.md`, `UKRAINIAN_TTS_ROADMAP.md`, `gen-tts/engines.py`. Weights: `../weights/tts/` (see `../weights/weights.lock`) |
| `workflows/` | ComfyUI API workflows (Qwen-Image 2512 + Edit 2511, LTX-2.5, Wan 2.2, FLUX, InfiniteTalk) + `weights.lock` (exact files + HF hashes, for render parity across machines) | `weights.lock` header |
| `adapters/` | old HTTP fronts (gen-image/video/avatar, embed-qwen3, mt-gate), CONTRACT v1, openapi, conformance, hostspec, sysinfo/keepwarm | `CONTRACT.md` |
| `hosts/` | dgx-spark, dgx-spark-2, rtx3090x3: host.yaml, install/up scripts, **READMEs with measured numbers** | each `README.md` |
| `training/` | FLUX LoRA tuning (kohya) | `run_tune.py` |
| `containers/` | `docker inspect` of the containers running on 2026-10-09 (exact vLLM flags, env, mounts; secrets redacted). The images `vllm-node`, `product_dream-svc-embed` and `pd-comfyui:base` have **no Dockerfile anywhere**, so these dumps are their only recipe | `spark-llm.inspect.json` |
| `docs/` | models table, LTX notes, embeddings, LLM API (Obliq), cost ideas, image ladder, translator bake-off | `MODELS.md` |

Licence notes that still matter: FLUX.1-dev and Qwen-Image-2.1 are non-commercial; RADTTS weights are research terms.
