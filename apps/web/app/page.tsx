import { ClockCountdownIcon } from "@phosphor-icons/react/dist/ssr/ClockCountdown";
import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr/MagnifyingGlass";
import { PlayCircleIcon } from "@phosphor-icons/react/dist/ssr/PlayCircle";
import { WaveformIcon } from "@phosphor-icons/react/dist/ssr/Waveform";

import { ApiConnectivity } from "@/components/api-connectivity";
import { ThemeToggle } from "@/components/theme-toggle";

const evidenceTypes = [
  {
    title: "What was said",
    description: "Transcript evidence stays connected to its original cues.",
    icon: WaveformIcon,
  },
  {
    title: "What was shown",
    description: "Screen text and visual moments become searchable evidence.",
    icon: MagnifyingGlassIcon,
  },
  {
    title: "When it happened",
    description: "Every result keeps a precise, seekable video interval.",
    icon: ClockCountdownIcon,
  },
];

const sampleEvidence = [
  { label: "Transcript", time: "08:42–09:06", width: "w-[72%]" },
  { label: "Screen text", time: "08:55–09:18", width: "w-[54%]" },
  { label: "Visual", time: "09:02–09:24", width: "w-[42%]" },
];

export default function Home() {
  return (
    <main className="relative isolate flex min-h-screen flex-col overflow-hidden bg-background text-foreground">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-20 bg-[radial-gradient(circle_at_14%_10%,color-mix(in_oklch,var(--primary)_18%,transparent),transparent_30%),radial-gradient(circle_at_86%_80%,color-mix(in_oklch,var(--chart-2)_14%,transparent),transparent_34%)]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10 bg-[linear-gradient(to_right,var(--border)_1px,transparent_1px),linear-gradient(to_bottom,var(--border)_1px,transparent_1px)] bg-size-[4rem_4rem] opacity-35 mask-[linear-gradient(to_bottom,black,transparent_85%)]"
      />

      <header className="mx-auto flex w-full max-w-7xl items-center justify-between px-6 py-6 lg:px-10">
        <div className="flex items-center gap-3">
          <div className="relative grid size-10 place-items-center rounded-2xl bg-primary text-primary-foreground shadow-sm shadow-primary/25">
            <span className="font-heading text-sm font-bold tracking-tight">GF</span>
            <span className="absolute -top-1 left-1.5 size-2.5 rounded-full border-2 border-background bg-primary" />
            <span className="absolute -top-1 right-1.5 size-2.5 rounded-full border-2 border-background bg-primary" />
          </div>
          <div>
            <p className="font-heading text-sm font-semibold tracking-tight">
              Galaxy Frog
            </p>
            <p className="text-xs text-muted-foreground">A Video RAG</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <div className="hidden items-center gap-2 rounded-full border bg-background/70 px-3 py-1.5 text-xs text-muted-foreground shadow-sm backdrop-blur sm:flex">
            <span className="size-1.5 rounded-full bg-chart-2 shadow-[0_0_0_3px_color-mix(in_oklch,var(--chart-2)_18%,transparent)]" />
            Phase 0 · Foundation
          </div>
          <ThemeToggle />
        </div>
      </header>

      <section className="mx-auto grid w-full max-w-7xl flex-1 items-center gap-14 px-6 py-14 lg:grid-cols-[1.05fr_0.95fr] lg:px-10 lg:py-20">
        <div className="max-w-3xl">
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-primary/20 bg-primary/8 px-3 py-1.5 text-xs font-medium text-primary">
            <PlayCircleIcon aria-hidden="true" weight="fill" />
            Temporal multimodal retrieval
          </div>

          <h1 className="font-heading text-5xl leading-[0.95] font-semibold tracking-[-0.045em] text-balance sm:text-6xl lg:text-7xl">
            Find the moment.
            <span className="block text-primary">Prove the answer.</span>
          </h1>

          <p className="mt-7 max-w-2xl text-base leading-7 text-pretty text-muted-foreground sm:text-lg sm:leading-8">
            Galaxy Frog connects spoken words, screen text, and visual evidence
            to the exact intervals where they appear—so every answer can take
            you back to its source.
          </p>

          <div className="mt-9 flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled
              className="inline-flex h-10 items-center justify-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground opacity-70"
            >
              Import video · Phase 1
            </button>
            <span className="text-xs leading-5 text-muted-foreground">
              The browser remains presentation-only.
              <br className="hidden sm:block" /> All retrieval will run in
              Python.
            </span>
          </div>
        </div>

        <div className="relative mx-auto w-full max-w-xl">
          <div className="absolute -inset-8 -z-10 rounded-[2.5rem] bg-primary/10 blur-3xl" />
          <div className="overflow-hidden rounded-[1.75rem] border bg-card/90 shadow-2xl shadow-foreground/5 backdrop-blur-xl">
            <div className="flex items-center justify-between border-b px-5 py-4">
              <div>
                <p className="text-sm font-medium">Evidence timeline</p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  One moment, multiple modalities
                </p>
              </div>
              <span className="rounded-md bg-muted px-2 py-1 font-mono text-[0.68rem] text-muted-foreground">
                09:06
              </span>
            </div>

            <div className="space-y-5 p-5 sm:p-6">
              <div className="relative h-2 overflow-hidden rounded-full bg-muted">
                <div className="absolute inset-y-0 left-[34%] w-[28%] rounded-full bg-primary" />
                <div className="absolute top-1/2 left-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-[3px] border-card bg-primary shadow" />
              </div>

              <div className="space-y-3">
                {sampleEvidence.map((item) => (
                  <div
                    key={item.label}
                    className="grid grid-cols-[5.5rem_1fr] items-center gap-3"
                  >
                    <div>
                      <p className="text-xs font-medium">{item.label}</p>
                      <p className="font-mono text-[0.62rem] text-muted-foreground">
                        {item.time}
                      </p>
                    </div>
                    <div className="h-8 rounded-lg bg-muted/65 p-1.5">
                      <div
                        className={`${item.width} h-full rounded-md bg-primary/25`}
                      />
                    </div>
                  </div>
                ))}
              </div>

              <div className="rounded-xl border border-primary/15 bg-primary/6 p-4">
                <p className="text-xs font-medium text-primary">
                  Grounded answer contract
                </p>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  Every claim must resolve to known evidence, a modality, and a
                  valid start/end interval.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto w-full max-w-7xl px-6 pb-14 lg:px-10 lg:pb-20">
        <ApiConnectivity />
      </section>

      <section className="mx-auto grid w-full max-w-7xl gap-px overflow-hidden border-y bg-border sm:grid-cols-3 lg:rounded-2xl lg:border">
        {evidenceTypes.map(({ title, description, icon: Icon }) => (
          <article key={title} className="bg-background/95 p-6 lg:p-7">
            <Icon
              aria-hidden="true"
              className="mb-5 text-primary"
              size={24}
              weight="duotone"
            />
            <h2 className="font-heading text-base font-semibold">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              {description}
            </p>
          </article>
        ))}
      </section>

      <footer className="mx-auto flex w-full max-w-7xl flex-col gap-2 px-6 py-7 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between lg:px-10">
        <p>Galaxy Frog · Evidence before fluency.</p>
        <p>Next.js presentation shell · Python application boundary</p>
      </footer>
    </main>
  );
}
