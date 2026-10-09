# weights/

Gitignored model binaries that are slow or painful to re-provision. Only this README and
`weights.lock` are committed. `tts.sha256` lists every file under `tts/` with its hash, so check a copy with
`sha256sum -c tts.sha256`. It isn't committed (it's large and only meaningful next to the files), so keep a copy with backups.

## weights.lock: where each directory came from

Moved here from `advertisment_system/data/models/` on 2026-10-09 (TTS stack, dormant: see `knowledge/tts-uk/`).
"verified" = the source was confirmed in the old repo's docs, not re-downloaded and diffed.
