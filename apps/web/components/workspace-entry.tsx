"use client"

import { useCallback, useEffect, useState } from "react"

import { PrivateWorkspace } from "@/components/private-workspace"
import { TranscriptWorkspace } from "@/components/transcript-workspace"
import { Button } from "@/components/ui/button"
import { WorkspaceShell, type WorkspacePane } from "@/components/workspace-shell"
import { WorkspaceSignIn } from "@/components/workspace-sign-in"
import { ApiClientError } from "@/lib/api/client"
import { getWorkspaceSession, getWorkspaceStatus } from "@/lib/api/workspace"

type Mode =
  | { kind: "checking" | "local" }
  | { kind: "signed-out"; ready: boolean }
  | { kind: "private"; accountId: string }
  | { kind: "error"; message: string }

export function WorkspaceEntry() {
  const [mode, setMode] = useState<Mode>({ kind: "checking" })
  const [attempt, setAttempt] = useState(0)
  const [pane, setPane] = useState<WorkspacePane>("work")
  const onExpired = useCallback(() => {
    setPane("work")
    setMode({ kind: "signed-out", ready: true })
  }, [])

  useEffect(() => {
    let stopped = false
    async function load() {
      try {
        const status = await getWorkspaceStatus()
        if (stopped) return
        if (status.mode === "local" && status.legacy_resources_available) {
          setMode({ kind: "local" })
          return
        }
        const ready = status.authentication_configured && status.creator_access_configured
        if (!ready) {
          setMode({ kind: "signed-out", ready })
          return
        }
        try {
          const session = await getWorkspaceSession()
          if (!stopped) setMode({ kind: "private", accountId: session.account.account_id })
        } catch (caught) {
          if (stopped) return
          if (caught instanceof ApiClientError && caught.status === 401) setMode({ kind: "signed-out", ready })
          else throw caught
        }
      } catch (caught) {
        if (!stopped) setMode({ kind: "error", message: caught instanceof Error ? caught.message : "Could not open the workspace." })
      }
    }
    void load()
    return () => { stopped = true }
  }, [attempt])

  if (mode.kind === "local") return <TranscriptWorkspace />
  if (mode.kind === "private") return <PrivateWorkspace key={mode.accountId} accountId={mode.accountId} onExpired={onExpired} />

  return (
    <WorkspaceShell title="Workspace" pane={pane} onPaneChange={setPane} galaxy={null}>
      {mode.kind === "signed-out" ? <WorkspaceSignIn ready={mode.ready} /> : mode.kind === "error" ? (
        <div className="space-y-3 py-5">
          <h1 className="text-2xl font-semibold">Could not open the workspace</h1>
          <p role="alert" className="text-muted-foreground">{mode.message}</p>
          <Button variant="outline" className="min-h-11" onClick={() => { setMode({ kind: "checking" }); setAttempt(attempt + 1) }}>Try again</Button>
        </div>
      ) : <p role="status" className="py-5 text-muted-foreground">Opening workspace…</p>}
    </WorkspaceShell>
  )
}
