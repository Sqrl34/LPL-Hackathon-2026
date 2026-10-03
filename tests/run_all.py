"""Run every offline test. No AWS calls, no Bedrock usage.

    python tests/run_all.py

Covers: scoring rules and quotes, the full pipeline with fake Bedrock, every AWS request shape
(checked against botocore's API definitions), the Strands agent loop, and click-throughs of the
screen in sample mode and in simulated live mode.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NO_AWS = {"AWS_ACCESS_KEY_ID": "offline-test", "AWS_SECRET_ACCESS_KEY": "offline-test",
          "AWS_SESSION_TOKEN": "offline-test"}

SUITES = [
    ("rules and quotes", ["run_scoring.py", "--rules-only"]),
    ("pipeline with fake Bedrock", ["run_scoring.py", "--all", "--mock", "--include-demo", "--targets"]),
    ("AWS request shapes", ["tests/test_aws_shapes.py"]),
    ("Strands agent", ["tests/test_agent.py"]),
    ("screen, sample mode", ["tests/test_app.py", "sample"]),
    ("screen, simulated live mode", ["tests/test_app.py", "live"]),
]

failed = []
for name, args in SUITES:
    env = {**os.environ, **NO_AWS}  # fake keys: nothing here can reach real AWS
    proc = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True)
    lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
    print(f"{'PASS' if proc.returncode == 0 else 'FAIL'}  {name}: {lines[-1] if lines else ''}")
    if proc.returncode:
        failed.append(name)
        print(proc.stdout[-2000:], proc.stderr[-2000:], sep="\n")

print("\nAll suites passed." if not failed else f"\n{len(failed)} suite(s) failed: {', '.join(failed)}")
sys.exit(1 if failed else 0)
