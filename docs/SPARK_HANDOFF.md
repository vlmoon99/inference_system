# Handoff to the Sparks (written on the owner's Mac, 2026-10-10)

For whoever works on dgx-spark (`100.64.0.1`) and dgx-spark-2 (`100.64.0.12`) next, human or Claude.
Read this after `docs/PROGRESS.md`. Everything below was done from the Mac (`100.64.0.8`, macs-macbook-pro)
while the Sparks were off, and nothing on the Sparks was changed.

## What is true right now

| | |
|---|---|
| `boostcontent.io`, `api.vramhouse.com` | DNS points at the Cloudflare tunnel `bc-mac` (id `7c903b26-…`), which runs **on the Mac**. Before, both pointed at the Sparks' tunnel `test_ads_client` (id `b76c35fe-…`) |
| The live BoostContent backend | **the Mac** (`boostcontent_backend/deploy/mac`): its own database and its own pictures. Accounts, items and posts made since the power cut exist only there |
| Model calls of that backend | **the Sparks**: Postgres on the Mac calls `http://100.64.0.12:8000` with the project key `boostcontent`. The Sparks fetch photos from and put pictures to the Mac's storage, `http://100.64.0.8:3900` |
| `api.vramhouse.com/v1` | the Mac's Caddy forwards it to `100.64.0.12:8000`, then `100.64.0.1:8000` |
| The Sparks' own BoostContent stack | running on dgx-spark-2 (master) but **gets no public traffic**. Its database has none of today's data and stops at migration `…0011` |
| `bc-cloudflared` on dgx-spark-2 | running; harmless while DNS points at the Mac |
| Seen from the Mac at 20:50 UTC | both nodes: gateway, llm, embed, image OK. `node.sh status` on dgx-spark-2: **node0 (100.64.0.1) "database not answering (host alive)"** |

New in git since the Sparks last pulled (new folders and three migrations; no existing code file was edited):

* inference_system: `hosts/mac/` (a stand-in host for the Mac: MTPLX + FLUX.2 klein), this file.
* boostcontent_backend: `deploy/mac/` (the Mac launcher, tunnel and inference switch), and migrations
  `20261010000012_owner_wishes` (ideas prompt i4: obey the profile key `wishes`),
  `20261010000013_items` (market items; `generate_posts(business_id, n, item_id?, about?)`),
  `20261010000014_discard_batch` (`discard_batch(batch_id)`), plus pgTAP `db/tests/50_items.sql` (19).

> **Update 2026-10-10 21:10 UTC:** steps 1, 2 and 3 below were done from the Mac over ssh (details in
> `docs/PROGRESS.md`). Steps 4 and 5 are standing rules. What is left is the watchdog bug described there.

## Do this on the Sparks, in this order

1. **Pull both repos on both nodes.** `git pull` in `inference_system` and `boostcontent_backend`.
   Nothing restarts by itself: the new folders are not used by the Sparks.
2. **Find out why node0's database does not answer and bring it back as a follower.**
   On dgx-spark-2: `deploy/node.sh status` in both repos. On dgx-spark: `docker ps -a`, `journalctl --user -u inf-node -u bc-node --since -3h | grep -E 'ALERT|TAKEOVER|FAILED'`.
   Then walk the five checks in `README.md` → "Never tried on a real power loss" (they were still open after the
   outage of 14:44 UTC, and there was a second power cut after it). Expected end state: dgx-spark-2 master of both
   databases, dgx-spark follower of both, in both `status` outputs.
3. **Apply the three BoostContent migrations on the Sparks' master database** (dgx-spark-2), the way this
   repo applies migrations (through `deploy/node.sh`, never `compose up` by hand), then `scripts/test.sh`
   (expect `50_items.sql` 19/19 and everything else as before) and `./smoke.sh`.
   This makes the Sparks' backend able to take the public name back later with the app that is in use now.
4. **Leave the public names alone.** Do NOT run `cloudflared tunnel route dns …` and do not change the tunnel:
   two machines with two databases must not answer for one name. The owner decides when to move back (below).
5. **Keep the gateway key `boostcontent` at 120 requests/min and 12 in parallel** (the Mac's engine runs 4 LLM +
   4 image jobs at once, as the Sparks' own did).
6. **Write what you found and did into `docs/PROGRESS.md`** and push it.

## Not to do

* Do not stop the inference engines or the gateways: the live site depends on `100.64.0.12:8000`.
* Do not delete `bc-*` volumes or dumps on the Sparks: that database is the pre-outage production data.
* Do not edit `hosts/mac/` or `deploy/mac/` from the Sparks; they describe the Mac.

## Later, when the owner says so: giving the public names back to the Sparks

The data is the open question: the Mac's database (accounts, items, posts, pictures since the power cut) and
the Sparks' database (everything before it) are two separate histories. Moving back means choosing one, or
dumping the Mac's and restoring it on the Sparks (`pg_dump -Fc` of `boostcontent` on the Mac, the pictures
with `rclone copy` from `http://100.64.0.8:3900` to the master's Garage; the S3 keys differ, the object keys
do not). Until that is decided, nothing here should be switched. The DNS commands themselves are in
`boostcontent_backend/deploy/mac/README.md`.
