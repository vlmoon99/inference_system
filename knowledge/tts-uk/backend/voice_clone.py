"""Voice-clone test — the approval gate for brand voice cloning.

Uploading a voice sample (library kind "voice_ref") does NOT activate cloning.
Instead this job (auto-enqueued on upload, type `voice_clone_test`) synthesizes
one fixed test phrase TWICE:

    baseline — the project's current voice (named voice, no ref)
    cloned   — the same phrase spoken through the uploaded sample

and stores both audio paths on the library row:

    analysis = {"status": "ready", "baseline_path", "cloned_path", "text", ...}

The client compares them side by side in the Library and APPROVES or REJECTS.
Only `status == "approved"` refs are ever sent to the shorts pipeline
(short.py) — an unapproved clone can never reach a real video.
"""

import uuid
from datetime import datetime, timezone

from ads.core.config import settings
from ads.core.db import session_scope
from ads.core.models import App, Job, LibraryAsset
from ads.providers.tts import tts_provider
from ads.pipelines.video_analysis import _set  # same job-progress helper

# One vivid phrase per language — numbers-free (voice rules), stress-sensitive
# words for uk so the clone's наголос is judged too.
TEST_PHRASES = {
    "uk": ("Вітаємо! Це тест голосу для вашого бренду. Перевірені товари, "
           "чесні ціни та швидка доставка — щодня, для вас."),
    "ru": ("Здравствуйте! Это тест голоса для вашего бренда. Проверенные "
           "товары, честные цены и быстрая доставка — каждый день."),
    "en": ("Hello! This is a voice test for your brand. Trusted products, "
           "honest prices and fast delivery — every single day."),
}


async def run_voice_clone_test(job_id: uuid.UUID) -> None:
    try:
        async with session_scope() as s:
            job = await s.get(Job, job_id)
            if job is None:
                return
            app = await s.get(App, job.app_id)
            params = dict(job.params)
            app_id = job.app_id
            lang = (app.default_language or "en") if app else "en"
            engine = app.tts_engine if app else None
            voice = app.tts_voice if app else None
            pronunciations = app.pronunciations if app else None

        lib_id = uuid.UUID(str(params["library_id"]))
        async with session_scope() as s:
            row = await s.get(LibraryAsset, lib_id)
            if row is None or row.app_id != app_id or row.kind != "voice_ref":
                raise RuntimeError("voice sample not found")
            ref_rel = row.path

        text = TEST_PHRASES.get(lang, TEST_PHRASES["en"])
        sub = f"voice-clone/{app_id}"

        await _set(job_id, status="running", progress=20,
                   message="synthesizing the project voice (baseline)")
        baseline = await tts_provider.generate(
            text=text, subdir=sub, lang=lang, voice=voice, engine=engine,
            pronunciations=pronunciations)

        await _set(job_id, progress=60, message="cloning your voice sample")
        cloned = await tts_provider.generate(
            text=text, subdir=sub, lang=lang, pronunciations=pronunciations,
            ref_audio_path=ref_rel)

        analysis = {
            "status": "ready",  # awaiting the client's approve/reject
            "text": text,
            "baseline_path": baseline["path"],
            "cloned_path": cloned["path"],
            "cloned_engine": str(cloned.get("engine") or ""),
            "tested_at": datetime.now(timezone.utc).isoformat(),
        }
        async with session_scope() as s:
            row = await s.get(LibraryAsset, lib_id)
            if row is not None:
                row.analysis = analysis
        await _set(job_id, status="done", progress=100,
                   message="clone ready — compare and approve",
                   result={"library_id": str(lib_id), "analysis": analysis})
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        print(f"[voice_clone] {job_id} failed: {err}", flush=True)
        try:
            async with session_scope() as s:
                row = await s.get(LibraryAsset, uuid.UUID(str(params["library_id"])))
                if row is not None:
                    row.analysis = {"status": "error", "error": err[:300]}
        except Exception:  # noqa: BLE001
            pass
        await _set(job_id, status="error", message="failed", error=err[:500])
