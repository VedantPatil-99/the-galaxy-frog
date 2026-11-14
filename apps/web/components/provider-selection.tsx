"use client"

import Link from "next/link"
import { useEffect, useId, useState } from "react"

import {
  getConsentOptions,
  getProviderProfiles,
  grantProviderConsent,
  revokeProviderConsent,
  type ConsentScope,
  type ProviderOperation,
  type ProviderProfile,
  type ProviderSelection,
} from "@/lib/api/providers"

const scopeLabels: Record<ConsentScope["data"], string> = {
  audio: "Send normalized audio to Cloudflare Workers AI.",
  frames: "Send selected frames to LlamaParse.",
  frames_and_text: "Send a selected frame and accompanying text to Gemini.",
  paid_text: "Send selected text to AWS Bedrock and accept possible charges.",
}

export function useProviderSelection(operation: ProviderOperation) {
  const [profiles, setProfiles] = useState<ProviderProfile[]>([])
  const [selected, setSelected] = useState("")
  const [scopes, setScopes] = useState<ConsentScope[]>([])
  const [approved, setApproved] = useState<string[]>([])
  const [grantId, setGrantId] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let current = true
    void getProviderProfiles().then(values => {
      if (current) setProfiles(values)
    }).catch(() => {
      if (current) setMessage("Profiles are unavailable. The default local profile is selected.")
    })
    return () => { current = false }
  }, [])

  async function choose(id: string) {
    setSelected(id)
    setApproved([])
    setScopes([])
    setMessage(null)
    const profile = profiles.find(item => item.profile_id === id)
    if (!profile) return
    setLoading(true)
    try {
      setScopes(await getConsentOptions(profile, operation))
    } catch {
      setMessage("Could not load the sharing options for this profile.")
    } finally {
      setLoading(false)
    }
  }

  async function selection(source: string): Promise<ProviderSelection | undefined> {
    const chosen = profiles.find(item => item.profile_id === selected)
    if (!chosen) return undefined
    const profile = { profile_id: chosen.profile_id, version: chosen.version }
    const permitted = scopes.filter(scope => approved.includes(scope.provider_id))
    setApproved([])
    if (permitted.length === 0) return { profile }
    const grant = await grantProviderConsent(profile, operation, source, permitted)
    setGrantId(grant.consent_id)
    return { profile, consent_id: grant.consent_id }
  }

  async function revoke() {
    if (!grantId) return
    try {
      const receipt = await revokeProviderConsent(grantId)
      setGrantId(null)
      setMessage(receipt.dispatch_count
        ? "Consent revoked for future calls. A provider dispatch already started."
        : "Consent revoked before any provider dispatch.")
    } catch {
      setMessage("Could not revoke consent. Try again.")
    }
  }

  return { profiles, selected, scopes, approved, setApproved, grantId, message, loading, choose, selection, revoke }
}

export function ProviderSelectionFields({ control, busy = false, evidenceOnly = false }: {
  control: ReturnType<typeof useProviderSelection>
  busy?: boolean
  evidenceOnly?: boolean
}) {
  const id = useId()
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <label htmlFor={id} className="font-medium">Provider profile</label>
        <select id={id} value={control.selected} disabled={busy || control.loading}
          onChange={event => { void control.choose(event.target.value) }}
          className="rounded-lg border bg-background px-2 py-1.5">
          <option value="">Default local profile</option>
          {control.profiles.map(profile => (
            <option key={profile.profile_id} value={profile.profile_id}>
              {profile.profile_id.replaceAll("_", " ")} · v{profile.version}
            </option>
          ))}
        </select>
        <Link className="text-primary underline underline-offset-4" href="/settings/providers">Provider settings</Link>
      </div>
      {!evidenceOnly && control.scopes.length > 0 ? (
        <fieldset className="space-y-1.5 rounded-lg border px-3 py-2" disabled={busy}>
          <legend className="px-1 text-xs font-medium">Cloud sharing for this run</legend>
          {control.scopes.map(scope => (
            <label key={scope.provider_id} className="flex items-start gap-2 text-xs">
              <input type="checkbox" className="mt-0.5" checked={control.approved.includes(scope.provider_id)}
                onChange={event => control.setApproved(current => event.target.checked
                  ? [...current, scope.provider_id]
                  : current.filter(id => id !== scope.provider_id))} />
              {scopeLabels[scope.data]}
            </label>
          ))}
        </fieldset>
      ) : null}
      {control.grantId ? (
        <button type="button" className="rounded border px-2 py-1 text-xs" onClick={() => { void control.revoke() }}>
          Revoke cloud consent
        </button>
      ) : null}
      {control.message ? <p role="status" className="text-xs text-muted-foreground">{control.message}</p> : null}
    </div>
  )
}
