import { workspaceAuthClient, workspaceAuthConfig } from "@/lib/workspace-auth"

const headers = { "Cache-Control": "private, no-store" }

function message(text: string, status: number) {
  return Response.json({ message: text }, { status, headers })
}

export async function POST(request: Request) {
  const config = workspaceAuthConfig()
  if (!config) return message("Browser sign-in is not configured. Set the web origin and Supabase settings on the web server.", 503)
  if (request.headers.get("origin") !== config.origin) return message("Open Galaxy Frog at its configured web address and try again.", 403)

  const body: unknown = await request.json().catch(() => null)
  if (body === null || typeof body !== "object" || !("action" in body)) return message("Choose a sign-in action.", 400)

  if (body.action === "sign-out") {
    const supabase = await workspaceAuthClient(config)
    if (!("session" in body) || body.session !== supabase.marker) {
      return message("The workspace sign-in changed. Reopen the workspace and try again.", 409)
    }
    const { error } = await supabase.auth.signOut({ scope: "local" })
    if (error) return message("Sign-out could not finish. Try again.", 503)
    supabase.end()
    return new Response(null, { status: 204, headers })
  }

  if (body.action !== "email-link" || !("email" in body) || typeof body.email !== "string" || body.email.length > 320) {
    return message("Enter a valid email address.", 400)
  }
  const email = body.email.trim()
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return message("Enter a valid email address.", 400)

  const supabase = await workspaceAuthClient(config, { kind: "email-link" })
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: {
      shouldCreateUser: false,
      emailRedirectTo: `${config.origin}/auth/callback`,
    },
  })
  if (error?.status === 429) return message("Too many sign-in requests. Wait a moment before trying again.", 429)
  if (error && error.status && error.status >= 500) return message("Sign-in email is temporarily unavailable. Try again later.", 503)
  return message("If this email has access, check your inbox. Open the link in this browser.", 200)
}
