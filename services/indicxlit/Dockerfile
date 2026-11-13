# IndicXlit Latin→Telugu suggestion service — fully isolated from the main
# app's Python environment (fairseq vs ASR/torch stack conflict).
#
# Proven recipe — do not "upgrade" any of these:
#   * python:3.10-slim
#   * pip<24.1        (newer pip rejects fairseq's package metadata)
#   * torch CPU wheel (no GPU; keeps the image far smaller than the CUDA build)
#   * torch.load monkeypatched to weights_only=False (engine.py, before load)
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# fairseq may compile C extensions if no wheel matches.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install "pip<24.1"

# CPU-only torch FIRST so ai4bharat/fairseq resolve against it instead of
# pulling the multi-GB CUDA build from PyPI.
# --extra-index-url: the PyTorch index lacks some helper packages (typing-extensions build deps); fall back to PyPI
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple

COPY requirements.txt /srv/requirements.txt
RUN pip install -r /srv/requirements.txt

WORKDIR /srv
COPY engine.py preload.py app.py /srv/

# Bake the model + rescoring dicts into the image and smoke-test inference,
# so container startup is just load-into-RAM and a broken install fails here.
RUN python preload.py

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
