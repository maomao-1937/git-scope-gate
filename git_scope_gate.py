#!/usr/bin/env python3
"""Read-only, conservative Git checkout scope gate. Python 3.11+, stdlib only."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

MAX_POLICY = 1024 * 1024
MAX_ENTRIES = 4096
MAX_OUTPUT = 16 * 1024 * 1024
MAX_RECORDS = 100000
TIMEOUT = 30
REDIRECT_ENV = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
                "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"}


class GateError(Exception):
    """A setup, unsupported inventory, or I/O failure; never policy success."""


def git_environment():
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in REDIRECT_ENV
           and not k.upper().startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_", "GIT_TRACE"))}
    env["GIT_NO_LAZY_FETCH"] = "1"
    return env


def git(root, *args, allow_missing=False, input_data=None):
    # File-backed capture avoids first reading arbitrary output into memory.
    # Files may grow until Git exits or the timeout expires; reads are bounded.
    if input_data is not None and len(input_data) > MAX_OUTPUT:
        raise GateError("Git input exceeds 16 MiB")
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, tempfile.TemporaryFile() as source:
        if input_data is not None:
            source.write(input_data)
            source.seek(0)
        try:
            process = subprocess.Popen(
                ["git", "--no-optional-locks", "--no-lazy-fetch", "-c", "core.fsmonitor=false", "-C", str(root), *args],
                stdout=out, stderr=err, stdin=source if input_data is not None else subprocess.DEVNULL,
                env=git_environment(), shell=False)
            try:
                code = process.wait(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise GateError("Git inspection timed out") from None
        except OSError:
            raise GateError("Git could not be executed") from None
        out.seek(0)
        data = out.read(MAX_OUTPUT + 1)
        if len(data) > MAX_OUTPUT:
            raise GateError("Git output exceeds 16 MiB")
        if allow_missing and code == 1:
            return None
        if code:
            # Raw stderr may disclose config, URLs, paths, or file contents.
            raise GateError("Git inspection failed (exit %d)" % code)
        return data


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GateError("Policy contains duplicate object keys")
        result[key] = value
    return result


def valid_entry(value):
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        return False
    if any(c in value for c in "\\*?[]") or re.match(r"^[A-Za-z]:", value):
        return False
    components = value[:-1].split("/") if value.endswith("/") else value.split("/")
    return all(c and c not in (".", "..") for c in components)


def load_policy(path):
    try:
        with open(path, "rb") as stream:
            raw = stream.read(MAX_POLICY + 1)
        if len(raw) > MAX_POLICY:
            raise GateError("Policy exceeds 1 MiB")
        policy = json.loads(raw.decode("utf-8"), object_pairs_hook=no_duplicate_keys,
                            parse_constant=lambda _: (_ for _ in ()).throw(GateError("Invalid JSON constant")))
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise GateError("Policy must be readable strict UTF-8 JSON") from None
    if not isinstance(policy, dict) or set(policy) not in ({"allow"}, {"allow", "deny"}):
        raise GateError("Policy requires exactly allow and optional deny")
    if not isinstance(policy["allow"], list) or not policy["allow"]:
        raise GateError("Policy allow must be a nonempty array")
    if not isinstance(policy.get("deny", []), list):
        raise GateError("Policy deny must be an array")
    entries = policy["allow"] + policy.get("deny", [])
    if len(entries) > MAX_ENTRIES:
        raise GateError("Policy exceeds 4096 entries")
    if not all(valid_entry(entry) for entry in entries):
        raise GateError("Policy has an invalid relative path or directory prefix")
    return policy


def nul_fields(data):
    if data and not data.endswith(b"\0"):
        raise GateError("Malformed Git output: missing NUL terminator")
    return data[:-1].split(b"\0") if data else []


def decode_path(raw):
    try:
        path = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        raise GateError("Git path bytes are not UTF-8") from None
    if (not path or path.startswith("/") or re.match(r"^[A-Za-z]:", path)
            or any(part in ("", ".", "..") for part in path.split("/"))):
        raise GateError("Malformed or unsupported Git path")
    return path


def parse_status(data):
    fields = nul_fields(data)
    records = []
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if len(records) >= MAX_RECORDS:
            raise GateError("Git status exceeds 100000 records")
        if len(field) < 4 or field[2:3] != b" ":
            raise GateError("Malformed porcelain status record")
        try:
            status = field[:2].decode("ascii")
        except UnicodeError:
            raise GateError("Unsupported porcelain status") from None
        valid_tracked = ((status[0] == " " and status[1] in "MTADRC")
                         or (status[0] in "MTARC" and status[1] in " MTD")
                         or (status[0] == "D" and status[1] in " MT"))
        if status != "??" and not valid_tracked:
            raise GateError("Unmerged or unsupported porcelain status")
        if field[3:].endswith(b"/"):
            raise GateError("Untracked directory is not fully enumerated (possible nested repository)")
        paths = [decode_path(field[3:])]
        if "R" in status or "C" in status:
            if index >= len(fields):
                raise GateError("Malformed rename record: missing origin")
            paths.append(decode_path(fields[index]))
            index += 1
        records.append({"status": status, "paths": paths})
    return records


def gitlinks(root):
    links = set()
    stages = nul_fields(git(root, "ls-files", "--stage", "-z"))
    if len(stages) > MAX_RECORDS:
        raise GateError("Git index exceeds 100000 records")
    for entry in stages:
        metadata, sep, raw = entry.partition(b"\t")
        if not sep or not re.fullmatch(rb"[0-7]{6} [0-9a-f]+ [0-3]", metadata):
            raise GateError("Malformed index inventory")
        mode, _, stage = metadata.split(b" ")
        if stage != b"0":
            raise GateError("Unmerged index inventory")
        path = decode_path(raw)
        if mode == b"160000":
            links.add(path)
    # HEAD is needed as well: a staged deletion removes the gitlink from the index.
    if git(root, "rev-parse", "--verify", "--quiet", "HEAD", allow_missing=True) is not None:
        entries = nul_fields(git(root, "ls-tree", "-r", "-z", "HEAD"))
        if len(entries) > MAX_RECORDS:
            raise GateError("Git HEAD tree exceeds 100000 records")
        for entry in entries:
            metadata, sep, raw = entry.partition(b"\t")
            if not sep or not re.fullmatch(rb"[0-7]{6} (blob|commit) [0-9a-f]+", metadata):
                raise GateError("Malformed HEAD inventory")
            path = decode_path(raw)
            if metadata.startswith(b"160000 "):
                links.add(path)
    return links


def reject_active_filters(root):
    """Resolve attributes without running Git's content-conversion machinery."""
    configured = git(root, "config", "--null", "--get-regexp",
                     r"^filter\..*\.(clean|process)$", allow_missing=True)
    if configured is None:
        return
    values = {}
    entries = nul_fields(configured)
    if len(entries) > MAX_RECORDS:
        raise GateError("Git filter configuration exceeds 100000 records")
    for entry in entries:
        key, sep, value = entry.partition(b"\n")
        match = re.fullmatch(rb"filter\.(.+)\.(clean|process)", key)
        if not sep or not match:
            raise GateError("Unsupported Git filter configuration")
        # Later definitions override earlier ones, as in Git's filter config.
        values[(match[1], match[2])] = value
    active = {driver for (driver, _), value in values.items() if value}
    if not active:
        return
    raw_names = nul_fields(git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"))
    if len(raw_names) > MAX_RECORDS:
        raise GateError("Git filter inventory exceeds 100000 records")
    for name in raw_names:
        decode_path(name)
    if not raw_names:
        return
    attributes = nul_fields(git(root, "check-attr", "-z", "--stdin", "filter",
                               input_data=b"\0".join(raw_names) + b"\0"))
    if len(attributes) != len(raw_names) * 3:
        raise GateError("Malformed Git filter attribute inventory")
    for index, name in enumerate(raw_names):
        path, attribute, driver = attributes[index * 3:index * 3 + 3]
        if path != name or attribute != b"filter":
            raise GateError("Malformed Git filter attribute inventory")
        if driver in active:
            # check-attr renders special values as reserved words; collisions
            # with driver names are conservatively rejected as well.
            raise GateError("Active Git clean/process filters are unsupported")


def matches(path, entry):
    return path.startswith(entry) if entry.endswith("/") else path == entry


def audit(repo, policy_path):
    policy = load_policy(policy_path)
    try:
        root = Path(repo).resolve(strict=True)
    except (OSError, RuntimeError):
        raise GateError("Repository path could not be resolved") from None
    if not root.is_dir():
        raise GateError("Repository path must be a directory")
    top_raw = git(root, "rev-parse", "--show-toplevel")
    if not top_raw.endswith(b"\n"):
        raise GateError("Malformed repository root output")
    try:
        top = Path(top_raw[:-1].decode("utf-8")).resolve(strict=True)
    except (OSError, UnicodeError, RuntimeError):
        raise GateError("Repository root could not be resolved as UTF-8") from None
    if root != top:
        raise GateError("--repo must resolve to the Git top-level directory")
    links = gitlinks(root)
    if links:
        # Status recursively inspects populated children and could run their
        # filters before we can classify a submodule as clean or dirty.
        raise GateError("Repositories containing gitlinks/submodules are unsupported")
    reject_active_filters(root)
    records = parse_status(git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                               "--ignore-submodules=none", "--renames"))
    violations = []
    for index, record in enumerate(records):
        for path in record["paths"]:
            denied = any(matches(path, entry) for entry in policy.get("deny", []))
            allowed = any(matches(path, entry) for entry in policy["allow"])
            if denied or not allowed:
                violations.append({"record": index, "path": path,
                                   "reason": "denied" if denied else "outside_allow"})
    code = 1 if violations else 0
    return {"exit_code": code, "result": "violation" if code else "clean",
            "records": records, "violations": violations, "reasons": []}


def render(result, output_format):
    if output_format == "json":
        return json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n"
    lines = ["git-scope-gate: " + result["result"]]
    for record in result["records"]:
        lines.append(record["status"] + " " + " -> ".join(json.dumps(p, ensure_ascii=True) for p in record["paths"]))
    for violation in result["violations"]:
        lines.append(violation["reason"] + ": " + json.dumps(violation["path"], ensure_ascii=True))
    lines.extend(result["reasons"])
    return "\n".join(lines) + "\n"


def discard_stdout():
    # Prevent CPython's shutdown flush from replacing exit 2 with exit 120.
    try:
        sys.stdout = open(os.devnull, "w")
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--format", choices=("json", "text"), default="text")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        try:
            sys.stdout.flush()
        except (BrokenPipeError, OSError):
            discard_stdout()
            return 2
        return error.code
    except (BrokenPipeError, OSError):
        discard_stdout()
        return 2
    try:
        result = audit(args.repo, args.policy)
    except (GateError, OSError) as error:
        result = {"exit_code": 2, "result": "error", "records": [], "violations": [],
                  "reasons": [str(error) if isinstance(error, GateError) else "Filesystem I/O failed"]}
    try:
        sys.stdout.write(render(result, args.format))
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        discard_stdout()
        return 2
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
