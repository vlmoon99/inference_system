#!/bin/sh
# ads-dropcache — keep the page cache from posing as used GPU memory (dgx-spark-2).
# On GB10 unified memory cudaMemGetInfo reports MemFree, not MemAvailable: after a big
# safetensors read the page cache looks "used" to ComfyUI, which then evicts a resident
# model to make room it actually has.
# No root needed: the cache that matters is the weight files', and `dd iflag=nocache
# count=0` (posix_fadvise DONTNEED) drops a file's clean pages as any user who can read
# it. Weights already loaded live in torch memory, not in these pages — nothing warm is lost.
# Runs every minute from the ads-dropcache user timer; acts when the gap > GAP_GB.
GAP_GB="${GAP_GB:-6}"
MODELS="${1:-$HOME/ComfyUI/models}"
gap=$(awk '/^MemFree:/{f=$2} /^MemAvailable:/{a=$2} END{print int((a-f)/1048576)}' /proc/meminfo)
[ "$gap" -gt "$GAP_GB" ] || exit 0
find -L "$MODELS" -type f -size +1M -exec dd if={} iflag=nocache count=0 status=none \;
echo "dropped weight page cache (gap was ${gap} GB, now $(awk '/^MemFree:/{f=$2} /^MemAvailable:/{a=$2} END{print int((a-f)/1048576)}' /proc/meminfo) GB)"
