"""tts_qa — the automatic ear of the TTS improvement loop.

Synthesize the given text (default engine/voice unless overridden), TRANSCRIBE
the result with ASR (faster-whisper on the TTS service), and word-align what
was HEARD against what was MEANT (the service returns the normalized text it
actually spoke). Mismatches become an auto-filed tts_feedback row — the same
dataset human 👎 verdicts land in — so the ✨ fix-mining job (tts_improve)
consumes machine findings and human findings identically.

Enqueued automatically after every short's uk/ru voiceover (pipelines/short.py)
and manually via POST /v1/tts/qa. ASR is imperfect: a mismatch is a *signal*,
not a verdict — rows are tagged and prefixed [auto-QA] so a human reviews the
mined fixes before anything is applied (the tts_improve contract)."""

import difflib
import re
import unicodedata
import uuid

from num2words import num2words

from ads.core.db import session_scope
from ads.core.models import App, Job, TtsFeedback
from ads.providers.tts import tts_provider
from ads.pipelines.ad_post import _set

WER_DOWN_THRESHOLD = 0.15   # above this the take is auto-flagged 👎
MAX_MISMATCHES = 8          # kept in the comment/result
# Word pairs at least this similar count as the same word: ASR re-inflects
# endings (першого/перше) and splits clusters — those are not TTS errors it
# can attest. This QA catches GROSS errors (wrong/dropped words, garbled
# numbers); stress placement is inaudible to ASR by design.
FUZZY_MATCH_RATIO = 0.66

# Whisper INVERSE-normalizes ("п'ятнадцять відсотків" -> "15%"): undo that on
# the heard side before comparing, with the same num2words the TTS chain uses.
_UNIT_WORDS = {
    "uk": {"%": "відсотків", "грн": "гривень", "₴": "гривень"},
    "ru": {"%": "процентов", "грн": "гривен", "₴": "гривен"},
}


# month names (genitive) for date-ITN undo; mirrors the TTS service's date pass
_MONTHS = {
    "uk": "січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня",
    "ru": "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря",
}


def _ordinal_gen(n: int, lang: str) -> str | None:
    """'31' -> 'тридцять першого' (genitive ordinal, the spoken date form).
    Same last-word ending transform as the service's normalize.py."""
    try:
        word = num2words(n, lang=lang, to="ordinal")
    except Exception:  # noqa: BLE001
        return None
    parts = word.split()
    last = parts[-1]
    if lang == "uk":
        last = last[:-2] + "ього" if last.endswith("ій") else \
            last[:-2] + "ого" if last.endswith("ий") else None
    else:
        last = last[:-2] + "ьего" if last.endswith("ий") else \
            last[:-2] + "ого" if last.endswith(("ый", "ой")) else None
    return " ".join(parts[:-1] + [last]) if last else None


def _expand_heard_digits(text: str, lang: str) -> str:
    for unit, word in _UNIT_WORDS.get(lang, {}).items():
        text = text.replace(unit, f" {word} ")

    def _date(m: re.Match) -> str:
        word = _ordinal_gen(int(m.group(1)), lang)
        return f"{word} {m.group(2)}" if word else m.group(0)

    # Whisper writes dates back as digits ("31 серпня") — undo as ordinals; the
    # fuzzy match absorbs the nominative/genitive difference.
    text = re.sub(rf"\b([0-3]?\d)\s+({_MONTHS.get(lang, '')})\b", _date, text,
                  flags=re.IGNORECASE) if lang in _MONTHS else text

    def _num(m: re.Match) -> str:
        s = m.group(0).replace(",", ".")
        try:
            return num2words(float(s) if "." in s else int(s), lang=lang)
        except Exception:  # noqa: BLE001
            return m.group(0)

    return re.sub(r"\d+(?:[.,]\d+)?", _num, text)


def _words(text: str) -> list[str]:
    """Comparable word stream: casefold, strip combining accents (stress marks),
    apostrophe variants and punctuation — ASR never emits those faithfully."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # apostrophe variants and the '+' manual stress marker are not spoken
    text = text.replace("’", "").replace("'", "").replace("ʼ", "").replace("+", "")
    return [w for w in re.findall(r"[а-яіїєґёa-z0-9]+", text.casefold()) if w]


def compare_texts(expected: str, heard: str, lang: str = "uk") -> dict:
    """Word-level alignment of spoken-vs-heard: WER + the substituted word
    pairs. Heard digits are expanded to words first; near-identical pairs
    (inflection variants, ASR cluster splits) count as matches."""
    exp = _words(expected)
    got = _words(_expand_heard_digits(heard, lang))
    sm = difflib.SequenceMatcher(a=exp, b=got, autojunk=False)
    errors = 0
    mismatches: list[dict] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        exp_span, got_span = " ".join(exp[i1:i2]), " ".join(got[j1:j2])
        if op == "replace" and difflib.SequenceMatcher(
                None, exp_span.replace(" ", ""), got_span.replace(" ", "")
        ).ratio() >= FUZZY_MATCH_RATIO:
            continue  # same word modulo inflection/splitting — not a TTS error
        errors += max(i2 - i1, j2 - j1)
        if op == "replace" and len(mismatches) < MAX_MISMATCHES:
            mismatches.append({"expected": exp_span, "heard": got_span})
    wer = round(errors / max(len(exp), 1), 3)
    return {"wer": wer, "expected_words": len(exp), "mismatches": mismatches}


async def run_tts_qa(job_id: uuid.UUID) -> None:
    """Queue runner for the 'tts_qa' job kind. params: {text, lang, engine?,
    voice?, speed?} — text is the RAW input (the synth normalizes it)."""
    try:
        async with session_scope() as s:
            job = await s.get(Job, job_id)
            if job is None:
                return
            app = await s.get(App, job.app_id)
            if app is None:
                return
            app_id, params = app.id, dict(job.params or {})
            pronunciations = dict(app.pronunciations or {})

        text = str(params.get("text") or "").strip()
        lang = str(params.get("lang") or "uk")
        if not text or lang not in ("uk", "ru"):
            await _set(job_id, status="done", progress=100,
                       message="nothing to QA (empty text or unsupported lang)",
                       result={"skipped": True})
            return

        await _set(job_id, status="running", progress=15, message="synthesizing")
        audio = await tts_provider.generate(
            text=text, subdir=f"tts-qa/{app_id}", lang=lang,
            voice=params.get("voice"), pronunciations=pronunciations,
            engine=params.get("engine"), speed=params.get("speed"))
        spoken = str(audio.get("text") or text)  # normalized text; raw on old services

        await _set(job_id, progress=55, message="listening back (ASR)")
        heard = (await tts_provider.transcribe(audio["path"], lang=lang)).get("text", "")

        cmp = compare_texts(spoken, heard, lang)
        verdict = "down" if cmp["wer"] > WER_DOWN_THRESHOLD or cmp["mismatches"] else "up"
        pairs = "; ".join(f"«{m['expected']}» → «{m['heard']}»" for m in cmp["mismatches"])
        comment = (f"[auto-QA] WER {cmp['wer']:.0%}" + (f" — {pairs}" if pairs else ""))[:1000]

        async with session_scope() as s:
            row = TtsFeedback(id=uuid.uuid4(), app_id=app_id, lang=lang,
                              engine=str(audio.get("engine") or ""),
                              voice=str(audio.get("voice") or ""),
                              speed=params.get("speed"),
                              text=text, verdict=verdict,
                              tags=["wrong-word"] if verdict == "down" else [],
                              comment=comment, audio_path=str(audio.get("path") or ""))
            s.add(row)
            feedback_id = row.id

        await _set(job_id, status="done", progress=100,
                   message=(f"QA: WER {cmp['wer']:.0%}, "
                            f"{len(cmp['mismatches'])} mismatch(es) — "
                            + ("flagged for fix mining" if verdict == "down" else "clean"))[:300],
                   result={**cmp, "verdict": verdict, "heard": heard, "spoken": spoken,
                           "audio_path": audio.get("path"),
                           "feedback_id": str(feedback_id)})
    except Exception as e:  # noqa: BLE001 — surface any failure to the client
        await _set(job_id, status="error", message="failed", error=f"{type(e).__name__}: {e}")
