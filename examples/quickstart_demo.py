#!/usr/bin/env python3
"""
Quickstart Demo - End-to-End Machina Experience in One Command

Usage:
    python3 examples/quickstart_demo.py

Demonstrates:
1. Policy driver execution (hello_policy)
2. Direct tool execution (shell)
3. Replay verification
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Set MACHINA_ROOT based on script location
SCRIPT_DIR = Path(__file__).parent.resolve()
MACHINA_ROOT = SCRIPT_DIR.parent
os.environ["MACHINA_ROOT"] = str(MACHINA_ROOT)

# Paths
MACHINA_CLI = MACHINA_ROOT / "build" / "machina_cli"
INBOX = MACHINA_ROOT / "work" / "queue" / "inbox"
DONE = MACHINA_ROOT / "work" / "queue" / "done"

def print_step(step: int, total: int, message: str):
    """Print progress indicator"""
    print(f"\n{'='*60}")
    print(f"▶️  [{step}/{total}] {message}")
    print('='*60)

def run_cmd(cmd: list, stdin_data: str = None, timeout: int = 30) -> tuple:
    """Run subprocess command with timeout"""
    try:
        result = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=MACHINA_ROOT
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)

def main():
    print("🤖 Machina Trinity Legend - Quickstart Demo")
    print(f"   MACHINA_ROOT: {MACHINA_ROOT}")

    # Step 1: Verify binary exists
    print_step(1, 4, "Verifying C++ binary")
    if not MACHINA_CLI.exists():
        print(f"❌ Binary not found: {MACHINA_CLI}")
        print("\n💡 Build Machina first:")
        print("   ./scripts/build_fast.sh")
        print("   # or: mkdir build && cd build && cmake .. && make -j$(nproc)")
        sys.exit(1)
    print(f"✅ Found: {MACHINA_CLI}")

    # Step 2: Run policy driver demo
    print_step(2, 4, "Policy driver execution (hello_policy)")

    # Create inbox directory
    INBOX.mkdir(parents=True, exist_ok=True)

    # Generate request file
    request_file = INBOX / "demo_request.json"
    request_data = {
        "user_task": "Say hello and pick any tool",
        "context": {"demo": True}
    }
    request_file.write_text(json.dumps(request_data, indent=2))
    print(f"📝 Created: {request_file}")

    # Run hello_policy
    env = os.environ.copy()
    env["MACHINA_POLICY_CMD"] = f"python3 {MACHINA_ROOT}/examples/policy_drivers/hello_policy.py"

    returncode, stdout, stderr = run_cmd(
        [str(MACHINA_CLI), "run", str(request_file)],
        timeout=30
    )

    if returncode != 0:
        print(f"❌ Policy execution failed (exit {returncode})")
        print(f"stderr: {stderr}")
        print("\n💡 Check that hello_policy.py exists and is executable")
        sys.exit(1)

    print("✅ Policy executed successfully")
    print(f"Output preview:\n{stdout[:200]}...")

    # Step 3: Direct tool execution
    print_step(3, 4, "Direct tool execution (shell)")

    tool_input = json.dumps({"cmd": "echo hello machina"})
    returncode, stdout, stderr = run_cmd(
        [str(MACHINA_CLI), "tool_exec", "AID.SHELL.EXEC.v1"],
        stdin_data=tool_input,
        timeout=10
    )

    if returncode != 0:
        print(f"❌ Tool execution failed (exit {returncode})")
        print(f"stderr: {stderr}")
        sys.exit(1)

    print("✅ Tool executed successfully")
    print(f"Output: {stdout.strip()}")

    # Step 4: Replay demo
    print_step(4, 4, "Replay verification")

    # Find the most recent done directory
    done_dirs = sorted(DONE.glob("demo_request*"), key=os.path.getmtime, reverse=True)

    if not done_dirs:
        print("⚠️  No replay directory found (policy may not have completed)")
        print("   This is normal if the policy didn't generate logs")
    else:
        replay_dir = done_dirs[0]
        print(f"📁 Replaying: {replay_dir}")

        returncode, stdout, stderr = run_cmd(
            [str(MACHINA_CLI), "replay", str(replay_dir)],
            timeout=30
        )

        if returncode != 0:
            print(f"⚠️  Replay returned exit code {returncode}")
            print(f"stderr: {stderr}")
        elif "replay matched" in stdout.lower() or "ok" in stdout.lower():
            print("✅ Replay verified successfully")
        else:
            print("⚠️  Replay completed but verification unclear")

        print(f"Output preview:\n{stdout[:200]}...")

    # Success summary
    print("\n" + "="*60)
    print("✅ 10분 온보딩 완료!")
    print("="*60)
    print("\n다음 단계:")
    print("  📖 docs/QUICKSTART.md - Full setup guide")
    print("  🏗️  docs/ARCHITECTURE.md - System design")
    print("  🔧 examples/policy_drivers/ - More policy examples")
    print("  🤖 python3 telegram_bot.py - Start the autonomous bot")
    print("\n💡 Tip: Set MACHINA_DEV_EXPLORE=1 for aggressive learning mode")

if __name__ == "__main__":
    main()
