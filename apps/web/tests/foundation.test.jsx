import { expect, test } from "bun:test"
import { renderToStaticMarkup } from "react-dom/server"

import Home from "@/app/page"

test("renders the transcript-first workspace", () => {
  const markup = renderToStaticMarkup(<Home />)

  expect(markup).toContain("Ask the video. Keep the receipts.")
  expect(markup).toContain("Queue video")
  expect(markup).toContain("Your evidence workspace is ready.")
  expect(markup).toContain("Phase 2 · Durable ingestion")
  expect(markup).toContain("bounded local audio")
  expect(markup).toContain('aria-label="Toggle color theme"')
})
