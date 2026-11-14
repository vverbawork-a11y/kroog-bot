"""Kroog's single-process supervisor and pull-based deployment agent.

Only successful push runs of our main-branch CI are eligible. Downloads and
preflight checks run while the previous bot stays online. No public deploy API,
GitHub token, git binary or Docker socket is needed.
"""
from __future__ import annotations

import compileall
from dataclasses import dataclass
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import venv
import zipfile

REPOSITORY = "vverbawork-a11y/kroog-bot"
WORKFLOW = "miniapp-tests.yml"
BRANCH = "main"
ROOT = Path(__file__).resolve().parent
MAX_ARCHIVE = 50 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
LOG = logging.getLogger("kroog.autodeploy")


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def request(url: str):
    return urllib.request.urlopen(urllib.request.Request(url, headers={
        "User-Agent": "Kroog-Autodeploy/1.0",
        "Accept": "application/vnd.github+json",
    }), timeout=25)


def github(path: str) -> dict:
    with request(f"https://api.github.com/repos/{REPOSITORY}/{path}") as response:
        return json.load(response)


def candidate() -> tuple[str, int] | None:
    # Inspect the latest push run, including pending/failed runs. Never fall
    # back to an older successful commit when the current one is failing CI.
    data = github(f"actions/workflows/{WORKFLOW}/runs?branch={BRANCH}&event=push&per_page=1")
    runs = data.get("workflow_runs", [])
    if not runs:
        return None
    run = runs[0]
    sha = run.get("head_sha", "")
    if (run.get("status") != "completed" or run.get("conclusion") != "success"
            or run.get("event") != "push" or run.get("head_branch") != BRANCH
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
            or not re.fullmatch(r"[0-9a-f]{40}", sha)
            or type(run.get("run_number")) is not int):
        return None
    return sha, run["run_number"]


def is_tip(sha: str) -> bool:
    return github(f"git/ref/heads/{BRANCH}").get("object", {}).get("sha") == sha


def managed(path: PurePosixPath) -> bool:
    # Never install data, credentials, dotfiles, CI scripts or repository state.
    return ((len(path.parts) == 1 and (path.suffix == ".py" or path.name == "requirements.txt")
             and path.name != "config_local.py")
            or (path.parts[0] in {"miniapp", "photos", "templates"}
                and not any(p.startswith(".") for p in path.parts)))


def fingerprint(folder: Path) -> str:
    digest = hashlib.sha256()
    files = list(folder.glob("*.py")) + [folder / "requirements.txt"]
    for name in ("miniapp", "photos", "templates"):
        if (folder / name).exists():
            files.extend((folder / name).rglob("*"))
    for file in sorted(files):
        path = PurePosixPath(file.relative_to(folder).as_posix())
        if file.is_file() and managed(path) and "__pycache__" not in path.parts and "previews" not in path.parts:
            digest.update(str(path).encode())
            digest.update(file.read_bytes())
    return digest.hexdigest()


def unpack(archive: Path, target: Path) -> None:
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > 3000 or sum(i.file_size for i in members) > MAX_EXPANDED:
            raise ValueError("Source archive exceeds extraction limits")
        roots = set()
        for info in members:
            path = PurePosixPath(info.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in info.filename
                    or stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError("Unsafe source archive entry")
            if path.parts:
                roots.add(path.parts[0])
        if len(roots) != 1:
            raise ValueError("Expected one GitHub archive root")
        for info in members:
            parts = PurePosixPath(info.filename).parts[1:]
            if not parts or info.is_dir():
                continue
            path = PurePosixPath(*parts)
            if not managed(path):
                continue
            output = target.joinpath(*path.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            with source.open(info) as src, output.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    for name in ("main.py", "config.py", "database.py", "shop.py", "requirements.txt"):
        if not (target / name).is_file():
            raise ValueError("Incomplete application archive")


@dataclass
class Release:
    path: Path
    python: str
    sha: str = "image"
    number: int = 0

    def metadata(self) -> dict:
        return {"path": str(self.path), "python": self.python, "sha": self.sha, "number": self.number}


class Supervisor:
    def __init__(self, root: Path = ROOT):
        self.root = root
        database = Path(os.getenv("DB_PATH", "kroog.db")).expanduser()
        self.database = database if database.is_absolute() else root / database
        self.database = self.database.resolve()
        self.home = self.database.parent / ".kroog-autodeploy"
        self.home.mkdir(parents=True, exist_ok=True)
        self.releases = self.home / "releases"
        self.releases.mkdir(exist_ok=True)
        self.status_file = self.home / "status.json"
        self.ready_file = self.home / "ready"
        self.state = read_json(self.home / "state.json")
        self.base_hash = fingerprint(root)
        self.base = Release(root, sys.executable)
        self.current = self.base
        self.previous = self.base
        self.child = None
        self.stopping = False
        self.interval = max(120, int(os.getenv("KROOG_UPDATE_INTERVAL", "120")))
        self.start_timeout = max(10, int(os.getenv("KROOG_START_TIMEOUT", "90")))
        self.probation = max(1, int(os.getenv("KROOG_PROBATION_SECONDS", "10")))
        self.status = {"enabled": True, "interval_seconds": self.interval}
        # On a simple container restart, reuse the last healthy release. A new
        # platform image takes precedence when its bundled source has changed.
        if self.state.get("base_hash") == self.base_hash:
            active = self.state.get("active", {})
            path = Path(active.get("path", "/nonexistent"))
            python = active.get("python", "")
            if (path.parent == self.releases and (path / "main.py").is_file()
                    and python and Path(python).exists()):
                self.current = Release(path, python, active["sha"], active["number"])
        else:
            self.state = {"base_hash": self.base_hash, "failed": []}

    def report(self, phase: str, **details) -> None:
        self.status.update(phase=phase, current_sha=self.current.sha, **details)
        atomic_json(self.status_file, self.status)
        LOG.info("%s: current=%s", phase, self.current.sha[:12])

    def save(self) -> None:
        self.state.update(base_hash=self.base_hash, active=self.current.metadata())
        atomic_json(self.home / "state.json", self.state)

    def environment(self) -> dict:
        env = os.environ.copy()
        env.update(DB_PATH=str(self.database), KROOG_COMMIT=self.current.sha,
                   KROOG_READY_FILE=str(self.ready_file),
                   KROOG_DEPLOY_STATUS=str(self.status_file), PYTHONDONTWRITEBYTECODE="1")
        # Preserve optional local credentials without copying them into releases.
        env["PYTHONPATH"] = str(self.root) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    def stop_child(self) -> None:
        if self.child and self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait()
        self.ready_file.unlink(missing_ok=True)

    def launch(self) -> bool:
        self.ready_file.unlink(missing_ok=True)
        self.child = subprocess.Popen([self.current.python, str(self.current.path / "main.py")],
                                     cwd=self.current.path, env=self.environment())
        deadline = time.monotonic() + self.start_timeout
        ready_since = None
        while time.monotonic() < deadline and not self.stopping:
            if self.child.poll() is not None:
                return False
            if self.ready_file.exists():
                ready_since = ready_since or time.monotonic()
                if time.monotonic() - ready_since >= self.probation:
                    return True
            time.sleep(.2)
        self.stop_child()
        return False

    def prepare(self, sha: str, number: int) -> Release:
        target = self.releases / sha
        if target.exists():
            shutil.rmtree(target)
        target.mkdir()
        try:
            with tempfile.TemporaryFile() as archive:
                with request(f"https://codeload.github.com/{REPOSITORY}/zip/{sha}") as response:
                    size = 0
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_ARCHIVE:
                            raise ValueError("Source download exceeds size limit")
                        archive.write(chunk)
                archive.seek(0)
                unpack(archive, target)
            if not compileall.compile_dir(target, quiet=2):
                raise ValueError("Python syntax check failed")
            python = sys.executable
            if (target / "requirements.txt").read_bytes() != (self.root / "requirements.txt").read_bytes():
                # A release-specific venv keeps the running bot's dependencies
                # untouched. Inherit image packages; install only changed pins.
                venv.EnvBuilder(with_pip=True, system_site_packages=True).create(target / ".venv")
                python = str(target / ".venv/bin/python")
                subprocess.run([python, "-m", "pip", "install", "--disable-pip-version-check",
                                "--no-cache-dir", "-r", str(target / "requirements.txt")],
                               check=True, timeout=240, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([python, "-c", "import main"], cwd=target, env=self.environment(),
                           check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return Release(target, python, sha, number)
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise

    def backup(self) -> None:
        if self.database.exists():
            # Online SQLite backup includes WAL data; never restore it over
            # live orders automatically during a code rollback.
            with sqlite3.connect(self.database) as source:
                with sqlite3.connect(self.home / "before-update.sqlite3") as target:
                    source.backup(target)

    def reject(self, sha: str) -> None:
        self.state["failed"] = (self.state.get("failed", []) + [sha])[-20:]
        self.save()

    def activate(self, release: Release) -> bool:
        old = self.current
        self.report("restarting", candidate_sha=release.sha)
        self.stop_child()
        try:
            self.backup()
            self.current = release
            if not self.launch():
                raise RuntimeError("New application did not become ready")
        except Exception:
            self.stop_child()
            self.current = old
            self.reject(release.sha)
            self.launch()
            self.report("rolled_back", error="startup_failed", candidate_sha=release.sha)
            return False
        self.previous = old
        self.save()
        self.report("running", error=None, candidate_sha=None)
        self.cleanup()
        return True

    def cleanup(self) -> None:
        keep = {self.current.path, self.previous.path}
        for path in self.releases.iterdir():
            if path.is_dir() and path not in keep:
                shutil.rmtree(path, ignore_errors=True)

    def check(self) -> None:
        self.report("checking", last_checked=int(time.time()))
        item = candidate()
        if item is None:
            self.report("waiting_for_ci", error=None)
            return
        sha, number = item
        if sha == self.current.sha or number < self.current.number:
            self.report("running", error=None)
            return
        if sha in self.state.get("failed", []):
            self.report("update_rejected", error="previous_attempt_failed", candidate_sha=sha)
            return
        self.report("preparing", candidate_sha=sha)
        try:
            release = self.prepare(sha, number)
            # Recheck after download/preflight: a newer push may have arrived.
            if not is_tip(sha) or self.stopping:
                shutil.rmtree(release.path, ignore_errors=True)
                self.report("waiting_for_ci")
                return
            if self.current.sha == "image" and fingerprint(self.root) == fingerprint(release.path):
                self.current.sha, self.current.number = sha, number
                self.save()
                self.report("running", error=None, candidate_sha=None)
                shutil.rmtree(release.path)
                return
        except Exception:
            self.report("update_failed", error="preparation_failed", candidate_sha=sha)
            LOG.exception("Could not prepare update; keeping the running application")
            return
        self.activate(release)

    def run(self) -> None:
        lock = (self.home / "supervisor.lock").open("w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def stop(signum, frame):
            self.stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            self.report("starting")
            if not self.launch():
                if self.current.path == self.root:
                    raise RuntimeError("Bundled application failed to start")
                failed = self.current.sha
                self.stop_child()
                self.current = self.base
                self.reject(failed)
                if not self.launch():
                    raise RuntimeError("Fallback application failed to start")
            self.report("running")
            next_check = time.monotonic() + 15
            while not self.stopping:
                if self.child.poll() is not None:
                    failed = self.current
                    self.current = self.previous
                    if failed.path != self.current.path:
                        self.reject(failed.sha)
                    self.report("recovering", error="application_exited")
                    if not self.launch():
                        raise RuntimeError("Application recovery failed")
                if time.monotonic() >= next_check:
                    try:
                        self.check()
                    except Exception:
                        self.report("check_failed", error="github_unavailable")
                        LOG.warning("GitHub check failed; retrying in %s seconds", self.interval)
                    next_check = time.monotonic() + self.interval
                time.sleep(.5)
        finally:
            self.stop_child()
            lock.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if os.getenv("KROOG_AUTOUPDATE", "1") == "0":
        os.execv(sys.executable, [sys.executable, str(ROOT / "main.py")])
    Supervisor().run()
