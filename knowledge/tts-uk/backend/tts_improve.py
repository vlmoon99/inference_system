"""tts_improve — mine collected TTS feedback into proposed pronunciation fixes.

Propose -> review -> apply (the analytics-profiler pattern): ONE llm chat_json
call over the app's 'down' tts_feedback rows + its current pronunciations. The
validated proposal lives in job.result ONLY — nothing is applied here; the user
reviews the fixes in the TTS lab and applies accepted ones (client merges them
into brand pronunciations, the store every voiceover already reads)."""

import logging
import re
import uuid

from sqlalchemy import select

from ads.core.db import session_scope
from ads.core.models import App, Job, TtsFeedback
from ads.providers import llm_provider
from ads.pipelines.ad_post import _set

MAX_FEEDBACK_ROWS = 100   # newest 'down' rows considered
MAX_FIXES = 15            # proposal cap
MAX_WHY = 200

log = logging.getLogger("tts-improve")

IMPROVE_SYSTEM = (
    "You are a Ukrainian/Russian TTS pronunciation engineer. Users flagged "
    "synthesized ad voiceovers as sounding wrong. Propose per-word pronunciation "
    "fixes: map a word EXACTLY AS WRITTEN in the flagged text to how it should be "
    "spoken. There are exactly two legitimate kinds of fix:\n"
    "1. A FOREIGN or BRAND word (contains Latin letters) -> its Cyrillic "
    "respelling, e.g. Aurora -> Авро+ра, MacBook -> макбук.\n"
    "2. A word already written in Cyrillic -> THE SAME WORD with a '+' inserted "
    "directly BEFORE the stressed vowel, e.g. замок -> з+амок, колекцію -> "
    "кол+екцію. You may ONLY add stress marks here — never change, add or drop "
    "any letter. Rewriting a Cyrillic word's letters corrupts it (сайті -> "
    "з+аїті is exactly the mistake to avoid) and such a fix will be discarded.\n"
    "Only propose fixes clearly supported by the feedback (its tags and "
    "comments); never invent problems. Reply with JSON only."
)

_LATIN = re.compile(r"[A-Za-z]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def _strip_stress(s: str) -> str:
    """A word reduced to its letters — stress notation in either dialect removed."""
    return s.replace("+", "").replace("́", "").replace("´", "").strip().lower()


def _is_plausible(word: str, repl: str) -> bool:
    """Guard against corrupting respellings of native words.

    Respelling is for foreign/brand words; a word already in Cyrillic may only
    GAIN stress marks. Without this, one bad proposal ("сайті" -> "з+аїті")
    silently rewrites the word in every voiceover the brand ever generates.
    A legitimate phonetic respelling of a native word is rejected too — rare,
    and the operator can still add that rule by hand in the brand editor.
    """
    if _LATIN.search(word) or not _CYRILLIC.search(word):
        return True  # transliteration, or digits/symbols — length checks apply
    return _strip_stress(word) == _strip_stress(repl)


def _improve_prompt(pronunciations: dict, rows: list[TtsFeedback]) -> str:
    lines = ["Current fixes already applied (do NOT repeat these words):"]
    lines += [f"  {k} -> {v}" for k, v in (pronunciations or {}).items()] or ["  (none)"]
    lines.append("\nFlagged voiceovers (newest first):")
    for r in rows:
        parts = [f'text: "{r.text}"']
        if r.tags:
            parts.append(f"tags: {', '.join(r.tags)}")
        if r.comment:
            parts.append(f'comment: "{r.comment}"')
        if r.voice:
            parts.append(f"voice: {r.voice}")
        lines.append("- " + " | ".join(parts))
    lines.append(
        '\nReturn JSON: {"fixes": [{"word": "<as written>", "replacement": '
        '"<how to speak it>", "why": "<short reason>"}], "rationale": "<1-2 '
        "sentences>\"}. At most "
        f"{MAX_FIXES} fixes; an empty fixes list is a valid answer."
    )
    return "\n".join(lines)


def _validate_fixes(raw: object, pronunciations: dict) -> list[dict]:
    """Keep well-formed, novel fixes; drop everything else silently. Never raises."""
    existing = {k.strip().lower() for k in (pronunciations or {})}
    out: list[dict] = []
    seen: set[str] = set()
    items = raw.get("fixes") if isinstance(raw, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word") or "").strip()
        repl = str(item.get("replacement") or "").strip()
        why = str(item.get("why") or "").strip()[:MAX_WHY]
        if not (1 <= len(word) <= 100 and 1 <= len(repl) <= 100) or word == repl:
            continue
        if not _is_plausible(word, repl):
            log.warning("tts_improve: discarding corrupting fix %r -> %r", word, repl)
            continue
        key = word.lower()
        if key in existing or key in seen:
            continue
        seen.add(key)
        out.append({"word": word, "replacement": repl, "why": why})
        if len(out) >= MAX_FIXES:
            break
    return out


async def run_tts_improve(job_id: uuid.UUID) -> None:
    """Queue runner for the 'tts_improve' job kind."""
    try:
        async with session_scope() as s:
            job = await s.get(Job, job_id)
            if job is None:
                return
            app = await s.get(App, job.app_id)
            if app is None:
                return
            app_id, pronunciations = app.id, dict(app.pronunciations or {})

        await _set(job_id, status="running", progress=10,
                   message="reading collected feedback")
        async with session_scope() as s:
            rows = (await s.execute(
                select(TtsFeedback).where(TtsFeedback.app_id == app_id,
                                          TtsFeedback.verdict == "down")
                .order_by(TtsFeedback.created_at.desc()).limit(MAX_FEEDBACK_ROWS)
            )).scalars().all()

        if not rows:
            await _set(job_id, status="done", progress=100,
                       message="no negative feedback yet — nothing to mine",
                       result={"fixes": [], "considered": 0})
            return

        await _set(job_id, progress=40, message="mining fixes (LLM)")
        raw = await llm_provider.chat_json(
            IMPROVE_SYSTEM, _improve_prompt(pronunciations, rows), max_tokens=900,
            task="extract")

        fixes = _validate_fixes(raw, pronunciations)
        rationale = str(raw.get("rationale") or "").strip()[:500] if isinstance(raw, dict) else ""
        msg = (f"{len(fixes)} fix(es) proposed from {len(rows)} flagged take(s) — "
               "review and apply in the TTS lab" if fixes else
               f"no confident fixes found in {len(rows)} flagged take(s)")
        await _set(job_id, status="done", progress=100, message=msg[:300],
                   result={"fixes": fixes, "rationale": rationale, "considered": len(rows)})
    except Exception as e:  # noqa: BLE001 — surface any failure to the client
        await _set(job_id, status="error", message="failed", error=f"{type(e).__name__}: {e}")
