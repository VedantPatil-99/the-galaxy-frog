import { createServerClient } from "@supabase/ssr"
import { cookies } from "next/headers"
import { legacyWorkspaceSession, workspaceEndedCookie, workspaceSessionCookie, workspaceSessionScope } from "@/lib/workspace-session-marker"

const activeScopeCookie = "gf-workspace-auth"
const sessionCookieAge = 400 * 24 * 60 * 60

function scopedCookieName(name: string, scope: string) {
  return scope === legacyWorkspaceSession ? name : `gf-auth-${scope}-${name}`
}

export function workspaceAuthConfig() {
  const url = process.env.SUPABASE_URL
  const key = process.env.SUPABASE_PUBLISHABLE_KEY
  const origin = process.env.WORKSPACE_WEB_ORIGIN
  if (!url || !key || !origin) return null

  try {
    const web = new URL(origin)
    const supabase = new URL(url)
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(web.hostname)
    if (web.origin !== origin || (web.protocol !== "https:" && !(loopback && web.protocol === "http:"))) return null
    if (supabase.protocol !== "https:" || supabase.origin !== url.replace(/\/$/, "")) return null
    if (!key.startsWith("sb_publishable_")) return null
    return { url, key, origin, secure: web.protocol === "https:" }
  } catch {
    return null
  }
}

type AuthIntent = { kind: "session" | "email-link" } | { kind: "callback"; nextScope: string }

export async function workspaceAuthClient(
  config: NonNullable<ReturnType<typeof workspaceAuthConfig>>,
  intent: AuthIntent = { kind: "session" },
) {
  const jar = await cookies()
  const active = workspaceSessionScope(jar.get(activeScopeCookie)?.value)
  const scope = intent.kind === "callback" ? intent.nextScope : active
  const sessionKey = `sb-${new URL(config.url).hostname.split(".")[0]}-auth-token`
  const privateCookie = { httpOnly: true, secure: config.secure, sameSite: "lax", path: "/" } as const
  const notificationCookie = { ...privateCookie, httpOnly: false, maxAge: sessionCookieAge }
  const revokedCookie = (id: string) => `gf-workspace-revoked-${id}`
  const isSession = (name: string) => name === sessionKey || name.startsWith(`${sessionKey}.`)
    || name === `${sessionKey}-user` || name.startsWith(`${sessionKey}-user.`)
  const expired = jar.has(revokedCookie(scope))
  const prefix = scopedCookieName("", scope)
  const client = createServerClient(config.url, config.key, {
    cookieOptions: { ...privateCookie, name: sessionKey },
    cookies: {
      getAll: () => jar.getAll().flatMap(cookie => {
        if (prefix && cookie.name.startsWith(prefix) && !expired) {
          return [{ ...cookie, name: cookie.name.slice(prefix.length) }]
        }
        return cookie.name.startsWith("gf-auth-") || ((prefix || expired) && isSession(cookie.name))
          ? []
          : [cookie]
      }),
      setAll: (updates) => {
        for (const { name, value, options } of updates) {
          if (isSession(name)) {
            if (!expired) jar.set(scopedCookieName(name, scope), value, options)
          } else if (intent.kind !== "session") {
            jar.set(name, value, options)
          }
        }
      },
    },
  })

  function retire(id: string) {
    const oldPrefix = scopedCookieName("", id)
    for (const { name } of jar.getAll()) {
      if (oldPrefix ? name.startsWith(oldPrefix) : isSession(name)) {
        jar.set(name, "", { ...privateCookie, maxAge: 0 })
      }
    }
    jar.set(revokedCookie(id), "1", { ...privateCookie, maxAge: sessionCookieAge })
    jar.set(workspaceEndedCookie(id), "1", notificationCookie)
  }

  return {
    auth: client.auth,
    marker: jar.has(revokedCookie(active)) ? `${active}:ended` : active,
    end: () => retire(active),
    activate: () => {
      if (intent.kind !== "callback") return
      // Refreshes use their original cookie namespace. Once the pointer moves,
      // only the legacy fallback and the new scope need to be kept.
      for (const { name } of jar.getAll()) {
        if (
          (name.startsWith("gf-auth-") && !name.startsWith(prefix))
          || name.startsWith("gf-workspace-ended-") || name.startsWith("gf-workspace-revoked-")
        ) jar.set(name, "", { ...privateCookie, maxAge: 0 })
      }
      retire(legacyWorkspaceSession)
      jar.set(activeScopeCookie, scope, { ...privateCookie, maxAge: sessionCookieAge })
      jar.set(workspaceSessionCookie, scope, notificationCookie)
    },
  }
}
