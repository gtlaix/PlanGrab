"""Run the whole offline test suite. Zero third-party deps (no pytest) — each
test file is a standalone script that prints "OK — N … passed" and exits non-zero
on the first failed assertion.

    python tests/run_all.py
"""
import subprocess
import sys
from pathlib import Path

TESTS = [
    "test_naming.py",
    "test_idox.py",
    "test_northgate.py",
    "test_civica_w2.py",
    "test_registry.py",
    "test_download.py",
    "test_batch.py",
    "test_compat.py",
    "test_build_map.py",
    "test_registry_data.py",
    "test_harvest_tools.py",
    "test_update.py",
    "test_web.py",
]


def main() -> int:
    here = Path(__file__).resolve().parent
    failed = []
    for name in TESTS:
        proc = subprocess.run([sys.executable, str(here / name)],
                              capture_output=True, text=True)
        line = (proc.stdout.strip().splitlines() or [""])[-1]
        if proc.returncode == 0:
            print(f"  PASS  {name:22} {line}")
        else:
            failed.append(name)
            print(f"  FAIL  {name:22}")
            print((proc.stdout + proc.stderr).strip())
    print()
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print(f"All {len(TESTS)} test modules passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
