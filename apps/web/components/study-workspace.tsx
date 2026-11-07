"use client"

import { useEffect, useId, useState } from "react"

import {
  listStudy,
  readStudyRun,
  readStudyVersion,
  startStudy,
  studyExportUrl,
  type StudyCitation,
  type StudyIndex,
  type StudyItem,
  type StudyKind,
  type StudyRun,
  type StudyVersion,
  type StudyVersionSummary,
} from "@/lib/api/study"
import { timestamp } from "@/lib/timestamp"

const LABELS = {
  chapters: "Chapters",
  summary: "Summary",
  notes: "Notes",
  flashcards: "Flashcards",
  quiz: "Quiz",
} satisfies Record<StudyKind, string>

const kinds = Object.keys(LABELS) as StudyKind[]
const control = "rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary disabled:cursor-not-allowed disabled:opacity-50"
const seekStyle = "rounded text-left font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-primary"

type Practice = { revealed?: boolean; choice?: number; checked?: boolean }

function active(run: StudyRun) {
  return run.state === "queued" || run.state === "running"
}

function withRuns(index: StudyIndex, updates: StudyRun[]): StudyIndex {
  const runs = new Map(index.runs.map((run) => [run.kind, run]))
  for (const run of updates) runs.set(run.kind, run)
  return { ...index, runs: [...runs.values()] }
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "The study request could not be completed."
}

function Evidence({ citations, onSeek }: {
  citations: readonly StudyCitation[]
  onSeek: (ms: number) => void
}) {
  if (!citations.length) return null
  return (
    <ul aria-label="Source evidence" className="mt-2 grid gap-2">
      {citations.map((citation, i) => (
        <li key={citation.source_id + i} className="border-l-2 border-primary/30 pl-3 text-sm">
          <button type="button" className={seekStyle} onClick={() => onSeek(citation.seek_ms)}>
            {citation.modality === "ocr" ? "On-screen text" : citation.origin === "asr" ? "Speech transcript" : "Captions"}
            {" · "}{timestamp(citation.start_ms)}–{timestamp(citation.end_ms)}
            {citation.seek_ms !== citation.start_ms ? " · jump to " + timestamp(citation.seek_ms) : ""}
          </button>
          <blockquote className="mt-1 text-muted-foreground">{citation.quote}</blockquote>
        </li>
      ))}
    </ul>
  )
}

function StudyMaterial({ item, practice, update, onSeek }: {
  item: StudyItem
  practice: Practice | undefined
  update: (value: Practice) => void
  onSeek: (ms: number) => void
}) {
  const group = useId()
  if (item.kind === "flashcards") {
    return (
      <div className="rounded-xl border p-4">
        <p className="font-medium">{item.question}</p>
        <button type="button" aria-expanded={!!practice?.revealed} className={control + " mt-2"}
          onClick={() => update({ revealed: !practice?.revealed })}>
          {practice?.revealed ? "Hide answer" : "Reveal answer"}
        </button>
        {practice?.revealed && <><p className="mt-2">{item.answer}</p><Evidence citations={item.citations} onSeek={onSeek} /></>}
      </div>
    )
  }
  if (item.kind === "quiz") {
    return (
      <fieldset className="rounded-xl border p-4">
        <legend className="px-2 font-medium">{item.question}</legend>
        <div className="grid gap-2">
          {item.choices.map((choice, i) => (
            <label key={choice} className="flex items-center gap-3 rounded-md border px-3 py-2 has-checked:border-primary">
              <input type="radio" name={group} checked={practice?.choice === i} disabled={practice?.checked}
                onChange={() => update({ choice: i })} />
              <span>{choice}{practice?.checked && i === item.correct_index ? " (correct)" : ""}</span>
            </label>
          ))}
        </div>
        {practice?.checked ? (
          <>
            <p role="status" className="mt-3 font-medium">
              {practice.choice === item.correct_index ? "Correct." : "The correct answer is " + item.choices[item.correct_index] + "."}
            </p>
            <Evidence citations={item.citations} onSeek={onSeek} />
            <button type="button" className={control + " mt-3"} onClick={() => update({})}>Try again</button>
          </>
        ) : (
          <button type="button" disabled={practice?.choice === undefined} className={control + " mt-3"}
            onClick={() => update({ ...practice, checked: true })}>Check answer</button>
        )}
      </fieldset>
    )
  }
  return (
    <div className="rounded-xl border p-4">
      {item.kind === "notes" && <h5 className="mb-1 font-semibold">{item.title}</h5>}
      <p>{item.text}</p>
      <Evidence citations={item.citations} onSeek={onSeek} />
    </div>
  )
}

export function StudyWorkspace({ videoId, onSeek }: { videoId: string; onSeek: (ms: number) => void }) {
  const [kind, setKind] = useState<StudyKind>("chapters")
  const [index, setIndex] = useState<StudyIndex | null>(null)
  const [history, setHistory] = useState<{ kind: StudyKind; versions: StudyVersionSummary[]; next: number | null } | null>(null)
  const [picked, setPicked] = useState<Partial<Record<StudyKind, string>>>({})
  const [loaded, setLoaded] = useState<StudyVersion | null>(null)
  const [practice, setPractice] = useState<Record<string, Practice>>({})
  const [refresh, setRefresh] = useState(0)
  const [busy, setBusy] = useState(false)
  const [moreBusy, setMoreBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const latest = index?.latest.find((item) => item.kind === kind)
  const latestChapters = index?.latest.find((item) => item.kind === "chapters")
  const selectedId = picked[kind] || latest?.artifact_id
  const version = loaded?.artifact_id === selectedId ? loaded : null
  const run = index?.runs.find((item) => item.kind === kind)
  const running = run !== undefined && active(run)
  const currentHistory = history?.kind === kind ? history : null
  const chapterNumber = version?.document.chapter_version

  useEffect(() => {
    const abort = new AbortController()
    void listStudy(videoId, kind, abort.signal).then((data) => {
      if (abort.signal.aborted) return
      setIndex(data)
      setHistory({ kind, versions: data.versions ?? [], next: data.next_before_version ?? null })
    }).catch((cause: unknown) => {
      if (!abort.signal.aborted) setError(errorText(cause))
    })
    return () => abort.abort()
  }, [videoId, kind, refresh])

  useEffect(() => {
    const abort = new AbortController()
    if (selectedId) {
      void readStudyVersion(videoId, selectedId, abort.signal).then((data) => {
        if (!abort.signal.aborted) setLoaded(data)
      }).catch((cause: unknown) => {
        if (!abort.signal.aborted) setError(errorText(cause))
      })
    }
    return () => abort.abort()
  }, [videoId, selectedId, refresh])

  useEffect(() => {
    const runs = index?.runs.filter(active)
    if (!runs?.length) return
    const abort = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function poll(pending: StudyRun[]) {
      try {
        const updates = await Promise.all(pending.map((item) => readStudyRun(videoId, item.run_id, abort.signal)))
        if (abort.signal.aborted) return
        setIndex((value) => value === null ? value : withRuns(value, updates))
        if (updates.some((item) => item.state === "succeeded")) setRefresh((value) => value + 1)
      } catch (cause) {
        if (abort.signal.aborted) return
        setError(errorText(cause))
        timer = setTimeout(() => void poll(pending), 2500)
      }
    }
    timer = setTimeout(() => void poll(runs), 1500)
    return () => { abort.abort(); clearTimeout(timer) }
  }, [videoId, index?.runs])

  async function generate() {
    setBusy(true)
    setError(null)
    try {
      const chapter = kind === "chapters" ? null : latestChapters?.artifact_id ?? null
      const result = await startStudy(videoId, kind, crypto.randomUUID(), chapter, version?.artifact_id ?? latest?.artifact_id)
      setIndex((value) => value === null ? value : withRuns(value, [result]))
    } catch (cause) {
      setError(errorText(cause))
      setRefresh((value) => value + 1)
    } finally {
      setBusy(false)
    }
  }

  async function older() {
    if (currentHistory?.next == null) return
    setMoreBusy(true)
    try {
      const data = await listStudy(videoId, kind, new AbortController().signal, currentHistory.next)
      setHistory((value) => value?.kind === kind ? {
        kind,
        versions: [...value.versions, ...(data.versions ?? [])],
        next: data.next_before_version ?? null,
      } : value)
    } catch (cause) {
      setError(errorText(cause))
    } finally {
      setMoreBusy(false)
    }
  }

  return (
    <section aria-labelledby="study-title" className="space-y-4 rounded-2xl border bg-card p-5">
      <div>
        <h3 id="study-title" className="font-heading text-xl font-semibold">Study</h3>
        <p className="mt-1 text-sm text-muted-foreground">Build study materials from this video. Select a timestamp to check the source.</p>
      </div>
      <div role="group" aria-label="Study material" className="flex flex-wrap gap-2">
        {kinds.map((item) => (
          <button type="button" key={item} className={control + " aria-pressed:border-primary aria-pressed:bg-primary/10"}
            aria-pressed={kind === item} onClick={() => { setKind(item); setError(null) }}>
            {LABELS[item]}
          </button>
        ))}
      </div>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {index === null && !error && <p role="status" className="text-sm text-muted-foreground">Loading study materials…</p>}
      {index === null && error && <button type="button" className={control} onClick={() => { setError(null); setRefresh((value) => value + 1) }}>Reload Study</button>}
      {index !== null && kind !== "chapters" && !latestChapters && (
        <p className="text-sm text-muted-foreground">Generate chapters first to organize the evidence for this material.</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {index !== null && (
          <button type="button" className={control} disabled={busy || running || (kind !== "chapters" && !latestChapters)}
            onClick={() => void generate()}>
            {latest ? "Regenerate " : "Generate "}{LABELS[kind].toLowerCase()}
          </button>
        )}
        {latest && (
          <>
            <label className="flex items-center gap-2 text-sm">
              Version
              <select aria-label="Saved version" className="rounded-lg border bg-background px-2 py-2"
                value={picked[kind] ?? ""} onChange={(event) => setPicked({ ...picked, [kind]: event.target.value })}>
                <option value="">Latest (v{latest.version})</option>
                {currentHistory?.versions.map((item) => (
                  <option value={item.artifact_id} key={item.artifact_id}>v{item.version}</option>
                ))}
                {picked[kind] && !currentHistory?.versions.some((item) => item.artifact_id === picked[kind]) && (
                  <option value={picked[kind]}>{version ? "v" + version.version : "Selected version"}</option>
                )}
              </select>
            </label>
            {currentHistory?.next != null && <button type="button" disabled={moreBusy} className={control} onClick={() => void older()}>Older versions</button>}
          </>
        )}
        {version && (
          <>
            <a className={control} href={studyExportUrl(videoId, version.artifact_id, "markdown")} download>Markdown</a>
            <a className={control} href={studyExportUrl(videoId, version.artifact_id, "json")} download>JSON</a>
          </>
        )}
      </div>
      {run && <p role="status" className="text-sm text-muted-foreground">
        {run.state === "queued" && "Queued for the local worker."}
        {run.state === "running" && (run.total_chapters
          ? "Generating: " + run.completed_chapters + " of " + run.total_chapters + " chapters."
          : "Reading the video timeline…")}
        {run.state === "succeeded" && "Generation saved."}
        {run.state === "failed" && (run.failure
          ? run.failure.message + " " + run.failure.suggested_action
          : "Generation failed.")}
      </p>}
      {running && run.warnings.length > 0 && <details className="text-sm text-muted-foreground">
        <summary>Run warnings</summary><ul className="list-inside list-disc">{run.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
      </details>}
      {selectedId && !version && !error && <p role="status" className="text-sm text-muted-foreground">Loading saved version…</p>}
      {version && (
        <div className="space-y-4">
          {version.chapter_version_id && <p className="text-sm text-muted-foreground">
            Based on chapters {chapterNumber ? "v" + chapterNumber : "saved before the current chapter version"}.
          </p>}
          {version.document.warnings?.length ? (
            <details className="rounded-lg bg-muted/60 px-3 py-2 text-sm">
              <summary>Evidence coverage and warnings</summary>
              <ul className="mt-2 list-inside list-disc">{version.document.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
            </details>
          ) : null}
          {version.document.chapters?.map((chapter) => (
            <article className="rounded-xl border p-4" key={chapter.number}>
              <h4><button type="button" className={seekStyle} onClick={() => onSeek(chapter.start_ms)}>
                {timestamp(chapter.start_ms)}–{timestamp(chapter.end_ms)} · {chapter.title}
              </button></h4>
              <Evidence citations={chapter.citations} onSeek={onSeek} />
            </article>
          ))}
          {version.document.sections?.map((section) => (
            <article className="space-y-3" key={section.chapter_number}>
              <h4><button type="button" className={seekStyle} onClick={() => onSeek(section.start_ms)}>
                {timestamp(section.start_ms)}–{timestamp(section.end_ms)} · {section.title}
              </button></h4>
              {section.items.length === 0 && <p className="text-sm text-muted-foreground">No supported items in this chapter.</p>}
              {section.items.map((item, i) => {
                const key = version.artifact_id + ":" + section.chapter_number + ":" + i
                return <StudyMaterial key={key} item={item} practice={practice[key]} onSeek={onSeek}
                  update={(value) => setPractice((previous) => ({ ...previous, [key]: value }))} />
              })}
            </article>
          ))}
        </div>
      )}
    </section>
  )
}
