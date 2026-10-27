#!/usr/bin/env python3
"""Reproducible examples; all Git writes are confined to a throwaway repository."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=("clean", "allowed", "blocked", "rename", "denied",
                                         "bad-policy", "nested", "subdir", "text"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="git-scope-gate-demo-") as directory:
        base = Path(directory)
        repo = base / "repo"
        repo.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        def git(*arguments):
            subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                            "-C", str(repo), *arguments], env=env,
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            git("init", "-q")
            git("config", "user.name", "Scope Demo")
            git("config", "user.email", "demo@example.invalid")
            policy = base / "policy.json"
            policy.write_text(json.dumps({"allow": ["src/"], "deny": ["src/private/"]}),
                              encoding="utf-8", newline="\n")
            (repo / "src").mkdir()
            if args.case in ("allowed", "text"):
                (repo / "src/hello.txt").write_text("hello\n", encoding="utf-8")
            elif args.case == "blocked":
                (repo / "outside.txt").write_text("outside\n", encoding="utf-8")
            elif args.case == "denied":
                (repo / "src/private").mkdir()
                (repo / "src/private/key").write_text("example\n", encoding="utf-8")
            elif args.case == "rename":
                (repo / "outside.txt").write_text("hello\n", encoding="utf-8")
                git("add", "outside.txt")
                git("commit", "-qm", "demo base")
                git("mv", "outside.txt", "src/moved.txt")
            elif args.case == "bad-policy":
                policy.write_text('{"allow":["../escape"]}', encoding="utf-8")
            elif args.case == "nested":
                nested = repo / "src/nested"
                nested.mkdir()
                subprocess.run(["git", "-C", str(nested), "init", "-q"],
                               env=env, capture_output=True, check=True)
                (nested / "file").write_text("nested\n", encoding="utf-8")
            target = repo / "src" if args.case == "subdir" else repo
            return subprocess.run([sys.executable, str(ROOT / "git_scope_gate.py"),
                                   "--repo", str(target), "--policy", str(policy),
                                   "--format", "text" if args.case == "text" else "json"]).returncode
        except (OSError, subprocess.CalledProcessError):
            print("Demo setup failed; Git must be available on PATH", file=sys.stderr)
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
