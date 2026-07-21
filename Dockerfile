# syntax=docker/dockerfile:1
#
# Multi-stage dev/CI image for Durin.
#
#   docker build --target cpu -t durin:cpu .   (default; no GPU required)
#   docker build --target gpu -t durin:gpu .   (needs `docker run --gpus all` at runtime)
#
# Neither target bakes the GGUF model into the image (see .dockerignore) --
# mount the host `models/` directory instead and set LLM_MODEL_PATH.

# ---------------------------------------------------------------------------
# cpu: python:slim base, prebuilt CPU wheel for llama-cpp-python.
# ---------------------------------------------------------------------------
FROM python:3.14-slim AS cpu

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    LLM_MODEL_PATH=/models/gemma4_e4b_it.gguf \
    LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6

# pip/uv compiles llama-cpp-python from sdist. The compiled ggml backend
# references C++ RTTI symbols but its own CMake build doesn't link libstdc++
# (upstream packaging gap, reproducible on this toolchain) -- LD_PRELOAD above
# forces it into the process's global symbol table so the dlopen succeeds.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependency layer cached separately from source so code edits don't
# invalidate the (slow) compiled-dependency layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .
RUN uv sync --frozen

ENTRYPOINT ["uv", "run"]
CMD ["pytest"]

# ---------------------------------------------------------------------------
# gpu: CUDA devel base so llama-cpp-python compiles with cuBLAS offload.
# torch/torchvision already resolve to CUDA wheels via the pytorch-cu132
# index in pyproject.toml regardless of target -- only llama-cpp-python needs
# a source rebuild here. Building this target does NOT require a physical
# GPU; only `docker run --gpus all` against it does.
# ---------------------------------------------------------------------------
FROM nvidia/cuda:13.0.0-devel-ubuntu24.04 AS gpu

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    LLM_MODEL_PATH=/models/gemma4_e4b_it.gguf \
    LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6 \
    PATH="/root/.local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
RUN uv python install 3.14

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .
RUN uv sync --frozen

# Recompile llama-cpp-python against the CUDA toolchain now present in the
# image (the layer above installed the plain CPU wheel from PyPI).
RUN CMAKE_ARGS="-DGGML_CUDA=on" uv pip install --no-cache-dir \
        --force-reinstall --no-binary llama-cpp-python llama-cpp-python

ENTRYPOINT ["uv", "run"]
CMD ["pytest"]
