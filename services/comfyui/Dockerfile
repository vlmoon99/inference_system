# ComfyUI runtime for GB10 (sm_121): NGC PyTorch 25.09 base (pd-comfyui:base, built 2026-07; it exists only
# as an image, see knowledge/containers/pd-comfyui.inspect.json) + the exact packages ComfyUI v0.33.3
# needs on top. BASE differs per box (dgx-spark-2: ads-comfyui:v0.33.3, which already has them). The list = `docker diff` of the last working container (2026-10-09). --no-deps so pip never
# touches the NGC torch. The ComfyUI code + models are bind-mounted from ~/ComfyUI.
ARG BASE=pd-comfyui:base
FROM ${BASE}
RUN pip install --no-cache-dir --no-deps \
      comfy_aimdo==0.4.13 comfy_kitchen==0.2.31 comfyui_embedded_docs==0.5.9 comfyui_frontend_package==1.49.6 \
      comfyui_workflow_templates==0.11.44 comfyui_workflow_templates_core==0.3.315 \
      comfyui_workflow_templates_json==0.1.50 comfyui_workflow_templates_media_assets_01==0.1.30 \
      color_matcher==0.6.0 ddt==1.7.2 docutils==0.23 mss==10.2.0 opencv_python_headless==5.0.0.93 \
      peft==0.21.0 pyloudnorm==0.2.0
