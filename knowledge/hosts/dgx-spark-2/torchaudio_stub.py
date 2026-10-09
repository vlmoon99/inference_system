"""Stub torchaudio — this build is VIDEO-ONLY (no matched torchaudio for the NGC
alpha torch on GB10). Any attribute returns a lazy dummy so imports and class-body
references succeed; the audio path is simply unused for product-ad video."""
__version__ = "0.0.0-stub"


class _Any:
    def __getattr__(self, name):
        return _Any()

    def __call__(self, *a, **k):
        return _Any()

    def __iter__(self):
        return iter(())


def __getattr__(name):
    return _Any()
