"""Run Task Pilot's CI checks in an isolated Antigravity worktree.

Usage: python scripts/verify_worker.py [--preflight]
The full run bootstraps local dependencies and writes one status per check to
.worker-results/report.json. It does not edit application source files.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
RESULTS = ROOT / ".worker-results"
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(command: list[str], *, cwd: Path = ROOT, timeout: int = 900) -> dict:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return {
            "exit_code": completed.returncode,
            "seconds": round(time.monotonic() - started, 1),
            "output": completed.stdout + completed.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "exit_code": None,
            "seconds": round(time.monotonic() - started, 1),
            "output": str(exc),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true", help="Check tools without installing or testing")
    parser.add_argument("--python-only", action="store_true", help="Bootstrap and run Python checks only")
    args = parser.parse_args()

    RESULTS.mkdir(exist_ok=True)
    revision = run(["git", "rev-parse", "HEAD"])
    report = {
        "revision": revision["output"].strip() if revision["exit_code"] == 0 else None,
        "scope": "preflight" if args.preflight else "python" if args.python_only else "full",
        "checks": [],
    }

    def record(name: str, command: list[str] | None, *, cwd: Path = ROOT,
               timeout: int = 900, unavailable: str | None = None,
               failure_is_unverified: bool = False) -> bool:
        if unavailable:
            entry = {"name": name, "status": "UNVERIFIED", "reason": unavailable}
        else:
            assert command is not None
            result = run(command, cwd=cwd, timeout=timeout)
            status = "PASS" if result["exit_code"] == 0 else (
                "UNVERIFIED" if result["exit_code"] is None or failure_is_unverified else "FAIL"
            )
            output = result.pop("output")
            log_path = RESULTS / f"{len(report['checks']) + 1:02d}-{name}.log"
            log_path.write_text(output, encoding="utf-8")
            entry = {"name": name, "status": status, "command": command,
                     "log": str(log_path.relative_to(ROOT)), **result}
            test_count = re.search(r"Ran (\d+) tests?", output) or re.search(r"(\d+) passed", output)
            if test_count:
                entry["test_count"] = int(test_count.group(1))
            if status != "PASS":
                entry["reason"] = f"See {entry['log']}"
        report["checks"].append(entry)
        print(f"{entry['status']:10} {name}" + (f" - {entry['reason']}" if "reason" in entry else ""), flush=True)
        return entry["status"] == "PASS"

    npm = shutil.which("npm")
    npx = shutil.which("npx")
    docker = shutil.which("docker")
    uv = shutil.which("uv")
    record("python_available", [sys.executable, "--version"])
    docker_ok = False
    if not args.python_only:
        record("npm_available", [npm, "--version"] if npm else None,
               unavailable=None if npm else "npm is not installed")
        docker_ok = record("docker_engine", [docker, "info", "--format", "{{.ServerVersion}}"] if docker else None,
                           timeout=15, unavailable=None if docker else "Docker CLI is not installed",
                           failure_is_unverified=True)

    if not args.preflight:
        venv_command = ([uv, "venv", "--python", sys.executable, str(VENV)] if uv else
                        [sys.executable, "-m", "venv", str(VENV)])
        venv_ok = record("python_environment", venv_command, failure_is_unverified=True)
        deps_command = ([uv, "pip", "install", "--python", str(PYTHON), "-r", "requirements.txt"] if uv else
                        [str(PYTHON), "-m", "pip", "install", "-r", "requirements.txt"])
        deps_ok = record("python_dependencies", deps_command,
                         unavailable=None if venv_ok else "Python environment could not be created",
                         failure_is_unverified=True)
        record("python_tests", [str(PYTHON), "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
               unavailable=None if deps_ok else "Python dependencies are unavailable")
        record("evaluation", [str(PYTHON), "-m", "app.evaluation"],
               unavailable=None if deps_ok else "Python dependencies are unavailable")

        if not args.python_only:
            npm_ok = record("frontend_dependencies", [npm, "ci"] if npm else None, cwd=FRONTEND,
                            unavailable=None if npm else "npm is not installed")
            record("frontend_build", [npm, "run", "build"] if npm else None, cwd=FRONTEND,
                   unavailable=None if npm_ok else "Frontend dependencies are unavailable")
            record("frontend_tests", [npm, "test"] if npm else None, cwd=FRONTEND,
                   unavailable=None if npm_ok else "Frontend dependencies are unavailable")
            browser_ok = record("playwright_browser", [npx, "playwright", "install", "chromium"] if npx else None,
                                cwd=FRONTEND, timeout=600,
                                unavailable=None if npm_ok and npx else "Playwright dependencies are unavailable")
            record("browser_tests", [npx, "playwright", "test", "--reporter=line"] if npx else None,
                   cwd=FRONTEND, timeout=600,
                   unavailable=None if browser_ok else "Playwright browser is unavailable")

            for image, dockerfile in (("api", "Dockerfile.api"), ("web", "Dockerfile.web")):
                record(f"docker_{image}_build",
                       [docker, "build", "--file", dockerfile, "--tag", f"task-pilot-{image}:worker", "."]
                       if docker else None, timeout=1200,
                       unavailable=None if docker_ok else "Docker engine is unavailable; verify in CI")

    destination = RESULTS / "report.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {destination}")
    if any(check["status"] == "FAIL" for check in report["checks"]):
        return 1
    if any(check["status"] == "UNVERIFIED" for check in report["checks"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
