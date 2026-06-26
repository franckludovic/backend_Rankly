# ── Rankly backend — Hugging Face Spaces (Docker SDK) ──────────────────────────
# HF Spaces require the app to listen on port 7860.
# Build context is the backend/ folder.

FROM python:3.12-slim

# System libs: lxml needs libxml2/libxslt; xgboost needs libgomp.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces run as a non-root user (uid 1000). Cache dirs must be writable.
ENV HOME=/home/user \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/home/user/.cache/huggingface \
    TRANSFORMERS_NO_ADVISORY_WARNINGS=1

RUN useradd -m -u 1000 user
WORKDIR /home/user/app

# ── Python deps (cached layer) ────────────────────────────────────────────────
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Bake the sentence-transformer model into the image ────────────────────────
# Avoids an ~80MB download on every cold start.
COPY --chown=user download_semantic_model.py .
RUN python download_semantic_model.py

# ── App code + ML models + the 701MB CC lookup file ───────────────────────────
# (data/ and models/ arrive via git-LFS in the Space repo.)
COPY --chown=user . .

USER user
EXPOSE 7860

# Single worker: the 701MB duckdb + torch model are memory-shared per process,
# and HF free tier is 2 vCPU. Scale workers only on a bigger instance.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
