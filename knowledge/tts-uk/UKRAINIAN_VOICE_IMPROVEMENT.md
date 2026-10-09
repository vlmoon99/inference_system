# Improving the Ukrainian Male Voice — research

Date: 2026-09-02. **Implementation status (same day): the deterministic chain is LIVE
on the RadTTS-uk path (the engine of record — mykyta, explicit наголос):** stress-symbol
fix (#1), joiner + per-sentence seeded RadTTS synthesis (#2/#9/#13/#15/#23), short.py
two-pass loudnorm + alimiter (#3), accentor OOV fallback + house double-stress collapse
(#4), `male_smooth` preset with despectral/mb_comp/RBJ/lp24k/noise-floor (#7/#17/#18/#19/#28),
date + грн text fixes (#16/#24). Not yet done: style diffusion (#5), multi-voice audition (#6),
enhancer registry (#8/#11), QA metrics (#14), times/phones/Latin fallback (#10, partial via accentor). Scope: StyleTTS2-Ukrainian "filatov" primary path + RAD-TTS "mykyta" + polish.py/short.py chain, on the DGX Spark (aarch64, CUDA). All findings below were adversarially verified against the actual repo; verdicts noted.

---

## 1. TL;DR — the recommended path

- **Fix the stress-symbol bug first (hours, CONFIRMED, empirically A/B'd).** `Stressifier()` emits spacing acute U+00B4; `ipa_uk` only understands combining U+0301, and NFKC in `_phonemize` splits every stressed word in half. Every word the model hears today is fragmented and vowel-reduced. One-line fix at `engines.py:245` and `normalize.py:221`: `Stressifier(stress_symbol=StressSymbol.CombiningAcuteAccent)`. Verified: same sentence 5.83 s (halting) → 4.97 s (fluent).
- **Build the deterministic joiner (hours, CONFIRMED).** Replace the bare `torch.cat(wavs)` at `engines.py:306` with silence-trim → punctuation-scaled pauses (0.35 s after `.?!`, 0.25 s after `:`) → 10–15 ms cosine edge fades → per-chunk RMS match (±3 dB clamp) → per-sentence speed variation. This kills the machine-gun pacing and seam clicks — the loudest robotic tell in multi-sentence reads.
- **Ship the `male_smooth` polish preset (section 3) and stop re-compressing in short.py.** `short.py:662,766` runs dynamic one-pass `loudnorm` plus a 3:1 `acompressor` on audio polish.py already compressed and normalized — replace with deterministic two-pass loudnorm (`linear=true` with measured values). Every short ships through this path.
- **Wire per-sentence text-conditioned prosody (days, WEAKENED → corrected recipe works).** `predict_style_single` exists in the installed package and the filatov checkpoint carries a trained 43.8 M-param diffusion head that is never called. Blend its prosody half (`s_pred[:,128:]`, t≈0.3–0.5) into the frozen `style.pt`, with `torch.manual_seed` per call and a mandatory finite/norm guard (unguarded, seed 1234 + `embedding_scale=1.3` deterministically NaN'd on a real sentence).
- **Measure, don't vibe.** Extend `tts_qa.py` (WER-only today) with click count, `librosa.pyin` f0 stats, and SpeechMOS `utmos22_strong` (MIT; do NOT use NISQA — weights are CC BY-NC). Then A/B: current filatov vs. fixed chain vs. `styletts2_multi` male voices vs. resemble-enhance behind a flag.

---

## 2. Why the current voice sounds robotic — root causes

Ranked by audibility, each tied to code:

1. **Corrupted phoneme input (the primary cause).** `engines.py:245` and `normalize.py:221` construct `Stressifier()` with the default spacing acute `´` (U+00B4). `ipa_uk` defines `ACUTE = chr(0x301)` and maps only that to `ˈ`. Worse, `_phonemize` (`engines.py:274-285`) runs `unicodedata.normalize('NFKC', t)`, which decomposes U+00B4 into SPACE + U+0301 — splitting every stressed word mid-word. Actual model input today: `krɐftɔʋɐ kɐ ˈʋɐ, sʲʋʲi ˈʒɐ ʋɪ ˈpʲit͡ʃkɐ` instead of `krɐftɔʋɐ ˈkaʋɐ, ˈsʲʋʲiʒɐ ˈʋɪpʲit͡ʃkɐ`. Words broken, stress on the wrong fragment, all vowels reduced to unstressed allophones. Reproduced end-to-end this session; fixed chain synthesizes the same sentence 15% shorter and fluent (A/B wavs in scratchpad: `A_current.wav` / `B_fixed.wav`).
2. **Machine-gun pacing and seams.** `engines.py:297-306`: each `_split_parts()` sentence is synthesized independently and joined with a bare `torch.cat` — zero inter-sentence silence, no fade, no level match. The inference package additionally caps the final token's duration: `pred_dur[-1].clamp(max=6)` (`.venv/.../styletts2_inference/models.py:706`) = ~75 ms at hop 300 / 24 kHz, so each sentence's trailing pause is truncated and the next starts instantly.
3. **Frozen prosody.** `engines.py:303` passes the same `style.pt` tensor (loaded at `engines.py:249`) as `s_prev` for every sentence of every request. The checkpoint's text-conditioned style diffusion (`predict_style_single`, `models.py:642`) is never called — melody cannot follow sentence content. Same-reset pitch on every sentence = the "TTS chant".
4. **OOV/anglicism stress holes.** Measured: `крафтова`, `бургер`, `кешбек`, `застосунок`, `худі`, `світшотів` etc. get zero stress mark from the dictionary → fully vowel-reduced, flat rendering on exactly the brand-critical words. Latin tokens (`yaknove.ua`, `Instagram`, `QR`) reach `ipa_uk` raw and phonemize as garbage (`yɐknove.ʊɐ`, `qr kɔd`).
5. **Triple-cascaded dynamics downstream.** polish.py compresses (~2:1, `polish.py:115-127`) and RMS-normalizes (`polish.py:256`); then `short.py:662` (music path) and `short.py:766` (voice-only) both apply `loudnorm=I=-16:TP=-1.5:LRA=11,acompressor=threshold=-18dB:ratio=3`. One-pass loudnorm runs in *dynamic* mode — time-varying gain that pumps on pauses and lifts vocoder rasp between phrases; the 3:1 compressor then flattens what survived.
6. **Minor colorations in polish.py itself.** `_shelf` (`polish.py:164-170`) blends a causal Butterworth copy back onto dry signal → measured phase-rotation ripple (up to −1.1 dB dip at 328 Hz) in the male body band; no declick/join stage; nothing tames the 24 kHz istftnet top-octave hash.

---

## 3. THE DETERMINISTIC SMOOTHING FUNCTION

Two layers, both pure functions of (text, audio) — no RNG except fixed-seed generators, fully unit-testable.

### 3a. Engine-side joiner (engines.py — replace the `torch.cat` at line 306)

Implement in `StyleTTS2Engine.synthesize` (base class, so `styletts2_multi` inherits it). Per chunk, after `.cpu().numpy()`:

1. **Trim** leading/trailing silence where 10 ms-window RMS < −50 dBFS (reuse `_envelope_fast`, `polish.py:100`). Mandatory — `_phonemize` appends terminal punctuation so the model emits variable trailing silence; without trimming, inserted pauses double up.
2. **Cosine edge fades**, 10–15 ms (`n = int(0.015*sr)`, `g = cos/sin(t*pi/2)`) on each chunk's edges. Note: with silence inserted between chunks, an "equal-power crossfade" degenerates to a fade-out/fade-in pair — implement it as exactly that.
3. **Punctuation-scaled pauses** (zeros), scaled by `1/speed`: `.?!` → 0.35 s, `:` → 0.25 s, comma-subchunks (if the length cap below splits there) → 0.15 s, paragraph break → 0.6 s. Last chunk: no appended pause (polish.py `_edge_fade` handles the tail). `_split_parts` (`engines.py:253-263`) preserves per-chunk terminal punctuation, so this is a lookup.
4. **RMS match**: scale each chunk toward the running median chunk RMS, gain clamped ±3 dB.
5. **Per-sentence speed**: `speed_i = base * (1 + s_i)`, with `s_1 = +0.02`, `s_last = -0.04` (last wins for single-sentence inputs), middle `s_i = ((zlib.crc32(part.encode()) % 7) - 3) * 0.005`. Hash of the text, not an RNG — same script always renders identically. Speed is already threaded per part (`engines.py:295-304`). Note RAD-TTS already has a native lever (`dur_scale`, `engines.py:563-564`) and Piper supports `length_scale` — no WSOLA needed anywhere; drop that from scope.
6. **Breath insertion** into pauses ≥ 300 ms where the next chunk is ≥ 80 chars, at most every 2nd slot, never slot 1: 300 ms of `default_rng(31 + slot_index)` noise → bandpass 400–2500 Hz (butter order 2) → raised-cosine envelope 120 ms attack / 180 ms decay → −32 dB relative to clip voice RMS → overlap last 50 ms into the next chunk's fade-in. Seed varies by slot so breaths aren't identical waveforms.
7. **Chunk length cap** in `_split_parts`: while a part exceeds ~180 chars, split at the comma/`—` nearest the midpoint (fallback: nearest space); add `…` to the split set. Prevents StyleTTS2's long-input rush/garble on comma-only enumeration copy. Apply the same idea in `verbalize.py` (sentences > `_MAX_SENT_CHARS=350` silently skip the neural verbalizer today).

Also apply the trim+fade joiner to the Piper fallback concat (`engines.py:97-101`).

### 3b. polish.py — `male_smooth` preset

New `PRESETS` entry (auto-appears in the TTS lab via `app.py` `/catalogue` → `list(PRESETS)`; select per project via `apps.tts_profile`):

```python
"male_smooth": {
    "hp": 70,
    "despectral": 0.4,        # cepstral de-buzz (new stage, see below)
    "deess": 0.45,
    "deharsh": 0.55,
    "mb_comp": {"xover": (160, 4000), "ratios": (2.0, 2.5, 3.0)},  # fallback "comp": 0.40
    "warmth": 1.5, "depth": 0.30,
    "lp24k": 10500,           # gentle top-octave tame, sr==24000 only
    "exciter": 0.0,           # off by default (see caveat)
    "drift_cents": 0,         # off by default (near-JND; doctrine conflict)
    "double": 0.0,            # off by default (mono comb risk)
    "room": 0.20, "room_mix": 0.12,
    "noise_floor_db": -63,
    "target_db": -18.0,
}
```

Chain order in `polish()` (extends the existing pattern at `polish.py:231-257`):

`hp → despectral → deess → deharsh → mb_comp → warmth/depth shelves (RBJ) → phase rotator → lp24k → room → noise floor → normalize → edge fades`

Stage specs with corrected parameters:

- **despectral (cepstral de-buzz)** — attacks the −13 dB HNR rasp mechanism (`polish.py:144-152` measurement) rather than its level. `scipy.signal.stft` nperseg 1024 / hop 256 / hann at 24 kHz; per frame `ceps = np.fft.irfft(np.log(|X|+eps))`; attenuate quefrency bins 20–60 by g=0.6 — **applied symmetrically to bins `n` and `N-n`** (asymmetric liftering halves the effect via implicit symmetrization — verified numerically); gate: only frames whose 20–60-bin energy exceeds the 75th percentile of a 1 s rolling window; keep original bins below 1 kHz (blend mask); reapply original phase, istft. Pitch quefrency for these voices (f0 ~110–130 Hz) sits at bin ~185–220 — safely far from the lifter. Re-measure HNR on filatov first (the −13 dB figure was measured on radtts/mykyta).
- **mb_comp** — complementary split: `low = LP160`, `high = HP4k` (zero-phase `sosfiltfilt`, butter order 2), `mid = x - low - high` (sums exactly flat at unity gain — unit-test that). LOW: thresh −22 dB, 2:1, 25 ms window (stops vowel boom pumping the presence band); MID: current `_compress` settings; HIGH: thresh −28, 3:1, 4 ms (catches 4–5 kHz sibilance the 5 kHz de-esser split misses). Single makeup after summing. Reuses `_envelope_fast`.
- **RBJ shelves** — replace `_shelf`'s filtered-copy blend (`polish.py:164-170`) with closed-form RBJ shelving biquads via `sosfilt` (~15 lines) for warmth/presence/tilt/depth. Removes the measured phase-ripple coloration in the 200 Hz–3 kHz body.
- **phase rotator** — two cascaded first-order allpasses at ~200 Hz before `_normalize`. **Coefficient sign matters**: `a = (tan(pi*f/sr) - 1) / (tan(pi*f/sr) + 1)` (≈ −0.949); the (1−tan)/(1+tan) form is inert. Buys ~1 dB crest factor on asymmetric male speech; only pays if `target_db` is raised (matters for `broadcast` at −15).
- **noise floor** — `default_rng(23).standard_normal` → `_filt` band (100, 6000) → −63 dBFS RMS, added after `_normalize`, before `_edge_fade`, inside the preset-gated chain. Mostly matters once the joiner inserts digital-silence pauses; redundant when an ambience bed is active (harmless). Optional TPDF dither (`rng(29)`, 1 LSB int16) at the `sf.write` in app.py.
- **lp24k** — 1st-order (or gentle 2nd-order butter ~11 kHz if hash remains audible) lowpass for `sr==24000` only; keep out of `broadcast`.
- Gated-off knobs kept implemented for the lab: `drift_cents` (LFO micro-pitch wow, ≤12 cents, rates <4 Hz — safe but near-JND; use `pos = np.cumsum(rate) - rate[0]`), `double` (must be built as duration-preserving fixed-grain OLA, NOT plain resample — a constant-rate resample drifts 4.6 ms/s and turns into pre-echo past ~3 s; the pipeline is mono end-to-end so "Haas" framing doesn't apply), `exciter` (normalize the 90–320 Hz band to fixed RMS before `tanh` or the harmonics sit 50–70 dB down; honest rationale is missing-fundamental psychoacoustics, not the 60–200 Hz LTAS hole — its output lands at 180–960 Hz).
- **declick** for chunk-boundary protection: micro 2 ms cosine fades at joins is the primary defense (3a); if a generic declicker is added, do not use an absolute 0.2 FS sample-delta threshold (false-positives on sibilants — an 8 kHz component at 0.15 FS gives deltas ~0.26); use a relative/median-outlier criterion on a low-passed residual.

At 44.1 kHz (Vocos/RAD-TTS path) only STFT sizes scale (nperseg 2048 / hop 512); Hz-domain parameters stay as written.

### 3c. short.py fix

Replace `loudnorm=I=-16:TP=-1.5:LRA=11,acompressor=threshold=-18dB:ratio=3` at `short.py:662` and `:766` with **two-pass loudnorm**: first pass measures, second passes `measured_I/measured_TP/measured_LRA/measured_thresh` + `linear=true` — deterministic per input, one extra ffmpeg invocation, and correct even when polish was disabled or failed (polish deliberately never fails — `polish.py:257-260` returns dry audio on exception, and `target_db` is preset-dependent: broadcast −15, intimate −19 — so a blind fixed `volume=2dB` is wrong). In `_music_audio_filter` keep only `alimiter` after `amix`. Keep `-ar 48000`.

### 3d. Tests to extend (`backend/tests/test_tts_polish.py`)

- Joiner: synthetic ramps in → assert pause lengths per punctuation, no sample-delta > threshold at joins, RMS-match clamp, hash-derived speeds stable across runs, breath slots (skip slot 1, every-2nd rule).
- `mb_comp`: `low+mid+high == x` at unity gain (exact).
- RBJ shelves: frequency-response assertions at corner/passband points.
- despectral: symmetric-lifter attenuation factor; pure tone below 1 kHz passes bit-identical.
- Preset registry: `male_smooth` present, unknown-key tolerance (`cfg.get` pattern), byte-identical output for identical input (determinism golden test — feasible once section 5's seed pinning lands).
- short.py: filter-string construction unit test for the two-pass path.

---

## 4. Ranked improvement table

| # | What | Impact | Effort | Det. | License | Verdict |
|---|------|--------|--------|------|---------|---------|
| 1 | Stress symbol U+00B4→U+0301 fix (engines.py:245, normalize.py:221) | High (verified A/B) | Hours | Yes | MIT | CONFIRMED |
| 2 | Chunk joiner: trim + pauses + fades + RMS match (engines.py:306) | High | Hours | Yes | own code | CONFIRMED (×3 findings) |
| 3 | short.py double-compression → two-pass loudnorm (short.py:662,766) | High | Hours | Yes | own code | CONFIRMED |
| 4 | OOV stress: port fork's `ukrainian-accentor` fallback + repo house-dict for brand terms | High | Hours | Yes (argmax) | MIT | CONFIRMED |
| 5 | Per-sentence diffusion style blend (prosody half `[128:]`, t≈0.3–0.5, seeded, norm-guarded) | High | Days | Yes w/ per-call `torch.manual_seed` | MIT | WEAKENED — recipe corrected |
| 6 | `styletts2_multi` male voices (13 candidates, zero code change) | High (audition-dependent) | Hours | Yes (s_prev direct) | MIT | CONFIRMED |
| 7 | `male_smooth` preset + chain (section 3b) | High (combined) | Days | Yes | own code | CONFIRMED |
| 8 | resemble-enhance stage (enhance-only, `lambd≈0.0–0.1`, nfe 32–64) | High (timbre) | Days | Reproducible (internal `manual_seed(0)`) | MIT | CONFIRMED ×2 |
| 9 | Per-sentence speed variation (crc32 hash schedule) | Low-med alone, multiplies with pauses | Hours | Yes | own code | CONFIRMED |
| 10 | Latin/brand/URL fallback layers in normalize.py | Med-high | Days | Yes | own code | CONFIRMED |
| 11 | `enhance.py` registry blueprint (`ENHANCERS`, tts_profile key) | High (enabler) | Days | Yes | own code | CONFIRMED |
| 12 | Seed-pin SineGen noise (fork_rng + manual_seed) → golden-file tests | Medium (enabler) | Hours | Yes | MIT | CONFIRMED |
| 13 | RAD-TTS per-call reseeding (`RADTTS_SEED`) | Medium (enabler) | Hours | Yes | Apache-2.0 | CONFIRMED |
| 14 | QA metrics: clicks + pyin f0 stats + SpeechMOS utmos22_strong | Medium (multiplier) | Days | Yes | MIT/ISC | WEAKENED — drop NISQA |
| 15 | Chunk length cap ~180 chars + `…` split | Medium | Hours | Yes | own code | CONFIRMED |
| 16 | Date regex fix `(?!\d\|[.,]\d)` (normalize.py:115) | Medium | Minutes | Yes | own code | CONFIRMED |
| 17 | polish gaps: RBJ shelves, declick, 10.5 kHz LP | Medium | Days | Yes | own code | CONFIRMED |
| 18 | 3-band mb_comp | Medium | Hours-days | Yes | own code | CONFIRMED |
| 19 | Cepstral de-buzz (symmetric lifter) | Medium | Days | Yes | own code | CONFIRMED |
| 20 | AP-BWE 24k→48k (StyleTTS2 path only) | Medium | Days | Yes | MIT incl. weights | CONFIRMED |
| 21 | Sidon v0.1 experiment (A/B vs resemble-enhance) | Medium (uncertain) | Days | Yes | MIT (verify weights) | CONFIRMED as experiment |
| 22 | `STYLETTS2_SPEED=0.93` default (scope per-lang: `STYLETTS2_SPEED_UK`) | Low-med, free | Hours | Yes | MIT | CONFIRMED |
| 23 | Breath insertion (dep. on #2) | Low-med | Hours | Yes | own code | CONFIRMED |
| 24 | Text-norm safety net: times, abbrevs (`вул.` is broken even WITH verbalizer; standalone `грн` bug is live) | Medium on live bugs, insurance otherwise | Hours | Yes | own code | CONFIRMED/WEAKENED mix |
| 25 | byt5 stress **benchmark** now; hybrid stressifier later | Med-high (benchmark: med) | Days | Yes (beam, no sampling) | Apache code; weights unlicensed — resolve first | WEAKENED |
| 26 | RAD-TTS sigma experiments (sigma_f0 0.8, decoder 0.5–0.7 via `RADTTS_SIGMA`) | Medium (uncertain; current defaults ARE reference) | Hours | Yes w/ #13 | Apache-2.0 | WEAKENED |
| 27 | Phase rotator + zero-phase static EQ | Low-med | Hours | Yes | own code | CONFIRMED (sign fixed) |
| 28 | Comfort-noise floor + TPDF dither | Low (with #2: low-med) | Hours | Yes | own code | WEAKENED — contingent on #2 |
| 29 | Brief-writing rules + lint in TTS lab | Medium | Hours | Yes | own code | WEAKENED — drop ALL-CAPS rule, fix dash rule |
| 30 | HolosTTS + filatov clone | High if quality lands | Days | Seed-fixable | Weights MIT; **inference code unlicensed** | WEAKENED — blocked on license |
| 31 | Micro-pitch drift LFO | Modest/uncertain | Hours | Yes | own code | WEAKENED — ship off |
| 32 | TD-PSOLA declination | Med (uncertain premise) | Days | Yes | own impl | WEAKENED — measure f0 slope first |
| 33 | Low-mid exciter | Low-med | Hours | Yes | own code | WEAKENED — needs band normalization |
| 34 | Static doubler | Low | Hours | Yes | own code | WEAKENED — OLA impl, default 0 |
| 35 | RVC filatov→smoother male (via Applio, NOT rvc-python) | Medium (timbre only) | Weeks | Yes | MIT + Apache corpus | WEAKENED — only if timbre complaints persist |
| 36 | Digit ordinals / phone `+380` regex passes | Low (fallback + `+380` is live) | Hours | Yes | own code | WEAKENED |

**Build order by ROI:** 1 → 2 → 3 → 4 → 16 → 22 → 9 → 7 (preset skeleton: mb_comp, RBJ, LP, floor) → 12/13 → 14 → 5 → 6 (audition) → 15 → 10 → 19 → 23 → 8 (behind flag) → 20/21 A/B → rest as data demands.

---

## 5. Model & vocoder upgrades

**`patriotyk/styletts2_ukrainian_multispeaker_hifigan`** (2025-03-25) and **`patriotyk/styletts2_ukrainian_multispeaker_istftnet`** (2025-06-07), both MIT, both `config.yml + pytorch_model.bin`, 24 kHz. `StyleTTS2MultiEngine` (`engines.py:318`) already implements the layout: download into `data/models/styletts2/uk-multi` (`STYLETTS2_MULTI_UK_MODEL_DIR`), pull `voices/*.pt` from the HF Space `patriotyk/styletts2-ukrainian` (31 vectors; 13 male stems incl. Артем Окороков, Денис Денисенко, Роман Куліш, Тарас Василюк…), set `TTS_ENGINE_UK=styletts2_multi` + `STYLETTS2_UK_VOICE=<stem>`. The old multispeaker repo id 307-redirects to `_hifigan`, so the Space's vectors are provably paired with the hifigan variant — make it the default, A/B istftnet (faster; hifigan applies a `pred_dur[0]=30` lead-in hack at `models.py:709-710`). Gotchas: `Вʼячеслав Дудко` needs the exact U+02BC apostrophe or lookup silently falls back to the alphabetically-first (female) stem; a `ref_audio_path` override beats the named style (`engines.py:365-375`). This path uses `s_prev` directly — as deterministic as today.

**`patriotyk/HolosTTS`** (weights MIT, 2026-08-16; StyleTTS2 evolution, no diffusion at inference, SineGen/iSTFT, 24 kHz, voice cloning via `VoiceEncoder.encode`). Could clone filatov from `patriotyk/filatov_24000` (MIT, 48,594 clips) onto a newer decoder. **Blocked**: github.com/patriotyk/HolosTTS ships no LICENSE — open an issue or vendor/reimplement before commercial use; install from git, not PyPI `holos` (unrelated); no speed param; seed-fixable not seed-free (SineGen `torch.rand`/`randn_like` at decoder.py ~137/163). Watch-list, not roadmap.

**Determinism pinning (do this regardless):** the s_prev path's only stochastic ops are SineGen — `styletts2_inference/Modules/decoder.py:121,208,261` (+`models.py:485`, only relevant if diffusion is adopted). Wrap the model call in `torch.random.fork_rng(devices=[device])` + `torch.manual_seed(0)`. Audibly identical, enables golden-file tests. Bit-exactness on CUDA additionally needs `cudnn.deterministic=True`, `benchmark=False`, pinned TF32 — or compare with a spectral/MAE tolerance. Same for RAD-TTS: `torch.manual_seed(int(os.environ.get('RADTTS_SEED','1234')))` before `self._model.infer` (`engines.py:572`) — note today the CPU generator feeding the f0/decoder latents (radtts.py:748,823) is *never* seeded on the CUDA path.

**Enhancers, slotted via `enhance.py`** (`ENHANCERS = {'none','apbwe48','resemble','sidon'}`, common `enhance(audio, sr, name, **cfg) -> (audio, sr)`, best-effort try/except like polish.py, called in app.py between `engine.synthesize()` and polish, persisted as an `enhancer` key in `apps.tts_profile`, lazy-loaded like `_engines`):
- **resemble-enhance** (MIT): enhancer-only, `lambd=0.0–0.1` (the real knob — `denoise_before_enhancement` is just the Gradio checkbox), `nfe=64`, `tau=0.5`, `solver='midpoint'`. CFM noise is internally `manual_seed(0)` — reproducible per machine by default. Vendor the inference path and stub the module-level `import deepspeed` (train-only, breaks aarch64 pip install; `--no-deps` does NOT work). Chunks internally at 30 s / 1 s overlap — seam risk starts >30 s. Output 44.1 kHz; run BEFORE polish with `deharsh` lowered to ~0.15. Unmaintained since Dec 2023 — pin versions; `torch.load` may need `weights_only=False`.
- **AP-BWE** (MIT code+weights, `config_24kto48k.json`): pure conv forward pass, genuinely deterministic, <1 s per 60 s clip. StyleTTS2 24 kHz path ONLY — RAD-TTS already outputs 44.1 kHz via Vocos (`engines.py:458`) and needs no BWE. Checkpoints on Google Drive (manual vendor). A/B Ukrainian sibilants (ш/щ/ц) before default-on (VCTK-trained).
- **Sidon v0.1** (`sarulab-speech/sidon-v0.1`, MIT repo, TorchScript `.pt` files, trained on FLEURS-R incl. uk): Miipher-style resynthesis — can genuinely erase vocoder artifacts but risks filatov timbre drift. Load the TorchScript files directly + `SeamlessM4TFeatureExtractor` (do NOT pip-install the repo — drags flash-attn/ray). Verify output rate at load. Experiment behind a flag; run before polish.
- Unit tests for the stage: duration preserved ±1 frame **in seconds** (AP-BWE doubles sample count), output RMS within 3 dB of input, per-machine golden hashes with deterministic-algorithms flags.

**Vocoder swaps: don't.** BigVGAN v2 is MIT but expects 128-band/100-band mels; RAD-TTS-uk emits 80-band 44.1 kHz mels (matches only `patriotyk/vocos-mel-hifigan-compat-44100khz`), and StyleTTS2/HolosTTS decoders are end-to-end trained with no external mel interface. A swap means weeks of finetuning for uncertain gain.

---

## 6. Text/prosody quick wins

All deterministic regex/own-code, placed in `normalize.py`; note the neural verbalizer is ON by default here and already handles times/тис./млн/phones correctly — these are (a) live-bug fixes and (b) a unit-testable safety net for the fallback path (verbalizer off/failed/sentences >350 chars):

1. **Date regex bug (live, CONFIRMED):** `normalize.py:115` lookahead `(?![\d.,])` rejects sentence-final dates — `'по 15.09.2026.'` reads as `'п'ятнадцять кома нуль дев'ять'`. Fix: `(?!\d|[.,]\d)`. Also fixes the comma-followed case. Minutes of work; regression tests for both.
2. **Standalone `грн` (live bug even WITH verbalizer):** `'5 тис. грн'` leaves `грн` → phonemizes as vowel-less `ɦrn`. Add `грн` as a standalone token rule, not just number-suffixed (`normalize.py:19-25`).
3. **`вул./просп./б-р/пл.` street abbreviations** — unexpanded even by the verbalizer (measured). Table pass **BEFORE `verbalize()`** — abbreviation dots also fragment `verbalize.py`'s naive `_SENT_SPLIT` and measurably garble/reorder its output, so early expansion helps the neural path too. Make `тис./млн` number-aware (fixed genitive is wrong after numerals ending 1–4) or leave them to the verbalizer.
4. **`expand_times()`** before `verbalize()` (not merely before `expand_numbers`, or it's a no-op on the live path): `(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)` → feminine genitive/locative ordinal after з/до/від/о/об (add `о`/`об` only when no preposition precedes; `об` before vowel-initial hours). Needs a feminine ending map — `_ordinal()` is masc/neut only.
5. **Phone numbers:** `+380` is mishandled even with the verbalizer (drops «плюс», reads «триста вісімдесят нуль…»). Add the `+380`/`0XX XXX XX XX` detector before `verbalize()`, group-wise reading with spoken zeros.
6. **Latin fallback (CONFIRMED, med-high):** three ordered layers — global pronunciation dict (Instagram→Інстаграм, QR→кʼю-ар, …) merged UNDER per-brand overrides (stored in `apps.pronunciations` JSONB, not `tts_profile`); URL reader (strip scheme/www, `.`→` крапка `, `ua`→«юа»), run before `expand_dates`; last-resort Latin→Cyrillic digraph transliteration for `[A-Za-z]{1,}` (length-1 too, or `iPhone X` leaks an `X`), before the stresser.
7. **OOV stress fallback (CONFIRMED, high):** do NOT install the patriotyk fork wholesale (it's based on pre-2.x code and would downgrade the base lib) — keep upstream 2.1.0, `pip install git+https://github.com/egorsmkv/ukrainian-accentor` (branch `main`), and port the fork's 2-line fallback (accentor on tokens the trie left unstressed) into `normalize.py`/`_phonemize`. Verified working on this box's torch 2.13/aarch64, byte-stable. The accentor guesses wrong sometimes (`бурге́р`, `ху́ді`) — the repo-owned house dict for client brand terms is mandatory, with unit-pinned pronunciations. Also collapse dictionary double-stress (`за´ти´шна` → keep last mark) with one regex in `_phonemize`.
8. **Stress benchmark now:** add `lang-uk/ukrainian-tts-preprocessing`'s `lexical_stress_benchmark` CSV (1,026 sentences, '+' after vowel — small notation shim vs `to_plus_stress`) as a pytest score for the whole stress stack. Gate the ByT5 **hybrid** (dictionary-first + ByT5 fallback: paper's Table 2 shows 92.5% word-level vs ~86.0% current; a straight swap is a wash) on measured delta and on resolving the `mouseyy/stressifier-byt5-g2p-model` weights license (untagged on HF).
9. **Brief-writing rules + lint** for the mini-film-studio prompts and docs/clients: sentences <180 chars; commas for micro-pauses; em-dash **with spaces** (` — ` maps to a `:` pause token but never splits; unspaced em-dash is silently deleted — lint that); end hooks with `!`/`?`; `+` before the stressed vowel for brand words (`Якн+ове` — verified end-to-end: `+`→U+0301→`ˈ`); write inflection-critical numbers as words; avoid emoji/compatibility Unicode (™→TM under NFKC). Drop the ALL-CAPS rule — dictionary and ipa_uk are case-insensitive (verified).

---

## 7. What NOT to do

- **Neural verbalizer integration** — already fully built and live (`verbalize.py`, skypro1111 m2m100 ct2, deterministic greedy, assets vendored). Zero remaining work.
- **NC/incompatible-license models:** `facebook/mms-tts-ukr` (CC-BY-NC), `skypro1111/fish-speech-1.5-ukrainian` (CC-BY-NC-SA), `coqui/XTTS-v2` (CPML non-commercial), Kokoro (no uk voice or G2P), F5-TTS (no uk finetune exists; base is NC), `pyflowtts_uk_elevenlabs` (2h20m synthetic ElevenLabs data — quality ceiling + ToS grey zone). robinhad/ukrainian-tts stays GPL-sidecar-only.
- **NISQA as a QA metric** — weights are CC BY-NC-SA; only the code is MIT.
- **BigVGAN vocoder swap** — mel-interface mismatch; weeks of finetuning for uncertain gain.
- **Miipher-2** — no public weights; Sidon is its actionable open lineage. Record in docs.
- **DeepFilterNet3** on TTS output — denoiser for a noise-free signal; would eat breaths/sibilance; cargo build pain on aarch64. Legit only for cleaning user reference audio/music beds.
- **VoiceFixer** — 2021-era, produces watery artifacts on clean TTS.
- **AudioSR/FlashSR** — diffusion SR for music; hallucinates HF texture on speech, slow, FlashSR license unclear. AP-BWE dominates.
- **rvc-python** — pins `fairseq==0.12.2` + `numpy<=1.23.5`, uninstallable on py3.12/aarch64 (verified). Use IAHispano/Applio if RVC is ever pursued.
- **Fixing `self.noise` alone for diffusion determinism** — the ADPM2 sampler injects `randn_like` per step (`sampler.py:351`); per-call `torch.manual_seed` is the working recipe (verified bit-identical).
- **Unguarded `predict_style_single`** — seed 1234 + `embedding_scale=1.3` deterministically exploded (s_pred norm 2.1e7 → NaN audio, 13× length) on a real sentence, and NaN passes silently into polish.py (no exception → Piper fallback never fires). Guard: `torch.isfinite(s_pred).all()` and `s_pred.norm() < 10` (healthy 2.4–3.5); fixed retry ladder 1234→7→99 → fall back to `style.pt`. Default `embedding_scale` 1.0–1.1.

---

## 8. Experiment protocol — measuring smoothness with tts_qa.py

`tts_qa.py` today is WER-only (`WER_DOWN_THRESHOLD` at `tts_qa.py:27`; difflib word alignment at 101–122) — deaf to everything in this report. Upgrade, then run structured A/Bs:

**Metrics pass** (new function in `run_tts_qa`, on the generated wav; all deterministic):
1. **Click count** — per 10 ms window, flag `max |sample delta| > k × local median delta` (relative, NOT absolute 0.15 FS — sibilants false-positive). Directly validates the joiner.
2. **f0 stats** — `librosa.pyin` (already in the shared repo-root `.venv`; pin it in requirements), `fmin=60, fmax=300`: jitter proxy = mean |frame-to-frame f0 delta| on voiced frames; monotony proxy = f0 std/mean per sentence and across sentences (the diffusion-style change should raise cross-sentence std).
3. **MOS proxy** — `tarepan/SpeechMOS` via `torch.hub` `utmos22_strong` (MIT, fairseq-free, runs on existing torch). Pin the hub commit; run on CPU or compare with tolerance; treat scores as *relative A/B signal only* on Ukrainian.
4. **Pause histogram** — inter-sentence silence durations (validates punctuation-pause mapping).

Store scores in the `tts_feedback` row's comment/tags so `tts_improve` and the TTS lab surface them.

**Protocol per change:**
1. Land seed pinning (section 5) first — otherwise takes aren't comparable (RAD-TTS especially: same text = different audio every call today).
2. Fixed corpus: ~20 real ad scripts (Yaknove copy + synthetic briefs covering times, prices, phone numbers, brand Latin, enumerations, 1-sentence and 45 s reads).
3. For each candidate change, render corpus at baseline and treatment with identical seeds/config; diff the metric vector; require: clicks ↓ or =, WER = (regression gate), jitter within band (some ↑ is *desired* for the style-diffusion change — judge against monotony ↓), UTMOS ↑ or =.
4. Blind ABX listen only on candidates that pass the metric gate — 5 pairs per change, phone-speaker playback (the shorts' real target), judge pacing/smoothness/pronunciation separately.
5. Lock winners with golden-file unit tests (hash per machine with deterministic flags, or spectral-MAE tolerance) in `backend/tests/test_tts_polish.py`.
6. Order of experiments: stress fix (expect WER/pronunciation win + duration drop) → joiner (clicks→0, pause histogram correct) → short.py loudnorm (compare final short audio, not just TTS wav) → speed 0.93 → style diffusion t∈{0.2,0.3,0.4} × es∈{1.0,1.1} → `styletts2_multi` 13-voice audition (UTMOS + ABX vs fixed filatov) → resemble-enhance vs AP-BWE vs Sidon on the same 20 clips.