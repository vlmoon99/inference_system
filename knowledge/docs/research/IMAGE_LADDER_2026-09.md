# Image ladder — first same-seed comparison (2026-09-27)

Twelve briefs across the businesses the product serves, one seed (20260927),
1024², both rungs through the live `gen-image` adapter on the Spark. Qwen on
the left of each pair, FLUX.1-dev on the right.

![sheet 1](img/image_ladder_2026-09-27_sheet1.jpg)
![sheet 2](img/image_ladder_2026-09-27_sheet2.jpg)

| | Qwen-Image-2512 fp8, Lightning 4-step | FLUX.1-dev fp8, Hyper 8-step |
|---|---|---|
| Licence | Apache-2.0 | non-commercial (docs/MODELS.md) |
| Warm render, 1024² | 9.0 s | 16.1 s |
| Reload after eviction | ~150 s (28 GB) | ~20 s (17 GB) |
| Resident memory | ~28 GB | ~17 GB |

**Reviewer's read (not the owner's yet):** Qwen clearly better on bakery,
coffee, robotics, pets, furniture, sushi, florist (anatomy, materials,
scene coherence, fewer "AI look" tells); even on dental, detailing, yoga,
hotel, clothing; no pair where FLUX is better.

**Decision path:** the owner runs blind pairs in admin → AI → Image lab; a win
or tie for Qwen flips `image_model_default` and closes the image licence
question. The prototype tier stays available for comparison only.

Prompts: `backend/data/assets/image-lab/pairs/` holds the renders; the prompt
list is in the commit that added this note.
