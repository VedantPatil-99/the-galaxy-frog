import { createServerClient } from "@supabase/ssr"
import { cookies } from "next/headers"

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

export async function workspaceAuthClient(config: NonNullable<ReturnType<typeof workspaceAuthConfig>>) {
  const jar = await cookies()
  return createServerClient(config.url, config.key, {
    cookieOptions: { httpOnly: true, secure: config.secure, sameSite: "lax", path: "/" },
    cookies: {
      getAll: () => jar.getAll(),
      setAll: (updates) => {
        for (const { name, value, options } of updates) jar.set(name, value, options)
      },
    },
  })
}
