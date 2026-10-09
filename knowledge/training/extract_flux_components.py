"""One-time extraction of kohya-format FLUX components from the local combined
ComfyUI checkpoint (flux1-dev-fp8.safetensors, ~17GB) — NO downloads.

kohya's flux_train_network.py wants four separate files (--pretrained_model_name_or_path,
--clip_l, --t5xxl, --ae); the Comfy-Org single-file checkpoint holds all four
under prefixes. This splits it:

    model.diffusion_model.*              -> flux1-dev-fp8-unet.safetensors  (prefix stripped)
    text_encoders.clip_l.transformer.*   -> clip_l.safetensors              ("text_model.*" keys)
    text_encoders.t5xxl.transformer.*    -> t5xxl-fp8.safetensors           ("encoder.*"/"shared.*" keys)
    vae.*                                -> ae.safetensors                  (prefix stripped)

Run with the TRAINING venv (needs torch + safetensors; NOT part of backend deps):

    .venv-train/bin/python -m inference.training.extract_flux_components \
        [--checkpoint /home/server/ComfyUI/models/checkpoints/flux1-dev-fp8.safetensors] \
        [--out-dir data/models/flux-train]

Idempotent: existing non-empty outputs are kept (use --force to rewrite).
"""

from __future__ import annotations

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT = "/home/server/ComfyUI/models/checkpoints/flux1-dev-fp8.safetensors"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "models" / "flux-train"

# output filename -> (key prefix in the combined checkpoint, drop-keys)
COMPONENTS = {
    "flux1-dev-fp8-unet.safetensors": ("model.diffusion_model.", ()),
    "clip_l.safetensors": ("text_encoders.clip_l.transformer.", ("logit_scale",)),
    "t5xxl-fp8.safetensors": ("text_encoders.t5xxl.transformer.", ("logit_scale",)),
    "ae.safetensors": ("vae.", ()),
}


def extract(checkpoint: Path, out_dir: Path, force: bool = False) -> list[Path]:
    # heavy imports stay inside the function: this module is importable in CI
    import torch  # noqa: F401  (safetensors needs it for fp8 dtypes)
    from safetensors import safe_open
    from safetensors.torch import save_file

    if not checkpoint.is_file():
        raise FileNotFoundError(f"combined FLUX checkpoint not found: {checkpoint}")
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    with safe_open(str(checkpoint), framework="pt", device="cpu") as f:
        all_keys = list(f.keys())
        for fname, (prefix, drop) in COMPONENTS.items():
            dest = out_dir / fname
            if dest.is_file() and dest.stat().st_size > 0 and not force:
                print(f"keep existing {dest}")
                written.append(dest)
                continue
            keys = [k for k in all_keys
                    if k.startswith(prefix) and not any(d in k for d in drop)]
            if not keys:
                raise KeyError(
                    f"no keys with prefix {prefix!r} in {checkpoint} — not a "
                    "Comfy-Org combined FLUX checkpoint?")
            print(f"extracting {len(keys):4d} tensors -> {dest}")
            tensors = {k[len(prefix):]: f.get_tensor(k) for k in keys}
            tmp = dest.with_suffix(".part")
            save_file(tensors, str(tmp))
            tmp.replace(dest)
            written.append(dest)
            del tensors
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--force", action="store_true", help="rewrite existing outputs")
    args = ap.parse_args()
    for p in extract(Path(args.checkpoint), Path(args.out_dir), force=args.force):
        print(f"  ready: {p} ({p.stat().st_size / 1e9:.2f} GB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
