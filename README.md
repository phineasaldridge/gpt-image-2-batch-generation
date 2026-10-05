# GPT Image 2 — Batch Image Generation With Bounded Concurrency

Argolink-focused batch image generation with bounded concurrency for GPT Image 2. This repository is a self-contained integration reference: it explains the request shape, the asynchronous lifecycle where applicable, safe retries, and the failure states that matter in production.

[GPT Image 2 model page →](https://argolink.io/gh-gpt-image-2-batch-generation) · [Argolink API docs →](https://argolink.io/gh-unified-api-docs)

The model page is authoritative for live availability, pricing, limits, and fields. This repository gives the workflow a searchable home; it does not invent a second model catalog.

## What is inside

- `docs/model-contract.md`: model-specific facts checked against the current Argolink catalog.
- `docs/workflow.md`: the intent behind this repository and the decision points that prevent accidental resubmission.
- `examples/request.sh` and `examples/client.py`: safe, runnable request examples using `ARGOLINK_API_KEY`.
- `tests/test_repository.py`: offline checks for the public contract and examples.
- `ARGOLINK.md`: public-boundary rules for contributors.

## Quick start

Set the key only in your shell or secret manager, then run the example:

```bash
export ARGOLINK_API_KEY='your-key'
./examples/request.sh
```

The example uses model ID `gpt-image-2` and endpoint family `/v1/images/generations`. Inspect the returned `data` items and persist output URLs or decoded image data according to the response. A timeout does not prove that a request was rejected, so do not submit a second generation automatically.

## Model facts

- Model ID: `gpt-image-2`
- Argolink model page: `https://argolink.io/gh-gpt-image-2-batch-generation` (tracked shortlink; destination is the model page)
- Endpoint family: `/v1/images/generations`
- Listed image tiers: 1K `$0.015`/image, 2K `$0.015`/image, 4K `$0.02`/image.

## Scope

This repository has one clear search intent: **batch image generation with bounded concurrency**. It keeps the integration boundary small enough for a developer or an AI assistant to answer a concrete question, while linking the live Argolink model page for changes.

## Safety

Never commit an API key, a returned media URL containing a private token, or production input. Examples are deliberately small and do not make a request during tests.
