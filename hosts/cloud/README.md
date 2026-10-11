# cloud — the same API, answered by clouds

A second gateway a node can run next to its own models: `qwen3.6-35b` and `qwen3-embedding-0.6b` go to NEAR AI
Cloud (the same Qwen models, inside a TEE), `qwen-image-edit` to Comfy Cloud (Qwen-Image-Edit-2511). It is the
code of `hosts/mac/gateway.py` and `services/image` in one small image; see `hosts/mac/README.md` for what the
settings do and what was measured.

```
cp .env.example .env            # TAILNET_IP, API_KEY, the two cloud keys, IMAGE_QUALITY
docker compose up -d --build    # :8011 on the tailnet address; comes back by itself after a reboot
curl -s http://$TAILNET_IP:8011/health
```

It does not touch the node's own stack: `hosts/core.yaml`, `deploy/node.sh` and the models on `:8000` neither
know about it nor depend on it, and it is not supervised by the watchdog (only Docker's restart policy).
A project picks where its model calls go by the address and key it uses. BoostContent has a switch for that:
`boostcontent_backend/deploy/inference.sh cloud | own`.

`GET /v1/attestation` (with the key) gives NEAR AI's proof that the text model runs in a TEE; the node's own
gateway has no such path, which is how BoostContent knows whether to show its privacy note.
