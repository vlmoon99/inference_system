"""Voice catalog policy — which TTS voices the product exposes.

Product decision (2026-08-22): clients pick their project voice themselves,
and ONLY MALE voices are offered for now — no female voices. This module is
the single source of truth for that policy:

  * `male_voices(options, lang)` filters the TTS service's /health payload
    down to the male allowlist (feeds GET /v1/tts/voices — the picker);
  * `is_allowed_voice(engine, voice)` guards writes of App.tts_voice, so a
    crafted PATCH cannot select a female voice either.

Engines without named voices (piper, f5 with a reference clip, single-voice
styletts2) are not offered in the picker; their defaults are male-verified
(kokoro am_michael; elevenlabs catalog leads with George).

When female voices get enabled later: extend the allowlists (or replace them
with per-voice gender metadata) — the picker and the guard follow.

Separately, `UI_ENGINES` narrows WHICH ENGINES a language offers at all.

Policy switch (2026-09-26, owner: "I want to use them all and check"):
VOICE_POLICY=all (the default now) offers EVERY engine and EVERY voice the
TTS service reports — female speakers included, and engines without named
voices (piper, single-voice styletts2) as an engine-only entry that plays the
engine's default. VOICE_POLICY=male restores the male-only, two-engine
catalogue above without touching code.
"""

import os


def policy() -> str:
    """'all' or 'male' — read at call time so a test (or a restart) can flip it."""
    return "male" if os.environ.get("VOICE_POLICY", "all").strip().lower() == "male" else "all"


# Engines a language exposes in the UI (TTS lab chips + the client voice
# picker). A language absent here offers everything it has available.
#
# uk offers radtts + styletts2_multi (2026-09-02). radtts stays the configured
# default — the brand voice, and where feedback/QA data keeps accumulating —
# but styletts2_multi is now exposed alongside it so projects can audition and
# pick from its 13 male speakers (the stress-symbol fix of 2026-09-02 made that
# path competitive). piper and the rest stay registered in engines.py only —
# get_engine()'s fallback chain still degrades through them when the exposed
# engines cannot load, and any engine can be (un)exposed by editing this tuple.
UI_ENGINES: dict[str, tuple[str, ...]] = {"uk": ("radtts", "styletts2_multi")}


def ui_engines(lang: str) -> tuple[str, ...] | None:
    """Allowlist for a language, or None when it is unrestricted."""
    if policy() == "all":
        return None
    return UI_ENGINES.get((lang or "").lower())


def filter_options(options: dict, lang: str) -> dict:
    """Narrow one language's engine status to what the UI may offer: the
    UI_ENGINES allowlist, and within it the MALE voices only — the TTS lab
    picks its voices straight out of this payload, and a female voice listed
    there is a female voice that ends up in a client's short. Returns a copy;
    the service payload itself (used by fallback logic) is never mutated."""
    allowed = ui_engines(lang)
    per_lang = (options.get("engines") or {}).get(lang)
    if policy() == "all" or not allowed or not isinstance(per_lang, dict):
        return options
    out = dict(options)
    engines = dict(out.get("engines") or {})
    narrowed = dict(per_lang)
    if isinstance(per_lang.get("available"), dict):
        narrowed["available"] = {k: v for k, v in per_lang["available"].items()
                                 if k in allowed}
    if isinstance(per_lang.get("voices"), dict):
        narrowed["voices"] = {
            engine: [v for v in names or [] if v in MALE_VOICES.get(engine, ())]
            for engine, names in per_lang["voices"].items() if engine in allowed}
    engines[lang] = narrowed
    out["engines"] = engines
    return out

# Per-engine MALE allowlists. kokoro uses gendered prefixes; the rest are
# curated by hand against the service's catalogs.
_KOKORO_MALE_PREFIXES = ("am_", "bm_")  # american/british male

MALE_VOICES: dict[str, tuple[str, ...]] = {
    "elevenlabs": ("George", "Adam", "Charlie"),
    "ukrainian_tts": ("mykyta", "dmytro", "oleksa"),
    "radtts": ("mykyta",),  # RAD-TTS++ uk: lada/tetiana are female
    "styletts2_multi": (
        "Артем Окороков", "Вʼячеслав Дудко", "Денис Денисенко",
        "Кирило Татарченко", "Матвій Ніколаєв", "Михайло Тишин",
        "Олександр Ролдугін", "Павло Буковський", "Петро Філяк",
        "Роман Куліш", "Тарас Василюк", "Юрій Вихованець", "Юрій Кудрявець",
    ),
}


def is_allowed_voice(engine: str | None, voice: str) -> bool:
    """May this (engine, voice) pair be set as a project voice? Empty voice is
    always fine (service default — male-verified). Unknown engine: the voice
    must be male in AT LEAST one catalog (defensive for engine=None writes)."""
    if not voice:
        return True
    if policy() == "all":
        # Everything is offered, but a voice that is KNOWN to belong to another
        # engine is still refused: radtts + a styletts2 speaker name would render
        # radtts's default for every customer (the 2026-09-19 incident).
        if engine and voice not in MALE_VOICES.get(engine, ()):
            others = {v for e, names in MALE_VOICES.items() if e != engine for v in names}
            return voice not in others
        return True
    if engine == "kokoro" or (engine is None and voice.startswith(_KOKORO_MALE_PREFIXES)):
        return voice.startswith(_KOKORO_MALE_PREFIXES)
    if engine:
        return voice in MALE_VOICES.get(engine, ())
    return any(voice in names for names in MALE_VOICES.values())


def male_voices(options: dict, lang: str) -> list[dict]:
    """Flatten the TTS service /health payload into the picker list for one
    language: [{engine, voice}], active engine's voices first. Under the
    male policy: exposed engines and male voices only. Under `all`: every
    voice of every available engine, plus an engine-only entry (voice "") for
    each available engine that has no named voices."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    lang = (lang or "en").lower()
    everything = policy() == "all"
    exposed = ui_engines(lang)

    per_lang = (options.get("engines") or {}).get(lang) or {}
    available = per_lang.get("available") or {}

    def _add(engine: str, names) -> None:
        if exposed is not None and engine not in exposed:
            return
        if everything and available.get(engine) is False:
            return   # the service lists its speakers but cannot load it on this box
        for v in names or []:
            allowed = everything or (v.startswith(_KOKORO_MALE_PREFIXES) if engine == "kokoro"
                                     else v in MALE_VOICES.get(engine, ()))
            if allowed and (engine, v) not in seen:
                seen.add((engine, v))
                out.append({"engine": engine, "voice": v})

    voices_by_engine = per_lang.get("voices") or {}
    active = per_lang.get("active")
    if active in voices_by_engine:  # active engine's voices lead the list
        _add(active, voices_by_engine[active])
    for engine, names in voices_by_engine.items():
        _add(engine, names)
    if everything:
        for engine, ok in available.items():
            if ok and not voices_by_engine.get(engine) and (engine, "") not in seen:
                seen.add((engine, ""))
                out.append({"engine": engine, "voice": ""})   # the engine's own default voice
    if lang == "en" or not voices_by_engine:
        _add("kokoro", options.get("voices"))
    return out
