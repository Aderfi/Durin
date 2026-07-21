# Docker: a dev/CI environment, not a deployment

The `Dockerfile` and `docker-compose.yml` at the repo root exist to make the
dev environment reproducible — install once, run tests the same way on any
machine — not to deploy anything. There's no FastAPI service to containerize
yet (`main.py` is still a stub), so "containerize Durin" currently means
"containerize the toolchain: Python 3.14, the heavy ML deps, llama.cpp, and a
Neo4j to test against."

## Two build targets, one Dockerfile

```
docker build --target cpu -t durin:cpu .   # default
docker build --target gpu -t durin:gpu .   # needs --gpus all at run time
```

`cpu` is a `python:3.14-slim` image with the plain PyPI wheel for
`llama-cpp-python`. `gpu` starts from an `nvidia/cuda` devel image (needed for
`nvcc`, the CUDA compiler) and recompiles `llama-cpp-python` with
`CMAKE_ARGS="-DGGML_CUDA=on"` against that toolchain. `torch`/`torchvision`
don't need this — they already resolve to prebuilt CUDA wheels via the
`pytorch-cu132` index declared in `pyproject.toml`, on both targets, since
uv resolves the same lockfile either way. Building the `gpu` target doesn't
need a physical GPU present; only running the resulting image with
`--gpus all` does.

Both base images and the `uv` binary copied into the image are pinned to a
specific digest (`@sha256:...`), not a floating tag — a security review
flagged the original unpinned `python:3.14-slim`,
`nvidia/cuda:13.0.0-devel-ubuntu24.04`, and `ghcr.io/astral-sh/uv:latest`
references as a supply-chain risk (an upstream tag can change under you
without notice), and pinning is the direct fix. The GPU stage's ad-hoc
`llama-cpp-python` reinstall is likewise pinned to the exact version
(`==0.3.34`) `uv.lock` already resolved, so it can't silently drift from what
the `cpu` target ships.

## A real bug, not a config choice: LD_PRELOAD

Both targets set `LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libstdc++.so.6`. This
isn't stylistic — without it, `import llama_cpp` fails at runtime with
`undefined symbol: _ZTVN10__cxxabiv117__class_type_infoE`. The compiled ggml
backend references C++ RTTI symbols, but its own CMake build doesn't link
`libstdc++` on this toolchain (confirmed with `ldd` — the compiled
`.so` genuinely has no `libstdc++.so.6` in its dependency list, even though
it references symbols only that library provides). Passing extra linker
flags at `pip install` time didn't fix it; `LD_PRELOAD` does, by forcing
`libstdc++` into the process's global symbol table before `llama_cpp` tries
to load. This is a workaround for an upstream packaging gap, not something
under this project's control.

## The model file never goes in the image

`models/*.gguf` is excluded via `.dockerignore` and mounted at run time
instead (`./models:/models:ro` in `docker-compose.yml`), read from
`LLM_MODEL_PATH` (an env var, defaulting to `/models/gemma4_e4b_it.gguf` —
see the pharmacovigilance pipeline doc for why that needed to become an env
var instead of a hardcoded host path). The GGUF checkpoint is several
gigabytes; baking it into the image would make every build slow and every
image huge for no benefit, since it doesn't change alongside the code.

## docker-compose.yml: a Neo4j service, and a password that must be set

`docker-compose.yml` adds a `neo4j` service (official `neo4j:5-community`
image) so the stack doesn't depend on a system-installed Neo4j — this
reproduces what's otherwise a manually installed host service. `app` (the
`cpu` target) depends on it being healthy first. `app-gpu` is the same thing
against the `gpu` target, gated behind a `gpu` Compose profile so it's never
built or run by accident.

`NEO4J_PASSWORD` has no default — `${NEO4J_PASSWORD:?Set NEO4J_PASSWORD in
.env before starting the stack}` — compose refuses to start rather than
falling back to a guessable password. An earlier version did have a
default (`durin_dev_password`), also flagged by the security review; a
default password combined with the Neo4j ports being bound made that a real,
if minor, exposure. The ports are also now bound to `127.0.0.1` explicitly
rather than every interface, so the browser UI and bolt port aren't reachable
from outside the host even with a password set.

The bind-mounted source (`.:/app`) is paired with a named volume for
`.venv` (`durin-venv:/app/.venv`) specifically so the bind mount doesn't
shadow the image's already-compiled virtual environment with whatever
`.venv` (or lack of one) happens to exist on the host.

## Running tests that need Neo4j from inside the container

The test suite's Neo4j-backed tests use `testcontainers`, which itself needs
a Docker daemon to talk to. Running those tests from inside the `app`
container means that container needs to reach a Docker daemon too — the
compose file has a commented-out
`- /var/run/docker.sock:/var/run/docker.sock` line for that, off by default
since mounting the host's Docker socket into a container is a meaningful
privilege grant, not something to enable without thinking about it. Without
it, those specific tests skip cleanly (confirmed: 83 passed, 9 skipped,
running the test suite in the built image with no socket mounted) rather than
failing.
