"use client"

import { useEffect, useRef, useState } from "react"

import { PrivateWorkspace } from "@/components/private-workspace"
import { TranscriptWorkspace } from "@/components/transcript-workspace"
import { Button } from "@/components/ui/button"
import { WorkspaceShell, type WorkspacePane } from "@/components/workspace-shell"
import { WorkspaceSignIn } from "@/components/workspace-sign-in"
import { ApiClientError } from "@/lib/api/client"
import { getWorkspaceSession, getWorkspaceStatus } from "@/lib/api/workspace"
import { createWorkspaceSession, type WorkspaceSession } from "@/lib/api/workspace-session"
import { clearWorkspaceLocation } from "@/lib/workspace-draft-recovery"
import { WorkspaceSessionContext } from "@/lib/workspace-session-context"
import { notifyWorkspaceSession, readWorkspaceSessionMarker, workspaceSessionSignal } from "@/lib/workspace-session-marker"

type Mode =
  | { kind: "checking" | "local" }
  | { kind: "signed-out"; ready: boolean; message?: string }
  | { kind: "private"; session: WorkspaceSession }
  | { kind: "error"; message: string }

export function WorkspaceEntry() {
  const [mode, setMode] = useState<Mode>({ kind: "checking" })
  const [attempt, setAttempt] = useState(0)
  const [pane, setPane] = useState<WorkspacePane>("work")
  const [verifying, setVerifying] = useState(false)
  const privateView = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let active: WorkspaceSession | null = null
    let pending: AbortController | null = null
    let marker = readWorkspaceSessionMarker()
    let local = false
    const clear = () => {
      active?.dispose()
      active = null
    }
    async function verify(reopen = false) {
      if (local || (pending && !reopen)) return
      pending?.abort()
      const request = new AbortController()
      pending = request
      marker = readWorkspaceSessionMarker()
      if (reopen) {
        clear()
        clearWorkspaceLocation()
        setPane("work")
        setMode({ kind: "checking" })
      }
      setVerifying(true)
      try {
        const status = await getWorkspaceStatus(request.signal)
        if (request.signal.aborted) return
        if (status.mode === "local" && status.legacy_resources_available) {
          local = true
          clear()
          setMode({ kind: "local" })
          return
        }
        const ready = status.authentication_configured && status.creator_access_configured
        if (!ready) {
          clear()
          setMode({ kind: "signed-out", ready })
          return
        }
        const verified = await getWorkspaceSession(marker, request.signal)
        if (request.signal.aborted || reconcileMarker()) return
        const accountId = verified.account.account_id
        if (active?.accountId !== accountId || active.marker !== marker || active.signal.aborted) {
          if (active && active.accountId !== accountId) clearWorkspaceLocation()
          clear()
          const session = createWorkspaceSession(accountId, marker, () => {
            if (active === session) void verify(true)
          })
          active = session
          setMode({ kind: "private", session })
        }
      } catch (caught) {
        if (request.signal.aborted || reconcileMarker()) return
        if (active && !active.signal.aborted && caught instanceof ApiClientError
          && (caught.status === 0 || caught.status >= 500)) return
        clearWorkspaceLocation()
        clear()
        if (caught instanceof ApiClientError && caught.status === 401) {
          setMode({ kind: "signed-out", ready: true })
        } else if (caught instanceof ApiClientError && caught.response?.error.code === "WORKSPACE_INVITATION_REQUIRED") {
          setMode({ kind: "signed-out", ready: true, message: caught.message })
        } else {
          setMode({ kind: "error", message: caught instanceof Error ? caught.message : "Could not open the workspace." })
        }
      } finally {
        if (pending === request) {
          pending = null
          setVerifying(false)
        }
      }
    }

    function reconcileMarker() {
      if (local || readWorkspaceSessionMarker() === marker) return false
      void verify(true)
      return true
    }
    const onStorage = (event: StorageEvent) => { if (event.key === workspaceSessionSignal) reconcileMarker() }
    const onResume = () => { if (!reconcileMarker()) void verify() }
    const onVisibility = () => { if (document.visibilityState === "visible") onResume() }
    const onPageShow = (event: PageTransitionEvent) => { if (event.persisted) onResume() }
    const onPageHide = (event: PageTransitionEvent) => {
      if (event.persisted && privateView.current) {
        privateView.current.hidden = true
        active?.dispose()
        setVerifying(true)
      }
    }
    const onPopState = () => {
      const owner = window.history.state?.galaxyFrogAccount
      if (!local && (!active?.isCurrent() || owner !== active.accountId)) clearWorkspaceLocation()
    }
    window.addEventListener("storage", onStorage)
    window.addEventListener("focus", onResume)
    window.addEventListener("pageshow", onPageShow)
    window.addEventListener("pagehide", onPageHide)
    window.addEventListener("popstate", onPopState, true)
    document.addEventListener("visibilitychange", onVisibility)
    window.cookieStore?.addEventListener("change", reconcileMarker)
    // Read only the marker when browser storage notifications are unavailable.
    const timer = window.setInterval(reconcileMarker, 500)
    notifyWorkspaceSession()
    void verify()
    return () => {
      window.removeEventListener("storage", onStorage)
      window.removeEventListener("focus", onResume)
      window.removeEventListener("pageshow", onPageShow)
      window.removeEventListener("pagehide", onPageHide)
      window.removeEventListener("popstate", onPopState, true)
      document.removeEventListener("visibilitychange", onVisibility)
      window.cookieStore?.removeEventListener("change", reconcileMarker)
      window.clearInterval(timer)
      pending?.abort()
      pending = null
      clear()
    }
  }, [attempt])

  if (mode.kind === "local") return <TranscriptWorkspace />
  if (mode.kind === "private") {
    const { session } = mode
    return <>
      {verifying && <p role="status" className="p-5 text-sm text-muted-foreground">Checking your workspace sign-in…</p>}
      <div ref={privateView} hidden={verifying}>
        <WorkspaceSessionContext.Provider key={`${session.accountId}:${session.marker}`} value={session}>
          <PrivateWorkspace accountId={session.accountId} onExpired={session.invalidate} />
        </WorkspaceSessionContext.Provider>
      </div>
    </>
  }

  return (
    <WorkspaceShell title="Workspace" pane={pane} onPaneChange={setPane} galaxy={null}>
      {mode.kind === "signed-out" ? <>
        {mode.message && <p role="alert" className="text-sm text-destructive">{mode.message}</p>}
        <WorkspaceSignIn ready={mode.ready} />
      </> : mode.kind === "error" ? (
        <div className="space-y-3 py-5">
          <h1 className="text-2xl font-semibold">Could not open the workspace</h1>
          <p role="alert" className="text-muted-foreground">{mode.message}</p>
          <Button variant="outline" className="min-h-11" onClick={() => { setMode({ kind: "checking" }); setAttempt(attempt + 1) }}>Try again</Button>
        </div>
      ) : <p role="status" className="py-5 text-muted-foreground">Opening workspace…</p>}
    </WorkspaceShell>
  )
}
