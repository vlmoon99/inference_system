"""Deterministic chunk joiner — how per-sentence TTS takes become one read.

Engines synthesize sentence-ish chunks independently; gluing them with a bare
concat is the loudest robotic tell: zero inter-sentence silence ("machine-gun"
pacing), clicks at the seams, level jumps between takes. This module replaces
the concat with a produced edit, all pure numpy and pure functions of
(text, audio) — reproducible, unit-testable, no RNG beyond fixed-seed
generators keyed by slot index.

Per chunk: trim leading/trailing silence -> short cosine edge fades ->
punctuation-scaled pause -> RMS match toward the running median (clamped).
Optionally a quiet synthetic breath is laid into longer pauses. Per-sentence
speed offsets come from a hash of the sentence text, so the same script always
renders identically while adjacent sentences stop marching in lock-step.
"""

import math
import zlib

import numpy as np

# Pause (seconds, at speed 1.0) inserted AFTER a chunk, by its final punctuation.
PAUSES = {".": 0.35, "?": 0.35, "!": 0.35, "…": 0.35,
          ":": 0.25, ";": 0.25, ",": 0.15, "-": 0.15}
DEFAULT_PAUSE = 0.25          # chunk ended without punctuation (length-cap split)
PARAGRAPH_PAUSE = 0.6

TRIM_DB = -50.0               # silence threshold for edge trimming
FADE_MS = 12.0                # cosine edge fade per chunk
RMS_CLAMP_DB = 3.0            # max level-match gain per chunk
BREATH_MIN_PAUSE_S = 0.30     # breaths only in pauses at least this long
BREATH_NEXT_MIN_CHARS = 80    # ...and only before a substantial next sentence
BREATH_DB = -32.0             # breath level relative to the read's voice RMS


def speed_offset(part: str, index: int, count: int) -> float:
    """Multiplicative speed nudge for sentence `index` of `count`. First
    sentence opens a touch quicker, the last lands slower (wins for a
    single-sentence read), middles vary by a hash of their own text — a
    fixed schedule, not randomness."""
    if count > 0 and index == count - 1:
        return -0.04
    if index == 0:
        return +0.02
    return ((zlib.crc32(part.encode("utf-8")) % 7) - 3) * 0.005


def pause_after(part: str, speed: float = 1.0, paragraph: bool = False) -> float:
    """Seconds of silence after a chunk, scaled so a slowed read pauses longer."""
    base = PARAGRAPH_PAUSE if paragraph else \
        PAUSES.get(part.rstrip()[-1:] or "", DEFAULT_PAUSE)
    return base / max(0.5, min(2.0, speed or 1.0))


def trim_silence(x: np.ndarray, sr: int, thresh_db: float = TRIM_DB) -> np.ndarray:
    """Cut leading/trailing stretches whose 10ms-window RMS sits under the
    threshold. Engines emit variable edge silence; without trimming, inserted
    pauses double up and pacing drifts per take."""
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    win = max(1, int(sr * 0.010))
    n_win = len(x) // win
    if n_win < 2:
        return x
    rms = np.sqrt(np.mean(np.square(
        x[: n_win * win].reshape(n_win, win), dtype=np.float64), axis=1))
    lim = 10.0 ** (thresh_db / 20.0)
    loud = np.flatnonzero(rms > lim)
    if loud.size == 0:
        return x
    start = loud[0] * win
    end = min(len(x), (loud[-1] + 1) * win)
    return x[start:end]


def edge_fade(x: np.ndarray, sr: int, ms: float = FADE_MS) -> np.ndarray:
    """Raised-cosine ramps on both ends — a hard first/last sample is a click."""
    n = min(len(x) // 2, max(1, int(sr * ms / 1000.0)))
    if n < 2:
        return x
    t = np.linspace(0.0, math.pi / 2.0, n, dtype=np.float32)
    x = np.asarray(x, dtype=np.float32).copy()
    x[:n] *= np.sin(t)
    x[-n:] *= np.cos(t)
    return x


def _breath(sr: int, slot: int, voice_rms: float) -> np.ndarray:
    """A 300ms synthetic inhale: seeded noise, 400-2500Hz, raised-cosine
    120ms-attack/180ms-decay envelope, well under the voice. Seed varies by
    slot so consecutive breaths are not the same waveform."""
    from scipy.signal import butter, sosfilt
    n = int(sr * 0.300)
    rng = np.random.default_rng(31 + slot)
    b = rng.standard_normal(n).astype(np.float32)
    nyq = sr / 2.0
    sos = butter(2, [400.0 / nyq, min(2500.0, nyq - 1.0) / nyq], btype="band",
                 output="sos")
    b = sosfilt(sos, b).astype(np.float32)
    na = int(sr * 0.120)
    env = np.ones(n, dtype=np.float32)
    env[:na] = 0.5 - 0.5 * np.cos(np.linspace(0, math.pi, na, dtype=np.float32))
    env[na:] = 0.5 + 0.5 * np.cos(np.linspace(0, math.pi, n - na, dtype=np.float32))
    b *= env
    rms = float(np.sqrt(np.mean(np.square(b, dtype=np.float64))) + 1e-12)
    return b * (voice_rms * 10.0 ** (BREATH_DB / 20.0) / rms)


def join_chunks(chunks: list[tuple[np.ndarray, str]], sr: int,
                speed: float = 1.0, breaths: bool = True) -> np.ndarray:
    """Glue per-sentence takes into one read.

    `chunks` is [(samples, source_text), ...] in order; `source_text` supplies
    the final punctuation for the pause and the length cue for breaths.
    """
    if not chunks:
        return np.zeros(1, dtype=np.float32)
    takes = [edge_fade(trim_silence(np.asarray(c, dtype=np.float32).reshape(-1), sr), sr)
             for c, _t in chunks]
    # level-match toward the running median RMS, gently
    rmss = [float(np.sqrt(np.mean(np.square(t, dtype=np.float64))) + 1e-12)
            for t in takes]
    lim = 10.0 ** (RMS_CLAMP_DB / 20.0)
    for i, t in enumerate(takes):
        ref = float(np.median(rmss[: i + 1]))
        g = min(lim, max(1.0 / lim, ref / rmss[i]))
        takes[i] = (t * g).astype(np.float32)
    voice_rms = float(np.median([r for r in rmss])) or 1e-9

    out: list[np.ndarray] = []
    for i, (take, (_c, text)) in enumerate(zip(takes, chunks)):
        out.append(take)
        if i == len(takes) - 1:
            break  # no appended tail pause — polish handles the ending
        gap_s = pause_after(text, speed)
        gap = np.zeros(int(sr * gap_s), dtype=np.float32)
        nxt_text = chunks[i + 1][1]
        if (breaths and i > 0 and i % 2 == 1 and gap_s >= BREATH_MIN_PAUSE_S
                and len(nxt_text) >= BREATH_NEXT_MIN_CHARS):
            br = _breath(sr, i, voice_rms)
            m = min(len(br), len(gap))
            gap[-m:] += br[-m:]
        out.append(gap)
    return np.concatenate(out).astype(np.float32)
