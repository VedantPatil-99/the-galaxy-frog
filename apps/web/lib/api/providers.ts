import { requestApi } from "@/lib/api/client"
import type { components } from "@/lib/api/generated/schema"

type Schemas = components["schemas"]
export type ProviderEntry = Schemas["ProviderEntry"]
export type ProviderHealth = Schemas["ProviderHealth"]
export type ProviderProfile = Schemas["ProviderProfileVersion"]
export type ProviderPolicy = Schemas["ProviderProfile"]
export type ProviderSelection = Schemas["ProviderSelection"]
export type ProviderOperation = Schemas["ProviderOperation"]
export type ConsentScope = Schemas["ConsentScope"]
export type Consent = Schemas["ProviderConsent"]
export type ProviderAttempt = Schemas["ProviderRunAttempt"]

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function list<T>(key: string) {
  return (value: unknown): value is T[] =>
    Array.isArray(value) && value.every(item => record(item) && typeof item[key] === "string")
}

export function getProviderCatalog() {
  return requestApi("/v1/providers/capabilities", list<ProviderEntry>("provider_id"), fetch)
}

export function getProviderHealth() {
  return requestApi("/v1/providers/health", list<ProviderHealth>("provider_id"), fetch)
}

export function getProviderProfiles() {
  return requestApi("/v1/provider-profiles", list<ProviderProfile>("profile_id"), fetch)
}

export function saveProviderProfile(id: string, expectedVersion: number, policy: ProviderPolicy) {
  return requestApi(
    `/v1/provider-profiles/${encodeURIComponent(id)}`,
    (value): value is ProviderProfile => record(value) && typeof value.version === "number",
    fetch,
    { method: "PUT", body: JSON.stringify({ expected_version: expectedVersion, policy }) },
  )
}

export async function getConsentOptions(profile: ProviderSelection["profile"], operation: ProviderOperation) {
  const query = new URLSearchParams({
    profile_id: profile.profile_id,
    version: String(profile.version),
    operation,
  })
  const response = await requestApi(
    `/v1/provider-consents/options?${query}`,
    (value): value is Schemas["ProviderConsentOptions"] => record(value) && Array.isArray(value.scopes),
    fetch,
  )
  return response.scopes
}

export function grantProviderConsent(
  profile: ProviderSelection["profile"],
  operation: ProviderOperation,
  source: string,
  scopes: ConsentScope[],
) {
  return requestApi(
    "/v1/provider-consents",
    (value): value is Consent => record(value) && typeof value.consent_id === "string",
    fetch,
    { method: "POST", body: JSON.stringify({ profile, operation, source, scopes }) },
  )
}

export function revokeProviderConsent(consentId: string) {
  return requestApi(
    `/v1/provider-consents/${encodeURIComponent(consentId)}`,
    (value): value is Consent => record(value) && typeof value.dispatch_count === "number",
    fetch,
    { method: "DELETE" },
  )
}

export function getProviderRun(operation: ProviderOperation, runId: string) {
  return requestApi(
    `/v1/provider-runs/${operation}/${encodeURIComponent(runId)}`,
    list<ProviderAttempt>("provider_id"),
    fetch,
  )
}
