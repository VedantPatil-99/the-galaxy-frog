"use client"

import { BookOpenIcon } from "@phosphor-icons/react/dist/csr/BookOpen"
import { ChatCircleIcon } from "@phosphor-icons/react/dist/csr/ChatCircle"
import { FlaskIcon } from "@phosphor-icons/react/dist/csr/Flask"
import { SidebarSimpleIcon } from "@phosphor-icons/react/dist/csr/SidebarSimple"
import { VideoCameraIcon } from "@phosphor-icons/react/dist/csr/VideoCamera"
import Image from "next/image"
import Link from "next/link"
import { useState, useSyncExternalStore, type ReactNode } from "react"

import { ThemeToggle } from "@/components/theme-toggle"
import { Button } from "@/components/ui/button"

export type WorkspacePane = "galaxy" | "work" | "sources" | "lab"
type Layout = "focus" | "notebook"
const paneIds: Record<WorkspacePane, string> = {
  galaxy: "workspace-notebooks",
  work: "workspace-content",
  sources: "workspace-sources",
  lab: "workspace-lab",
}
const layoutEvent = "galaxy-frog:workspace-layout"
const smallScreen = "(max-width: 51.99rem)"
const wideScreen = "(min-width: 82rem)"

function subscribeLayout(onChange: () => void) {
  window.addEventListener(layoutEvent, onChange)
  window.addEventListener("storage", onChange)
  return () => {
    window.removeEventListener(layoutEvent, onChange)
    window.removeEventListener("storage", onChange)
  }
}

function defaultLayout(): Layout {
  return "notebook"
}

function subscribeScreen(onChange: () => void) {
  const queries = [window.matchMedia(smallScreen), window.matchMedia(wideScreen)]
  queries.forEach(query => query.addEventListener("change", onChange))
  return () => queries.forEach(query => query.removeEventListener("change", onChange))
}

export function WorkspaceShell({
  title,
  pane,
  onPaneChange,
  galaxy,
  sources,
  lab,
  children,
  preferenceKey = "local",
}: {
  title: string
  pane: WorkspacePane
  onPaneChange: (pane: WorkspacePane) => void
  galaxy: ReactNode
  sources?: ReactNode
  lab?: ReactNode
  children: ReactNode
  preferenceKey?: string
}) {
  const [temporaryLayout, setTemporaryLayout] = useState<Layout | null>(null)
  const storageKey = `galaxy-frog:layout:${preferenceKey}`
  const savedLayout = useSyncExternalStore(subscribeLayout, () => {
    try {
      return localStorage.getItem(storageKey) === "focus" ? "focus" : "notebook"
    } catch {
      return "notebook"
    }
  }, defaultLayout)
  const layout = temporaryLayout ?? savedLayout
  const isSmall = useSyncExternalStore(subscribeScreen, () => window.matchMedia(smallScreen).matches, () => false)
  const isWide = useSyncExternalStore(subscribeScreen, () => window.matchMedia(wideScreen).matches, () => false)

  function chooseLayout(next: Layout) {
    try {
      localStorage.setItem(storageKey, next)
      window.dispatchEvent(new Event(layoutEvent))
    } catch {
      setTemporaryLayout(next)
    }
  }

  const sections = [
    { id: "work", label: "Workspace", Icon: ChatCircleIcon },
    ...(sources ? [{ id: "sources", label: "Sources", Icon: VideoCameraIcon } as const] : []),
    ...(lab ? [{ id: "lab", label: "Frog Lab", Icon: FlaskIcon } as const] : []),
  ] as const

  function isOpen(id: WorkspacePane) {
    if (isSmall) return pane === id
    if (id === "work" || id === "galaxy") return true
    if (id === "sources" && layout === "notebook") return isWide || pane !== "lab"
    return pane === id
  }

  return (
    <div className="workspace flex min-h-dvh flex-col" data-layout={layout} data-pane={pane} data-has-sources={Boolean(sources)}>
      <header className="workspace-header">
        <div className="flex min-w-0 items-center gap-2.5">
          <Image src="/logo.png" width={28} height={28} alt="" priority className="rounded-md bg-white" />
          <span className="workspace-wordmark">Galaxy Frog</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {sources ? <div className="workspace-layout-switch rounded-lg border bg-background p-0.5" role="group" aria-label="Workspace layout">
            <Button variant={layout === "focus" ? "secondary" : "ghost"} aria-pressed={layout === "focus"} onClick={() => chooseLayout("focus")}>
              <SidebarSimpleIcon aria-hidden="true" /> Focus
            </Button>
            <Button variant={layout === "notebook" ? "secondary" : "ghost"} aria-pressed={layout === "notebook"} onClick={() => chooseLayout("notebook")}>
              <BookOpenIcon aria-hidden="true" /> Notebook
            </Button>
          </div> : null}
          <div className="workspace-mobile-galaxy">
            <Button variant="ghost" className="min-h-11 px-2" aria-controls={paneIds.galaxy} aria-pressed={pane === "galaxy"} onClick={() => onPaneChange(pane === "galaxy" ? "work" : "galaxy")}>
              Notebooks
            </Button>
          </div>
          <ThemeToggle />
        </div>
      </header>
      <nav aria-label="Workspace panels" className="workspace-toolbar">
        <span className="workspace-location min-w-0 truncate text-sm font-semibold">{title}</span>
        <div className="workspace-panel-buttons">
          {sections.map(({ id, label, Icon }) => (
            <Button key={id} variant={(id === "work" ? pane === id : isOpen(id)) ? "secondary" : "ghost"} aria-controls={paneIds[id]} aria-pressed={id === "work" ? pane === id : isOpen(id)} onClick={() => onPaneChange(id)}>
              <Icon aria-hidden="true" /> {label}
            </Button>
          ))}
        </div>
      </nav>
      <div className="workspace-panes">
        <aside id={paneIds.galaxy} aria-label="Notebooks" className="workspace-galaxy">
          {galaxy}
          <Link href="/workflow" className="mt-auto inline-flex min-h-11 items-center text-sm text-muted-foreground underline-offset-4 hover:underline">How it works</Link>
        </aside>
        <main id={paneIds.work} className="workspace-work">
          <div className="workspace-reading">{children}</div>
        </main>
        {sources ? <section id={paneIds.sources} aria-label="Sources" className="workspace-sources">{sources}</section> : null}
        {lab ? <section id={paneIds.lab} aria-label="Frog Lab" className="workspace-lab">{lab}</section> : null}
      </div>
    </div>
  )
}
