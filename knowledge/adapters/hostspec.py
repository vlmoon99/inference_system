"""host.yaml schema (pydantic) + validator for every inference/hosts/*/host.yaml.

    python -m inference.hostspec            # validate all profiles, exit 1 on error
    python -m inference.hostspec dgx-spark  # print one, resolved

A profile is documentation that CI keeps honest: it says what runs where on a
machine class, which ports the worker dials, and whether services are owned by
this repo or merely referenced (a shared tier other projects use too).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

HOSTS_DIR = Path(__file__).resolve().parent / "hosts"
Kind = Literal["llm", "image", "video", "tts", "embed", "gate", "comfyui", "postgres"]


class Placement(BaseModel):
    engine: str                      # vllm | llama.cpp | mlx-lm | comfy | diffusers | mlx | kokoro | radtts | piper | ...
    port: int = Field(ge=1, le=65535)
    device: str = "cpu"              # cuda | cuda:0 | mps | xpu | cpu
    model: str = ""
    mem_gb: float | None = None
    shared: bool = False             # referenced (started if down, never stopped) rather than owned
    notes: str = ""


class WorkerSpec(BaseModel):
    concurrency: int = Field(default=2, ge=1, le=32)
    claims: Literal["auto", "all"] | list[str] = "auto"


class HostProfile(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    vendor: Literal["nvidia", "apple", "intel", "amd", "cpu"]
    memory_gb: float = Field(gt=0)
    unified_memory: bool = False
    placement: dict[Kind, Placement]
    worker: WorkerSpec = WorkerSpec()
    status: Literal["built", "planned"] = "built"
    description: str = ""

    @field_validator("placement")
    @classmethod
    def _ports_unique(cls, v: dict[Kind, Placement]) -> dict[Kind, Placement]:
        ports = [p.port for p in v.values()]
        if len(ports) != len(set(ports)):
            raise ValueError(f"duplicate ports in placement: {sorted(ports)}")
        return v

    def service_urls(self, host: str = "localhost") -> dict[str, str]:
        """What a worker on this box puts in its env for the four contract kinds."""
        out = {}
        for kind, env in (("llm", "LLM_BASE_URL"), ("image", "GEN_IMAGE_URL"),
                          ("video", "GEN_VIDEO_URL"), ("tts", "GEN_TTS_URL")):
            if kind in self.placement:
                suffix = "/v1" if kind == "llm" else ""
                out[env] = f"http://{host}:{self.placement[kind].port}{suffix}"
        return out


def load(name: str) -> HostProfile:
    path = HOSTS_DIR / name / "host.yaml"
    return HostProfile.model_validate(yaml.safe_load(path.read_text()))


def all_profiles() -> dict[str, HostProfile]:
    out = {}
    for d in sorted(HOSTS_DIR.iterdir()):
        if d.name.startswith("_") or not (d / "host.yaml").exists():
            continue
        out[d.name] = load(d.name)
        if out[d.name].name != d.name:
            raise ValueError(f"{d}/host.yaml: name {out[d.name].name!r} != folder {d.name!r}")
    return out


def main(argv: list[str]) -> int:
    if argv:
        p = load(argv[0])
        print(p.model_dump_json(indent=2))
        print("\n# worker env on this box:")
        for k, v in p.service_urls().items():
            print(f"{k}={v}")
        return 0
    try:
        profiles = all_profiles()
    except Exception as e:  # noqa: BLE001 — report and fail
        print(f"host.yaml invalid: {e}", file=sys.stderr)
        return 1
    for name, p in profiles.items():
        roles = ", ".join(f"{k}:{v.engine}@{v.device}:{v.port}" for k, v in p.placement.items())
        print(f"{name:12s} {p.vendor:6s} {p.memory_gb:>6.0f} GB  {p.status:8s} {roles}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
