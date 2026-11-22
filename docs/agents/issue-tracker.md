# GitHub Issues

Engineering tickets and specs live in [Galaxy Frog's GitHub Issues](https://github.com/VedantPatil-99/the-galaxy-frog/issues).
Use `gh` for tracker operations. Verify the target with `gh repo view --json nameWithOwner`
before the first write from a checkout. Pass `--repo VedantPatil-99/the-galaxy-frog`
when using a fork or when GitHub CLI resolves a different repository.

## Workspace tickets

Link to the work packet in [PLANS.md](../../PLANS.md) and the relevant design,
ADR, or verification record. Use public project details and synthetic examples.
Follow the implementation approval rules in [AGENTS.md](../../AGENTS.md).

## Tracker operations

- Publish a ticket with `gh issue create --title "..." --body-file <path>`.
- Fetch a ticket with `gh issue view <number> --json number,title,body,labels,comments`.
- List tickets with `gh issue list` using the relevant `--state` and `--label` filters.
- Use GitHub sub-issues for parent/child relationships and native dependencies for blockers.
  Where those are unavailable, record `Part of #<parent>` or `Blocked by: #<issue>` in
  the child issue. A ticket becomes unblocked when all its blockers are closed.
- Apply the [triage vocabulary](triage-labels.md) with `gh issue edit <number> --add-label "..."`
  or `--remove-label "..."`. Add decisions with `gh issue comment <number> --body-file <path>`.
  Post the resolution before closing with `gh issue close <number>`.

**PRs as a request surface: no.**
