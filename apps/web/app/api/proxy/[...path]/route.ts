import { proxyRequest } from "@/lib/api/proxy"
import { workspaceAuthClient, workspaceAuthConfig } from "@/lib/workspace-auth"
import { workspaceAccountHeader, workspaceSessionHeader } from "@/lib/workspace-session-marker"

export const runtime = "nodejs"

type ProxyContext = {
  params: Promise<{ path: string[] }>
}

async function handle(request: Request, context: ProxyContext): Promise<Response> {
  const { path } = await context.params
  const cookieAuth = path[0] === "v1" && path[1] === "workspace"
    && path[2] !== "status" && !request.headers.has("authorization")
  const config = cookieAuth ? workspaceAuthConfig() : null
  if (!config) return proxyRequest(request, path)
  if (request.method !== "GET" && request.method !== "HEAD" && request.headers.get("origin") !== config.origin) {
    return new Response(null, { status: 403, headers: { "Cache-Control": "private, no-store" } })
  }
  const supabase = await workspaceAuthClient(config)
  const expectedSession = request.headers.get(workspaceSessionHeader)
  const bootstrap = path.length === 3 && path[2] === "session"
  if (expectedSession !== supabase.marker || (!bootstrap && !request.headers.has(workspaceAccountHeader))) {
    return Response.json({ error: {
      code: "WORKSPACE_SESSION_CHANGED",
      message: "The workspace sign-in changed.",
      correlation_id: crypto.randomUUID(),
      retryable: false,
    } }, { status: 409, headers: { "Cache-Control": "private, no-store" } })
  }
  // Forward the token as transport only. FastAPI verifies the session and ownership.
  const { data } = await supabase.auth.getSession()
  return proxyRequest(request, path, undefined, undefined, data.session?.access_token)
}

export {
  handle as DELETE,
  handle as GET,
  handle as HEAD,
  handle as OPTIONS,
  handle as PATCH,
  handle as POST,
  handle as PUT,
}
