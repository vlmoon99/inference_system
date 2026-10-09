"""TTS provider — product_dream gen-tts contract (Piper, CPU-ONNX).
Writes WAV voiceover into the shared ASSETS_DIR. GEN_TTS_URL accepts a
comma-separated endpoint pool (first-healthy with failover — see pool.py)."""

import httpx

from ads.core.config import settings
from ads.core.voice import engine_matches
from ads.providers.files import ensure_local
from ads.providers.pool import EndpointPool, call_with_failover


class TTSProvider:
    def __init__(self, base_url: str):
        self.pool = EndpointPool(base_url)
        self.base_url = self.pool.urls[0]  # display/back-compat (see /v1/status)

    async def _probe(self, base_url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=4) as c:
                r = await c.get(f"{base_url}/health")
                return r.status_code == 200 and r.json().get("loaded", False)
        except (httpx.HTTPError, ValueError):
            return False

    async def health(self) -> bool:
        if self.pool.single:
            return await self._probe(self.base_url)
        return await self.pool.any_healthy(self._probe)

    async def generate(
        self,
        text: str,
        subdir: str,
        lang: str = "en",
        voice: str | None = None,
        pronunciations: dict | None = None,
        ref_audio_path: str | None = None,
        engine: str | None = None,
        speed: float | None = None,
        polish: str | None = None,
        expressiveness: float | None = None,
        depth: float | None = None,
        ambience: str | None = None,
        ambience_level_db: float | None = None,
    ) -> dict:
        """Return {path, sample_rate, elapsed_s, duration_s, engine, voice};
        path relative to ASSETS_DIR, engine/voice null on older services.
        pronunciations: brand-name -> Cyrillic replacement, applied by the service
        to the TTS input only. ref_audio_path (relative to ASSETS_DIR): per-brand
        voice reference for engines that clone. engine/speed: per-request engine
        override + rate multiplier (TTS lab). Older services ignore extra fields."""
        body = {"text": text, "lang": lang, "voice": voice, "product_id": subdir,
                "pronunciations": pronunciations, "ref_audio_path": ref_audio_path,
                "engine": engine, "speed": speed,
                "polish": polish, "expressiveness": expressiveness,
                "depth": depth, "ambience": ambience}
        if ambience_level_db is not None:
            body["ambience_level_db"] = ambience_level_db

        async def _call(base_url: str, want_engine: str | None = None) -> dict:
            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.post(f"{base_url}/generate", json=body)
                r.raise_for_status()
                data = r.json()
                audio = data["audio"]
            if want_engine and not engine_matches(data.get("model"), want_engine):
                # wrong engine on this host: report it WITHOUT downloading the take —
                # the caller moves on to the next host (and only fetches this one
                # if nobody else has the engine)
                return {"__degraded__": True, "engine": data.get("model"), "base_url": base_url,
                        "path": audio["path"], "data": data}
            await ensure_local(audio["path"], base_url)
            # Forward the service's top-level model/voice so gen_config can record
            # the engine actually used. Older services omit them — keys stay null.
            audio.setdefault("engine", data.get("model"))
            audio.setdefault("voice", data.get("voice"))
            # normalized text actually spoken (QA loop input); null on older services
            audio.setdefault("text", data.get("text"))
            # same text with наголос as "+" (uk); null on older services
            audio.setdefault("stress_text", data.get("stress_text"))
            # shaping that actually ran; null on older services
            audio.setdefault("polish", data.get("polish"))
            audio.setdefault("ambience", data.get("ambience"))
            return audio

        if engine and not self.pool.single:
            return await self._call_with_engine(engine, _call)
        return await call_with_failover(self.pool, self._probe, _call)

    async def _call_with_engine(self, engine: str, call) -> dict:
        """A pinned engine (the brand voice) lives on one host. Try the pool in
        order and accept the first host that actually ran that engine; a host
        that degraded to another engine, or errored, is skipped. If no host
        has it, the first degraded result is returned and logged — a short with
        the wrong voice beats a mute one, but never silently."""
        degraded: dict | None = None
        last_err: Exception | None = None
        for url in self.pool.urls:
            try:
                out = await call(url, engine)
            except (httpx.HTTPError, ValueError) as e:  # noqa: PERF203 — per-host fail-over
                last_err = e
                self.pool.mark_unhealthy(url)
                print(f"[tts] {url} failed for engine {engine!r} ({type(e).__name__}) — next host", flush=True)
                continue
            if not out.get("__degraded__"):
                return out
            print(f"[tts] {url} ran {out.get('engine')!r} instead of the pinned {engine!r} — next host", flush=True)
            degraded = degraded or out
        if degraded is not None:
            print(f"[tts] no host in the pool runs {engine!r}; keeping the degraded take", flush=True)
            # fetch the take we skipped, then shape the response like a normal one
            await ensure_local(degraded["path"], degraded["base_url"])
            audio = degraded["data"]["audio"]
            audio.setdefault("engine", degraded["data"].get("model"))
            audio.setdefault("voice", degraded["data"].get("voice"))
            return audio
        assert last_err is not None
        raise last_err
    async def transcribe(self, path: str, lang: str = "uk",
                         word_timestamps: bool = False) -> dict:
        """ASR on one generated wav (path relative to ASSETS_DIR) — the QA
        loop's ear and the caption builder's clock. Returns {text, language,
        duration_s} (+ words: [{word, start, end}] with word_timestamps)."""

        async def _call(base_url: str) -> dict:
            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.post(f"{base_url}/transcribe",
                                 json={"path": path, "lang": lang,
                                       "word_timestamps": word_timestamps})
                r.raise_for_status()
                return r.json()

        return await call_with_failover(self.pool, self._probe, _call)

    async def options(self) -> dict:
        """The TTS service's /health payload (per-lang engine availability,
        kokoro voices) — the TTS lab renders its picker from this."""

        async def _call(base_url: str) -> dict:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(f"{base_url}/health")
                r.raise_for_status()
                return r.json()

        return await call_with_failover(self.pool, self._probe, _call)


tts_provider = TTSProvider(settings.gen_tts_url)
