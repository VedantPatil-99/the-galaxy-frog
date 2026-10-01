---
trigger: model_decision
description: Use when creating or editing pull request titles or descriptions. Follow the required title format and reviewer-focused structure, covering summary, changes, testing, and risks
---

Generate pull request titles and descriptions in a pragmatic, review-friendly style.

Title rules:
- Use Conventional Commit style for the PR title where it fits
- Format: <type>(<optional-scope>): <short summary>
- Use one of: feat, fix, refactor, docs, style, test, chore, build, ci, perf, revert
- Keep the title specific and concise
- Do not use hype, filler, or vague wording

Description rules:
- Start with a short summary of what changed and why
- Focus on reviewer-relevant context
- Be concrete about behaviour, risk, and impact
- Do not pad with generic boilerplate
- Do not mention AI, Codex, or tool assistance

Use this structure:

## Summary
- What changed
- Why this change was needed

## Changes
- Key implementation points
- Important files or areas touched
- Any migrations, config, or dependency changes

## Testing
- What was tested
- How it was tested
- Anything not tested

## Risks / Notes
- Regressions or edge cases to watch
- Follow-up work, if any
- Breaking changes, rollout notes, or manual steps, if any

Additional rules:
- Keep it concise but complete
- Call out backwards-incompatible changes explicitly
- Mention database migrations, feature flags, env vars, and operational steps when relevant
- Prefer bullet points over long paragraphs
- Do not invent testing that did not happen