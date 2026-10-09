"""Per-run training dataset builder (spec P5 step 5).

Copies a training run's library images into a flat directory and writes one
sibling .txt caption per image: "<trigger_word>, <label>" (or a generic caption
when the library row has no label). Kohya-style trainers read exactly this
layout; ai-toolkit does too.

Pure stdlib — safe to import anywhere (tests, CI).
"""

from __future__ import annotations

import shutil
from pathlib import Path

# extensions the FLUX trainers accept as images
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def caption_for(trigger_word: str, label: str | None) -> str:
    label = (label or "").strip()
    if label:
        return f"{trigger_word}, {label}"
    return f"{trigger_word}, product photo"


def build_dataset(
    images: list[tuple[Path, str | None]],
    out_dir: Path,
    trigger_word: str,
    log=print,
) -> int:
    """Copy (image, label) pairs into out_dir with caption sidecars.

    Missing files and unsupported extensions are skipped WITH a log line (the
    library row may outlive its file); raises ValueError when nothing usable
    remains — the executor fails the run rather than training on air.
    Returns the number of images written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for src, label in images:
        src = Path(src)
        ext = src.suffix.lower()
        if not src.is_file():
            log(f"dataset: SKIP missing file {src}")
            continue
        if ext not in IMAGE_EXTS:
            log(f"dataset: SKIP unsupported extension {src.name!r}")
            continue
        stem = f"img_{n:03d}"
        shutil.copy2(src, out_dir / f"{stem}{ext}")
        (out_dir / f"{stem}.txt").write_text(
            caption_for(trigger_word, label) + "\n", encoding="utf-8")
        n += 1
    if n == 0:
        raise ValueError(
            f"no usable training images (of {len(images)} library rows) — "
            "were the library files deleted from disk?")
    log(f"dataset: {n} image(s) + captions in {out_dir}")
    return n
