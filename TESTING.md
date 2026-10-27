# Verification record

Local environment: Windows, Python 3.11.15, Git 2.54.0.windows.1, 2026-10-27.
All fixture Git writes occur in automatically cleaned temporary repositories;
the source project does not need to be initialized as a Git repository.

```console
python -m unittest tests.test_git_scope_gate.GateTests.test_unborn_empty_is_clean -v
python -m unittest discover -s tests -v
```

The initial single test failed with expected exit 0 versus actual 2 because the
CLI did not yet exist (RED). After implementation, the complete suite reached
39 tests, 37 passed and 2 skipped. The skipped integration cases create literal
tab/newline filename characters or arbitrary non-UTF-8 filename bytes, which
Windows cannot represent. The byte-level parser, rejection of non-UTF-8 input,
and escaped text output for tab/newline paths are still tested on Windows; the
real filename integration cases run on POSIX. The suite lives in `tests/` and
uses the same explicit discovery command as the controller and CI workflow.

Coverage includes unborn and empty repositories, staged/unstaged/untracked
entries, deletions, both directions of crossing renames, unstaged moves as two
independent records, exact/prefix boundaries, deny precedence, case sensitivity,
malformed and oversized policies, C1 control rejection, malformed NUL records,
non-UTF-8 parser input, conflicts, actual dirty submodules, staged gitlink
addition/deletion, untracked nested repositories, ignored files, subdirectory
rejection, polluted Git environment, and a configured fsmonitor sentinel hook.
Real downstream closed pipes are exercised for both JSON and text; both return
2 without CPython's shutdown-flush exit 120. A separate RED test reproduced
exit 120 for `--help` with closed stdout; after the fix it returns 2, and ordinary
`--help` still returns 0. Index/config bytes and modification timestamps are
unchanged after a real dirty-checkout audit. A lowered capture cap is tested
against real Git output; a deterministic timeout unit test verifies kill/error.

Independent review reproduced Git status executing configured content filters.
New RED tests separately reproduced clean and process commands writing a sentinel.
The guard now rejects effective nonempty clean/process drivers before status;
both regressions pass with the sentinel absent, not merely a failing exit code.
A populated submodule with its own clean-filter sentinel is also refused before
inspection, with the sentinel absent. Every index/HEAD gitlink is now unsupported,
including clean submodules, to avoid recursive execution in child repositories.
Tests also cover configured but unused drivers, a later empty command overriding
an earlier configured command, malformed attribute output, filter inventory
record limits, and bounded stdin before spawning Git.

All Git inspection commands include `--no-lazy-fetch` and the forced
`GIT_NO_LAZY_FETCH=1` environment variable. A focused unit test verifies removal
of inherited `GIT_TRACE*` settings and suppression of lazy fetch. This does not
claim an actual network/partial-clone stress test. Git must support the global
option; unsupported versions fail with exit 2. The final expanded suite completed
locally with 39 tests, 37 passed and 2 platform skips.

`checks.json` specifies nine executable CLI scenarios using `examples/demo.py`.
All nine scenarios were executed locally and matched their expected codes.
Each entry supplies Python arguments and the expected exit code. A launcher must
prepend the current Python executable. No fixture directories or generated
repositories need to be downloaded.

These are local tests. Ubuntu execution, remote CI, large-repository timing,
real concurrent writers, and production adoption were not established by this
record. Temporary output capture bounds reads, while spool disk usage can grow
until the Git command exits or reaches its 30-second timeout.
