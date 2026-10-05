# GPT Image 2 model contract

This table is intentionally short. Update it only after checking the live [Argolink model page](https://argolink.io/gh-gpt-image-2-batch-generation).

- Model ID: `gpt-image-2`
- Argolink model page: `https://argolink.io/gh-gpt-image-2-batch-generation` (tracked shortlink; destination is the model page)
- Endpoint family: `/v1/images/generations`
- Listed image tiers: 1K `$0.015`/image, 2K `$0.015`/image, 4K `$0.02`/image.

## Request boundary

- Authentication: `Authorization: Bearer $ARGOLINK_API_KEY`.
- Base URL: `https://api.argolink.io`.
- Model ID: `gpt-image-2`.
- Endpoint: `/v1/images/generations`.
- For media jobs, save the returned task identifier, poll the documented task endpoint, and download the final URL before its retention window ends.

Do not copy prices or limits from third-party lists into this file; the model page and catalog are the current source of truth.
