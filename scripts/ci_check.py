"""Run the CI checks locally -- the same steps .github/workflows/ci.yml runs.

CI calls this script (one ``--job`` per workflow job), so a local run and a CI
run execute identical commands; a step added here is checked in both places.

Usage (from the repo root, any Python 3.10+; the script is stdlib-only):

    python scripts/ci_check.py            # quick: lint, complexity, tests
                                          # (+ UI steps if UI files changed)
    python scripts/ci_check.py --full     # everything CI runs
    python scripts/ci_check.py --job ui   # one CI job (what the workflow calls)
    python scripts/ci_check.py --list     # show steps
    python scripts/ci_check.py --install-hook   # opt-in pre-push hook (--quick)

Requires ``uv`` on PATH; UI steps also need official Node 24 (see AGENTS.md)
and PowerShell 7 (``pwsh``).
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_REF = "origin/master"
PACKAGED_UI = "src/experiment_control/_ui_dist"
UI_SOURCE = "web/react_ui"

# Files under the incremental C901 complexity guard.
COMPLEXITY_GUARD_FILES = [
    "src/experiment_control/utils/command_interceptors.py",
    "src/experiment_control/utils/network_hosts.py",
    "src/experiment_control/processes/stream_analysis.py",
    "src/experiment_control/processes/influx_writer.py",
    "src/experiment_control/sequencer/sequencer.py",
    "src/experiment_control/processes/interlock.py",
    "src/experiment_control/processes/watchdog.py",
]

IN_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


class StepFailed(Exception):
    pass


@dataclass
class Step:
    name: str
    job: str
    run: Callable[[], None]
    quick: bool = True
    # A failed prerequisite skips the rest of its job (nothing after it is
    # meaningful, e.g. tests after a failed `uv sync`).
    prerequisite: bool = False
    ui: bool = False
    detail: str = field(default="")


def _exe(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise StepFailed(f"`{name}` not found on PATH")
    return path


def _run(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print(f"  $ {' '.join(cmd)}", flush=True)
    full_env = {**os.environ, **(env or {})}
    rc = subprocess.call([_exe(cmd[0]), *cmd[1:]], cwd=cwd, env=full_env)
    if rc != 0:
        raise StepFailed(f"exit code {rc}")


def _git_output(*args: str) -> str:
    return subprocess.run(
        [_exe("git"), *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


# --------------------------------------------------------------------------
# Step bodies
# --------------------------------------------------------------------------
def step_sync() -> None:
    _run(["uv", "sync"])


def step_lint() -> None:
    _run(["uv", "run", "ruff", "check", "src", "tests", "examples"])


def step_complexity() -> None:
    _run(["uv", "run", "ruff", "check", *COMPLEXITY_GUARD_FILES, "--select", "C901"])


def step_tests() -> None:
    # CI's runner is unittest discover (not pytest); keep them identical.
    _run(["uv", "run", "python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-q"])


def step_build_and_verify_wheel() -> None:
    # Build into a fresh dir so a stale wheel in dist/ can never be the one
    # verified.
    with tempfile.TemporaryDirectory(prefix="ec-ci-build-") as out:
        _run(["uv", "build", "--out-dir", out])
        wheels = sorted(Path(out).glob("*.whl"))
        if not wheels:
            raise StepFailed("no wheel produced")
        required = "experiment_control/_ui_dist/index.html"
        with zipfile.ZipFile(wheels[-1]) as zf:
            if required not in set(zf.namelist()):
                raise StepFailed(f"wheel {wheels[-1].name} is missing bundled UI asset {required}")
        print(f"  verified bundled UI in {wheels[-1].name}")


def step_ui_build() -> None:
    _run(["pwsh", "-NoProfile", "-File", str(ROOT / "scripts" / "build_packaged_ui.ps1")])


def step_ui_tests() -> None:
    # npm 11+ resolves package.json from the cwd before --prefix; run inside.
    _run(["npm", "test"], cwd=ROOT / UI_SOURCE)


# Budget for the UI's entry chunk (gzip). The 2026-10 split moved the YAML
# parser and sequencer editing code into lazy chunks (222 -> 182 KB); raise
# this deliberately, not to make a regression pass.
UI_ENTRY_GZIP_BUDGET_BYTES = 200 * 1024
# A string only the `yaml` package contains; it belongs in a lazy chunk.
UI_ENTRY_FORBIDDEN_MARKERS = ("YAMLParseError",)


def step_ui_bundle_budget() -> None:
    dist = ROOT / PACKAGED_UI
    index_html = (dist / "index.html").read_text(encoding="utf-8")
    # Lab PCs may have no internet: the page must not load anything external.
    external_urls = re.findall(r"""(?:src|href)=["']((?:https?:)?//[^"']+)""", index_html)
    if external_urls:
        raise StepFailed(f"index.html references external URLs: {external_urls}")
    # Stylesheets can pull in fonts or other CSS too (`@import`, `url()`).
    for css in sorted((dist / "assets").glob("*.css")):
        css_urls = re.findall(
            r"""(?:@import\s+(?:url\()?|url\()\s*["']?((?:https?:)?//[^"')\s]+)""",
            css.read_text(encoding="utf-8"),
        )
        if css_urls:
            raise StepFailed(f"{css.name} references external URLs: {css_urls}")
    entry =re.search(r"""<script[^>]+src=["']/?(assets/index-[^"']+\.js)["']""", index_html)
    if entry is None:
        raise StepFailed("could not find the entry chunk in index.html")
    code = (dist / entry.group(1)).read_bytes()
    gzip_bytes = len(gzip.compress(code, compresslevel=9))
    print(f"  entry chunk {entry.group(1)}: {len(code) / 1024:.0f} KB, gzip {gzip_bytes / 1024:.0f} KB")
    if gzip_bytes > UI_ENTRY_GZIP_BUDGET_BYTES:
        raise StepFailed(
            f"entry chunk gzip {gzip_bytes / 1024:.0f} KB exceeds the "
            f"{UI_ENTRY_GZIP_BUDGET_BYTES / 1024:.0f} KB budget"
        )
    text = code.decode("utf-8", errors="replace")
    found = [marker for marker in UI_ENTRY_FORBIDDEN_MARKERS if marker in text]
    if found:
        raise StepFailed(f"entry chunk contains code that should load lazily: {found}")


def bundle_changes(path: str) -> list[str]:
    """Files under ``path`` whose content differs from HEAD's index, plus new
    untracked files. Compares normalized content (like CI's ``git add`` +
    ``diff --cached``) without touching the index; ``git status`` would also
    flag files that differ only by autocrlf line endings on Windows."""
    changed = _git_output("diff", "--name-status", "--", path).splitlines()
    untracked = _git_output("ls-files", "--others", "--exclude-standard", "--", path).splitlines()
    return [line for line in changed if line.strip()] + [f"??\t{p}" for p in untracked if p.strip()]


def step_ui_dist_current() -> None:
    lines = bundle_changes(PACKAGED_UI)
    if lines:
        print("\n".join(f"    {line}" for line in lines[:20]))
        if len(lines) > 20:
            print(f"    ... and {len(lines) - 20} more")
        raise StepFailed(
            f"{PACKAGED_UI} is stale or uncommitted: run .\\scripts\\build_packaged_ui.ps1 "
            "and commit the result"
        )


def step_windows_lifecycle_smoke() -> None:
    _run(
        ["uv", "run", "python", "-m", "unittest", "-q", "tests.test_windows_lifecycle_smoke"],
        env={"EC_ENABLE_WINDOWS_LIFECYCLE_SMOKE": "1"},
    )


STEPS: list[Step] = [
    # job "python" == ci.yml `validate`
    Step("sync", "python", step_sync, prerequisite=True, detail="uv sync"),
    Step("lint", "python", step_lint, detail="ruff check src tests examples"),
    Step("complexity", "python", step_complexity, detail="ruff --select C901 (guarded files)"),
    Step("tests", "python", step_tests, detail="unittest discover"),
    Step("wheel", "python", step_build_and_verify_wheel, quick=False, detail="uv build + bundled UI check"),
    # job "ui" == ci.yml `ui_validate`
    Step("ui-build", "ui", step_ui_build, prerequisite=True, ui=True, detail="build_packaged_ui.ps1"),
    Step("ui-tests", "ui", step_ui_tests, ui=True, detail="npm test (vitest)"),
    Step("ui-bundle-budget", "ui", step_ui_bundle_budget, ui=True, detail="entry chunk size/content, no external URLs"),
    Step("ui-dist-current", "ui", step_ui_dist_current, ui=True, detail="_ui_dist matches the build"),
    # job "smoke" == ci.yml `windows_lifecycle_smoke`
    Step("lifecycle-smoke", "smoke", step_windows_lifecycle_smoke, quick=False, detail="Windows lifecycle smoke"),
]
JOBS = sorted({s.job for s in STEPS})


def ui_changed() -> bool:
    """True if UI source or the packaged bundle differ from the base branch
    (committed on this branch or uncommitted)."""
    paths = ["--", UI_SOURCE, PACKAGED_UI]
    try:
        base = _git_output("merge-base", BASE_REF, "HEAD").strip()
        committed = _git_output("diff", "--name-only", f"{base}...HEAD", *paths)
    except (subprocess.CalledProcessError, StepFailed):
        print(f"  (could not diff against {BASE_REF}; running UI steps to be safe)")
        return True
    working = [line for path in (UI_SOURCE, PACKAGED_UI) for line in bundle_changes(path)]
    return bool(committed.strip() or working)


def select_steps(args: argparse.Namespace) -> list[Step]:
    if args.job:
        return [s for s in STEPS if s.job in args.job]
    if args.full:
        return list(STEPS)
    steps = [s for s in STEPS if s.quick]
    if not ui_changed():
        print(f"No UI changes vs {BASE_REF}: skipping UI steps (use --full to force).")
        steps = [s for s in steps if not s.ui]
    return steps


def install_hook() -> int:
    hooks_dir = Path(_git_output("rev-parse", "--git-path", "hooks").strip())
    if not hooks_dir.is_absolute():
        hooks_dir = ROOT / hooks_dir
    hook = hooks_dir / "pre-push"
    if hook.exists() and "ci_check.py" not in hook.read_text(encoding="utf-8", errors="replace"):
        print(f"Refusing to overwrite an existing, unrelated hook: {hook}")
        return 1
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\n"
        "# Installed by scripts/ci_check.py --install-hook. Skip once: git push --no-verify\n"
        'exec python "$(git rev-parse --show-toplevel)/scripts/ci_check.py" --quick\n',
        encoding="utf-8",
        newline="\n",
    )
    hook.chmod(0o755)
    print(f"Installed pre-push hook: {hook}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--quick", action="store_true", help="default: lint, complexity, tests (+UI if changed)")
    mode.add_argument("--full", action="store_true", help="every CI step")
    mode.add_argument("--job", action="append", choices=JOBS, help="run one CI job's steps (repeatable)")
    parser.add_argument("--list", action="store_true", help="list steps and exit")
    parser.add_argument("--install-hook", action="store_true", help="install an opt-in pre-push hook")
    args = parser.parse_args(argv)

    if args.list:
        for s in STEPS:
            tag = "quick" if s.quick else "full "
            print(f"  [{s.job:6}] {tag}  {s.name:16} {s.detail}")
        return 0
    if args.install_hook:
        return install_hook()

    steps = select_steps(args)
    results: list[tuple[str, str, float]] = []
    skipped_jobs: set[str] = set()
    for step in steps:
        if step.job in skipped_jobs:
            results.append((step.name, "skipped", 0.0))
            continue
        print(f"\n==> [{step.job}] {step.name}", flush=True)
        t0 = time.monotonic()
        try:
            step.run()
            results.append((step.name, "ok", time.monotonic() - t0))
        except StepFailed as exc:
            results.append((step.name, "FAILED", time.monotonic() - t0))
            msg = f"{step.name}: {exc}"
            print(f"::error::{msg}" if IN_GITHUB_ACTIONS else f"  FAILED: {msg}", flush=True)
            if step.prerequisite:
                skipped_jobs.add(step.job)

    print("\nSummary:")
    for name, status, dt in results:
        print(f"  {status:8} {name:16} {dt:6.1f}s")
    failed = [name for name, status, _ in results if status != "ok"]
    if failed:
        print(f"\n{len(failed)} step(s) did not pass: {', '.join(failed)}")
        return 1
    print("\nAll steps passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
