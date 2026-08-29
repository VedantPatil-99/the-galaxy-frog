# Step 1 — Scope, repository, and invariants

## Goal

Create an unambiguous foundation for humans and implementation agents before framework generators add hundreds of files.

## Completed work

- Defined the MVP, non-MVP, and Phase 0 boundaries.
- Established the `apps/web`, `backend`, `infra`, `scripts`, and `docs` ownership model.
- Added repository and agent rules.
- Recorded architecture decisions in ADRs.
- Added safe environment placeholders and ignore rules.

## Verification commands for PowerShell

```powershell
Set-Location .\galaxy-frog
git status --short
Get-Content .\AGENTS.md
Get-Content .\PLANS.md
Get-ChildItem .\docs\adr -File | Select-Object Name
git check-ignore .env
```

Expected results: the repository is on `main`; the foundation files are untracked until the first local commit; `.env` is ignored; and three accepted ADRs exist.

## Suggested checkpoint commit

```powershell
git add .
git commit -m "chore: establish Galaxy Frog foundation"
```

Commit only after reviewing the scope and architecture decisions.
