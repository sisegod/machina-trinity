# Dependencies

This document is the source of truth for build/runtime dependencies.

## Required (all environments)

- CMake `3.21+` (matches `CMakeLists.txt`)
- C++20 compiler (`g++-11+` or `clang-14+`)
- `pkg-config`
- `json-c` development headers/libraries
- Python `3.10+`

## Linux (Debian/Ubuntu)

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential cmake pkg-config libjson-c-dev \
  python3 python3-pip curl ca-certificates
```

Optional hardening:

```bash
sudo apt-get install -y bubblewrap
```

## macOS (Homebrew)

```bash
brew update
brew install cmake pkg-config json-c python
```

## Python packages

Runtime:

```bash
pip install -r requirements.txt
```

Development/CI:

```bash
pip install -r requirements-dev.txt
```

## One-command installer

Install system + Python runtime deps:

```bash
./scripts/install_deps.sh
```

Install dev dependencies too:

```bash
./scripts/install_deps.sh --dev
```

Include bubblewrap on Linux:

```bash
./scripts/install_deps.sh --with-bwrap
```
