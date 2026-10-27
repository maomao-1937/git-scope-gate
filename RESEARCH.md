# Research and scope

Prepared 2026-10-27. Sources were inspected by the coordinating agent; this
implementation was written independently without copying upstream source code.

## Observed facts

- Git documents porcelain v1 as stable for scripts, paths relative to the
  repository root, and `-z` rename records as destination followed by origin:
  [git-status documentation](https://git-scm.com/docs/git-status).
- A September 2026 planning document for a review-until-clean skill discusses
  review scope and untracked changes:
  [agent-harness-plugins plan](https://github.com/cboone/agent-harness-plugins/blob/main/docs/plans/todo/2026-09-21-add-review-until-clean-skill.md).
  A planning document is evidence of an articulated workflow, not proof of
  shipped behavior, adoption, or demand.
- [dsh-simplify](https://github.com/MichengAI/dsh-simplify) is an alternative
  centered on agent-assisted scoped code simplification.

## Inference and product choice

Explicit path allow/deny checks can provide a small deterministic boundary after
agent or script work. This project audits current Git inventory and exits with a
machine-readable decision. It does not simplify code or conduct a semantic
review. Checking both rename endpoints avoids approving a destination while
silently overlooking an origin outside the permitted scope.

## Alternatives

- Direct `git status --porcelain=v1 -z` with an existing policy engine: suitable
  when a team already has robust NUL parsing and both-endpoint matching.
- Agent-assisted review or simplification: useful for semantic changes, with
  broader model, tooling, and review assumptions than this path gate.
- A dedicated worktree or sandbox: provides isolation this tool does not provide.

## Unknowns and boundaries

No adoption, paid demand, repository popularity, or Stars claim is established.
No production integration, hostile executable protection, transactional snapshot,
or actor attribution was validated. Local unit/integration tests cover the
supported inventory contract; they cannot prove the absence of concurrent
changes. Git ignore rules and checkout state define what inventory can observe.

The strict inspection profile refuses every repository with an index/HEAD
gitlink before status, including clean submodules. It also refuses effective
clean/process filter commands assigned to inventoried paths before worktree
status inspection. These conservative exclusions prevent Git from executing
configured conversion processors during observation. Configured but unused
drivers remain supported. The profile requires trusted Git/Python, quiescent
attributes/configuration, and Git support for `--no-lazy-fetch`; it is not a
general sandbox for malicious executables or concurrent configuration changes.
