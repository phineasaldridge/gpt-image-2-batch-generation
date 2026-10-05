"""Minimal Argolink example for gpt-image-2; no request is made on import."""
from __future__ import annotations
import os
import requests

BASE = "https://api.argolink.io"
MODEL = "gpt-image-2"

def request():
    key = os.environ["ARGOLINK_API_KEY"]
    body = {"model": MODEL, "prompt": "a clean editorial illustration", "size": "1024x1024", "n": 1}
    response = requests.post(BASE + "/v1/images/generations", headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=body, timeout=60)
    response.raise_for_status()
    return response.json()

if __name__ == "__main__":
    print(request())
