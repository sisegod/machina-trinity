# Contributing

Thanks for helping make Machina safer and more useful.

## What we love
- Small, composable tools (tier0 first)
- Hardening patches (timeouts, allowlists, sandboxing)
- Reproducible bug reports (replay logs, minimal JSON requests)
- Docs that make running Machina less scary

## Prerequisites

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install -y build-essential cmake pkg-config libjson-c-dev

# macOS
brew install cmake json-c pkg-config
```

If `libjson-c-dev` is not available on your system, build json-c locally and set:
```bash
export PKG_CONFIG_PATH=/path/to/local/lib/pkgconfig
```

## Build

```bash
# Load environment (sets MACHINA_ROOT, embedding config, LLM config, etc.)
source machina_env.sh

# Build with tests enabled
cmake -S . -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo -DBUILD_TESTING=ON
cmake --build build -j$(nproc)
```

## Run tests

```bash
# C++ unit tests (13 suites, ~2s)
cd build && ctest --output-on-failure && cd ..

# Full test catalog (requires serve mode + Ollama)
bash scripts/run_test_catalog.sh
```

## Run quick smoke tests

```bash
./build/machina_cli run examples/run_request.error_scan.json
./build/machina_cli serve --host 127.0.0.1 --port 8080 --workers 2
```

## Pull requests

Please include:
- **What changed** (1-3 sentences)
- **Why** (bug, security, performance, or design)
- **How to test** (commands + expected output)

If the change touches safety boundaries (shell / policy / plugin loading), add:
- threat model note (what we're protecting against)
- default behavior stays conservative

## Style

- Prefer explicit checks over "clever" code
- Keep unsafe capabilities behind:
  - allowlists
  - timeouts
  - output caps
  - OS-level sandbox recommendations
- Log decisions that matter (auditability)
