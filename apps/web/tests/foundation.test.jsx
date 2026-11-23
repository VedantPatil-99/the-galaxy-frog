import { expect, test } from "bun:test"
import { renderToStaticMarkup } from "react-dom/server"

import Home from "@/app/page"
import { TranscriptWorkspace } from "@/components/transcript-workspace"
import { WorkspaceSignIn } from "@/components/workspace-sign-in"

test("checks workspace mode before offering legacy or private actions", () => {
  const markup = renderToStaticMarkup(<Home />)

  expect(markup).toContain("Opening workspace")
  expect(markup).not.toContain("Add video")
  expect(markup).not.toContain("Email me a sign-in link")
})

test("defaults to the notebook layout and offers real source import", () => {
  const markup = renderToStaticMarkup(<TranscriptWorkspace />)

  expect(markup).toContain('data-layout="notebook"')
  expect(markup).toContain("Start with a video")
  expect(markup).toContain("YouTube link")
  expect(markup).toContain("Import settings")
  expect(markup).toContain('aria-expanded="false"')
  expect(markup).toContain('aria-label="Toggle color theme"')
  expect(markup).toContain('href="/workflow"')
})

test("offers email-link sign-in only when private auth is ready", () => {
  expect(renderToStaticMarkup(<WorkspaceSignIn ready />)).toContain("Email me a sign-in link")
  expect(renderToStaticMarkup(<WorkspaceSignIn ready={false} />)).not.toContain('type="email"')
})
