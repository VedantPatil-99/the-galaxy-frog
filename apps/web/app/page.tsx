import Image from "next/image"

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
          <div className="relative flex size-10 shrink-0 items-center justify-center rounded-2xl border border-border/80 bg-[#f1f1f1] p-1.5 shadow-sm shadow-primary/10 transition-transform duration-200 hover:scale-105 sm:size-11 dark:border-white/20 dark:bg-[#f1f1f1] dark:shadow-md dark:shadow-black/40">
            <Image
              src="/logo.png"
              alt="Galaxy Frog"
              width={44}
              height={44}
              priority
              className="size-full object-contain"
            />
          </div>
          <div>
            <p className="font-heading text-sm font-semibold tracking-tight">Galaxy Frog</p>
            <p className="text-xs text-muted-foreground">Evidence before fluency</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden rounded-full border bg-background/75 px-3 py-1.5 text-xs text-muted-foreground backdrop-blur sm:inline-flex">
            Caption-first · durable jobs
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
