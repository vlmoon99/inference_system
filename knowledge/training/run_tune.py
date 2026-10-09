"""Per-run training driver — the piece of scripts/tune.sh that touches the DB.

Invoked by tune.sh (which owns the gate / drain / service stop-start around it):

    .venv/bin/python -m inference.training.run_tune --list-queued
        -> one queued training-run id per line (oldest first), exit 0
    .venv/bin/python -m inference.training.run_tune --run-id <uuid> --log-file logs/tune-<uuid>.log
        -> executes ONE run: status transitions queued -> preparing -> training
           -> installing -> evaluating -> done (or failed), dataset build from
           the run's library images, trainer invocation, adapter install
           (backend/scripts/install_adapter.py), golden_run job enqueue.
           Exit 0 on success, 1 on failure (tune.sh continues with other runs).

DB access reuses the backend's own modules (models, flags, install_adapter) —
DATABASE_URL env (or backend default) decides the target, same as the API.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from ads.core.config import settings  # noqa: E402
from ads.core.db import SessionLocal  # noqa: E402
from ads.core.models import Job, LibraryAsset, TrainingRun  # noqa: E402
from scripts.install_adapter import install_adapter  # noqa: E402

from inference.training import TrainerUnavailable, get_trainer  # noqa: E402
from inference.training.dataset import build_dataset  # noqa: E402

PROGRESS_WRITE_INTERVAL_S = 5.0
# Mirrors the POST /v1/training rule (backend/app/routers/training.py). Library
# rows/files can vanish between queueing and the nightly run, so the minimum is
# re-checked at execution time — never silently train on a smaller set.
MIN_TRAINING_IMAGES = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RunLog:
    """Append-to-file logger with timestamps; also mirrors to stdout."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def __call__(self, line: str) -> None:
        stamped = f"[{_now().strftime('%H:%M:%S')}] {line}"
        self._fh.write(stamped + "\n")
        self._fh.flush()
        print(stamped, flush=True)

    def close(self) -> None:
        self._fh.close()


async def _set_run(run_id: uuid.UUID, **fields) -> None:
    async with SessionLocal() as s:
        run = await s.get(TrainingRun, run_id)
        if run is None:
            raise LookupError(f"training run {run_id} vanished")
        for k, v in fields.items():
            setattr(run, k, v)
        await s.commit()


async def list_queued() -> list[uuid.UUID]:
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(TrainingRun.id).where(TrainingRun.status == "queued")
            .order_by(TrainingRun.created_at))).scalars().all()
    return list(rows)


async def _load_run(run_id: uuid.UUID) -> tuple[TrainingRun, list[tuple[Path, str | None]]]:
    """The run row + its library images resolved to absolute paths."""
    async with SessionLocal() as s:
        run = await s.get(TrainingRun, run_id)
        if run is None:
            raise LookupError(f"training run {run_id} not found")
        image_ids = [uuid.UUID(i) for i in (run.params or {}).get("image_ids", [])]
        rows = (await s.execute(
            select(LibraryAsset).where(LibraryAsset.id.in_(image_ids),
                                       LibraryAsset.app_id == run.app_id))).scalars().all()
        by_id = {r.id: r for r in rows}
        # preserve the order the client picked
        images = [(settings.assets_dir / by_id[i].path, by_id[i].label)
                  for i in image_ids if i in by_id]
        s.expunge(run)
    return run, images


async def _enqueue_golden_run(app_id: uuid.UUID, training_run_id: uuid.UUID) -> uuid.UUID:
    """Queue a golden_run job for the app (spec P5 step 7). The queue worker
    claims nothing while the training gate is set, so enqueueing before the
    stack restart is safe — it runs once tune.sh clears the flag."""
    job = Job(id=uuid.uuid4(), app_id=app_id, type="golden_run", status="queued",
              params={"training_run_id": str(training_run_id),
                      "trigger": "post_training"})
    async with SessionLocal() as s:
        s.add(job)
        await s.commit()
    return job.id


async def execute_run(run_id: uuid.UUID, log_file: Path,
                      trainer_factory=get_trainer) -> int:
    """Drive one training run end to end. Returns the process exit code."""
    log = RunLog(log_file)
    try:
        run, images = await _load_run(run_id)
        if run.status != "queued":
            log(f"run {run_id} is '{run.status}', not 'queued' — nothing to do")
            return 1

        params = run.params or {}
        trigger = params.get("trigger_word") or f"style_{run.app_id.hex[:8]}"
        log(f"run {run_id}: app={run.app_id} kind={run.kind} "
            f"steps={params.get('steps')} rank={params.get('rank')} trigger={trigger!r}")

        # queued -> preparing
        await _set_run(run_id, status="preparing", started_at=_now(),
                       log_path=str(log_file))
        run_dir = REPO_ROOT / "data" / "training" / str(run_id)
        dataset_dir = run_dir / "dataset"
        usable = [(p, lbl) for p, lbl in images if p.is_file()]
        if len(usable) < MIN_TRAINING_IMAGES:
            raise ValueError(
                f"only {len(usable)} of {len(params.get('image_ids', []))} queued "
                f"training image(s) still usable (minimum {MIN_TRAINING_IMAGES}) — "
                "library rows/files were removed after queueing; re-queue the run "
                "with a full image set")
        n = build_dataset(usable, dataset_dir, trigger, log=log)
        log(f"dataset built: {n} image(s)")

        trainer = trainer_factory(run.kind)
        trainer.prepare(dataset_dir, params)

        # preparing -> training (with throttled progress writes)
        await _set_run(run_id, status="training")
        last_write = 0.0
        latest: dict = {}
        loop = asyncio.get_running_loop()

        def log_cb(line: str, step=None, total=None, loss=None) -> None:
            log(line)
            nonlocal last_write
            if step is not None:
                latest.update({"step": step, "total": total})
                if loss is not None:
                    latest["loss"] = loss
                now = time.monotonic()
                if now - last_write >= PROGRESS_WRITE_INTERVAL_S:
                    last_write = now
                    fut = asyncio.run_coroutine_threadsafe(
                        _set_run(run_id, progress=dict(latest)), loop)
                    fut.result(timeout=30)

        adapter_path = await asyncio.to_thread(trainer.run, log_cb)
        if latest:
            await _set_run(run_id, progress=dict(latest))

        # training -> installing
        await _set_run(run_id, status="installing")
        adapter = await install_adapter(
            app_id=run.app_id, file=Path(adapter_path),
            name=f"{trigger}-{_now().strftime('%Y%m%d')}",
            training_run_id=run_id, activate=True)
        log(f"adapter installed: id={adapter.id} comfy_name={adapter.comfy_name} "
            f"(activated; siblings deactivated)")

        # installing -> evaluating -> done (spec: just enqueue golden_run + mark done)
        await _set_run(run_id, status="evaluating")
        job_id = await _enqueue_golden_run(run.app_id, run_id)
        log(f"golden_run job enqueued: {job_id} (runs after the stack restarts)")

        await _set_run(run_id, status="done", finished_at=_now(), error=None)
        log(f"run {run_id}: DONE")
        return 0

    except TrainerUnavailable as e:
        log(f"TRAINER UNAVAILABLE: {e.reason}")
        for line in e.recipe.splitlines():
            log(f"  {line}")
        await _set_run(run_id, status="failed", finished_at=_now(), error=str(e))
        return 1
    except Exception as e:  # noqa: BLE001 — the run row carries the failure
        err = f"{type(e).__name__}: {e}"
        log(f"FAILED: {err}")
        try:
            await _set_run(run_id, status="failed", finished_at=_now(), error=err)
        except Exception as e2:  # noqa: BLE001
            log(f"could not mark run failed: {e2}")
        return 1
    finally:
        log.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list-queued", action="store_true",
                   help="print queued run ids, oldest first")
    g.add_argument("--run-id", help="execute this queued run")
    ap.add_argument("--log-file", default=None,
                    help="log destination (default logs/tune-<run_id>.log)")
    args = ap.parse_args()

    if args.list_queued:
        for rid in asyncio.run(list_queued()):
            print(rid)
        return 0

    run_id = uuid.UUID(args.run_id)
    log_file = Path(args.log_file) if args.log_file else (
        REPO_ROOT / "logs" / f"tune-{run_id}.log")
    return asyncio.run(execute_run(run_id, log_file))


if __name__ == "__main__":
    raise SystemExit(main())
