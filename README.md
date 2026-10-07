# GPT Image 2 batch generation — YAML or CSV in, a folder of images out

`batchgen.py` turns a list of prompts into images with GPT Image 2 (or GPT Image 2.5 Flare / Sunburst): bounded concurrency, a dry run that prints the bill before you spend it, skip-existing so a rerun only does what is missing, backoff on rate limits, and a JSONL manifest of every file and its cost. One Python file, standard library only.

It works with any OpenAI-style `/v1/images/generations` endpoint. The default is [ArgoLink](https://argolink.io/gh-gpt-image-2-batch-generation), which bills GPT Image 2 at a flat $0.015 per image up to 2K and $0.02 at 4K, whatever the `quality`.

<sub>Last checked: 2026-10-07 (prices and limits below).</sub>

## Features

- **Config as data**: YAML, JSON or CSV; a shared `style` and `avoid` list plus one line per image.
- **Concurrency you choose**: `--concurrency N` parallel requests, each asking for up to 7 images.
- **Dry run with an estimate**: tier and price per entry, from the size you ask for.
- **Budget guard**: `--max-cost 5` refuses to start a plan estimated above $5.
- **Rerun-safe**: entries whose files exist are skipped; `--force` regenerates, `--only a,b` picks entries.
- **Real cost after the fact**: the manifest records the pixels each image came back with and bills it by that tier.
- **Ratio in words**: the prompt gets "16:9 composition." (derived from `size`) unless it already names a ratio, because this route reads the aspect ratio from the prompt.

## Quick start

```bash
git clone https://github.com/phineasaldridge/gpt-image-2-batch-generation
cd gpt-image-2-batch-generation
pip install pyyaml                         # only for YAML configs; JSON and CSV need nothing
export OPENAI_API_KEY=your-key             # an ArgoLink key, or your own endpoint's
# export OPENAI_BASE_URL=https://argolink.io/v1   # the default

python batchgen.py examples/icons.yaml --dry-run
python batchgen.py examples/icons.yaml --concurrency 4
```

The dry run for the bundled icon set prints:

```
run  calendar                  1024x1024 1K x1  $0.015
...
run  weather                   1024x1024 1K x2  $0.030
12 request(s), 13 image(s), estimate $0.20
```

## Config

```yaml
model: gpt-image-2        # or gpt-image-2.5-flare, gpt-image-2.5-sunburst
output: out/icons         # folder; created if missing (default ./out)
size: 1024x1024           # WIDTHxHEIGHT in pixels, not "1K" or "16:9"
quality: medium           # auto (default), low, medium, high
count: 1                  # images per entry, 1-7 (sent as n)
concurrency: 4            # parallel requests (default 2)

style: |                  # put in front of every prompt
  Flat vector app icon, two-tone palette, plain white background.
avoid: text, gradients    # appended as "Avoid: ..."

images:                   # name: prompt, or name: {prompt, size, quality, count}
  calendar: a calendar page with one day circled
  weather:
    prompt: a sun half covered by a cloud
    count: 2
```

CSV uses the columns `name,prompt,size,quality,count` (only the first two are required); see [blog-headers.csv](./examples/blog-headers.csv). JSON mirrors the YAML; see [product-variants.json](./examples/product-variants.json).

### Sizes that map to clean tiers

| Ratio | 1K | 2K | 4K |
|---|---|---|---|
| 1:1 | `1024x1024` | `1440x1440` | `2160x2160` |
| 4:3 / 3:4 | `1024x768` / `768x1024` | `1920x1440` / `1440x1920` | `2880x2160` / `2160x2880` |
| 3:2 / 2:3 | `1024x682` / `682x1024` | `1728x1152` / `1152x1728` | `3240x2160` / `2160x3240` |
| 16:9 / 9:16 | `1024x576` / `576x1024` | `2048x1152` / `1152x2048` | `3840x2160` / `2160x3840` |
| 21:9 | `1024x439` | `2048x878` | `3840x1646` |
| 5:2 | `1024x410` | `2048x819` | `3840x1536` |

These are the recommended sizes on the [GPT Image 2 model page](https://argolink.io/en/models/gpt-image-2). The tier is the returned image's longest edge: up to 1024 is 1K, 1025–2048 is 2K, above 2048 is 4K. The returned pixels can differ from the request, so the manifest bills what actually came back; `2560x1440`, for one, lands in 4K.

## Command line

| Option | Effect |
|---|---|
| `--dry-run` | print the plan and estimate, send nothing |
| `--concurrency N` | parallel requests (overrides the config) |
| `--only a,b` | run only these entry names |
| `--force` | regenerate entries whose files already exist |
| `--output DIR` | output folder (overrides the config) |
| `--max-cost USD` | exit with code 2 if the estimate is higher |

Exit codes: 0 all done, 1 some entries failed (see the manifest), 2 stopped by `--max-cost`.

## Files and the manifest

`name.png` for a single image, `name_001.png`, `name_002.png`, … when `count` is above 1. The extension follows what the API returned (PNG, JPEG or WebP, read from the file header). Each request appends one line to `<output>/manifest.jsonl` (example values; `seconds` is the request's wall time):

```json
{"name": "weather", "model": "gpt-image-2", "size": "1024x1024", "quality": "medium", "requested": 2,
 "files": [{"path": "out/icons/weather_001.png", "pixels": "1024x1024", "tier": "1K"}, {"path": "out/icons/weather_002.png", "pixels": "1024x1024", "tier": "1K"}],
 "cost": 0.03, "seconds": 41.2}
```

A failed entry has `"error"` instead of `"files"`, so `jq 'select(.error)' manifest.jsonl` lists what to look at.

## Retries and failures

- `429` and `503` are retried up to four times, waiting `Retry-After` seconds when the server sends it and an exponential backoff (2, 4, 8, 16 s plus jitter) when it doesn't.
- Every other error (bad size, refused prompt, bad key, network drop) is written to the manifest and the batch carries on. Because a dropped connection may still have produced a billed image, the tool does not resend it on its own: run the same command again and only the missing entries go out.
- Each request waits up to 600 seconds: the endpoint answers only when every image of the request is finished, and 2K/4K take longer.

## What a batch costs

Per image at 1024×1024, by the `quality` you send:

| Provider | low | medium | high | How it bills |
|---|---:|---:|---:|---|
| **[ArgoLink](https://argolink.io/en/models/gpt-image-2)** | **$0.015** | **$0.015** | **$0.015** | flat per returned image; 4K tier $0.02 |
| [OpenAI API](https://developers.openai.com/api/docs/guides/image-generation) | $0.006 | $0.053 | $0.211 | image output tokens at $30/M; prompt tokens extra |
| [fal.ai](https://fal.ai/models/openai/gpt-image-2) | $0.006 | $0.053 | $0.211 | same token rates; 3840×2160 is $0.012 / $0.101 / $0.401 |
| [Replicate](https://replicate.com/openai/gpt-image-2) | $0.012 | $0.047 | $0.128 | per output image by quality |

<sub>All four checked 2026-10-07; each page wins if it changes.</sub>

For 500 images at 1024×1024 that is $7.50 here at any quality, against $26.50 (medium) or $105.50 (high) of output tokens on OpenAI and $23.50 or $64.00 on Replicate. If low quality is good enough for your set, OpenAI direct is cheaper: $3.00.

## FAQ

**Can I batch GPT Image 2.5 Flare or Sunburst with this?**
Yes, set `model: gpt-image-2.5-flare` or `gpt-image-2.5-sunburst`. They take the same `size`, `quality` (auto, low, medium, high) and `n` (1–7) here, at the same per-image prices.

**How many requests should run at once?**
Start with 2–4. If you see 429s in the output, the tool is already backing off; lower `--concurrency` instead of waiting on retries.

**Why does the prompt end with "16:9 composition."?**
On this route `size` sets the pixels, and the aspect ratio has to be spelled out in the prompt as well. The tool derives it from `size`; write a ratio into the prompt yourself and it adds nothing.

**Does it do transparent backgrounds or edits?**
No. It calls `/images/generations` only. For edits with reference images (1–16 per request on GPT Image 2) use `/images/edits` directly.

**Why is the estimate different from the manifest total?**
The estimate uses the size you asked for; the manifest bills the pixels that came back, and an API can return fewer images than requested.

## Tests

```bash
python -m unittest discover -s tests
```

The HTTP call is mocked, so the tests need no key and spend nothing.

## License

MIT. Not affiliated with OpenAI; GPT Image is OpenAI's name for its models.
