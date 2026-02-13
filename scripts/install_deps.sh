#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

INSTALL_DEV=0
INSTALL_PYTHON=1
INSTALL_BWRAP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev)
      INSTALL_DEV=1
      shift
      ;;
    --skip-python)
      INSTALL_PYTHON=0
      shift
      ;;
    --with-bwrap)
      INSTALL_BWRAP=1
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--dev] [--skip-python] [--with-bwrap]" >&2
      exit 2
      ;;
  esac
done

run_as_root() {
  if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "Need root privilege for: $*" >&2
    exit 1
  fi
}

install_linux_apt() {
  local pkgs=(
    build-essential
    cmake
    pkg-config
    libjson-c-dev
    python3
    python3-pip
    curl
    ca-certificates
  )
  if [[ "$INSTALL_BWRAP" -eq 1 ]]; then
    pkgs+=(bubblewrap)
  fi

  echo "[deps] installing apt packages: ${pkgs[*]}"
  run_as_root apt-get update
  run_as_root apt-get install -y "${pkgs[@]}"
}

install_macos_brew() {
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew is required on macOS: https://brew.sh" >&2
    exit 1
  fi

  local pkgs=(cmake pkg-config json-c python)
  echo "[deps] installing brew packages: ${pkgs[*]}"
  brew update
  brew install "${pkgs[@]}"
}

install_python_deps() {
  if [[ "$INSTALL_PYTHON" -ne 1 ]]; then
    echo "[deps] skipping Python package install (--skip-python)"
    return
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found; cannot install Python dependencies" >&2
    exit 1
  fi

  local req_file="requirements.txt"
  if [[ "$INSTALL_DEV" -eq 1 ]]; then
    req_file="requirements-dev.txt"
  fi

  echo "[deps] installing pip packages from ${req_file}"
  python3 -m pip install --upgrade pip
  python3 -m pip install -r "$req_file"
}

OS="$(uname -s)"
case "$OS" in
  Linux)
    if command -v apt-get >/dev/null 2>&1; then
      install_linux_apt
    else
      echo "Unsupported Linux distro (apt-get not found)." >&2
      echo "Install manually: CMake 3.21+, C++20 compiler, pkg-config, json-c headers." >&2
      exit 1
    fi
    ;;
  Darwin)
    install_macos_brew
    ;;
  *)
    echo "Unsupported OS: $OS" >&2
    exit 1
    ;;
esac

install_python_deps

echo "[deps] done"
echo "[deps] next: cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j\$(nproc)"
