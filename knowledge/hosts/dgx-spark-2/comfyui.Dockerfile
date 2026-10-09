# ComfyUI for dgx-spark-2 — the reproducible version of dgx-spark's hand-built
# pd-comfyui:base. NGC is the only sane source of an sm_121 aarch64 torch; pip must
# never replace it (and NGC 25.09 has no ABI-matched torchaudio — see torchaudio_stub.py).
# Build context = the ComfyUI checkout (install.sh does this).
FROM nvcr.io/nvidia/pytorch:25.09-py3
COPY requirements.txt /tmp/comfy-req.txt
# custom-node deps too (gguf for ComfyUI-GGUF, color-matcher/mss/opencv for KJNodes) — pd-comfyui has them
COPY custom_nodes/ComfyUI-GGUF/requirements.txt /tmp/gguf-req.txt
COPY custom_nodes/ComfyUI-KJNodes/requirements.txt /tmp/kj-req.txt
RUN cat /tmp/comfy-req.txt /tmp/gguf-req.txt /tmp/kj-req.txt \
  | grep -viE '^(torch|torchvision|torchaudio)([<>=~ ]|$)' > /tmp/req.txt \
 && pip install --no-cache-dir -r /tmp/req.txt \
 && python -c "import comfy_kitchen, gguf, torch; print(torch.__version__)"
WORKDIR /comfy
