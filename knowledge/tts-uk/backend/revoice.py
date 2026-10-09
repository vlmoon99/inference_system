"""Re-record a short's voiceover — the 'revoice' job kind.

A customer who dislikes how a short SOUNDS (a wrong наголос, a rushed line)
gets a fresh TTS take of the same script, re-composed onto the same clip:
same video, same hook, fresh word-timed captions for the new audio. No GPU
video time is spent, so this never takes the video gate. The take counter on
the asset (meta.voiceover.takes_used / takes_max) is what the API enforces:
after the cap the customer makes a new short instead.

params: {"asset_id": <video asset of a done short>}. The voice is resolved the
same way run_short resolves it (request > project > brand voice), so after the
operator re-tunes the brand voice in the TTS lab, a re-record already speaks
with the new one.
"""

import asyncio
import uuid

from ads.core.blobs import store_blob
from ads.core.cancellation import Cancelled
from ads.core.config import settings
from ads.core.db import session_scope
from ads.core.errors import describe_exception
from ads.core.models import App, Asset, Job
from ads.pipelines.ad_post import _set
from ads.pipelines.subtitles import chunk_words
from ads.providers import tts_provider
from ads.providers.files import ensure_any

REVOICE_HISTORY = 5  # earlier composed files kept in meta so nothing is lost silently


def revoice_available(asset: Asset) -> tuple[bool, str]:
    """(ok, reason) — may this asset's voiceover be re-recorded at all?
    Shared by the API (to refuse up front) and the runner (to fail honestly)."""
    meta = asset.meta or {}
    vo, comp = meta.get("voiceover"), meta.get("compose")
    if asset.kind != "video" or not isinstance(vo, dict) or not isinstance(comp, dict):
        return False, "This video has no re-recordable voiceover."
    if not vo.get("text") or not comp.get("raw_video"):
        return False, "This video has no re-recordable voiceover."
    if int(vo.get("takes_used") or 0) >= int(vo.get("takes_max") or 0):
        return False, "All voice takes for this video are used — create a new one."
    return True, ""


async def run_revoice(job_id: uuid.UUID) -> None:
    # Local imports: short.py owns compose + the voice chain; importing it at
    # module load would drag the whole video pipeline into every worker import.
    from ads.core.ai_settings import read_ai_settings
    from ads.core.voice import resolve_voice
    from ads.pipelines.short import (_compose, _tts_supports_cloning, approved_voice_ref,
                                     voiceover_meta)
    try:
        async with session_scope() as s:
            job = await s.get(Job, job_id)
            if job is None:
                return
            params = dict(job.params or {})
            asset = await s.get(Asset, uuid.UUID(str(params["asset_id"])))
            if asset is None or asset.app_id != job.app_id:
                raise RuntimeError("asset not found")
            ok, reason = revoice_available(asset)
            if not ok:
                raise RuntimeError(reason)
            app = await s.get(App, job.app_id)
            meta = dict(asset.meta or {})
            vo, comp = dict(meta["voiceover"]), dict(meta["compose"])
            lang = str(vo.get("lang") or app.default_language or "en")
            ai_defaults = await read_ai_settings(s)
            v = resolve_voice({}, app, ai_defaults, lang)
            pronunciations = app.pronunciations
            polish = v.profile.get("polish") or ai_defaults.get("tts_polish_default")
            voice_ref_rel = await approved_voice_ref(s, app.id)
            brand_color = app.brand_color or None
            asset_id, asset_path = asset.id, asset.path

        sub = asset_path.split("/", 1)[0]
        text = str(vo["text"])
        await _set(job_id, status="running", progress=10, message="recording a new voice take")
        ref_audio = (voice_ref_rel
                     if voice_ref_rel and _tts_supports_cloning(lang, v.engine) else None)
        voice = await tts_provider.generate(
            text=text, subdir=sub, lang=lang, voice=v.voice, engine=v.engine,
            pronunciations=pronunciations, ref_audio_path=ref_audio,
            speed=v.profile.get("speed"), polish=polish,
            expressiveness=v.profile.get("expressiveness"), depth=v.profile.get("depth"),
            ambience=v.profile.get("ambience"),
            ambience_level_db=v.profile.get("ambience_level_db"))
        audio_rel = voice["path"]

        captions: list[dict] = []
        if comp.get("subtitles", True):
            await _set(job_id, progress=45, message="timing the captions")
            try:
                tr = await tts_provider.transcribe(audio_rel, lang=lang, word_timestamps=True)
                captions = chunk_words(tr.get("words") or [])
            except Exception as e:  # noqa: BLE001 — captions are optional, the take is not
                print(f"[revoice] captions skipped ({type(e).__name__}: {e})", flush=True)

        raw_video = str(comp["raw_video"])
        if not (settings.assets_dir / raw_video).is_file() and not await ensure_any(raw_video):
            raise RuntimeError("the original clip is no longer available — create a new video")
        await _set(job_id, progress=70, message="composing the video with the new voice")
        endcard = tuple(comp["endcard"]) if comp.get("endcard") else None
        final_rel = await asyncio.to_thread(
            _compose, settings.assets_dir, sub, raw_video, audio_rel, str(comp.get("hook") or ""),
            captions, bool(comp.get("music")), brand_color, endcard, comp.get("logo"),
            int(comp.get("max_s") or 60))
        if not final_rel or not (settings.assets_dir / final_rel).is_file():
            raise RuntimeError("composing the video produced no file")

        async with session_scope() as s:
            asset = await s.get(Asset, asset_id)
            meta = dict(asset.meta or {})
            takes = int((meta.get("voiceover") or {}).get("takes_used") or 0) + 1
            meta["voiceover"] = voiceover_meta(text, lang, voice, takes_used=takes)
            meta["has_audio"] = True
            gc = dict(meta.get("gen_config") or {})
            gc.update({"tts_engine": voice.get("engine") or voice.get("model"),
                       "tts_voice": voice.get("voice"), "tts_profile": v.profile or None,
                       "subtitles": bool(captions), "caption_chunks": len(captions)})
            meta["gen_config"] = gc
            meta["previous_paths"] = [*(meta.get("previous_paths") or []), asset.path][-REVOICE_HISTORY:]
            asset.meta = meta
            asset.path = final_rel
            await s.flush()
        async with session_scope() as s:
            await store_blob(s, final_rel)
        await _set(job_id, status="done", progress=100, message="new voice take ready",
                   result={"asset_id": str(asset_id), "path": final_rel,
                           "takes_used": takes, "stress_text": meta["voiceover"]["stress_text"]})
    except Cancelled:
        return  # deleted mid-take; no row left to update
    except Exception as e:  # noqa: BLE001
        await _set(job_id, status="error", message="failed", error=describe_exception(e))

