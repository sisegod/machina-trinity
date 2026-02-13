# Security policy

Machina can execute shell commands and load plugins. Please treat security reports seriously.

## Security Architecture (v3.4+)

### Kernel-Level Isolation
- **seccomp-BPF**: Syscall allowlist filtering (x86_64 + aarch64). Opt-in via `MACHINA_SECCOMP_ENABLE=1`.
- **Process sandboxing**: rlimits (CPU, memory, files, processes), `PR_SET_NO_NEW_PRIVS`
- **bubblewrap (bwrap) integration**: Optional OS-level namespace isolation for LLM-generated code execution (`machina_shared.py:sandboxed_run`)

### Supply Chain Protection
- **Plugin SHA-256 hash pinning**: Cryptographic verification before `dlopen` with constant-time comparison
- **Genesis source guard**: Blocklist for dangerous C/C++ APIs and headers — blocks `system()`, `popen()`, `fork()`, `exec*()`, `dlopen()`, `__asm__`, plus banned headers (`<unistd.h>`, `<sys/socket.h>`, `<netinet/*>`, `<arpa/inet.h>`, `<sys/ptrace.h>`, `<cstdlib>`, etc.)
- **CRC32 WAL framing**: Optional integrity checksums on every WAL entry for crash/corruption detection

### Network Safety (SSRF Defense)
- **DNS resolution + private IP blocking**: All resolved IPs checked against RFC 1918/5737/6598, loopback, link-local, and cloud metadata ranges
- **`curl --resolve` pinning**: After DNS validation, the resolved IP is pinned via `--resolve host:port:ip` to prevent DNS rebinding (TOCTOU)
- **Redirect blocking**: `--max-redirs 0` prevents open-redirect SSRF chains
- **Host allowlist**: `MACHINA_HTTP_ALLOWED_HOSTS` with wildcard support (`*.example.com`)

### Access Control
- **Permission leases**: 4-tier single-use tokens for privileged tool execution (opt-in `MACHINA_LEASE_ENFORCE=1`)
- **Capability filtering**: Per-request tool allow/block lists with glob patterns
- **HMAC request signing**: Nonce replay protection (TTL-based pruning at 5K entries, hard cap at 10K)

### Input Safety
- **`safe_merge_patch()`**: Blocks LLM injection of `_system`/`_queue`/`_meta` keys
- **Path traversal blocking**: `realpath()` / `canonical()` sandbox on all file operations, `O_NOFOLLOW` on writes, symlink-resolved path verification for shell allowlist
- **Tool idempotency**: LRU cache (1024 entries, 60s TTL) prevents duplicate executions

### Server Hardening
- **Per-connection threading**: Each HTTP connection handled in a detached thread
- **Slowloris defense**: 10-second per-connection socket timeout (`SO_RCVTIMEO`/`SO_SNDTIMEO`)
- **Rate limiting**: Per-endpoint configurable token bucket (global, enqueue, run_sync)
- **Enqueue dedup**: Request ID-based idempotency with TTL-based dedup cache (5 min default, WAL-persisted)
- **Body size limit**: `MACHINA_API_MAX_BODY_BYTES` (default 2MB)
- **Worker cap**: `--workers` clamped to 0-64

### Profile System
- **`MACHINA_PROFILE=prod`**: One-switch hardening (fsync, seccomp, genesis off, guard, strict timeouts)

## Reporting a vulnerability

- Please **do not** open a public issue for active vulnerabilities.
- Open a **private security advisory** on this repository, or contact the maintainers directly.
- Include in your report:
  - affected version / commit
  - reproduction steps (minimal)
  - expected vs actual behavior
  - impact assessment (data leak? RCE? sandbox escape?)

## Scope hints

High-value targets include:
- seccomp-BPF filter (`core/src/sandbox.cpp`)
- permission lease system (`core/src/lease.cpp`)
- plugin hash verification (`core/src/plugin_loader.cpp`, `core/src/crypto.cpp`)
- policy runner sandboxing (`core/src/selector_external.cpp`, `core/src/proc.cpp`)
- SSRF defense (`tools/tier0/http_get.cpp` — DNS resolution, private IP blocking, `--resolve` pinning)
- shell exec allowlist / CWD checks / symlink-resolved path verification
- plugin loader path validation
- HTTP `serve` auth + rate limiting + nonce dedup
- Genesis source guard (`tools/tier0/genesis.cpp`)
