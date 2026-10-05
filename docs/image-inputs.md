# Image request boundary

`gpt-image-2` uses `/v1/images/generations`. Keep the prompt, target pixel size, and requested result count explicit. The model page is authoritative for image editing fields and reference-image limits.

```json
{
  "model": "gpt-image-2",
  "prompt": "square 1:1 editorial illustration with a clear subject",
  "size": "1024x1024",
  "n": 1
}
```

For edits, use the documented edit route and multipart fields from the model page; do not guess a reference-image field from another model. Every returned image is a billable result, so treat `n` as a cost and response-size decision.
