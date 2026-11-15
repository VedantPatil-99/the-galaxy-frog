import type { Metadata } from "next"
import Image from "next/image"
import Link from "next/link"
import { ArrowLeftIcon } from "@phosphor-icons/react/dist/ssr/ArrowLeft"

import { ThemeToggle } from "@/components/theme-toggle"
import { WorkflowExplorer } from "@/components/workflow/explorer"

export const metadata: Metadata = {
  title: "How it works",
  description: "Follow the Galaxy Frog workflow from YouTube import to timestamped answers.",
}

export default function WorkflowPage() {
  return (
    <main className="wf-page min-h-screen">
      <header className="border-b border-(--wf-rule)">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3 px-4 py-3 sm:px-8 sm:py-4">
          <Link href="/" className="inline-flex min-h-11 items-center gap-2 rounded-md text-sm font-semibold">
            <Image src="/logo.png" alt="" width={32} height={32} className="size-8 shrink-0 rounded-lg border border-(--wf-rule) bg-white p-0.5" />
            Galaxy Frog
          </Link>
          <div className="flex shrink-0 items-center gap-2 sm:gap-4">
            <Link href="/" className="inline-flex min-h-11 items-center gap-1.5 rounded-md px-1 text-sm text-(--wf-muted) hover:text-(--wf-active)">
              <ArrowLeftIcon size={15} aria-hidden="true" /> Workspace
            </Link>
            <ThemeToggle />
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-3 pt-8 pb-10 sm:px-8 sm:pt-11 lg:pb-14">
        <div className="mb-6 max-w-3xl px-1 sm:mb-8 sm:px-0">
          <h1 className="font-heading text-4xl leading-[1.02] font-semibold tracking-[-0.06em] sm:text-6xl">From video to evidence.</h1>
          <p className="mt-3 max-w-xl text-sm leading-relaxed text-(--wf-muted) sm:text-base">
            Follow a YouTube link through import, indexing, and timestamped answers. Select a component to see what it does.
          </p>
        </div>
        <WorkflowExplorer />
      </div>
    </main>
  )
}
