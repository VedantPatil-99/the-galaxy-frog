import { ThemeToggle } from "@/components/theme-toggle"
import { TranscriptWorkspace } from "@/components/transcript-workspace"

export default function Home() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-background text-foreground">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[38rem] bg-[radial-gradient(circle_at_15%_0%,color-mix(in_oklch,var(--primary)_17%,transparent),transparent_42%),radial-gradient(circle_at_88%_12%,color-mix(in_oklch,var(--chart-2)_12%,transparent),transparent_36%)]"
      />
      <header className="mx-auto flex w-full max-w-7xl items-center justify-between px-5 py-6 sm:px-7 lg:px-10 lg:py-8">
        <div className="flex items-center gap-3">
          <div className="relative grid size-10 place-items-center rounded-2xl bg-primary text-primary-foreground shadow-sm shadow-primary/25">
            <span className="font-heading text-sm font-bold tracking-tight">GF</span>
            <span className="absolute -top-1 left-1.5 size-2.5 rounded-full border-2 border-background bg-primary" />
            <span className="absolute -top-1 right-1.5 size-2.5 rounded-full border-2 border-background bg-primary" />
          </div>
          <div>
            <p className="font-heading text-sm font-semibold tracking-tight">Galaxy Frog</p>
            <p className="text-xs text-muted-foreground">Evidence before fluency</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden rounded-full border bg-background/75 px-3 py-1.5 text-xs text-muted-foreground backdrop-blur sm:inline-flex">
            Caption-only · synchronous
          </span>
          <ThemeToggle />
        </div>
      </header>
      <TranscriptWorkspace />
      <footer className="border-t px-5 py-6 text-center text-xs text-muted-foreground">
        FastAPI owns persistence, retrieval, generation, and every HTTP schema.
      </footer>
    </main>
  )
}
