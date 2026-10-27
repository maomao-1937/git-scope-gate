# git-scope-gate

[![tests](https://github.com/maomao-1937/git-scope-gate/actions/workflows/tests.yml/badge.svg)](https://github.com/maomao-1937/git-scope-gate/actions/workflows/tests.yml)

A deterministic policy gate for the paths in a Git checkout's current changes.
Useful after a coding agent or script runs: reject inventory entries outside a
small, explicit path scope. Python 3.11+, a trusted Git on PATH supporting
`--no-lazy-fetch`, and no Python dependencies. Older Git versions that do not
support this option fail with exit 2. The recorded local Git version is 2.54.0.windows.1.

## Try it from source

Run from this source directory; no installation or initialized project is needed:

```console
python examples/demo.py allowed
python examples/demo.py blocked
python examples/demo.py rename
python -m unittest discover -s tests -v
```

The demos create and delete their own temporary repositories. `allowed` exits 0;
`blocked` and `rename` exit 1. In the rename example the destination is allowed
but the origin is outside scope, so the gate rejects it.

For your own checkout, save a policy **outside that checkout**, or commit/ignore
it first so the policy itself does not become an unexpected untracked path:

```json
{"allow":["src/","tests/","README.md"],"deny":["src/private/"]}
```

```console
python git_scope_gate.py --repo /absolute/path/to/checkout --policy /absolute/path/to/policy.json --format json
```

`--repo` must resolve to the Git top-level directory. A subdirectory is an error,
even though Git itself can run from there. Windows absolute paths work too.

## Policy and output

The policy is strict UTF-8 JSON with exactly `allow` and optional `deny` arrays.
`allow` must be nonempty. Entries are exact relative paths, or directory prefixes
ending in `/`. `src/` matches `src/file` but neither `src` nor `src-other/file`.
Matching is case-sensitive on every platform; deny always wins. No globs,
backslashes, absolute/drive/UNC paths, empty path components, `.`/`..` components,
control characters, or unpaired surrogate characters are accepted. A policy
cannot grant a control-character filename directly; a parent directory prefix
can cover it. Such actual filenames remain escaped in the report.

Limits: 1 MiB policy, 4096 total entries, 16 MiB stdout per Git command,
100000 records per status/index/HEAD-tree/filter-configuration/filter-path
inventory, and 30 seconds per Git command. Attribute-check stdin is also capped
at 16 MiB. Output is
spooled to temporary files and read with a size cap; temporary disk use can grow
until the command exits or times out. The bound is on captured stdout accepted
by the gate, not a universal process-memory or disk quota.

| Exit | Meaning |
| --- | --- |
| 0 | Inventory is empty or every reported endpoint passes policy |
| 1 | One or more endpoints violate allow/deny |
| 2 | Setup, I/O, malformed/unsupported inventory, timeout, or closed stdout |

JSON contains `exit_code`, `result`, `records` (`status`, `paths`), `violations`
(`record` is a zero-based record index, `path`, `reason`), and `reasons`.
Text and JSON both escape tabs/newlines and other special filename characters.
Rename paths are **destination, origin**, in Git's NUL porcelain order; every
endpoint is checked. Both staged and unstaged entries and untracked files count.
An unstaged move reported by Git as deletion plus untracked addition remains two
records. The gate does not infer relationships between them.

## Read-only behavior and limits

Inspection uses `git --no-optional-locks --no-lazy-fetch -c core.fsmonitor=false -C ROOT status
--porcelain=v1 -z --untracked-files=all --ignore-submodules=none --renames`, plus
read-only root, index, and HEAD-tree inventory commands. Before status it checks
effective filter attributes using bounded `ls-files` and `check-attr` inventories.
If a configured nonempty `filter.*.clean` or `filter.*.process` command applies
to an inventoried path, the gate refuses the checkout with exit 2 before Git can
execute that processor. Configured but unused drivers, such as global LFS
settings with no matching attributes, are accepted. Filter commands are never
disabled to manufacture a different clean/dirty result. Reserved attribute-value
words that collide with a configured driver name are conservatively rejected.

No shell, external diff, configured content-filter processor, configured
fsmonitor hook, explicit index refresh, or config mutation is used.
Git's optional index writes are disabled. The executable found on PATH must be
trusted. Redirecting inherited `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`,
`GIT_COMMON_DIR`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`,
`GIT_NAMESPACE`, inherited `GIT_TRACE*` file-logging settings, and dynamic
`GIT_CONFIG_*` overrides are stripped for inspection. `GIT_NO_LAZY_FETCH=1` and
`--no-lazy-fetch` prevent inspection from hydrating a partial clone. Required
missing objects cause an error rather than a network fetch.
Other Git settings, including ignore rules, remain part of Git's view.

Unmerged states, malformed records, non-UTF-8 path bytes, any gitlinks or
submodules, and untracked directory entries indicating incomplete enumeration
(such as nested repositories) fail with exit 2. All submodule repositories,
including clean ones, are refused **before status** because Git can execute a
populated child's filters during recursive inspection. Index and HEAD gitlink
inventories cover staged additions and deletions. Ignored files are outside the inventory.
A deleted file that was never tracked leaves no status entry and cannot be
detected. An ignored nested repository is also outside the inventory.

This is a nontransactional snapshot assembled from several Git commands. Quiesce
other writers, including writers to attributes/configuration, while running it.
The installed Git and Python runtime must be trusted. It is not a sandbox, rollback tool, editing or
review agent, race-proof audit, baseline comparison, or proof of which actor
changed a file. Existing checkout changes are included. A pass says only that
the observed supported inventory fits the supplied policy.

## 中文快速开始

需要 Python 3.11 及以上版本，以及 PATH 中支持 `--no-lazy-fetch` 的可信 Git。
旧 Git 不支持该参数时返回 2。无需安装依赖：

```console
python examples/demo.py allowed
python examples/demo.py blocked
python examples/demo.py rename
python -m unittest discover -s tests -v
```

这些示例只在临时仓库中操作。`allowed` 返回 0；越界文件或跨范围重命名返回 1。
真实使用时将策略文件放在目标仓库外，再运行：

```console
python git_scope_gate.py --repo D:/your/checkout --policy D:/your/policy.json --format json
```

策略中 `allow` 必填且不能为空，`deny` 可选且优先。`src/` 表示目录前缀，
`README.md` 表示精确文件名，始终区分大小写。重命名会检查新旧两个路径。
返回 2 表示环境错误或不支持的状态，例如冲突、任何子模块、嵌套仓库、
非 UTF-8 路径或实际作用于文件的 clean/process 内容过滤器。
即使子模块干净，也会在运行 status 前拒绝，避免触发其内部过滤器。
仅配置但未匹配文件的过滤器不影响使用。运行时应暂停其他写入，包括属性和配置变更；
忽略文件及从未跟踪过的已删除文件不在检查范围。

See [RESEARCH.md](RESEARCH.md) for positioning and [TESTING.md](TESTING.md) for
the recorded local verification boundary. MIT licensed.
