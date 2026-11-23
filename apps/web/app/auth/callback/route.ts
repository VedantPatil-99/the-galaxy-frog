import { workspaceAuthClient, workspaceAuthConfig } from "@/lib/workspace-auth"

export async function GET(request: Request) {
  const config = workspaceAuthConfig()
  const headers = { "Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer" }
  if (!config) return new Response("Browser sign-in is not configured.", { status: 503, headers })
  const code = new URL(request.url).searchParams.get("code")
  if (code && code.length <= 512) {
    const supabase = await workspaceAuthClient(config, { kind: "callback", nextScope: crypto.randomUUID() })
    const { error } = await supabase.auth.exchangeCodeForSession(code)
    if (!error) {
      supabase.activate()
      return new Response(null, { status: 303, headers: { ...headers, Location: `${config.origin}/` } })
    }
  }
  return new Response(null, {
    status: 303,
    headers: { ...headers, Location: `${config.origin}/?sign-in=link-expired` },
  })
}
