#!/usr/bin/env python3
"""Machina embedding provider — intfloat/e5-small-v2

Single mode:
  stdin:  {"text":"...", "dim":384}
  stdout: {"embedding":[...], "provider":"e5-small-v2"}

Batch mode:
  stdin:  {"texts":["a","b","c"], "dim":384}
  stdout: {"embeddings":[[...],[...],[...]], "provider":"e5-small-v2"}

Usage:
  export MACHINA_EMBED_PROVIDER=cmd
  export MACHINA_EMBED_CMD="python3 tools/embed/embed_e5.py"
"""
import json, sys, os

device = os.environ.get("MACHINA_EMBED_DEVICE", "cuda:0")
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("intfloat/e5-small-v2", device=device)

req = json.loads(sys.stdin.read())
dim = req.get("dim", 384)

texts = req.get("texts", None)
if texts is not None:
    # Batch mode
    queries = [f"query: {t}" for t in texts]
    vecs = model.encode(queries, normalize_embeddings=True, batch_size=len(queries)).tolist()
    for i in range(len(vecs)):
        if len(vecs[i]) > dim:
            vecs[i] = vecs[i][:dim]
    print(json.dumps({"embeddings": vecs, "provider": "e5-small-v2"}))
else:
    # Single mode
    text = req.get("text", "")
    vec = model.encode(f"query: {text}", normalize_embeddings=True).tolist()
    if len(vec) > dim:
        vec = vec[:dim]
    print(json.dumps({"embedding": vec, "provider": "e5-small-v2"}))
