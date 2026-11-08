"""Isolated, bounded unittest matrix. No tests are run during discovery.

Usage: python test_matrix_runner.py
Results are new UUID-named files in docs/test-logs; existing files are never
overwritten. Each module gets a fresh child process and an independent timeout.
"""
import concurrent.futures
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parent
LOG_ROOT = ROOT / "docs" / "test-logs"
TIMEOUT_SECONDS = 22
WORKERS = 3
# Incompatible with the explicit prohibition on real GUI windows/foreground.
SKIP = {
    "test_r16_4g_actual_gui": "constructs real Tk root and TypeBridgeGui",
}
TK_ROOT = re.compile(r"\b(?:tk\.Tk|Tk)\s*\(")
RESULT_LINE = re.compile(r"Ran (\d+) tests? in ([\d.]+)s")
FAIL_LINE = re.compile(r"^(FAIL|ERROR): (.+)$", re.MULTILINE)


def module_run(module, log_dir):
    started = time.monotonic()
    cmd = [sys.executable, "test_matrix_module.py", module]
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        run = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=TIMEOUT_SECONDS,
                             creationflags=flags)
        output = run.stdout.decode("utf-8", "replace")
        status = "passed" if run.returncode == 0 else "failed"
        code = run.returncode
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b"").decode("utf-8", "replace")
        output += "\nMODULE TIMED OUT after " + str(TIMEOUT_SECONDS) + " seconds\n"
        status, code = "timeout", None
    except Exception as exc:
        output = "RUNNER ERROR: " + repr(exc) + "\n"
        status, code = "runner-error", None
    elapsed = round(time.monotonic() - started, 3)
    # Module is strictly a globbed basename, never a path supplied by a user.
    name = module + ".log"
    (log_dir / name).write_text(output, encoding="utf-8", errors="replace")
    match = RESULT_LINE.search(output)
    return {
        "module": module, "status": status, "duration_seconds": elapsed,
        "tests": int(match.group(1)) if match else None,
        "exit_code": code, "failures": [m.group(0) for m in FAIL_LINE.finditer(output)],
        "log": name,
    }


def main():
    global TIMEOUT_SECONDS, WORKERS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retry-from', type=Path,
                        help='Prior summary.json; only its timed-out modules will run')
    parser.add_argument('--modules', nargs='+',
                        help='Run only explicitly named test modules')
    parser.add_argument('--timeout', type=int, default=TIMEOUT_SECONDS)
    parser.add_argument('--workers', type=int, default=WORKERS)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 240 or not 1 <= args.workers <= 4:
        parser.error('timeout must be 1..240 seconds and workers 1..4')
    TIMEOUT_SECONDS, WORKERS = args.timeout, args.workers
    helpers = {Path(__file__).stem, 'test_matrix_module'}
    modules = sorted(p.stem for p in ROOT.glob("test_*.py")
                     if p.stem not in helpers)
    if args.retry_from:
        earlier = json.loads(args.retry_from.read_text(encoding='utf-8'))
        timed_out = {row['module'] for row in earlier['results']
                     if row['status'] == 'timeout'}
        modules = [name for name in modules if name in timed_out]
    if args.modules:
        requested = set(args.modules)
        unknown = requested - set(modules)
        if unknown:
            parser.error('unknown or out-of-scope modules: ' + ', '.join(sorted(unknown)))
        modules = [name for name in modules if name in requested]
    for name in modules:
        if TK_ROOT.search((ROOT / (name + ".py")).read_text(encoding="utf-8-sig")):
            SKIP.setdefault(name, "contains real Tk root construction; GUI foreground prohibited")
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    directory = LOG_ROOT / ("unittest-matrix-" + time.strftime("%Y%m%d-%H%M%S")
                            + "-" + uuid.uuid4().hex[:8])
    directory.mkdir(exist_ok=False)
    results = [
        {"module": name, "status": "gui-blocked", "reason": SKIP[name]}
        for name in modules if name in SKIP
    ]
    runnable = [name for name in modules if name not in SKIP]
    print(f"LOG_DIR {directory}; MODULES {len(modules)}; RUNNING {len(runnable)}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        tasks = {pool.submit(module_run, name, directory): name for name in runnable}
        for future in concurrent.futures.as_completed(tasks):
            result = future.result()
            results.append(result)
            if result["status"] != "passed" or result["duration_seconds"] >= 5:
                print(f'{result["module"]} {result["status"]} {result["duration_seconds"]}s '
                      f'{result["tests"]} tests', flush=True)
    results.sort(key=lambda r: r["module"])
    counters = {}
    for row in results:
        counters[row["status"]] = counters.get(row["status"], 0) + 1
    summary = {
        "modules": len(modules), "timeout_per_module_seconds": TIMEOUT_SECONDS,
        "parallel_workers": WORKERS, "counters": counters,
        "observed_tests": sum(r.get("tests") or 0 for r in results),
        "results": results,
    }
    (directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "results"},
                     ensure_ascii=False), flush=True)
    print("LOG_DIR " + str(directory), flush=True)
    return int(any(r["status"] not in ("passed", "gui-blocked") for r in results))


if __name__ == "__main__":
    sys.exit(main())
