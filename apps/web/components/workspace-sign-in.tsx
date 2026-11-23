"use client"

import { useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { ApiClientError } from "@/lib/api/client"
import { notifyWorkspaceSession } from "@/lib/workspace-session-marker"

export async function workspaceAuthAction(action: "email-link" | "sign-out", fields: { email?: string; session?: string }) {
  const response = await fetch("/api/auth", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...fields }),
  })
  if (response.status === 204) {
    if (action === "sign-out") notifyWorkspaceSession()
    return ""
  }
  const body: unknown = await response.json().catch(() => null)
  const message = body && typeof body === "object" && "message" in body && typeof body.message === "string"
    ? body.message : "Sign-in could not be completed. Try again."
  if (!response.ok) throw new ApiClientError(message, response.status, null)
  return message
}

export function WorkspaceSignIn({ ready }: { ready: boolean }) {
  const [email, setEmail] = useState("")
  const [message, setMessage] = useState("")
  const [error, setError] = useState(() => typeof window !== "undefined" && new URLSearchParams(window.location.search).get("sign-in") === "link-expired"
    ? "The sign-in link expired or was opened in another browser. Request a fresh link here." : "")
  const [busy, setBusy] = useState(false)

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError("")
    setMessage("")
    try {
      setMessage(await workspaceAuthAction("email-link", { email }))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not send the sign-in link.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="max-w-md space-y-5 py-5 sm:py-10">
      <h1 className="text-3xl font-semibold tracking-tight">Sign in to your workspace</h1>
      {ready ? (
        <form onSubmit={signIn} className="space-y-3">
          <label className="block text-sm font-medium" htmlFor="workspace-email">Email address</label>
          <input id="workspace-email" type="email" autoComplete="email" required maxLength={320} value={email} onChange={event => setEmail(event.target.value)} className="min-h-11 w-full rounded-md border border-input bg-card px-3 focus-visible:outline-2 focus-visible:outline-ring" />
          <Button type="submit" disabled={busy} className="min-h-11">{busy ? "Sending…" : "Email me a sign-in link"}</Button>
        </form>
      ) : <p className="text-muted-foreground">The workspace administrator needs to finish configuring sign-in.</p>}
      {message ? <p role="status" className="text-sm text-primary">{message}</p> : null}
      {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    </div>
  )
}
