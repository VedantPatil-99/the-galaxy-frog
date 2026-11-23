export const workspaceSessionCookie = "gf-workspace-session"
export const workspaceSessionSignal = "galaxy-frog:workspace-session"
export const legacyWorkspaceSession = "legacy"
export const workspaceSessionHeader = "x-workspace-session"
export const workspaceAccountHeader = "x-workspace-account"

export function workspaceSessionScope(value: string | undefined) {
  return value && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value)
    ? value
    : legacyWorkspaceSession
}

export function workspaceEndedCookie(scope: string) {
  return `gf-workspace-ended-${scope}`
}

export function readWorkspaceSessionMarker() {
  const cookies = new Map(document.cookie.split(";").map(cookie => {
    const separator = cookie.indexOf("=")
    return [cookie.slice(0, separator).trim(), cookie.slice(separator + 1)] as const
  }))
  const scope = workspaceSessionScope(cookies.get(workspaceSessionCookie))
  return cookies.has(workspaceEndedCookie(scope)) ? `${scope}:ended` : scope
}

export function notifyWorkspaceSession() {
  try {
    localStorage.setItem(workspaceSessionSignal, readWorkspaceSessionMarker())
  } catch {
    // Tabs can still discover the cookie change when they resume or make a request.
  }
}
