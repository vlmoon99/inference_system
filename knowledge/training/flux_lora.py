"""FLUX.1-dev LoRA trainer backed by kohya sd-scripts (sd3 branch), run in a
SEPARATE venv so its pinned deps never touch the app's .venv.

Verified working on this box (DGX Spark, aarch64, CUDA 13, torch 2.13+cu130) —
see the install recipe below; scripts/tune.sh invokes it via inference/training/run_tune.py.

Everything is subprocess-based: this module imports NO torch/toolkit code, so it
is importable under CI's backend-only dependency set. When any piece of the
toolchain is missing, prepare() raises TrainerUnavailable carrying the exact
operator recipe — the executor fails the run fast with that text in its error.

Environment knobs (all optional):
  SD_SCRIPTS_DIR    kohya checkout        (default <repo>/.trainer/sd-scripts)
  TRAINER_PYTHON    training venv python  (default <repo>/.venv-train/bin/python)
  FLUX_TRAIN_DIR    extracted components  (default <repo>/data/models/flux-train)
  TRAINER_EXTRA_ARGS  extra CLI args appended to the kohya command (shlex-split)
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

from . import LogCallback, TrainerUnavailable

REPO_ROOT = Path(__file__).resolve().parents[2]

# The recipe that was actually executed to set this box up. Kept in one place:
# surfaced verbatim in TrainerUnavailable and quoted by docs.
INSTALL_RECIPE = """\
# one-time, ~10 min, no model downloads (components are split from the local
# ComfyUI combined checkpoint):
cd /home/server/Documents/dev/advertisment_system

# 1. training venv reusing the app venv's torch 2.13+cu130 (aarch64 wheels):
python3 -m venv .venv-train
SP=.venv-train/lib/python3.12/site-packages; SRC=$PWD/.venv/lib/python3.12/site-packages
for p in torch torch-*.dist-info torchgen functorch triton triton-*.dist-info \\
         nvidia nvidia_*.dist-info cuda cuda_*.dist-info sympy sympy-*.dist-info \\
         networkx networkx-*.dist-info filelock filelock-*.dist-info \\
         fsspec fsspec-*.dist-info jinja2 jinja2-*.dist-info markupsafe \\
         markupsafe-*.dist-info mpmath mpmath-*.dist-info; do
  for m in $SRC/$p; do [ -e "$m" ] && ln -sfn "$m" "$SP/$(basename "$m")"; done
done
.venv-train/bin/pip install typing_extensions setuptools packaging

# 2. kohya sd-scripts (sd3 branch has flux_train_network.py):
git clone --depth 1 -b sd3 https://github.com/kohya-ss/sd-scripts.git .trainer/sd-scripts
cd .trainer/sd-scripts && ../../.venv-train/bin/pip install -r requirements.txt && cd ../..
.venv-train/bin/pip install --no-deps torchvision==0.28.0 \\
    --index-url https://download.pytorch.org/whl/cu130

# 3. split the local combined FLUX checkpoint into kohya-format components:
.venv-train/bin/python -m inference.training.extract_flux_components
"""

# tqdm line: "steps:   3%|▎| 15/500 [00:41<22:24,  2.77s/it, avr_loss=0.328]"
_PROGRESS_RE = re.compile(r"(\d+)/(\d+)\s*\[")
_LOSS_RE = re.compile(r"a?vr?_?loss[=:]\s*([0-9.]+)")


class FluxLoraTrainer:
    kind = "flux_lora"

    def __init__(self) -> None:
        self.sd_scripts = Path(os.environ.get(
            "SD_SCRIPTS_DIR", REPO_ROOT / ".trainer" / "sd-scripts"))
        self.python = Path(os.environ.get(
            "TRAINER_PYTHON", REPO_ROOT / ".venv-train" / "bin" / "python"))
        self.weights_dir = Path(os.environ.get(
            "FLUX_TRAIN_DIR", REPO_ROOT / "data" / "models" / "flux-train"))
        self._cmd: list[str] | None = None
        self._adapter_path: Path | None = None

    # ---- availability ----

    def _missing(self) -> list[str]:
        missing = []
        if not self.python.is_file():
            missing.append(f"training venv python: {self.python}")
        if not (self.sd_scripts / "flux_train_network.py").is_file():
            missing.append(f"kohya sd-scripts checkout: {self.sd_scripts}")
        for f in ("flux1-dev-fp8-unet.safetensors", "clip_l.safetensors",
                  "t5xxl-fp8.safetensors", "ae.safetensors"):
            if not (self.weights_dir / f).is_file():
                missing.append(f"FLUX component: {self.weights_dir / f}")
        return missing

    # ---- Trainer interface ----

    def prepare(self, dataset_dir: Path, params: dict) -> None:
        missing = self._missing()
        if missing:
            raise TrainerUnavailable(
                "flux_lora toolchain incomplete — missing:\n  " + "\n  ".join(missing),
                INSTALL_RECIPE)

        dataset_dir = Path(dataset_dir)
        steps = int(params.get("steps") or 1000)
        rank = int(params.get("rank") or 16)
        trigger = str(params.get("trigger_word") or "style")
        run_dir = dataset_dir.parent
        out_dir = run_dir / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"flux-lora-{re.sub(r'[^a-zA-Z0-9_-]', '', trigger)[:40] or 'style'}"

        toml_path = run_dir / "dataset.toml"
        toml_path.write_text(
            '[general]\n'
            'caption_extension = ".txt"\n'
            'shuffle_caption = false\n'
            'keep_tokens = 1\n\n'
            '[[datasets]]\n'
            'resolution = 512\n'
            'batch_size = 1\n'
            'enable_bucket = true\n'
            'bucket_no_upscale = true\n\n'
            '[[datasets.subsets]]\n'
            f'image_dir = "{dataset_dir.as_posix()}"\n'
            'num_repeats = 1\n',
            encoding="utf-8")

        w = self.weights_dir
        cmd = [
            str(self.python), str(self.sd_scripts / "flux_train_network.py"),
            "--pretrained_model_name_or_path", str(w / "flux1-dev-fp8-unet.safetensors"),
            "--clip_l", str(w / "clip_l.safetensors"),
            "--t5xxl", str(w / "t5xxl-fp8.safetensors"),
            "--ae", str(w / "ae.safetensors"),
            "--dataset_config", str(toml_path),
            "--output_dir", str(out_dir), "--output_name", out_name,
            "--save_model_as", "safetensors", "--save_precision", "bf16",
            "--network_module", "networks.lora_flux",
            "--network_dim", str(rank), "--network_alpha", str(rank),
            # unet-only: serving uses ComfyUI LoraLoaderModelOnly (spec P4), so
            # text-encoder deltas would be dead weight (and TE outputs are cached)
            "--network_train_unet_only",
            "--optimizer_type", "adamw8bit", "--learning_rate", "1e-4",
            "--max_train_steps", str(steps), "--seed", "42",
            "--mixed_precision", "bf16", "--sdpa", "--gradient_checkpointing",
            "--fp8_base", "--cache_latents_to_disk",
            "--cache_text_encoder_outputs", "--cache_text_encoder_outputs_to_disk",
            "--max_data_loader_n_workers", "2", "--persistent_data_loader_workers",
            "--timestep_sampling", "shift", "--discrete_flow_shift", "3.1582",
            "--model_prediction_type", "raw", "--guidance_scale", "1.0",
        ]
        cmd += shlex.split(os.environ.get("TRAINER_EXTRA_ARGS", ""))
        self._cmd = cmd
        self._adapter_path = out_dir / f"{out_name}.safetensors"

    def run(self, log_cb: LogCallback) -> Path:
        if self._cmd is None or self._adapter_path is None:
            raise RuntimeError("prepare() must be called before run()")
        log_cb("flux_lora: " + " ".join(self._cmd))
        proc = subprocess.Popen(
            self._cmd, cwd=str(self.sd_scripts),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1)
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            # tqdm redraws arrive as \r-separated chunks — keep the last state
            for part in filter(None, (p.strip() for p in line.split("\r"))):
                step = total = loss = None
                m = _PROGRESS_RE.search(part)
                if m:
                    step, total = int(m.group(1)), int(m.group(2))
                lm = _LOSS_RE.search(part)
                if lm:
                    try:
                        loss = float(lm.group(1))
                    except ValueError:
                        loss = None
                log_cb(part, step=step, total=total, loss=loss)
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"flux_train_network.py exited with code {rc}")
        if not self._adapter_path.is_file():
            raise RuntimeError(
                f"training reported success but adapter not found: {self._adapter_path}")
        log_cb(f"flux_lora: adapter ready at {self._adapter_path}")
        return self._adapter_path
