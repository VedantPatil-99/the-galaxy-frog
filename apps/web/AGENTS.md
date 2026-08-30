<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## Frontend conventions

- Follow the current Tailwind CSS v4 documentation and prefer canonical utilities over arbitrary CSS property syntax when an equivalent utility exists.
- Use the `Icon`-suffixed Phosphor React component names.
- Import each client icon directly from `@phosphor-icons/react/dist/csr/<IconName>`.
- Import each Server Component icon directly from `@phosphor-icons/react/dist/ssr/<IconName>`.
- Avoid Phosphor barrel and wildcard imports so development builds do not eagerly transpile the entire icon package.
