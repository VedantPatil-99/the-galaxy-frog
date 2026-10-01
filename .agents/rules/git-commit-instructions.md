---
trigger: model_decision
description: Use when creating or editing Git commit messages. Follow the required Conventional Commit format and rules for type, scope, atomicity, wording, length, breaking changes, and reverts.
---

Generate commit messages using Conventional Commits.

Format:
<type>(<optional-scope>): <short summary>

Rules:
- Use one of: feat, fix, refactor, docs, style, test, chore, build, ci, perf, revert
- make atomic commits,  a single commit should represent one logical change. Do not bundle a bug fix and a feature rewrite into one massive commit.
- Use lowercase for type and scope
- Keep the summary concise, specific, and in imperative mood
- Do not end the summary with a full stop
- Prefer a scope when it adds clarity
- Limit the subject line to 50 characters (soft limit; absolute max is 72
- Describe the actual change made, not the ticket or vague intent
- Do not use filler such as "update stuff", "misc fixes", or "changes"
- Do not mention AI, Codex, or tool assistance
- If the change is breaking, add ! after the type or scope and include a BREAKING CHANGE footer
- For revert commits, use the Conventional Commits revert form

Good examples:
- feat(auth): add magic link login flow
- fix(api): handle null invoice due date
- refactor(search): simplify query builder branching
- docs(setup): clarify local PostgreSQL steps
- perf(images): avoid duplicate thumbnail generation

When multiple changes exist, choose the commit type based on the primary user-visible or technical impact.
Prefer precision over generality.