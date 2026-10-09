"""Neural verbalizer for Ukrainian TTS input — skypro1111/m2m100-ukr-verbalization
(the model patriotyk's own StyleTTS2 Space uses). Rewrites digits, prices and
abbreviations as correctly INFLECTED Ukrainian words with sentence context —
what num2words fundamentally can't do ("з 5 січня" -> "з п'ятого січня",
"у 2 рази" -> "у два рази", "125 грн" -> "сто двадцять п'ять гривень").

OPTIONAL heavy extra, same contract as the stress libs: model dir or libs
absent => verbalize() returns text unchanged after one log line. Assets are
repo-owned:
  data/models/verbalizer/uk/model/      ct2 conversion (model.bin + configs)
  data/models/verbalizer/uk/tokenizer/  M2M100 tokenizer files
Env: TTS_VERBALIZER=0 disables; TTS_VERBALIZER_DIR overrides the base dir.
Runs on CPU (int8), lazily loaded once; only sentences containing digits go
through the model — pure-text sentences pass through untouched, so the model
can never rewrite clean copy.
"""

import logging
import os
import re
import threading

log = logging.getLogger("gen-tts-verbalize")

_MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "models")


def _base_dir() -> str:
    return os.environ.get("TTS_VERBALIZER_DIR",
                          os.path.join(_MODELS_DIR, "verbalizer", "uk"))


def _model_dir() -> str:
    return os.path.join(_base_dir(), "model")


def _tok_dir() -> str:
    return os.path.join(_base_dir(), "tokenizer")


_lock = threading.Lock()
_state: dict = {}  # {"translator": ..., "tok": ...} once loaded; {"failed": True} after a load error

_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_MAX_SENT_CHARS = 350  # M2M100 degrades on long inputs; longer sentences skip the model


def available() -> bool:
    """Cheap check: enabled + model/tokenizer files + importable libs."""
    if os.environ.get("TTS_VERBALIZER", "1") == "0" or _state.get("failed"):
        return False
    if not os.path.isfile(os.path.join(_model_dir(), "model.bin")):
        return False
    if not os.path.isfile(os.path.join(_tok_dir(), "sentencepiece.bpe.model")):
        return False
    try:
        import ctranslate2  # noqa: F401
        import sentencepiece  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


def _load():
    with _lock:
        if "translator" in _state or _state.get("failed"):
            return
        try:
            import ctranslate2
            from transformers import M2M100Tokenizer
            _state["translator"] = ctranslate2.Translator(
                _model_dir(), device="cpu", compute_type="int8")
            tok = M2M100Tokenizer.from_pretrained(_tok_dir())
            tok.src_lang = "uk"
            _state["tok"] = tok
            log.info("uk verbalizer loaded from %s", _base_dir())
        except Exception as e:  # noqa: BLE001 — never let the extra break synthesis
            log.warning("uk verbalizer failed to load (%s: %s) — disabled", type(e).__name__, e)
            _state["failed"] = True


def _verbalize_sentence(sent: str) -> str:
    tok, translator = _state["tok"], _state["translator"]
    lang_token = getattr(tok, "lang_code_to_token", {}).get("uk", "__uk__")
    source = tok.convert_ids_to_tokens(tok.encode(sent))
    result = translator.translate_batch(
        [source], target_prefix=[[lang_token]], beam_size=1, num_hypotheses=1)
    target = result[0].hypotheses[0]
    if target and target[0] == lang_token:
        target = target[1:]
    out = tok.decode(tok.convert_tokens_to_ids(target),
                     skip_special_tokens=True).strip()
    return out or sent


def verbalize(text: str) -> str:
    """Verbalize digit-bearing sentences; everything else passes through. Any
    failure returns the input — the deterministic num2words pass still runs
    after this as the safety net."""
    if not available():
        return text
    _load()
    if _state.get("failed"):
        return text
    out = []
    for sent in _SENT_SPLIT.split(text):
        if re.search(r"\d", sent) and len(sent) <= _MAX_SENT_CHARS:
            try:
                sent = _verbalize_sentence(sent)
            except Exception as e:  # noqa: BLE001
                log.warning("uk verbalizer failed on a sentence (%s: %s) — keeping original",
                            type(e).__name__, e)
        out.append(sent)
    return " ".join(out)
