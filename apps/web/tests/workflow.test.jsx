import { expect, test } from "bun:test"
import { renderToStaticMarkup } from "react-dom/server"

import WorkflowPage from "../app/workflow/page"
import { stages } from "../components/workflow/model"

test("opens the workflow with an inspectable source and controls for every stage", () => {
  const markup = renderToStaticMarkup(<WorkflowPage />)

  expect(markup).toContain("Import the video")
  expect(markup).toContain('aria-label="Workflow stages"')
  expect(markup.match(/aria-current="step"/g)).toHaveLength(1)
  expect(markup.match(/aria-pressed="true"/g)).toHaveLength(1)
  expect(markup).toContain("Play walkthrough")
  expect(markup).toContain("Video URL")
  expect(markup).toContain("Next: Index")
})

test("walkthrough steps follow connected devices in every stage", () => {
  for (const { devices, cables, tour } of stages) {
    for (const { from, to } of cables) {
      expect(devices[from]).toBeDefined()
      expect(devices[to]).toBeDefined()
    }
    for (let i = 1; i < tour.length; i++) {
      expect(cables.some(({ from, to }) => from === tour[i - 1] && to === tour[i])).toBe(true)
    }
  }
})
