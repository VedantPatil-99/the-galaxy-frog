"use client"

import { useEffect, useState, type FormEvent } from "react"

import {
  getProviderCatalog, getProviderHealth, getProviderProfiles, saveProviderProfile,
  type ProviderEntry, type ProviderHealth, type ProviderPolicy, type ProviderProfile,
} from "@/lib/api/providers"

const locations: Record<ProviderEntry["location"], string> = {
  local: "Local",
  free_cloud: "Free cloud",
  aws: "AWS",
}

const modes: Record<ProviderPolicy["mode"], string> = {
  automatic_local_only: "Automatic · local only",
  automatic_cloud_permitted: "Automatic · cloud permitted",
  manual: "Manual",
}

const newPolicy: ProviderPolicy = { mode: "automatic_local_only", candidates: {}, evaluation: false }

function loadSettings() {
  return Promise.allSettled([getProviderCatalog(), getProviderHealth(), getProviderProfiles()])
}

export function ProviderSettings() {
  const [catalog, setCatalog] = useState<ProviderEntry[]>([])
  const [health, setHealth] = useState<ProviderHealth[]>([])
  const [profiles, setProfiles] = useState<ProviderProfile[]>([])
  const [selected, setSelected] = useState("")
  const [profileId, setProfileId] = useState("")
  const [policy, setPolicy] = useState<ProviderPolicy>(newPolicy)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  function applySettings([entries, states, versions]: Awaited<ReturnType<typeof loadSettings>>) {
    if (entries.status === "fulfilled") setCatalog(entries.value)
    if (states.status === "fulfilled") setHealth(states.value)
    if (versions.status === "fulfilled") setProfiles(versions.value)
    if ([entries, states, versions].some(result => result.status === "rejected")) {
      setStatus("Some provider metadata is unavailable. Check the API and PostgreSQL, then refresh.")
    }
    return versions.status === "fulfilled" ? versions.value : []
  }

  useEffect(() => {
    let current = true
    void loadSettings().then(snapshot => {
      if (current) applySettings(snapshot)
    })
    return () => { current = false }
  }, [])

  function choose(id: string, versions = profiles) {
    const current = versions.find(profile => profile.profile_id === id)
    setSelected(id)
    setProfileId(id)
    setPolicy(current?.policy ?? newPolicy)
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setStatus(null)
    try {
      const current = profiles.find(profile => profile.profile_id === selected)
      const saved = await saveProviderProfile(profileId, current?.version ?? 0, policy)
      const versions = applySettings(await loadSettings())
      choose(saved.profile_id, versions)
      setStatus(`Saved ${saved.profile_id} v${saved.version}.`)
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not save the profile.")
    } finally {
      setBusy(false)
    }
  }

  function setChain(capability: ProviderEntry["capability"], sequence: string[]) {
    const candidates = { ...policy.candidates }
    if (sequence.length > 0) candidates[capability] = sequence
    else delete candidates[capability]
    setPolicy({ ...policy, candidates })
  }

  const capabilities = [...new Set(catalog.map(entry => entry.capability))]
  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <h2 className="font-heading text-xl font-semibold">Model catalog</h2>
          <button type="button" className="rounded-lg border px-3 py-2 text-sm" onClick={() => { void loadSettings().then(applySettings) }}>Refresh</button>
        </div>
        {Object.entries(locations).map(([location, label]) => (
          <div key={location} className="space-y-3">
            <h3 className="text-sm font-semibold">{label}</h3>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {catalog.filter(entry => entry.location === location).map(entry => {
                const observed = health.find(item => item.provider_id === entry.provider_id)
                return (
                  <article key={entry.provider_id} className="space-y-2 rounded-xl border bg-card p-4 text-sm">
                    <h4 className="font-semibold">{entry.model}</h4>
                    <p className="text-xs text-muted-foreground">{entry.capability.replaceAll("_", " ")} · {entry.readiness.replaceAll("_", " ")} · health {observed?.state.toLowerCase().replaceAll("_", " ") ?? "unknown"}</p>
                    {observed?.blocked_until ? <p className="text-xs text-muted-foreground">Retry after {new Date(observed.blocked_until).toLocaleString()}</p> : null}
                    <p>{entry.summary}</p>
                    <p className="text-xs text-muted-foreground">{entry.privacy}</p>
                    <dl className="grid gap-1 text-xs">
                      {[
                        ["Revision", entry.revision], ["Hardware", entry.hardware],
                        ["Languages", entry.language_coverage], ["Code-switching", entry.code_switching],
                        ["Timestamps", entry.timestamp_granularity], ["Confidence", entry.confidence],
                        ["Diarization", entry.diarization == null ? null : entry.diarization ? "Supported" : "Unavailable"],
                        ["Observed speed", entry.measured_speed], ["Local evidence", entry.local_execution_evidence],
                      ].filter(([, value]) => value).map(([name, value]) => (
                        <div key={name} className="flex flex-wrap gap-x-2"><dt className="font-medium">{name}</dt><dd className="break-all text-muted-foreground">{value}</dd></div>
                      ))}
                    </dl>
                    {entry.pricing ? (
                      <p className="text-xs text-muted-foreground">
                        <a href={entry.pricing.source_url} target="_blank" rel="noreferrer" className="underline">{entry.pricing.stale ? "Stale pricing" : "Provider pricing"}</a>
                        {" · "}{entry.pricing.billing_unit}{entry.pricing.currency ? ` · ${entry.pricing.currency}` : ""} · verified {entry.pricing.verified_at}. {entry.pricing.tier_note}
                      </p>
                    ) : null}
                    {entry.capability_url ? <a href={entry.capability_url} target="_blank" rel="noreferrer" className="text-xs text-primary underline">Provider documentation</a> : null}
                  </article>
                )
              })}
            </div>
          </div>
        ))}
      </section>
      <form onSubmit={save} className="space-y-4 rounded-xl border bg-card p-5">
        <h2 className="font-heading text-xl font-semibold">Profiles</h2>
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="grid gap-1">Edit profile
            <select value={selected} onChange={event => choose(event.target.value)} className="rounded border bg-background px-2 py-1.5">
              <option value="">New profile</option>
              {profiles.map(profile => <option key={profile.profile_id} value={profile.profile_id}>{profile.profile_id} · v{profile.version}</option>)}
            </select>
          </label>
          <label className="grid gap-1">Profile ID
            <input required pattern="[a-z][a-z0-9_\-]{0,39}" value={profileId} readOnly={Boolean(selected)}
              onChange={event => setProfileId(event.target.value)} className="rounded border bg-background px-2 py-1.5" />
          </label>
          <label className="grid gap-1">Provider mode
            <select value={policy.mode} onChange={event => setPolicy({ ...policy, mode: event.target.value as ProviderPolicy["mode"] })}
              className="rounded border bg-background px-2 py-1.5">
              {Object.entries(modes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={policy.evaluation ?? false} onChange={event => setPolicy({ ...policy, evaluation: event.target.checked })} />
          Disable fallback for evaluation
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          {capabilities.map(capability => {
            const chain = policy.candidates[capability] ?? []
            return (
              <fieldset key={capability} className="space-y-2 rounded-lg border px-3 pb-3 text-xs">
                <legend className="px-1 font-medium">{capability.replaceAll("_", " ")}</legend>
                {chain.map((id, index) => (
                  <div key={id} className="flex flex-wrap items-center gap-2">
                    <span className="min-w-28">{id}</span>
                    <button type="button" disabled={index === 0} className="rounded border px-2 py-1 disabled:opacity-40"
                      aria-label={`Move ${id} earlier`} onClick={() => {
                        const updated = [...chain]
                        const [provider] = updated.splice(index, 1)
                        updated.splice(index - 1, 0, provider)
                        setChain(capability, updated)
                      }}>Earlier</button>
                    <button type="button" className="rounded border px-2 py-1" aria-label={`Remove ${id}`}
                      onClick={() => setChain(capability, chain.filter(value => value !== id))}>Remove</button>
                    {policy.evaluation ? (
                      <label className="w-full">Pinned revision
                        <input required value={policy.pinned_revisions?.[id] ?? ""}
                          onChange={event => setPolicy({
                            ...policy, pinned_revisions: { ...policy.pinned_revisions, [id]: event.target.value },
                          })}
                          className="mt-1 w-full rounded border bg-background p-1.5" />
                      </label>
                    ) : null}
                  </div>
                ))}
                <select aria-label={`Add ${capability} provider`} value="" className="w-full rounded border bg-background p-1.5"
                  onChange={event => setChain(capability, [...chain, event.target.value])}>
                  <option value="">Add provider</option>
                  {catalog.filter(entry => entry.capability === capability && entry.readiness !== "catalog_only" && !chain.includes(entry.provider_id))
                    .map(entry => <option key={entry.provider_id} value={entry.provider_id}>{entry.model}</option>)}
                </select>
              </fieldset>
            )
          })}
        </div>
        {policy.evaluation ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {(["text_embedding", "visual_embedding"] as const).map(capability => (
              <label key={capability} className="grid gap-1 text-sm">
                {capability.replaceAll("_", " ")} collection ID
                <input required value={policy.embedding_collections?.[capability] ?? ""}
                  onChange={event => setPolicy({
                    ...policy, embedding_collections: { ...policy.embedding_collections, [capability]: event.target.value },
                  })}
                  className="rounded border bg-background p-2" />
              </label>
            ))}
          </div>
        ) : null}
        <button type="submit" disabled={busy} className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60">Save profile version</button>
        {status ? <p role="status" className="text-sm text-muted-foreground">{status}</p> : null}
      </form>
    </div>
  )
}
