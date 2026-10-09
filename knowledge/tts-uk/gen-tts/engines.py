"""Engine registry for uk/ru TTS (contract C3). One class per engine with
load()/synthesize(); get_engine(lang) resolves env config (TTS_ENGINE_UK /
TTS_ENGINE_RU, default piper) and falls back down the chain to Piper — the
never-fails baseline — with one log line when a configured engine's libs or
models are absent. "f5" is a real F5-TTS voice-cloning engine (checkpoint +
vocab.txt in F5_<LANG>_MODEL_DIR, bundled reference clips in F5_REFS_DIR);
"styletts2" is StyleTTS2-Ukrainian (patriotyk's MIT models, uk only in practice).
"""

import glob
import importlib
import logging
import os
import re
import threading
import unicodedata
import zlib

import numpy as np

try:
    import joiner
except ImportError:  # loaded by file path (tests) — resolve next to this file
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "joiner", os.path.join(os.path.dirname(os.path.abspath(__file__)), "joiner.py"))
    joiner = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(joiner)

log = logging.getLogger("gen-tts-engines")


def split_parts(text: str, min_len: int = 20, max_len: int = 180) -> list[str]:
    """Sentence-ish chunks for per-sentence synthesis. Tiny fragments group with
    the previous part (as in patriotyk's Space); parts longer than `max_len`
    split again at the comma/dash nearest their midpoint (fallback: nearest
    space) — both RadTTS and StyleTTS2 rush and garble on very long inputs."""
    text = re.sub(r"(\w+[^.,!:?\-])\n", r"\1. ", text).replace("\n", " ")
    parts = [""]
    last = len(text) - 1
    for i, ch in enumerate(text):
        parts[-1] += ch
        if ch in ".?!:…" and i < last and text[i + 1] == " " and len(parts[-1]) > min_len:
            parts.append("")
    out: list[str] = []
    for p in (s.strip() for s in parts):
        if not p:
            continue
        stack = [p]
        while stack:
            s = stack.pop(0)
            if len(s) <= max_len:
                out.append(s)
                continue
            mid = len(s) // 2
            cut = -1
            for m in re.finditer(r"[,—;] |, ", s):
                if cut == -1 or abs(m.end() - mid) < abs(cut - mid):
                    cut = m.end()
            if cut == -1:
                sp = [m.end() for m in re.finditer(r" ", s)]
                cut = min(sp, key=lambda c: abs(c - mid)) if sp else -1
            if cut <= 0 or cut >= len(s):
                out.append(s)
            else:
                stack = [s[:cut].strip(), s[cut:].strip()] + stack
    return [p for p in out if p]

_MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "models")
PIPER_DIR = os.environ.get("PIPER_VOICES_DIR", os.path.join(_MODELS_DIR, "piper"))
PIPER_VOICES = {"ru": "ru_RU-ruslan-medium.onnx", "uk": "uk_UA-ukrainian_tts-medium.onnx"}

_libs_ok: set[str] = set()  # cached successful import probes (keeps /health cheap)


def _probe_libs(*mods: str) -> bool:
    """True when every module imports. Successes are cached (the modules then sit in
    sys.modules anyway); failures are NOT cached — a failed probe is cheap and an
    install appearing later must flip the result."""
    key = ",".join(mods)
    if key in _libs_ok:
        return True
    try:
        for m in mods:
            importlib.import_module(m)
    except ImportError:
        return False
    _libs_ok.add(key)
    return True


class TTSEngine:
    """synthesize() returns (samples float32 [-1,1], sample_rate, voice_reported).
    After synthesize() returns, `produced_by` names the engine that actually made
    the audio (differs from `name` when a request degraded to Piper mid-flight)."""

    name = "base"

    def __init__(self, lang: str):
        self.lang = lang
        self.produced_by = self.name
        self._load_lock = threading.Lock()  # lazy load once under concurrent requests

    def available(self) -> bool:
        """Cheap check (imports/files) — no model weights loaded."""
        return False

    def load(self) -> None:
        """Load model weights; idempotent. Raises when assets are missing."""
        raise NotImplementedError

    def synthesize(self, text: str, voice: str | None,
                   ref_audio_path: str | None = None,
                   speed: float | None = None, **_kw) -> tuple[np.ndarray, int, str]:
        """speed: rate multiplier honored by engines that support it (styletts2);
        others ignore it. **_kw carries engine-specific knobs (e.g. radtts's
        `expressiveness`) — an engine that does not know one ignores it, so the
        caller never has to ask which engine it is talking to."""
        raise NotImplementedError


class PiperEngine(TTSEngine):
    name = "piper"

    def __init__(self, lang: str):
        super().__init__(lang)
        self._voice = None

    def _model_path(self) -> str:
        return os.path.join(PIPER_DIR, PIPER_VOICES[self.lang])

    def available(self) -> bool:
        return self.lang in PIPER_VOICES and os.path.isfile(self._model_path())

    def load(self) -> None:
        with self._load_lock:
            if self._voice is None:
                from piper import PiperVoice
                self._voice = PiperVoice.load(self._model_path())

    def synthesize(self, text, voice, ref_audio_path=None, speed=None, **_kw):
        # Piper voices are single-speaker; `voice`/`ref_audio_path`/`speed` are ignored.
        self.load()
        chunks = list(self._voice.synthesize(text))
        samples = np.concatenate([
            np.frombuffer(c.audio_int16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            for c in chunks]) if chunks else np.zeros(1, dtype=np.float32)
        sr = chunks[0].sample_rate if chunks else 22050
        return samples, sr, PIPER_VOICES[self.lang].split(".")[0]


class F5Engine(TTSEngine):
    """F5-TTS (flow matching, voice cloning). Model dir must hold one *.safetensors
    (or *.pt) checkpoint + vocab.txt; F5_REFS_DIR holds bundled reference clips as
    <name>.wav + <name>.txt transcript pairs, with <lang>.wav being the default
    voice. A per-request ref_audio_path clones that voice instead; its transcript
    comes from a sidecar .txt next to the wav, else F5's built-in ASR (first use
    downloads Whisper — slow once, then cached)."""

    name = "f5"
    _MODEL_DIR_ENV = {"uk": "F5_UK_MODEL_DIR", "ru": "F5_RU_MODEL_DIR"}
    _SAFE_VOICE = re.compile(r"^[\w-]+$")  # request `voice` joins a path — no separators/dots

    def __init__(self, lang: str):
        super().__init__(lang)
        self._tts = None

    def _model_dir(self) -> str:
        return os.environ.get(self._MODEL_DIR_ENV.get(self.lang, "")) or \
            os.path.join(_MODELS_DIR, "f5", self.lang)

    def _refs_dir(self) -> str:
        return os.environ.get("F5_REFS_DIR", os.path.join(_MODELS_DIR, "f5", "refs"))

    def _ckpt_file(self) -> str | None:
        for pat in ("*.safetensors", "*.pt"):
            hits = sorted(glob.glob(os.path.join(self._model_dir(), pat)))
            if hits:
                return hits[0]
        return None

    def _resolve_ref(self, voice: str | None, ref_audio_path: str | None) -> tuple[str, str] | None:
        """(ref_wav, ref_text). Priority: explicit clone path > named bundled ref
        (voice=<stem>) > per-lang default. Transcript from the sidecar .txt; empty
        string means 'let F5 transcribe'."""
        candidates = []
        if ref_audio_path:
            candidates.append(ref_audio_path)
        if voice and self._SAFE_VOICE.fullmatch(voice):
            # token pattern blocks traversal; realpath containment blocks symlinks out
            wav = os.path.join(self._refs_dir(), f"{voice}.wav")
            if os.path.realpath(wav).startswith(os.path.realpath(self._refs_dir()) + os.sep):
                candidates.append(wav)
        candidates.append(os.path.join(self._refs_dir(), f"{self.lang}.wav"))
        for wav in candidates:
            if os.path.isfile(wav):
                txt = os.path.splitext(wav)[0] + ".txt"
                ref_text = ""
                if os.path.isfile(txt):
                    with open(txt, encoding="utf-8") as f:
                        ref_text = f.read().strip()
                return wav, ref_text
        return None

    def available(self) -> bool:
        if self._ckpt_file() is None or not os.path.isfile(os.path.join(self._model_dir(), "vocab.txt")):
            return False
        if self._resolve_ref(None, None) is None:
            return False
        return _probe_libs("f5_tts")  # lazy: only when configured; success cached

    def load(self) -> None:
        with self._load_lock:
            if self._tts is None:
                from f5_tts.api import F5TTS
                self._tts = F5TTS(model="F5TTS_v1_Base", ckpt_file=self._ckpt_file(),
                                  vocab_file=os.path.join(self._model_dir(), "vocab.txt"))

    def synthesize(self, text, voice, ref_audio_path=None, speed=None, **_kw):
        # available() only checks files/imports; a deeper failure (e.g. first-run
        # vocoder fetch while offline, CUDA OOM) must still degrade to Piper, not 502.
        # `speed` is ignored — F5 paces from the reference clip.
        try:
            self.load()
            ref_wav, ref_text = self._resolve_ref(voice, ref_audio_path)
            wav, sr, _spec = self._tts.infer(
                ref_file=ref_wav, ref_text=ref_text, gen_text=text,
                nfe_step=int(os.environ.get("F5_NFE_STEP", "32")),
                remove_silence=False, show_info=lambda *a, **k: None)
        except Exception as e:  # noqa: BLE001
            log.warning("f5 synth failed for %s (%s: %s) — degrading to piper",
                        self.lang, type(e).__name__, e)
            piper = _instance("piper", self.lang)
            out = piper.synthesize(text, voice)
            self.produced_by = piper.name
            return out
        samples = np.asarray(wav, dtype=np.float32)
        self.produced_by = self.name
        return samples, int(sr), f"f5:{os.path.splitext(os.path.basename(ref_wav))[0]}"


class StyleTTS2Engine(TTSEngine):
    """StyleTTS2-Ukrainian (huggingface patriotyk/styletts2_ukrainian_single, MIT;
    inference via patriotyk's styletts2-inference package, also MIT). Model dir must
    hold config.yml + pytorch_model.bin + style.pt (the bundled speaker embedding —
    the "filatov" voice). Input text is already normalized (numbers as words), but
    StyleTTS2-Ukrainian is trained on IPA phonemes, so this engine runs the same
    stress+phonemization chain as the author's HF Space: ukrainian-word-stress ->
    ipa_uk. ref_audio_path is honored only when the checkpoint's config says
    multispeaker (the single-speaker model has exactly one voice; feeding it foreign
    style vectors degrades output), otherwise it is ignored gracefully.

    Prosody: by default every sentence would reuse the same frozen style tensor
    (s_prev), so pitch resets identically each sentence — the "TTS chant". When
    the checkpoint carries a trained style-diffusion head (the single-speaker
    filatov one does), _diffused_style blends its text-conditioned prosody into
    the frozen style per sentence. Knobs: STYLETTS2_DIFFUSION_T (blend weight,
    default 0.3, clamped 0..1; 0 disables — zero-cost no-op) and STYLETTS2_ES
    (diffusion embedding_scale, default 1.0). All model calls are seed-pinned so
    the same text renders identically (golden-file testable)."""

    name = "styletts2"
    _MODEL_DIR_ENV = {"uk": "STYLETTS2_UK_MODEL_DIR", "ru": "STYLETTS2_RU_MODEL_DIR"}
    _FILES = ("config.yml", "pytorch_model.bin", "style.pt")
    SAMPLE_RATE = 24000

    # Retry ladder for the style-diffusion sampler: some (seed, text) pairs
    # deterministically explode (see the guard in _diffused_style), so a NaN at
    # one seed is retried at the next before giving up on diffusion for the part.
    _DIFFUSION_SEEDS = (1234, 7, 99)

    def __init__(self, lang: str):
        super().__init__(lang)
        self._model = None
        self._style = None
        self._stressify = None
        # set True once the loaded checkpoint proves it has no usable diffusion
        # head (every seed raised, e.g. the multispeaker sampler wants voice
        # features predict_style_single never passes) — skips 3 doomed calls
        # per sentence forever after. Guard *rejections* (NaN/huge norm) are
        # text-dependent and never trip this.
        self._diffusion_broken = False

    def _model_dir(self) -> str:
        return os.environ.get(self._MODEL_DIR_ENV.get(self.lang, "")) or \
            os.path.join(_MODELS_DIR, "styletts2", self.lang)

    def available(self) -> bool:
        d = self._model_dir()
        if not all(os.path.isfile(os.path.join(d, f)) for f in self._FILES):
            return False
        # lazy: only when configured; success cached
        return _probe_libs("ipa_uk", "styletts2_inference", "ukrainian_word_stress")

    def load(self) -> None:
        with self._load_lock:
            self._load()

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from styletts2_inference.models import StyleTTS2
        from ukrainian_word_stress import Stressifier
        d = self._model_dir()
        device = os.environ.get("STYLETTS2_DEVICE") or \
            ("cuda" if torch.cuda.is_available() else "cpu")
        self._model = StyleTTS2(config_path=os.path.join(d, "config.yml"),
                                weights_path=os.path.join(d, "pytorch_model.bin"),
                                device=torch.device(device))
        self._load_styles()
        # Combining acute (U+0301) — NOT the default spacing acute U+00B4:
        # ipa_uk maps only U+0301 to the IPA stress mark, and NFKC in
        # _phonemize decomposes U+00B4 into SPACE+U+0301, splitting every
        # stressed word in half (verified A/B — the single loudest cause of
        # the halting, vowel-reduced read).
        from ukrainian_word_stress import StressSymbol
        self._stressify = Stressifier(
            stress_symbol=StressSymbol.CombiningAcuteAccent)

    def _load_styles(self) -> None:
        import torch
        self._style = torch.load(os.path.join(self._model_dir(), "style.pt"),
                                 map_location="cpu")

    @staticmethod
    def _split_parts(text: str, min_len: int = 20) -> list[str]:
        """See module-level split_parts — kept as a method for tests/subclasses."""
        return split_parts(text, min_len=min_len)

    def _resolve_style(self, voice: str | None, ref_audio_path: str | None):
        """(style_tensor, spoken_as). Single-speaker: the bundled style; a
        per-brand ref clip is honored only when the checkpoint is multispeaker
        (feeding the single model foreign style vectors degrades output)."""
        if ref_audio_path and self._model.config.model_params.multispeaker:
            return (self._model.extract_voice_features(ref_audio_path),
                    os.path.splitext(os.path.basename(ref_audio_path))[0])
        return self._style, "default"

    def _phonemize(self, part: str) -> str:
        """The Space's text prep verbatim: '+' is the user-facing manual stress
        marker; stressify is idempotent on words normalize.py already stressed."""
        from ipa_uk import ipa
        from ukrainian_word_stress import StressSymbol
        t = part.replace('"', "").replace("+", StressSymbol.CombiningAcuteAccent)
        t = unicodedata.normalize("NFKC", t)
        t = re.sub(r"[᠆‐‑‒–—―⁻₋−⸺⸻]", "-", t)
        if t and t[-1] not in ".?!:-":
            t += "."
        t = re.sub(r" - ", ": ", t)
        return ipa(self._stressify(t))

    def _rng_devices(self) -> list:
        """Device list for torch.random.fork_rng — the model's CUDA device, or
        empty on CPU (fork_rng's `devices` covers CUDA generators only; the CPU
        generator is always forked)."""
        dev = getattr(self._model, "device", None)
        return [dev] if dev is not None and dev.type == "cuda" else []

    def _diffused_style(self, tokens, base_style, part_text: str):
        """Text-conditioned prosody for one sentence: sample the checkpoint's
        style-diffusion head (predict_style_single) on this part's tokens and
        blend ONLY the prosody half (dims 128:) into the frozen base style —
        the acoustic half (timbre, dims :128) stays untouched so the voice
        never drifts. Best-effort: any failure returns base_style unchanged.

        STYLETTS2_DIFFUSION_T is the blend weight (default 0.3, clamped 0..1);
        0 — or a checkpoint without the head — makes this a zero-cost no-op.
        STYLETTS2_ES is the sampler's embedding_scale (default 1.0)."""
        import torch
        try:
            t = min(1.0, max(0.0, float(
                os.environ.get("STYLETTS2_DIFFUSION_T", "0.3"))))
        except ValueError:
            t = 0.3
        predict = getattr(self._model, "predict_style_single", None)
        if t <= 0.0 or predict is None or self._diffusion_broken:
            return base_style
        try:
            es = float(os.environ.get("STYLETTS2_ES", "1.0"))
        except ValueError:
            es = 1.0
        errors = 0
        for seed in self._DIFFUSION_SEEDS:
            try:
                with torch.no_grad(), \
                        torch.random.fork_rng(devices=self._rng_devices()):
                    # ADPM2 injects randn_like every step — per-call seeding
                    # (not just the fixed init noise) is what makes the sample
                    # bit-identical across runs.
                    torch.manual_seed(seed)
                    pred = predict(tokens.to(self._model.device),
                                   embedding_scale=es)
                if pred.dim() == 1:
                    pred = pred.unsqueeze(0)  # -> [1, 256]
                # MANDATORY guard: the sampler can deterministically explode on
                # certain (seed, text) pairs — measured s_pred norm 2.1e7 →
                # NaN audio 13× the expected length, and NaN passes *silently*
                # through polish/joiner (no exception, so the Piper fallback
                # never fires). Healthy norms sit at 2.4–3.5; anything
                # non-finite or >= 10 is rejected and the next seed tried.
                if pred.shape != base_style.shape or \
                        not torch.isfinite(pred).all() or \
                        float(pred.norm()) >= 10.0:
                    log.debug("styletts2 diffusion guard rejected seed %d for "
                              "part %r", seed, part_text[:60])
                    continue
                pred = pred.to(base_style.device, base_style.dtype)
                out = base_style.clone()
                out[:, 128:] = (1.0 - t) * base_style[:, 128:] + t * pred[:, 128:]
                return out
            except Exception as e:  # noqa: BLE001 — never break synthesis
                errors += 1
                log.debug("styletts2 diffusion seed %d failed for part %r "
                          "(%s: %s)", seed, part_text[:60],
                          type(e).__name__, e)
        if errors == len(self._DIFFUSION_SEEDS):
            # every rung *raised* (vs guard-rejected) — the head is structurally
            # unusable on this checkpoint; stop paying for it.
            log.warning("styletts2 %s: style diffusion unusable on this "
                        "checkpoint — using the frozen style", self.name)
            self._diffusion_broken = True
        return base_style

    def synthesize(self, text, voice, ref_audio_path=None, speed=None, **_kw):
        # available() only checks files/imports; a deeper failure (CUDA OOM, bad
        # checkpoint, phonemizer edge case) must still degrade to Piper, not 502.
        try:
            self.load()
            import torch
            style, spoken_as = self._resolve_style(voice, ref_audio_path)
            if speed is None:
                speed = float(os.environ.get("STYLETTS2_SPEED", "1.0"))
            chunks: list[tuple[np.ndarray, str]] = []
            parts = self._split_parts(text)
            for i, part in enumerate(parts):
                ps = self._phonemize(part)
                tokens = self._model.tokenizer.encode(ps) if ps else []
                if len(tokens) == 0:
                    continue
                part_speed = speed * (1.0 + joiner.speed_offset(part, i, len(parts)))
                part_style = self._diffused_style(tokens, style, part)
                # Seed-pinned synthesis: SineGen draws fresh noise per call, so
                # unpinned the same text renders differently every time. fork_rng
                # keeps the pinning from leaking into the process-wide RNG state.
                with torch.no_grad(), \
                        torch.random.fork_rng(devices=self._rng_devices()):
                    torch.manual_seed(0)
                    wav = self._model(tokens, speed=part_speed, s_prev=part_style)
                chunks.append((wav.cpu().numpy().astype(np.float32), part))
            if not chunks:
                raise ValueError("no synthesizable text after phonemization")
            samples = joiner.join_chunks(chunks, self.SAMPLE_RATE, speed=speed)
        except Exception as e:  # noqa: BLE001
            log.warning("styletts2 synth failed for %s (%s: %s) — degrading to piper",
                        self.lang, type(e).__name__, e)
            piper = _instance("piper", self.lang)
            out = piper.synthesize(text, voice)
            self.produced_by = piper.name
            return out
        self.produced_by = self.name
        return samples, self.SAMPLE_RATE, f"styletts2:{spoken_as}"


class StyleTTS2MultiEngine(StyleTTS2Engine):
    """Multispeaker StyleTTS2-Ukrainian (patriotyk/styletts2_ukrainian_multispeaker,
    MIT) + the voice styles from the author's HF Space (voices/*.pt). Same text
    chain as the single-speaker engine; `voice` picks a style by file stem via
    exact dict lookup — request input never builds a path. Default voice:
    STYLETTS2_UK_VOICE when it names an existing style, else first stem
    alphabetically. Text-conditioned prosody (_diffused_style) is attempted but
    this checkpoint's diffusion head may not be usable single-voice-style — the
    first failed sentence flips _diffusion_broken and the engine runs on frozen
    styles from then on, exactly as fast as before."""

    name = "styletts2_multi"
    _MODEL_DIR_ENV = {"uk": "STYLETTS2_MULTI_UK_MODEL_DIR"}
    _FILES = ("config.yml", "pytorch_model.bin")

    def __init__(self, lang: str):
        super().__init__(lang)
        self._voices: dict | None = None
        self._default_voice: str | None = None

    def _model_dir(self) -> str:
        return os.environ.get(self._MODEL_DIR_ENV.get(self.lang, "")) or \
            os.path.join(_MODELS_DIR, "styletts2", f"{self.lang}-multi")

    def _voices_dir(self) -> str:
        return os.path.join(self._model_dir(), "voices")

    def voice_names(self) -> list[str]:
        """Cheap (filenames only, no torch) — /health advertises these."""
        try:
            return sorted(os.path.splitext(f)[0] for f in os.listdir(self._voices_dir())
                          if f.endswith(".pt"))
        except OSError:
            return []

    def available(self) -> bool:
        d = self._model_dir()
        if not all(os.path.isfile(os.path.join(d, f)) for f in self._FILES):
            return False
        if not self.voice_names():
            return False
        return _probe_libs("ipa_uk", "styletts2_inference", "ukrainian_word_stress")

    def _load_styles(self) -> None:
        import torch
        self._voices = {}
        for f in sorted(os.listdir(self._voices_dir())):
            if f.endswith(".pt"):
                self._voices[os.path.splitext(f)[0]] = torch.load(
                    os.path.join(self._voices_dir(), f), map_location="cpu")
        env_default = os.environ.get("STYLETTS2_UK_VOICE", "")
        self._default_voice = env_default if env_default in self._voices \
            else sorted(self._voices)[0]

    def _resolve_style(self, voice, ref_audio_path):
        if ref_audio_path:  # per-brand voice ref beats named styles, as elsewhere
            return (self._model.extract_voice_features(ref_audio_path),
                    os.path.splitext(os.path.basename(ref_audio_path))[0])
        name = voice if voice in self._voices else self._default_voice
        return self._voices[name], name


class UkrainianTTSEngine(TTSEngine):
    """robinhad/ukrainian-tts (ESPnet VITS) via the ISOLATED sidecar service on
    :8106 (inference/adapters/gen-tts-ukrainian, own venv — its dependency tree stays out
    of this process). uk only. Model weights are GPL v3: server-side use only,
    never distribute them. `voice` picks one of the five bundled speakers."""

    name = "ukrainian_tts"
    VOICES = ("tetiana", "mykyta", "lada", "dmytro", "oleksa")
    _PROBE_TTL_S = 30

    def __init__(self, lang: str):
        super().__init__(lang)
        self._probe: tuple[float, bool] | None = None  # (checked_at, ok)

    def _url(self) -> str:
        return os.environ.get("UKTTS_URL", "http://localhost:8106")

    def voice_names(self) -> list[str]:
        return list(self.VOICES) if self.lang == "uk" else []

    def available(self) -> bool:
        """Sidecar health probe, cached briefly — /health calls this often."""
        if self.lang != "uk":
            return False
        import time as _time
        now = _time.time()
        if self._probe and now - self._probe[0] < self._PROBE_TTL_S:
            return self._probe[1]
        try:
            import httpx
            r = httpx.get(f"{self._url()}/health", timeout=2)
            ok = r.status_code == 200 and r.json().get("model_dir_ok", False)
        except Exception:  # noqa: BLE001
            ok = False
        self._probe = (now, ok)
        return ok

    def load(self) -> None:  # model lives in the sidecar; nothing to load here
        return

    def synthesize(self, text, voice, ref_audio_path=None, speed=None, **_kw):
        # `ref_audio_path`/`speed` unsupported by this engine — ignored.
        try:
            import io as _io

            import httpx
            import soundfile as _sf
            r = httpx.post(f"{self._url()}/generate",
                           json={"text": text, "voice": voice}, timeout=300)
            r.raise_for_status()
            samples, sr = _sf.read(_io.BytesIO(r.content), dtype="float32")
            spoken_as = r.headers.get("X-Voice", voice or "dmytro")
        except Exception as e:  # noqa: BLE001
            log.warning("ukrainian_tts synth failed (%s: %s) — degrading to piper",
                        type(e).__name__, e)
            self._probe = None  # re-probe on next availability check
            piper = _instance("piper", self.lang)
            out = piper.synthesize(text, voice)
            self.produced_by = piper.name
            return out
        self.produced_by = self.name
        return samples, int(sr), f"ukrainian_tts:{spoken_as}"


class RadTTSEngine(TTSEngine):
    """RAD-TTS++ Ukrainian (Yehor/radtts-uk, MIT weights) + patriotyk's 44.1kHz
    Vocos vocoder. In-process engine: model code is vendored in radtts_uk/
    (from the Yehor/radtts-uk-vocos HF Space). uk only, three speakers
    (lada f / mykyta m / tetiana f). Стрес dialect: this model wants "+"
    BEFORE the stressed vowel, while our normalize chain emits a combining
    acute AFTER it — synthesize() converts. `speed` maps to token duration
    scaling; `ref_audio_path` unsupported (fixed speaker embeddings)."""

    name = "radtts"
    VOICES = {"lada": 0, "mykyta": 1, "tetiana": 2}
    # Measured f0 (Hz) per speaker on real output — the base the `depth` control
    # shifts DOWN from. RADTTS.infer renormalizes the whole contour to
    # (f0_std, f0_mean) in Hz, and ONLY when f0_mean > 0, so both must be real
    # frequencies: passing a small f0_std here would flatten the read to a
    # monotone rather than "reduce variation a little".
    F0_HZ = {"mykyta": (128.0, 15.0), "lada": (190.0, 20.0), "tetiana": (185.0, 20.0)}
    SAMPLE_RATE = 44100
    _VOCOS_REPO = "patriotyk/vocos-mel-hifigan-compat-44100khz"

    def __init__(self, lang: str):
        super().__init__(lang)
        self._model = None
        self._vocos = None
        self._tp = None

    def _ckpt(self) -> str:
        d = os.environ.get("RADTTS_UK_MODEL_DIR") or \
            os.path.join(_MODELS_DIR, "radtts", "uk")
        return os.path.join(d, "model_dap_84000_state.pt")

    def voice_names(self) -> list[str]:
        return sorted(self.VOICES) if self.lang == "uk" else []

    def available(self) -> bool:
        return self.lang == "uk" and os.path.isfile(self._ckpt()) and \
            _probe_libs("torch", "vocos")

    def _load_vocos(self):
        """The vocoder config is written for the langtech-bsc vocos fork whose
        MelSpectrogramFeatures grew extra kwargs; we only need decode() (backbone
        + head), so instantiate those two directly from known hparams instead of
        Vocos.from_hparams, and load the state dict non-strictly."""
        import torch
        from huggingface_hub import hf_hub_download
        from vocos import Vocos
        from vocos.feature_extractors import MelSpectrogramFeatures
        from vocos.heads import ISTFTHead
        from vocos.models import VocosBackbone
        path = hf_hub_download(self._VOCOS_REPO, "pytorch_model.bin")
        vocos = Vocos(
            feature_extractor=MelSpectrogramFeatures(
                sample_rate=self.SAMPLE_RATE, n_fft=2048, hop_length=512,
                n_mels=80, padding="same"),
            backbone=VocosBackbone(input_channels=80, dim=512,
                                   intermediate_dim=1536, num_layers=8),
            head=ISTFTHead(dim=512, n_fft=2048, hop_length=512, padding="same"))
        state = torch.load(path, map_location="cpu")
        missing, unexpected = vocos.load_state_dict(state, strict=False)
        real_missing = [k for k in missing if not k.startswith("feature_extractor.")]
        if real_missing:
            raise RuntimeError(f"vocos state dict incomplete: {real_missing[:4]}")
        return vocos

    def _load(self) -> None:
        import json as _json

        import torch

        from radtts_uk.data import TextProcessor
        from radtts_uk.radtts import RADTTS
        from radtts_uk.torch_env import device
        cfg = _json.loads(open(os.path.join(
            os.path.dirname(__file__), "radtts_uk", "config.json")).read())
        data_config, model_config = cfg["data_config"], cfg["model_config"]
        model = RADTTS(**model_config).to(device)
        model.enable_inverse_cache()
        ckpt = torch.load(self._ckpt(), map_location="cpu")
        model.load_state_dict(ckpt["state_dict"], strict=False)
        model.eval()
        self._tp = TextProcessor(
            data_config["training_files"],
            **{k: v for k, v in data_config.items()
               if k not in ("training_files", "validation_files")})
        self._vocos = self._load_vocos().to(device).eval()
        self._model = model
        self._device = device

    def load(self) -> None:
        with self._load_lock:
            if self._model is None:
                self._load()

    @staticmethod
    def _to_plus_stress(text: str) -> str:
        """'+' before the stressed vowel — the notation this model was trained on."""
        from normalize import to_plus_stress
        return to_plus_stress(text)

    def synthesize(self, text, voice, ref_audio_path=None, speed=None,
                   expressiveness=None, depth=None, **_kw):
        """`expressiveness` 0..1 (None = 0.5, the reference setting) is
        RAD-TTS++'s answer to a monotone read: it widens the sampled spread of
        pitch and energy. It cuts both ways — the same sampling noise that adds
        melody also adds f0 jitter, heard as roughness on vowels, so lower it
        when the voice sounds unsteady rather than flat.

        `depth` 0..1 lowers the speaker's mean pitch (0 = the model's own, 1 =
        about -20%), renormalizing the contour around the new centre while
        keeping a natural spread. Pitch is moved HERE rather than in DSP on the
        way out: resampling would drag the formants with it and add exactly the
        artefacts polish.py is trying to remove."""
        # ref_audio_path unsupported (fixed speaker table) — ignored.
        try:
            import torch
            self.load()
            name = voice if voice in self.VOICES else \
                (os.environ.get("RADTTS_UK_VOICE") if os.environ.get("RADTTS_UK_VOICE")
                 in self.VOICES else "mykyta")
            spk = torch.LongTensor([self.VOICES[name]]).to(self._device)
            base_speed = max(0.5, min(2.0, speed)) if speed else 1.0
            expr = 0.5 if expressiveness is None else float(np.clip(expressiveness, 0.0, 1.0))
            dep = 0.0 if depth is None else float(np.clip(depth, 0.0, 1.0))
            base_hz, base_std = self.F0_HZ.get(name, (0.0, 0.0))
            # f0_mean 0 leaves the model's own contour untouched (its own guard)
            f0_mean = base_hz * (1.0 - 0.20 * dep) if (dep > 0 and base_hz) else 0.0
            f0_std = base_std * (0.7 + 0.6 * expr) if f0_mean else 0.0
            # Same text = same audio: RAD-TTS samples its f0/energy/duration
            # latents from torch RNGs that were never seeded (the CPU generator
            # feeding the CUDA path especially), so every render used to be a
            # different take. Seed per sentence FROM the sentence, so a take is
            # a pure function of (text, config) and independent of position.
            base_seed = int(os.environ.get("RADTTS_SEED", "1234"))
            # Per-sentence synthesis + deterministic joiner: one giant infer
            # gives marching, pause-free pacing (and the model degrades on long
            # inputs); sentence takes with punctuation-scaled pauses read like
            # an edited voiceover. Sentence speeds get hash-based micro-offsets.
            parts = split_parts(text)
            chunks: list[tuple[np.ndarray, str]] = []
            with torch.inference_mode():
                for i, part in enumerate(parts):
                    plus = self._to_plus_stress(part)
                    tokens = torch.LongTensor(
                        self._tp.tp.encode_text(plus)).to(self._device)
                    if tokens.numel() == 0:
                        continue
                    part_speed = base_speed * (1.0 + joiner.speed_offset(part, i, len(parts)))
                    dur_scale = 1.0 / max(0.5, min(2.0, part_speed))
                    torch.manual_seed(base_seed + zlib.crc32(part.encode("utf-8")) % 100000)
                    out = self._model.infer(
                        spk, tokens[None], sigma=0.8, sigma_dur=0.666,
                        sigma_f0=0.6 + 0.8 * expr, sigma_energy=0.6 + 0.8 * expr,
                        f0_mean=f0_mean, f0_std=f0_std,
                        token_dur_scaling=dur_scale, token_duration_max=100,
                        speaker_id_text=spk, speaker_id_attributes=spk)
                    wav = self._vocos.decode(out["mel"])
                    chunks.append((wav[0].float().cpu().numpy(), part))
            if not chunks:
                raise ValueError("no synthesizable text")
            samples = joiner.join_chunks(chunks, self.SAMPLE_RATE, speed=base_speed)
        except Exception as e:  # noqa: BLE001
            fb = _instance("styletts2_multi", self.lang)
            if not fb.available():
                fb = _instance("piper", self.lang)
            log.warning("radtts synth failed (%s: %s) — degrading to %s",
                        type(e).__name__, e, fb.name)
            out = fb.synthesize(text, voice)
            self.produced_by = fb.produced_by
            return out
        self.produced_by = self.name
        return samples, self.SAMPLE_RATE, f"radtts:{name}"


class ElevenLabsEngine(TTSEngine):
    """ElevenLabs API (paid cloud, eleven_multilingual_v2 — supports uk/ru).
    Enabled only when ELEVENLABS_API_KEY is set. Voice catalog comes from
    ELEVENLABS_VOICES ("Name:voice_id,..." — scoped keys often can't list
    voices via API), defaulting to four premade voices. Cloud caveat: the TEXT
    LEAVES THE BOX; per-character billing. Audio arrives as mp3 and is decoded
    to mono 24 kHz via ffmpeg."""

    name = "elevenlabs"
    _API = "https://api.elevenlabs.io/v1"
    # Current-gen premade voices — verified 200 on a free-tier key 2026-08-20
    # (Rachel/Aria are legacy-gated: 402 on free tiers, so deliberately absent).
    _DEFAULT_VOICES = ("George:JBFqnCBsd6RMkjVDRZzb", "Adam:pNInz6obpgDQGcFmaJgB",
                       "Sarah:EXAVITQu4vr4xnSDxMaL", "Laura:FGY2WhTYpPnrIDTdsKH5",
                       "Charlie:IKne3meq5aSn9XLyUdCD", "Alice:Xb7hH8MSUJpSbSDYk0k2")
    SAMPLE_RATE = 24000

    def _voice_map(self) -> dict[str, str]:
        raw = os.environ.get("ELEVENLABS_VOICES") or ",".join(self._DEFAULT_VOICES)
        out = {}
        for pair in raw.split(","):
            name, _, vid = pair.strip().partition(":")
            if name and vid:
                out[name] = vid
        return out

    def voice_names(self) -> list[str]:
        return sorted(self._voice_map()) if self.available() else []

    def available(self) -> bool:
        return bool(os.environ.get("ELEVENLABS_API_KEY")) and self.lang in ("uk", "ru")

    def load(self) -> None:  # cloud API — nothing to load
        return

    @staticmethod
    def _clean(text: str) -> str:
        """Strip combining stress accents and '+' markers — ElevenLabs does its
        own prosody; the marks would be read as garbage. The verbalized number
        words are kept (correct inflection helps every engine)."""
        text = unicodedata.normalize("NFD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return unicodedata.normalize("NFC", text).replace("+", "")

    def _request_body(self, text: str, speed: float | None) -> dict:
        """Tuned request: voice_settings for ad delivery (env-overridable),
        request `speed` (the lab slider) clamped to EL's 0.7-1.2, a fixed seed
        for take-to-take reproducibility, and language_code on models that
        accept it (turbo/flash v2.5) so mixed-script text never flips language."""
        model = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")
        settings = {
            "stability": float(os.environ.get("ELEVENLABS_STABILITY", "0.5")),
            "similarity_boost": float(os.environ.get("ELEVENLABS_SIMILARITY", "0.75")),
            "style": float(os.environ.get("ELEVENLABS_STYLE", "0.15")),
            "use_speaker_boost": os.environ.get("ELEVENLABS_SPEAKER_BOOST", "1") != "0",
        }
        if speed is not None:
            settings["speed"] = min(1.2, max(0.7, float(speed)))
        body = {"text": self._clean(text), "model_id": model,
                "voice_settings": settings,
                "seed": int(os.environ.get("ELEVENLABS_SEED", "42"))}
        if "v2_5" in model or "flash" in model or "turbo" in model:
            body["language_code"] = self.lang
        return body

    def synthesize(self, text, voice, ref_audio_path=None, speed=None, **_kw):
        # ref_audio_path unsupported (cloning happens in the EL account, not here).
        try:
            import io as _io
            import subprocess as _sp

            import httpx
            import soundfile as _sf
            voices = self._voice_map()
            name = voice if voice in voices else \
                (os.environ.get("ELEVENLABS_VOICE") if os.environ.get("ELEVENLABS_VOICE") in voices
                 else sorted(voices)[0])
            r = httpx.post(
                f"{self._API}/text-to-speech/{voices[name]}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                json=self._request_body(text, speed),
                timeout=120)
            r.raise_for_status()
            try:  # repo convention: the venv-bundled ffmpeg (no system install)
                import imageio_ffmpeg
                ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                ffmpeg = "ffmpeg"
            wav = _sp.run(
                [ffmpeg, "-v", "error", "-i", "pipe:0", "-f", "wav",
                 "-ar", str(self.SAMPLE_RATE), "-ac", "1", "pipe:1"],
                input=r.content, capture_output=True, check=True).stdout
            samples, sr = _sf.read(_io.BytesIO(wav), dtype="float32")
        except Exception as e:  # noqa: BLE001
            # Quality ladder: dead cloud (quota/network) -> best local neural
            # voice, then piper. A billing hiccup must not audibly wreck shorts.
            fb = _instance("styletts2_multi", self.lang) if self.lang == "uk" else None
            if fb is None or not fb.available():
                fb = _instance("piper", self.lang)
            log.warning("elevenlabs synth failed (%s: %s) — degrading to %s",
                        type(e).__name__, e, fb.name)
            out = fb.synthesize(text, None)
            self.produced_by = fb.produced_by
            return out
        self.produced_by = self.name
        return samples, int(sr), f"elevenlabs:{name}"


ENGINE_CLASSES = {"piper": PiperEngine, "f5": F5Engine, "styletts2": StyleTTS2Engine,
                  "styletts2_multi": StyleTTS2MultiEngine,
                  "ukrainian_tts": UkrainianTTSEngine,
                  "radtts": RadTTSEngine,
                  "elevenlabs": ElevenLabsEngine}

_engines: dict[tuple[str, str], TTSEngine] = {}   # (lang, name) -> instance
_warned: set = set()                              # one fallback log line per (lang, name)


def configured_engine_name(lang: str) -> str:
    name = os.environ.get(f"TTS_ENGINE_{lang.upper()}", "piper").lower()
    if name not in ENGINE_CLASSES:
        if (lang, name) not in _warned:
            _warned.add((lang, name))
            log.warning("unknown TTS engine %r for %s — using piper", name, lang)
        return "piper"
    return name


_registry_lock = threading.Lock()


def _instance(name: str, lang: str) -> TTSEngine:
    key = (lang, name)
    with _registry_lock:
        if key not in _engines:
            _engines[key] = ENGINE_CLASSES[name](lang)
        return _engines[key]


def get_engine(lang: str, override: str | None = None) -> TTSEngine:
    """Resolve the engine for a language, walking the fallback chain to Piper.
    `override` (a per-request engine name, e.g. from the TTS lab) beats the env
    config when it names a known engine; unknown names fall back to config.
    uk quality ladder: a dead cloud engine falls back to the best LOCAL neural
    voice (styletts2_multi) before the piper baseline."""
    name = override.lower() if override and override.lower() in ENGINE_CLASSES \
        else configured_engine_name(lang)
    chain = [name]
    if lang == "uk" and name not in ("piper", "styletts2_multi", "styletts2"):
        chain.append("styletts2_multi")
    if name != "piper":
        chain.append("piper")
    for n in chain:
        eng = _instance(n, lang)
        if eng.available():
            return eng
        if (lang, n) not in _warned:
            _warned.add((lang, n))
            log.warning("TTS engine %r unavailable for %s (libs/models missing) — falling back", n, lang)
    # Last resort: Piper even when its model file is missing — synthesis will
    # surface a clear 502 instead of the registry inventing silence.
    return _instance("piper", lang)


def engine_status(lang: str) -> dict:
    """For /health: what is configured, what will actually run, assets loadable?
    `available` lists every registered engine with its readiness — the TTS lab
    renders its engine picker from this."""
    configured = configured_engine_name(lang)
    active = get_engine(lang)
    status = {"configured": configured, "active": active.name, "loadable": active.available(),
              "available": {n: _instance(n, lang).available() for n in ENGINE_CLASSES}}
    # Engines with selectable named voices advertise them (filename listing only).
    voices = {n: _instance(n, lang).voice_names() for n in ENGINE_CLASSES
              if hasattr(ENGINE_CLASSES[n], "voice_names")}
    voices = {n: v for n, v in voices.items() if v}
    if voices:
        status["voices"] = voices
    return status
