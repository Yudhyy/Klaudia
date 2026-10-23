# Contributing to Klaudia

Use GitHub issues to describe work and pull requests to review it. Start with the
[issue workflow](docs/docs/05-development/contributing/issue-workflow.mdx).
Coding agents should also read [AGENTS.md](AGENTS.md).

## Before a change

Search existing issues and PRs. For a substantial change, agree on the problem,
scope, and acceptance criteria in an issue before implementation. Report small
fixes with enough context for a reviewer to reproduce and assess them.

Read the [current architecture](docs/docs/05-development/architecture/overview.mdx)
and inspect the affected code before using the
[target design](docs/docs/05-development/architecture/target.mdx) as an implementation
guide. [Issue #3](https://github.com/Yudhyy/Klaudia/issues/3) tracks architecture
documentation and gradual module changes. Its proposed layout is not the current
repo map. Keep behavior changes separate from file moves where practical.

## Local setup and checks

The backend uses Python 3.11 or later and a uv workspace:

```bash
uv sync --all-packages --group dev
uv run ruff check app/ klaudia/ config/ mcp-ledger/ tests/
uv run ruff format --check app/ klaudia/ config/ mcp-ledger/ tests/
uv run pytest tests/unit -q
```

The mobile CI uses Node 24.12.0. Run these commands from `apps/mobile/`:

```bash
npm ci
npm run typecheck
npm test
```

See [README.md](README.md) for local services and
[tests/e2e/README.md](tests/e2e/README.md) for integration and evaluation setup.
Check actual test-store targets before running service tests. Keep user stores
separate. Live-model checks need model access and can incur charges.

Run tests for the changed behavior and the affected component. Use
[.circleci/continue.yml](.circleci/continue.yml) for the CI command definitions.
Record exact commands and results. State any checks that need services or access
you do not have. Documentation-only changes need link and diff checks; they do
not require model calls or application tests unless they change those contracts.

## Pull requests

Keep each PR focused. Include the problem, resulting behavior, linked issue,
verification, and remaining limits. Add tests for new logic and bug fixes. Update
public docs when a public contract changes. Reviewers must be able to assess the
change without private session notes.

Use Conventional Commit titles for commits and PRs:

```text
fix(ledger): reject stale operation revisions
docs(agent): add repository and issue workflow guidance
```

Use `Related to #N` for partial work. Use a closing reference only when the PR
satisfies the whole issue. Check the PR's base branch before relying on automatic
issue closure. Leave merge and release decisions to maintainers.

## Project knowledge

Keep current behavior, proposed architecture, and research distinct. Record a
lasting design decision when it has a real tradeoff and would be costly to
reverse. Include the reason, alternatives, consequences, and conditions for a
new decision. Add these records when a decision exists, not as empty templates.

Private notes and generated GitHub copies are optional. Contributors must be
able to build, test, and review the public project without them.
