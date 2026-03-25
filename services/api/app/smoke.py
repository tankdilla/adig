from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


@dataclass
class SmokeCheckResult:
    path: str
    status_code: int
    ok: bool
    details: str = ""


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/"


def fetch_url(url: str, timeout: float = 5.0) -> tuple[int, str]:
    request = Request(url, headers={"User-Agent": "h2n-startup-smoke-check/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read().decode("utf-8", errors="replace")
            return status, body
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body


def wait_for_http_ready(url: str, timeout: float = 30.0, interval: float = 0.5) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    last_error = "service did not become ready"
    while time.monotonic() < deadline:
        try:
            status, _ = fetch_url(url, timeout=min(5.0, interval + 1.0))
            if 200 <= status < 500:
                return True, f"received HTTP {status}"
            last_error = f"received HTTP {status}"
        except URLError as exc:
            last_error = str(exc)
        except Exception as exc:  # pragma: no cover
            last_error = str(exc)
        time.sleep(interval)
    return False, last_error


def run_smoke_suite(base_url: str, timeout: float = 5.0) -> list[SmokeCheckResult]:
    base = _normalize_base_url(base_url)
    checks: list[tuple[str, callable]] = [
        ("/health", _check_health),
        ("/login", _check_login),
        ("/", _check_root_redirect),
    ]
    results: list[SmokeCheckResult] = []
    for path, validator in checks:
        url = urljoin(base, path.lstrip("/"))
        status, body = fetch_url(url, timeout=timeout)
        ok, details = validator(status, body)
        results.append(SmokeCheckResult(path=path, status_code=status, ok=ok, details=details))
    return results


def _check_health(status: int, body: str) -> tuple[bool, str]:
    if status != 200:
        return False, f"expected 200, got {status}"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return False, "response was not valid JSON"
    if payload.get("ok") is True:
        return True, "health endpoint returned ok=true"
    return False, "health endpoint did not return ok=true"


def _check_login(status: int, body: str) -> tuple[bool, str]:
    if status != 200:
        return False, f"expected 200, got {status}"
    markers = ("<form", "username", "token")
    missing = [marker for marker in markers if marker not in body.lower()]
    if missing:
        return False, f"login page missing markers: {', '.join(missing)}"
    return True, "login page rendered"


def _check_root_redirect(status: int, body: str) -> tuple[bool, str]:
    if status in (200, 302, 303, 307, 308):
        return True, "root endpoint responded"
    return False, f"unexpected status {status}"


def _terminate_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a startup smoke check against the API.")
    parser.add_argument("--url", default=os.getenv("SMOKE_CHECK_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--ready-path", default="/health")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument(
        "--startup-command",
        help="Optional shell command to start the API before probing it. Example: 'uvicorn main:app --host 127.0.0.1 --port 8000'",
    )
    args = parser.parse_args(argv)

    process: subprocess.Popen | None = None
    try:
        if args.startup_command:
            process = subprocess.Popen(
                args.startup_command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                preexec_fn=os.setsid if os.name != "nt" else None,
            )

        ready_url = urljoin(_normalize_base_url(args.url), args.ready_path.lstrip("/"))
        ready, message = wait_for_http_ready(ready_url, timeout=args.timeout, interval=args.interval)
        if not ready:
            print(f"Smoke check failed before suite ran: {message}", file=sys.stderr)
            if process and process.stdout:
                output = process.stdout.read()
                if output:
                    print(output, file=sys.stderr)
            return 1

        print(f"Ready check passed: {message}")
        results = run_smoke_suite(args.url, timeout=min(5.0, args.timeout))
        failed = [result for result in results if not result.ok]
        for result in results:
            status = "PASS" if result.ok else "FAIL"
            print(f"[{status}] {result.path} -> HTTP {result.status_code} :: {result.details}")
        return 0 if not failed else 1
    finally:
        if process:
            if os.name != "nt" and process.poll() is None:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            _terminate_process(process)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_cli())
