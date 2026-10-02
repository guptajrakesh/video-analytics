FROM python:3.12-slim

# libgl1/libglib2.0-0 are required by opencv-python-headless's underlying
# native dependencies even in headless mode.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# yolox is installed on its own, without its dependency chain (onnx,
# pycocotools, ... are training/export tooling this product never uses).
# Its setup.py compiles a C++ extension and needs torch (already installed
# above) visible at build time, so build without pip's isolated environment
# and with a compiler that is removed again in the same layer.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir setuptools wheel \
    && pip install --no-cache-dir --no-build-isolation --no-deps yolox==0.3.0 \
    && pip install --no-cache-dir loguru thop \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY . .

ENV PYTHONUNBUFFERED=1
