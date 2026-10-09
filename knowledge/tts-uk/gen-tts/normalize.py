"""Text normalization for uk/ru TTS input (contract C3). Pure functions, no service
state — unit-testable without the FastAPI app.

Pipeline: pronunciations (brand-name -> Cyrillic) -> numbers/percent/currency to words
(num2words uk/ru) -> optional stress marks. Stress libs (ukrainian-word-stress,
ruaccent) are heavy OPTIONAL extras (services/requirements-tts-extras.txt); when
missing, stress is skipped silently with one log line per language.
"""

import logging
import re

from num2words import num2words

log = logging.getLogger("gen-tts-normalize")

# Fixed unit words. Grammatical case agreement with the number is out of scope —
# genitive plural is the least-wrong form for ad copy ("50 відсотків", "199 гривень").
_PERCENT = {"uk": "відсотків", "ru": "процентов"}
_CURRENCY = {
    "uk": {"грн": "гривень", "₴": "гривень", "$": "доларів", "usd": "доларів",
           "€": "євро", "eur": "євро", "руб": "рублів", "₽": "рублів"},
    "ru": {"грн": "гривен", "₴": "гривен", "$": "долларов", "usd": "долларов",
           "€": "евро", "eur": "евро", "руб": "рублей", "₽": "рублей"},
}

_NUM = r"\d+(?:[.,]\d+)?"  # integers and simple decimals; 4-digit years read fine as cardinals

# --- dates: "31 серпня" must be an ORDINAL ("тридцять перше серпня"), not the
# cardinal "тридцять один" the generic number pass would produce. Case is picked
# from the preceding word: date-governing prepositions take the genitive
# ("до тридцять першого серпня"), anything else the neuter nominative.

_MONTHS = {
    "uk": ["січня", "лютого", "березня", "квітня", "травня", "червня",
           "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"],
    "ru": ["января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"],
}
_GEN_PREPS = {
    "uk": {"до", "з", "із", "зі", "від", "після", "крім", "окрім", "протягом",
           "станом", "починаючи", "напередодні"},
    "ru": {"до", "с", "со", "от", "после", "кроме", "около", "начиная",
           "накануне", "вплоть"},
}
_YEAR_WORD = {"uk": "року", "ru": "года"}


def _ordinal(n: int, lang: str, genitive: bool) -> str | None:
    """num2words ordinals are masculine nominative ('тридцять перший'); only the
    last word inflects. Returns None when the form is unexpected — the caller
    then leaves the digits for the cardinal pass (never worse than before)."""
    try:
        word = num2words(n, lang=lang, to="ordinal")
    except Exception:  # noqa: BLE001
        return None
    parts = word.split()
    last = parts[-1]
    if lang == "uk":
        if last.endswith("ій"):      # третій -> третє / третього
            last = last[:-2] + ("ього" if genitive else "є")
        elif last.endswith("ий"):    # перший -> перше / першого
            last = last[:-2] + ("ого" if genitive else "е")
        else:
            return None
    else:
        if last.endswith("ий"):      # третий -> третье / третьего
            last = last[:-2] + ("ьего" if genitive else "ье")
        elif last.endswith(("ый", "ой")):  # первый/второй -> первое / первого
            last = last[:-2] + ("ого" if genitive else "ое")
        else:
            return None
    return " ".join(parts[:-1] + [last])


def _wants_genitive(m: re.Match, lang: str) -> bool:
    words = re.findall(r"[\w’']+", m.string[: m.start()])
    return bool(words) and words[-1].lower() in _GEN_PREPS[lang]


def expand_dates(text: str, lang: str) -> str:
    """'31 серпня' / 'до 31 серпня' / '20.08' / '31.08.2026' -> ordinal day
    (+ month name, + ordinal-genitive year). Runs BEFORE expand_numbers so the
    generic cardinal pass never sees date digits. Not exhaustive by design —
    unmatched digits keep today's cardinal reading."""
    months = _MONTHS[lang]

    def _named(m: re.Match) -> str:
        day = int(m.group(1))
        if not 1 <= day <= 31:
            return m.group(0)
        word = _ordinal(day, lang, _wants_genitive(m, lang))
        return f"{word} {m.group(2)}" if word else m.group(0)

    month_re = "|".join(months)
    text = re.sub(rf"(?<![\w.])([0-3]?\d)\s+({month_re})(?![\w’'])",
                  _named, text, flags=re.IGNORECASE)

    def _dotted(m: re.Match) -> str:
        day, mon = int(m.group(1)), int(m.group(2))
        if not (1 <= day <= 31 and 1 <= mon <= 12):
            return m.group(0)
        word = _ordinal(day, lang, _wants_genitive(m, lang))
        if word is None:
            return m.group(0)
        out = f"{word} {months[mon - 1]}"
        if m.group(3):
            year = _ordinal(int(m.group(3)), lang, genitive=True)
            if year:
                out += f" {year} {_YEAR_WORD[lang]}"
        return out

    # Month must be TWO digits (20.08, 31.08.2026) so decimals like "1.5 кг"
    # never read as dates. Lookahead rejects only MORE digits or a decimal tail
    # — a sentence-final "по 15.09.2026." must still match (the old (?![\d.,])
    # refused it and the cardinal pass read "п'ятнадцять кома нуль дев'ять").
    return re.sub(r"(?<![\d.,])([0-3]?\d)\.(0[1-9]|1[0-2])(?:\.(\d{4}))?(?!\d|[.,]\d)",
                  _dotted, text)


def _words(num_str: str, lang: str) -> str:
    s = num_str.replace(",", ".")
    try:
        val = float(s) if "." in s else int(s)
        return num2words(val, lang=lang)
    except Exception:  # noqa: BLE001 — never let normalization break synthesis
        return num_str


def expand_numbers(text: str, lang: str) -> str:
    """50% -> 'п'ятдесят відсотків', '199 грн'/'$199' -> price words, bare digits -> words."""
    units = _CURRENCY[lang]
    unit_re = "|".join(re.escape(u) for u in units)

    def _suffixed(m: re.Match) -> str:
        unit = m.group(2).lower()
        word = _PERCENT[lang] if unit == "%" else units[unit]
        return f"{_words(m.group(1), lang)} {word}"

    def _prefixed(m: re.Match) -> str:
        return f"{_words(m.group(2), lang)} {units[m.group(1).lower()]}"

    # number followed by % or a currency token ("50%", "199 грн", "199 USD")
    text = re.sub(rf"({_NUM})\s*(%|(?:{unit_re})(?![\w’']))", _suffixed, text, flags=re.IGNORECASE)
    # currency symbol before the number ("$199", "€ 5")
    text = re.sub(rf"([$€₴])\s*({_NUM})", _prefixed, text)
    # standalone currency tokens left over ("5 тис. грн" — the number pass never
    # saw "грн" because "тис." sits between; vowel-less "грн" phonemizes as an
    # unreadable "ɦrn")
    text = re.sub(rf"(?<![\w’'])(грн|₴)(?![\w’'])", units["грн"], text, flags=re.IGNORECASE)
    # remaining bare numbers
    return re.sub(_NUM, lambda m: _words(m.group(0), lang), text)


def apply_pronunciations(text: str, pronunciations: dict[str, str]) -> str:
    """Replace brand names with Cyrillic spellings; case-insensitive, word-boundary.
    (?<!\\w)/(?!\\w) instead of \\b so keys ending in punctuation still anchor."""
    for src, repl in pronunciations.items():
        if not src:
            continue
        text = re.sub(rf"(?<!\w){re.escape(src)}(?!\w)", lambda _m, r=repl: r,
                      text, flags=re.IGNORECASE)
    return text


# Manual stress override: "+" typed BEFORE a vowel (RAD-TTS demo syntax, e.g.
# "Сл+ава Укра+їні") converts to the vowel + combining acute — the dictionary
# stresser then SKIPS those words (verified: Stressifier leaves pre-accented
# words alone), so a human/LLM mark always beats the dictionary. Engines that
# want the "+" dialect back (radtts) re-derive it from the accent mark.
_CYR_VOWELS = "аеєиіїоуюяёэыАЕЄИІЇОУЮЯЁЭЫ"


def apply_manual_stress(text: str) -> str:
    text = re.sub(rf"\+([{_CYR_VOWELS}])", "\\1\u0301", text)
    return text.replace("+", "")  # a "+" not before a vowel would be read aloud


def extract_manual_stress(text: str) -> tuple[str, dict[str, str]]:
    """Split "+"-marked input into (clean text, {clean word: accented word}).

    The marks cannot simply be converted up front: the uk neural verbalizer
    reads a literal "+" as the word "плюс", and a combining accent makes it
    mis-read the word entirely (it turned "се́рпня" into "вересня"). Any
    sentinel character fares worse — it is a text-correcting model, so a
    foreign glyph derails the whole sentence. So the marks travel OUTSIDE the
    text and are re-applied afterwards by reapply_manual_stress().
    """
    marks: dict[str, str] = {}
    for token in re.findall(r"[^\s]*\+[^\s]*", text):
        clean = token.replace("+", "")
        accented = apply_manual_stress(token)
        if clean and clean != accented:
            marks[clean] = accented
    return text.replace("+", ""), marks


def reapply_manual_stress(text: str, marks: dict[str, str]) -> str:
    """Put the operator's accents back on whole-word matches. A word the
    verbalizer rewrote simply is not found — the mark is dropped and the
    dictionary stresser handles that word, which is the safe degradation."""
    for clean, accented in marks.items():
        text = re.sub(rf"(?<!\w){re.escape(clean)}(?!\w)", accented.replace("\\", "\\\\"), text)
    return text


def to_plus_stress(text: str) -> str:
    """Inverse of apply_manual_stress: vowel + acute (combining U+0301 or the
    spacing U+00B4 the uk stressifier emits) -> "+" BEFORE the vowel. Two uses:
    RAD-TTS was trained on this notation, and the TTS lab shows it to the
    operator because "+" is far easier to spot than a combining accent."""
    text = re.sub(rf"([{_CYR_VOWELS}])[\u0301\u00b4]", r"+\1", text)
    return text.replace("\u0301", "").replace("\u00b4", "")


# Script tells: letters exclusive to each language. A real Ukrainian sentence
# carries і/ї/є/ґ within a few words; ё/ъ/ы/э exist only in Russian. Ambiguous
# Cyrillic (neither set present — short brand snippets) defaults to uk: this
# product's Cyrillic clients are Ukrainian-first (TTS_CYRILLIC_DEFAULT overrides).
_UK_LETTERS = set("єїіґЄЇІҐ")
_RU_LETTERS = set("ёъыэЁЪЫЭ")


def detect_registry_lang(text: str, lang: str) -> str:
    """Guard against Cyrillic text reaching an English voice. A caller that
    already picked a registry lang (uk/ru) is honored; any other lang value is
    OVERRIDDEN when the text is predominantly Cyrillic — a wrong language chip
    or a stale project default must never ship a Ukrainian script read by an
    American Kokoro voice."""
    if lang in ("uk", "ru"):
        return lang
    letters = [c for c in text if c.isalpha()]
    cyr = [c for c in letters if "Ѐ" <= c <= "ӿ"]
    if not letters or len(cyr) / len(letters) < 0.3:
        return lang
    if any(c in _UK_LETTERS for c in cyr):
        return "uk"
    if any(c in _RU_LETTERS for c in cyr):
        return "ru"
    import os
    return os.environ.get("TTS_CYRILLIC_DEFAULT", "uk")


def _with_accentor_fallback(base):
    """Wrap the dictionary stresser with an OOV fallback: words the trie left
    unstressed (anglicisms, brand names — «крафтова», «бургер», «кешбек» — are
    exactly the words ad copy leans on) go to ukrainian-accentor, a tiny LSTM
    with an argmax head (deterministic). Optional extra: without it, the base
    stresser runs alone, exactly as before."""
    try:
        import ukrainian_accentor as _accentor
    except Exception:  # noqa: BLE001 — lib or its torch dep missing
        log.info("ukrainian-accentor unavailable — OOV words stay unstressed")
        return base

    cache: dict[str, str] = {}

    def _fallback_word(w: str) -> str:
        if w not in cache:
            try:
                cache[w] = _accentor.process(w, mode="stress")
            except Exception:  # noqa: BLE001
                cache[w] = w
        return cache[w]

    vowels = set(_CYR_VOWELS)

    def _stress_all(text: str) -> str:
        text = base(text)

        def _fix(m: re.Match) -> str:
            w = m.group(0)
            if "́" in w or "´" in w or sum(c in vowels for c in w) < 2:
                return w  # already stressed, or monosyllabic (stress is trivial)
            return _fallback_word(w)

        # The class MUST include the accent chars (U+0301/U+00B4): without
        # them an already-stressed word («коле́кцію») matches as TWO fragments
        # around the mark, each fragment gets an accentor guess, and the
        # double-stress collapse then keeps the WRONG mark — silently
        # overriding the operator's manual «+» (real bug, 2026-09-03).
        return re.sub(r"[А-ЩЬЮЯЄІЇҐа-щьюяєіїґ’'́´]{4,}", _fix, text)

    return _stress_all


# lang -> stress callable, or None once probing failed (log once, then silent)
_stress: dict = {}


def _stressor(lang: str):
    if lang in _stress:
        return _stress[lang]
    fn = None
    try:
        if lang == "uk":
            # Combining acute U+0301, NOT the default spacing U+00B4: ipa_uk
            # (styletts2 path) only understands U+0301, and NFKC decomposes
            # U+00B4 into SPACE+U+0301 — splitting every stressed word in half.
            from ukrainian_word_stress import Stressifier, StressSymbol  # heavy optional extra
            base = Stressifier(stress_symbol=StressSymbol.CombiningAcuteAccent)
            fn = _with_accentor_fallback(base)
        elif lang == "ru":
            from ruaccent import RUAccent  # heavy optional extra
            acc = RUAccent()
            acc.load(omograph_model_size="turbo", use_dictionary=True)
            fn = acc.process_all
    except Exception as e:  # noqa: BLE001 — lib or its model missing => no stress
        log.info("stress marking unavailable for %s (%s: %s) — skipping", lang, type(e).__name__, e)
        fn = None
    _stress[lang] = fn
    return fn


def apply_stress(text: str, lang: str) -> str:
    fn = _stressor(lang)
    if fn is None:
        return text
    try:
        text = fn(text)
        # The dictionary sometimes emits DOUBLE marks («за́ти́шна»); every
        # consumer expects exactly one — keep the last mark in the word.
        return re.sub(r"́(?=[^\s]*́)", "", text)
    except Exception as e:  # noqa: BLE001
        log.info("stress marking failed for %s (%s) — skipping", lang, e)
        _stress[lang] = None
        return text


def normalize_for_tts(text: str, lang: str, pronunciations: dict[str, str] | None = None) -> str:
    """Full normalization for a TTS request. No-op for languages other than uk/ru."""
    if lang not in ("uk", "ru"):
        return text
    # Manual наголос marks ride alongside the text, never through it — see
    # extract_manual_stress() for why the verbalizer cannot be shown either a
    # "+" or an accent. They go back on just before the dictionary stresser,
    # which then skips those words.
    text, manual = extract_manual_stress(text)
    if pronunciations:
        text = apply_pronunciations(text, pronunciations)
    text = expand_dates(text, lang)
    if lang == "uk":
        # Neural verbalizer (optional extra): contextual case inflection for
        # remaining digits/abbreviations. No-op when its assets/libs are absent.
        try:
            import verbalize as _verbalize
            text = _verbalize.verbalize(text)
        except ImportError:
            pass
    text = expand_numbers(text, lang)  # deterministic safety net for leftover digits
    text = reapply_manual_stress(text, manual)
    return apply_stress(text, lang)    # dictionary fills in the unmarked words
