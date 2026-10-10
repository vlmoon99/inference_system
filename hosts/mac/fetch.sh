#!/bin/bash
# Download the three model packs this host serves into MODELS_DIR, file by file with curl.
# (The hf CLI stalled at 0 bytes on a slow line on 2026-10-10; plain HTTP with resume did not.)
# Safe to run again: finished files are skipped, a partial file continues where it stopped.
set -uo pipefail
MODELS_DIR=${MODELS_DIR:-$HOME/.cache/inf-mac/models}
REPOS=${*:-"Youssofal/Qwen3.5-4B-MTPLX-Optimized-Speed mlx-community/Qwen3-Embedding-0.6B-4bit-DWQ mflux-community/flux2-klein-4b-mflux-q4"}
for repo in $REPOS; do
  dir="$MODELS_DIR/${repo#*/}"; mkdir -p "$dir"
  curl -sf -m 60 "https://huggingface.co/api/models/$repo/tree/main?recursive=1" -o "$dir/.tree.json" || { echo "FAILED to list $repo"; exit 1; }
  python3 - "$dir/.tree.json" > "$dir/.files" <<'PY'
import json, sys
for f in json.load(open(sys.argv[1])):
    if f.get("type") == "file":
        print(f["size"], f["path"], sep="\t")
PY
  while IFS=$'\t' read -r size path; do
    out="$dir/$path"; mkdir -p "$(dirname "$out")"
    have=$(stat -f %z "$out" 2>/dev/null || echo 0)
    [ "$have" = "$size" ] && continue
    for try in $(seq 30); do
      curl -sfL -C - --retry 5 --retry-delay 5 -m 7200 -o "$out" "https://huggingface.co/$repo/resolve/main/$path"
      [ "$(stat -f %z "$out" 2>/dev/null || echo 0)" = "$size" ] && break
      sleep 5
    done
    [ "$(stat -f %z "$out" 2>/dev/null || echo 0)" = "$size" ] || { echo "FAILED $repo/$path"; exit 1; }
  done < "$dir/.files"
  echo "done $repo → $dir ($(du -sh "$dir" | cut -f1))"
done
