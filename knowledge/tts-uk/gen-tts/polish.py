"""Voice polish — post-processing that takes the "synthetic" edge off TTS output.

Neural TTS sounds robotic for reasons that are only partly the model's:

  * it is bone dry. Real recorded speech always carries a room; zero
    reverberation is a cue nothing physical produced the sound. This is the
    single biggest win here.
  * sibilance is harsh and the top end is brittle (vocoder artefacts).
  * levels are flat and unproduced next to any real ad voiceover.
  * clips start and stop on a hard sample edge.

So: high-pass -> de-ess -> gentle compression -> presence/air EQ -> a short
room -> normalize -> edge fades. Every stage is numpy/scipy, no subprocess, so
this is unit-testable and adds milliseconds.

`ambience()` mixes a background bed UNDER the voice — instrumental/atmospheric
only, ducked by the voice's own envelope so it never fights the words. Beds are
generated procedurally (no assets to license or ship) or loaded from the
operator's data/music folder.

Everything is best-effort: any stage that fails returns the audio unchanged,
because a voiceover that ships slightly dry beats a 502.
"""

import logging
import math
import os

import numpy as np

log = logging.getLogger("gen-tts-polish")

# name -> knobs. "natural" is the default: audible as "recorded", not as "effected".
PRESETS: dict[str, dict] = {
    "off":       {},
    "natural":   {"hp": 75, "deess": 0.35, "deharsh": 0.30, "comp": 0.35,
                  "presence": 1.0,
                  "room": 0.16, "room_mix": 0.10, "target_db": -18.0},
    # warm/deep lean on _deharsh + _depth and DO NOT lift presence: the 1.2-5kHz
    # band they would brighten is the one carrying the rasp.
    "warm":      {"hp": 65, "deess": 0.55, "deharsh": 0.55, "comp": 0.45,
                  "warmth": 2.0, "tilt": -1.5, "depth": 0.35,
                  "room": 0.24, "room_mix": 0.16, "target_db": -18.0},
    "deep":      {"hp": 55, "deess": 0.55, "deharsh": 0.85, "comp": 0.40,
                  "warmth": 2.5, "tilt": -2.5, "depth": 0.85,
                  "room": 0.22, "room_mix": 0.14, "target_db": -18.0},
    "broadcast": {"hp": 85, "deess": 0.45, "deharsh": 0.25, "comp": 0.65,
                  "presence": 2.0,
                  "room": 0.10, "room_mix": 0.06, "target_db": -15.0},
    "intimate":  {"hp": 70, "deess": 0.30, "deharsh": 0.45, "comp": 0.55,
                  "warmth": 1.5, "depth": 0.30,
                  "room": 0.30, "room_mix": 0.20, "target_db": -19.0},
    # The male-voice smoothness chain (research: docs/research/
    # UKRAINIAN_VOICE_IMPROVEMENT.md §3b): cepstral de-buzz on the rasp band,
    # 3-band compression instead of the wideband one, gentle top-octave tame
    # for the 24kHz istftnet hash, and a low noise floor under the joiner's
    # inserted pauses.
    "male_smooth": {"hp": 70, "despectral": 0.4, "deess": 0.45, "deharsh": 0.55,
                    "mb_comp": {"xover": (160, 4000), "ratios": (2.0, 2.5, 3.0)},
                    "warmth": 1.5, "depth": 0.30, "lp24k": 10500,
                    "room": 0.20, "room_mix": 0.12,
                    "noise_floor_db": -63, "target_db": -18.0},
}
DEFAULT_PRESET = os.environ.get("TTS_POLISH", "natural")

# Background beds. `tone` shapes filtered noise; `file` pulls from data/music.
AMBIENCES: dict[str, dict] = {
    "none":   {},
    "room":   {"kind": "noise", "low": 40, "high": 700, "brown": True},
    "studio": {"kind": "noise", "low": 20, "high": 260, "brown": True},
    "rain":   {"kind": "noise", "low": 350, "high": 7000, "brown": False,
               "shimmer": 0.35},
    "cafe":   {"kind": "noise", "low": 90, "high": 1600, "brown": True,
               "shimmer": 0.5},
    "street": {"kind": "noise", "low": 30, "high": 900, "brown": True,
               "shimmer": 0.25},
}


def _sos(kind: str, cutoff, sr: int, order: int = 2):
    from scipy.signal import butter
    nyq = sr / 2.0
    if isinstance(cutoff, (list, tuple)):
        wn = [max(1e-4, min(0.999, c / nyq)) for c in cutoff]
    else:
        wn = max(1e-4, min(0.999, cutoff / nyq))
    return butter(order, wn, btype=kind, output="sos")


def _filt(x: np.ndarray, kind: str, cutoff, sr: int, order: int = 2) -> np.ndarray:
    from scipy.signal import sosfilt
    return sosfilt(_sos(kind, cutoff, sr, order), x).astype(np.float32)


def _envelope(x: np.ndarray, sr: int, attack_ms: float, release_ms: float) -> np.ndarray:
    """One-pole attack/release follower over |x| — the shared primitive behind
    the compressor, the de-esser and the ambience ducker."""
    a_att = math.exp(-1.0 / max(1.0, sr * attack_ms / 1000.0))
    a_rel = math.exp(-1.0 / max(1.0, sr * release_ms / 1000.0))
    mag = np.abs(x).astype(np.float64)
    env = np.empty_like(mag)
    prev = 0.0
    for i, v in enumerate(mag):
        coef = a_att if v > prev else a_rel
        prev = coef * prev + (1.0 - coef) * v
        env[i] = prev
    return env


def _envelope_fast(x: np.ndarray, sr: int, ms: float) -> np.ndarray:
    """Symmetric smoothing of |x| — same idea as _envelope but vectorised via a
    moving average, for the places where attack/release asymmetry does not
    matter (ducking, de-essing). Python-loop followers are far too slow on
    44.1kHz clips of ad length."""
    from scipy.signal import sosfilt
    n = max(1, int(sr * ms / 1000.0))
    mag = np.abs(x).astype(np.float32)
    # zero-phase-ish: two passes of a one-pole in opposite directions
    sos = _sos("low", max(1.0, sr / (2.0 * n)), sr, order=1)
    fwd = sosfilt(sos, mag)
    bwd = sosfilt(sos, fwd[::-1])[::-1]
    return np.maximum(bwd, 1e-9).astype(np.float32)


def _compress(x: np.ndarray, sr: int, amount: float) -> np.ndarray:
    """Downward compression, `amount` 0..1 -> ratio ~1.5:1..4:1 with makeup.
    Evens out the delivery so the read sits like a produced voiceover."""
    if amount <= 0:
        return x
    thresh_db = -24.0
    ratio = 1.0 + 3.0 * float(np.clip(amount, 0, 1))
    env = _envelope_fast(x, sr, 12.0)
    env_db = 20.0 * np.log10(env + 1e-9)
    over = np.maximum(0.0, env_db - thresh_db)
    gain_db = -over * (1.0 - 1.0 / ratio)
    makeup = 10 ** ((-thresh_db * (1.0 - 1.0 / ratio) * 0.45) / 20.0)
    return (x * (10 ** (gain_db / 20.0)) * makeup).astype(np.float32)


def _deess(x: np.ndarray, sr: int, amount: float) -> np.ndarray:
    """Split off 5kHz+ and duck it only where it is hot. Tames the hissy "с/ш/щ"
    that vocoders exaggerate — a top-end shelf alone would dull the whole read."""
    if amount <= 0 or sr <= 12000:
        return x
    hi = _filt(x, "high", 5000, sr, order=2)
    rest = x - hi
    env = _envelope_fast(hi, sr, 6.0)
    thresh = float(np.percentile(env, 96)) + 1e-6
    over = np.maximum(0.0, env - thresh) / (thresh + 1e-9)
    duck = 1.0 / (1.0 + over * (4.0 * float(np.clip(amount, 0, 1))))
    return (rest + hi * duck).astype(np.float32)


def _deharsh(x: np.ndarray, sr: int, amount: float) -> np.ndarray:
    """Dynamic EQ on the band where vocoder rasp lives — a de-esser moved down.

    Measured on radtts/mykyta output: harmonic-to-noise ratio is ~-6dB below
    1kHz but ~-13dB from 1.2-5kHz, i.e. the roughness you hear on vowels is
    noise riding on the harmonics up there. A static cut would dull the whole
    read, so this only pulls the band down where it is actually hot, exactly
    like a harshness tamer on a vocal bus.
    """
    if amount <= 0 or sr <= 12000:
        return x
    band = _filt(x, "band", (1200, min(5000, sr / 2 - 200)), sr, order=2)
    rest = x - band
    env = _envelope_fast(band, sr, 18.0)
    thresh = float(np.percentile(env, 75)) + 1e-6
    over = np.maximum(0.0, env - thresh) / (thresh + 1e-9)
    duck = 1.0 / (1.0 + over * (3.0 * float(np.clip(amount, 0, 1))))
    return (rest + band * duck).astype(np.float32)


def _shelf(x: np.ndarray, sr: int, freq: float, gain_db: float,
           kind: str) -> np.ndarray:
    """RBJ shelving biquad (Audio EQ Cookbook, S=1). Replaces the old
    filtered-copy blend, whose causal-Butterworth phase rotation measurably
    rippled the 200Hz-3kHz body of a male voice (-1.1dB dip at 328Hz)."""
    if abs(gain_db) < 0.01:
        return x
    from scipy.signal import sosfilt
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2.0 * math.pi * min(freq, sr * 0.475) / sr
    cosw, sinw = math.cos(w0), math.sin(w0)
    alpha = sinw / 2.0 * math.sqrt(2.0)
    sqA = math.sqrt(A)
    if kind == "low":
        b0 = A * ((A + 1) - (A - 1) * cosw + 2 * sqA * alpha)
        b1 = 2 * A * ((A - 1) - (A + 1) * cosw)
        b2 = A * ((A + 1) - (A - 1) * cosw - 2 * sqA * alpha)
        a0 = (A + 1) + (A - 1) * cosw + 2 * sqA * alpha
        a1 = -2 * ((A - 1) + (A + 1) * cosw)
        a2 = (A + 1) + (A - 1) * cosw - 2 * sqA * alpha
    else:
        b0 = A * ((A + 1) + (A - 1) * cosw + 2 * sqA * alpha)
        b1 = -2 * A * ((A - 1) + (A + 1) * cosw)
        b2 = A * ((A + 1) + (A - 1) * cosw - 2 * sqA * alpha)
        a0 = (A + 1) - (A - 1) * cosw + 2 * sqA * alpha
        a1 = 2 * ((A - 1) - (A + 1) * cosw)
        a2 = (A + 1) - (A - 1) * cosw - 2 * sqA * alpha
    sos = np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]])
    return sosfilt(sos, x).astype(np.float32)


def _mb_comp(x: np.ndarray, sr: int, cfg: dict) -> np.ndarray:
    """3-band compression tuned for male speech. Complementary split (mid is
    the exact residual, so low+mid+high == x at unity gain): LOW below the
    first crossover stops vowel boom pumping the presence band; HIGH above the
    second catches 4-5kHz sibilance the de-esser's 5kHz split misses. One
    makeup after summing (RMS restore) instead of per-band makeup."""
    from scipy.signal import sosfiltfilt
    lo_x, hi_x = cfg.get("xover", (160, 4000))
    r_lo, r_mid, r_hi = cfg.get("ratios", (2.0, 2.5, 3.0))
    low = sosfiltfilt(_sos("low", lo_x, sr, order=2), x).astype(np.float32)
    high = sosfiltfilt(_sos("high", min(hi_x, sr * 0.45), sr, order=2), x).astype(np.float32)
    mid = (x - low - high).astype(np.float32)

    def _band(b: np.ndarray, thresh_db: float, ratio: float, win_ms: float) -> np.ndarray:
        env = _envelope_fast(b, sr, win_ms)
        env_db = 20.0 * np.log10(env + 1e-9)
        over = np.maximum(0.0, env_db - thresh_db)
        return (b * (10 ** (-over * (1.0 - 1.0 / ratio) / 20.0))).astype(np.float32)

    y = _band(low, -22.0, r_lo, 25.0) + _band(mid, -24.0, r_mid, 12.0) \
        + _band(high, -28.0, r_hi, 4.0)
    rms_in = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-12)
    rms_out = float(np.sqrt(np.mean(np.square(y, dtype=np.float64))) + 1e-12)
    return (y * min(4.0, rms_in / rms_out)).astype(np.float32)


def _despectral(x: np.ndarray, sr: int, amount: float) -> np.ndarray:
    """Cepstral de-buzz — attacks the vocoder rasp MECHANISM (broadband noise
    riding the harmonics, measured ~-13dB HNR at 1.2-5kHz on radtts/mykyta)
    instead of just ducking its level. Per STFT frame, lifter quefrency bins
    20-60 — applied SYMMETRICALLY (bins n and N-n; asymmetric liftering halves
    the effect via implicit symmetrization). The pitch quefrency of a male
    voice (f0 110-130Hz) sits far above the lifter, so voicing is untouched;
    frames are gated so only rasp-heavy ones are treated, and everything below
    1kHz keeps its original spectrum."""
    if amount <= 0:
        return x
    from scipy.signal import istft, stft
    nper = 1024 if sr <= 32000 else 2048
    hop = nper // 4
    _f, _t, Z = stft(x, fs=sr, nperseg=nper, noverlap=nper - hop, window="hann")
    mag = np.abs(Z)
    logmag = np.log(mag + 1e-9)
    ceps = np.fft.irfft(logmag, n=nper, axis=0)
    lo, hi = 20, 60
    band_energy = np.sum(np.square(ceps[lo:hi]), axis=0)
    gate = band_energy > np.percentile(band_energy, 75.0)
    g = 1.0 - float(np.clip(amount, 0.0, 1.0))
    ceps_mod = ceps.copy()
    ceps_mod[lo:hi, gate] *= g
    ceps_mod[nper - hi:nper - lo, gate] *= g          # symmetric partner bins
    logmag_new = np.fft.rfft(ceps_mod, n=nper, axis=0).real
    keep = _f < 1000.0                                # body band stays original
    logmag_new[keep] = logmag[keep]
    Z_new = np.exp(logmag_new) * np.exp(1j * np.angle(Z))
    _t2, y = istft(Z_new, fs=sr, nperseg=nper, noverlap=nper - hop, window="hann")
    y = y[: len(x)].astype(np.float32)
    if len(y) < len(x):
        y = np.pad(y, (0, len(x) - len(y)))
    return y


def _noise_floor(x: np.ndarray, sr: int, floor_db: float) -> np.ndarray:
    """A just-audible fixed-seed noise bed at floor_db dBFS RMS. Digital-zero
    pauses (which the joiner now inserts) are their own robotic tell — nothing
    physical records silence that black."""
    rng = np.random.default_rng(23)
    nz = rng.standard_normal(len(x)).astype(np.float32)
    nz = _filt(nz, "band", (100, min(6000, sr / 2 - 100)), sr, order=2)
    rms = float(np.sqrt(np.mean(np.square(nz, dtype=np.float64))) + 1e-12)
    return (x + nz * (10 ** (floor_db / 20.0) / rms)).astype(np.float32)


def _depth(x: np.ndarray, sr: int, amount: float) -> np.ndarray:
    """Weight the voice toward its fundamental. The measured LTAS sits ~10dB
    lower at 60-200Hz than at 200-500Hz, which is why the read has no chest;
    a low shelf puts the body back while a gentle top tilt keeps the added
    weight from sounding boxy. Pitch itself is lowered in the MODEL
    (`depth` -> f0_mean), not resampled here — shifting pitch in DSP smears
    the formants and adds exactly the artefacts we are removing."""
    if amount <= 0:
        return x
    a = float(np.clip(amount, 0, 1))
    x = _shelf(x, sr, 190, 4.5 * a, "low")
    x = _shelf(x, sr, 5200, -3.0 * a, "high")
    return x


def _room(x: np.ndarray, sr: int, seconds: float, mix: float) -> np.ndarray:
    """Convolve with a synthetic room: exponentially decaying noise with a short
    pre-delay. Dryness is THE robotic tell, so this is the stage that matters
    most — kept subtle (mix ~0.1) so it reads as 'recorded', not 'in a hall'."""
    if seconds <= 0 or mix <= 0:
        return x
    from scipy.signal import fftconvolve
    n = max(8, int(sr * seconds))
    rng = np.random.default_rng(7)  # fixed: same room every render, no shimmer drift
    ir = rng.standard_normal(n).astype(np.float32)
    ir *= np.exp(-np.linspace(0.0, 6.0, n)).astype(np.float32)
    ir = _filt(ir, "low", min(7000, sr / 2 - 100), sr, order=2)
    ir = _filt(ir, "high", 180, sr, order=2)
    pre = np.zeros(int(sr * 0.012), dtype=np.float32)
    ir = np.concatenate([pre, ir])
    ir /= (np.sqrt(np.sum(ir ** 2)) + 1e-9)
    wet = fftconvolve(x, ir)[: len(x)].astype(np.float32)
    return ((1.0 - mix) * x + mix * wet).astype(np.float32)


def _normalize(x: np.ndarray, target_db: float) -> np.ndarray:
    """RMS-normalize to `target_db`, then hold true peak under -1 dBFS."""
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))) + 1e-12)
    x = (x * (10 ** (target_db / 20.0) / rms)).astype(np.float32)
    peak = float(np.max(np.abs(x)) + 1e-12)
    ceiling = 10 ** (-1.0 / 20.0)
    if peak > ceiling:
        x = (x * (ceiling / peak)).astype(np.float32)
    return x


def _edge_fade(x: np.ndarray, sr: int, ms: float = 12.0) -> np.ndarray:
    """A hard first sample is an audible click; ramp both ends."""
    n = min(len(x) // 2, max(1, int(sr * ms / 1000.0)))
    if n < 2:
        return x
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    x = x.copy()
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x


def polish(samples: np.ndarray, sr: int, preset: str | None = None) -> np.ndarray:
    """Run the smoothing chain. Unknown/'off' preset returns input untouched."""
    name = (preset or DEFAULT_PRESET or "off").lower()
    cfg = PRESETS.get(name)
    if not cfg:
        return samples
    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    if x.size < 64:
        return samples
    try:
        if cfg.get("hp"):
            x = _filt(x, "high", cfg["hp"], sr, order=2)
        x = _despectral(x, sr, cfg.get("despectral", 0.0))
        x = _deess(x, sr, cfg.get("deess", 0.0))
        # before compression: compressing first would pump the rasp up between
        # peaks, which is how a harsh vocal usually gets worse, not better
        x = _deharsh(x, sr, cfg.get("deharsh", 0.0))
        if cfg.get("mb_comp"):
            x = _mb_comp(x, sr, cfg["mb_comp"])
        else:
            x = _compress(x, sr, cfg.get("comp", 0.0))
        if cfg.get("warmth"):
            x = _shelf(x, sr, 220, cfg["warmth"], "low")
        if cfg.get("presence"):
            x = _shelf(x, sr, 3200, cfg["presence"], "high")
        if cfg.get("tilt"):
            x = _shelf(x, sr, 8000, cfg["tilt"], "high")
        x = _depth(x, sr, cfg.get("depth", 0.0))
        if cfg.get("lp24k") and sr == 24000:
            # tame the istftnet top-octave hash; 44.1k Vocos output skips it
            x = _filt(x, "low", cfg["lp24k"], sr, order=1)
        x = _room(x, sr, cfg.get("room", 0.0), cfg.get("room_mix", 0.0))
        x = _normalize(x, cfg.get("target_db", -18.0))
        if cfg.get("noise_floor_db"):
            x = _noise_floor(x, sr, cfg["noise_floor_db"])
        return _edge_fade(x, sr)
    except Exception as e:  # noqa: BLE001 — never fail a voiceover over cosmetics
        log.warning("polish %r failed (%s: %s) — returning dry audio",
                    name, type(e).__name__, e)
        return samples


def _music_dir() -> str:
    return os.environ.get(
        "SHORT_MUSIC_DIR",
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "music"))


def bed_names() -> list[str]:
    """Beds offered to the UI: the procedural ones plus every instrumental in
    data/music (prefixed "music:"). Cheap — a directory listing."""
    out = [n for n in AMBIENCES if n != "none"]
    try:
        for f in sorted(os.listdir(_music_dir())):
            if f.lower().endswith((".wav", ".mp3", ".m4a", ".ogg", ".flac")):
                out.append(f"music:{os.path.splitext(f)[0]}")
    except OSError:
        pass
    return out


def _noise_bed(cfg: dict, n: int, sr: int) -> np.ndarray:
    rng = np.random.default_rng(11)
    x = rng.standard_normal(n).astype(np.float32)
    if cfg.get("brown"):  # integrate white -> brown: weighted to the low end
        x = np.cumsum(x).astype(np.float32)
        x /= (np.max(np.abs(x)) + 1e-9)
    x = _filt(x, "band", (cfg.get("low", 60), cfg.get("high", 2000)), sr, order=2)
    if cfg.get("shimmer"):
        # slow amplitude drift so the bed breathes instead of sitting static
        t = np.linspace(0, n / sr, n, dtype=np.float32)
        lfo = 1.0 + cfg["shimmer"] * 0.5 * np.sin(2 * math.pi * 0.13 * t).astype(np.float32)
        x = x * lfo
    return (x / (np.max(np.abs(x)) + 1e-9)).astype(np.float32)


def _file_bed(stem: str, n: int, sr: int) -> np.ndarray | None:
    """Load an operator-supplied instrumental, resample crudely, loop/trim to n.
    Vocal tracks are the operator's responsibility — the product promise is
    'no singing under the voice', enforced by what is put in data/music."""
    import soundfile as sf
    for ext in (".wav", ".mp3", ".m4a", ".ogg", ".flac"):
        path = os.path.join(_music_dir(), stem + ext)
        if not os.path.isfile(path):
            continue
        data, file_sr = sf.read(path, dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1)
        if file_sr != sr and file_sr > 0:  # linear resample: a bed, not a master
            idx = np.linspace(0, len(data) - 1, int(len(data) * sr / file_sr))
            data = np.interp(idx, np.arange(len(data)), data).astype(np.float32)
        if len(data) == 0:
            return None
        reps = int(np.ceil(n / len(data)))
        return np.tile(data, reps)[:n].astype(np.float32)
    return None


def ambience(samples: np.ndarray, sr: int, name: str | None,
             level_db: float = -26.0) -> np.ndarray:
    """Mix a background bed under the voice, ducked by the voice's own envelope.

    level_db is the bed's level relative to the voice; the duck pulls it down
    another ~9dB wherever speech is present, so the words always sit on top.
    Adds a short lead-in/tail so the bed opens before the first word and closes
    after the last instead of switching on with it.
    """
    if not name or name.lower() in ("none", "off"):
        return samples
    voice = np.asarray(samples, dtype=np.float32).reshape(-1)
    try:
        lead = int(sr * 0.35)
        n = len(voice) + 2 * lead
        key = np.concatenate([np.zeros(lead, np.float32), voice,
                              np.zeros(lead, np.float32)])
        if name.lower().startswith("music:"):
            bed = _file_bed(name.split(":", 1)[1], n, sr)
            if bed is None:
                log.info("ambience %r not found in data/music — skipping", name)
                return samples
        else:
            cfg = AMBIENCES.get(name.lower())
            if not cfg:
                return samples
            bed = _noise_bed(cfg, n, sr)

        voice_rms = float(np.sqrt(np.mean(np.square(voice, dtype=np.float64))) + 1e-12)
        bed_rms = float(np.sqrt(np.mean(np.square(bed, dtype=np.float64))) + 1e-12)
        bed = bed * (voice_rms * 10 ** (level_db / 20.0) / bed_rms)

        env = _envelope_fast(key, sr, 120.0)
        env = env / (float(np.max(env)) + 1e-9)
        duck = 1.0 / (1.0 + 1.8 * env)           # ~-9dB under speech
        bed = (bed * duck).astype(np.float32)
        bed = _edge_fade(bed, sr, ms=300.0)      # bed opens/closes gently

        out = key + bed
        peak = float(np.max(np.abs(out)) + 1e-12)
        ceiling = 10 ** (-1.0 / 20.0)
        if peak > ceiling:
            out = out * (ceiling / peak)
        return out.astype(np.float32)
    except Exception as e:  # noqa: BLE001
        log.warning("ambience %r failed (%s: %s) — returning voice only",
                    name, type(e).__name__, e)
        return samples
