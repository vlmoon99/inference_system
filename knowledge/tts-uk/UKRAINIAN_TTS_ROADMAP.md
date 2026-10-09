# Ukrainian voice for AI shorts — learning roadmap

What to learn to make the Ukrainian voiceover genuinely good, ordered by
payoff. Context: the current uk voice is StyleTTS2-Ukrainian
(`patriotyk/styletts2_ukrainian_single`, the "filatov" voice) behind a
stress → IPA chain (`ukrainian-word-stress` → `ipa-uk`), Piper fallback —
already the best open Ukrainian TTS. So most of the perceived badness comes
from *around* the model: stress, normalization, prosody, mastering. Stack
reference: `docs/SHORTS_OPTIMIZATION.md` §4, service:
`inference/adapters/gen-tts/`.

## 1. Ukrainian stress & pronunciation — biggest lever, cheapest

StyleTTS2-Ukrainian lives or dies by stress placement.
`ukrainian-word-stress` guesses wrong on ambiguous words (за́мок/замо́к),
names, brands, loanwords — exactly what ad copy is full of. One wrong stress
per sentence makes a voice sound broken even when the audio is clean.

**Learn:** how the stress dictionary and `ipa-uk` phonemization work; how to
inject manual stress marks into the input text.

**Apply here:** build a per-project pronunciation override list — onboarding
already collects `pronunciations`; make sure the TTS normalization chain
(`inference/adapters/gen-tts/normalize.py`) actually consumes them for brand
names.

## 2. Text normalization for ad copy

"-15%", "iPhone 15 Pro", "з 20.08", "грн" must become correctly **inflected**
Ukrainian words (п'ятнадцяти відсотків vs п'ятнадцять відсотків depending on
case). `num2words` doesn't know grammatical case.

**Learn:** Ukrainian morphology tooling (pymorphy3 + UA dictionaries); the
existing `normalize.py` pipeline.

**Free complementary trick — ✅ DONE 2026-08-20:** the shorts voiceover prompt
(`pipelines/short.py`) now tells the LLM the text is read aloud by TTS: numbers,
prices and dates as words in the correct grammatical form, no digits or
abbreviations, short sentences. Layered under it: the deterministic
date-ordinal pass and the neural verbalizer (§4b) catch whatever the LLM still
writes as digits.

## 3. Prosody, delivery, mastering

**Learn:** what StyleTTS2 exposes (speed — `STYLETTS2_SPEED` — and
style/diffusion parameters); how sentence splitting changes intonation
(synthesizing sentence-by-sentence with explicit pauses usually beats one
long paragraph); basic voiceover mastering beyond loudnorm — light
compression, de-essing, a touch of EQ. Ads tolerate a produced sound; raw TTS
does not.

## 4. The Ukrainian speech community

**Follow:** the `speech-uk` / Yehor Smoliakov ecosystem on Hugging Face and
patriotyk's repos — Ukrainian checkpoints, stress dictionaries, and corpora
appear there first. Check whether patriotyk's multispeaker StyleTTS2
checkpoint offers a nicer voice than filatov. Verify licenses before shipping
anything (MODELS.md already tracks this discipline).

## 4b. Open-source shortlist (researched 2026-08-20)

Everything below is free; licenses verified where shown. Ordered by
integration payoff:

| What | Where | License | Why |
|---|---|---|---|
| **Neural verbalizer** — ✅ INTEGRATED 2026-08-20 | `skypro1111/m2m100-ukr-verbalization-ct2` (CTranslate2, CPU int8) | check card | Converts numbers/abbreviations to **correctly inflected** Ukrainian — fixes the num2words case problem. Now live: `inference/adapters/gen-tts/verbalize.py`, assets in `data/models/verbalizer/uk/`, `TTS_VERBALIZER=0` disables. Deterministic date-ordinal pass + num2words remain as the fallback chain. |
| **Multispeaker StyleTTS2** | `patriotyk/styletts2_ukrainian_multispeaker_hifigan` (+`_istftnet` variant) | MIT | Multiple voices (style `.pt` per speaker), and "faster because no diffusion at inference". Our engine already supports a model-dir override (`STYLETTS2_UK_MODEL_DIR`); needs voice-style selection. |
| **Manual stress syntax** | `+` after the stressed vowel, supported by the stress→IPA chain | — | The escape hatch for brand names and ambiguous words; can be driven from the app's `pronunciations` overrides. |
| **Neural stress model** | `patriotyk/stressifier-byt5-g2p-model` (0.3B ByT5) | no card yet | Candidate replacement for dictionary-based `ukrainian-word-stress` on ambiguous words — needs evaluation. |
| **HolosTTS** | `patriotyk/HolosTTS` (ONNX) | MIT | His brand-new engine ("faster, higher quality"), README still empty — watch it, don't bet on it yet. |
| **RAD-TTS++ Ukrainian** | `Yehor/radtts-uk` (+ Vocos/HiFiGAN/BigVGAN Spaces) | check card | The main alternative engine family from the speech-uk community; useful as an A/B reference voice. |
| **Corpus tool (later)** | `patriotyk/narizaka` (GitHub) | open | Builds high-quality TTS corpora from audiobooks — the tool for the eventual fine-tune phase. Reference dataset: `patriotyk/filatov_24000`. |

Skip: `facebook/mms-tts-ukr` (CC-BY-NC — non-commercial) and the older
`robinhad/ukrainian-tts` Coqui/ESPnet voices (superseded by StyleTTS2-Ukrainian).

## 5. Fine-tuning your own voice — the real fix, aim for it last

To get a branded, genuinely pleasant voice:

1. Hire a Ukrainian voice actor; record 1–3 clean hours **with a contract
   covering synthetic reuse**.
2. **Learn the dataset pipeline:** segmentation, transcription, forced
   alignment (WhisperX), quality filtering.
3. **Learn TTS fine-tuning:** StyleTTS2 (known-good recipe for Ukrainian) or
   F5-TTS (no public UA checkpoint exists — a UA fine-tune would be a first).
4. **Learn evaluation:** a fixed set of ~30 real ad scripts, A/B listening on
   every change; UTMOS-style automatic scores help, ears decide.

## Sequencing

Do §1–3 inside the current stack first — days, not weeks, mostly
prompt/dictionary work. Evaluate honestly against the fixed script set. Only
then invest in §5 — otherwise the fine-tuned model inherits the same broken
stress and normalization.

## The feedback flywheel (live as of 2026-08-20)

Three loops, from instant to long-term:

1. **Pronunciation fixes** (`/tts-lab`, stored on the project): word →
   respelling or `+`-stress. Applies to every future voiceover immediately.
2. **Take verdicts** (`tts_feedback` table, append-only): 👍/👎 per take with
   tags (wrong-stress / wrong-number-form / wrong-word / robotic / speed) and
   a comment. 👎 rows carry the exact text+engine+voice — the **regression
   set** to re-synthesize after every pipeline change, and the raw material
   for mining new pronunciation fixes. Shorts ratings' `wrong-voice` tag flags
   TTS problems from the feed side.
3. **Auto-QA — the machine ear** (live 2026-08-20): every uk/ru short's
   voiceover auto-enqueues a `tts_qa` job (also manual: `POST /v1/tts/qa`):
   re-synthesize → transcribe with faster-whisper (medium, int8 CPU,
   `data/models/whisper/medium`, service `/transcribe`) → word-align heard vs
   the normalized text actually spoken. The comparator undoes Whisper's
   inverse normalization (digits, %, dates→ordinals) and fuzzy-matches
   inflection variants, so only GROSS errors flag (validated on live audio:
   clean take = WER 0, wrong word = flagged). Findings land as `[auto-QA]`
   rows in the same `tts_feedback` dataset — the ✨ fix miner consumes human
   and machine findings identically. Note: ASR can't hear stress errors —
   those still need human 👎.
4. **Fine-tune fuel** (future): when the dataset is big enough, verdicts
   become the eval set for a custom-voice fine-tune (§5), and an LLM job can
   mine the comments to auto-propose fixes (propose → review → apply, like
   the analytics profiler).

**Default voice** (2026-08-20): `styletts2_multi`, voice #09 "Денис
Денисенко" (`TTS_ENGINE_UK` / `STYLETTS2_UK_VOICE` in `inference/hosts/dgx-spark/services.sh`).
