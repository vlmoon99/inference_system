"""Pluggable training backends for the tune executor (spec P5).

A Trainer turns a prepared image dataset into a LoRA adapter file. The ONLY
caller is inference/training/run_tune.py (itself invoked per-run by scripts/tune.sh);
nothing in the API process ever imports a trainer.

Contract (frozen):
    trainer = get_trainer(kind)              # lazy — never imports torch/toolkits at module load
    trainer.prepare(dataset_dir, params)     # validate env + write configs; raises TrainerUnavailable
    adapter_path = trainer.run(log_cb)       # blocking; streams progress via log_cb; returns the
                                             # trained .safetensors path

log_cb(line: str, step: int | None = None, total: int | None = None,
       loss: float | None = None) — every trainer output line goes through it;
step/total/loss are best-effort parsed progress for the training_runs.progress
column.

CI-safety: this package is importable with ONLY backend+services+dev deps
installed. Heavy work happens in a SEPARATE venv (.venv-train) via subprocess —
no torch import ever happens in-process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol

# log_cb(line, step=None, total=None, loss=None)
LogCallback = Callable[..., None]


class TrainerUnavailable(RuntimeError):
    """The training backend is not installed/configured on this machine.

    .recipe holds the exact operator instructions to make it available; the
    executor fails the run fast and surfaces the recipe in the run's error."""

    def __init__(self, reason: str, recipe: str):
        super().__init__(f"{reason}\n\nInstall recipe:\n{recipe}")
        self.reason = reason
        self.recipe = recipe


class Trainer(Protocol):
    """Interface every training backend implements."""

    kind: str

    def prepare(self, dataset_dir: Path, params: dict) -> None:
        """Validate the environment and write run configs.

        dataset_dir contains image files with sibling .txt captions (see
        inference/training/dataset.py). params is TrainingRun.params:
        {image_ids, steps, rank, trigger_word}. Raises TrainerUnavailable when
        the backend is not installed — before any model is stopped for longer
        than necessary."""
        ...

    def run(self, log_cb: LogCallback) -> Path:
        """Train. Blocking; may take hours. Returns the adapter .safetensors
        path. Raises on failure (log already streamed through log_cb)."""
        ...


def get_trainer(kind: str) -> Trainer:
    """Lazy factory — imports the backend module only when asked for it."""
    if kind == "flux_lora":
        from .flux_lora import FluxLoraTrainer  # local import: keeps CI light

        return FluxLoraTrainer()
    raise ValueError(f"unknown trainer kind: {kind!r} (known: flux_lora)")
