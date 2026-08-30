import { expect, test } from "bun:test"
import { renderToStaticMarkup } from "react-dom/server"

import Home from "@/app/page"

test("renders the evidence-first product foundation", () => {
  const markup = renderToStaticMarkup(<Home />)

  expect(markup).toContain("Find the moment.")
  expect(markup).toContain("Prove the answer.")
  expect(markup).toContain("Grounded answer contract")
  expect(markup).toContain("API connectivity")
  expect(markup).toContain("Check connection")
  expect(markup).toContain("Preview error")
  expect(markup).toContain('aria-label="Toggle color theme"')
})
