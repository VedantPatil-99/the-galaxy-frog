import { proxyRequest } from "@/lib/api/proxy"
import { workspaceAuthClient, workspaceAuthConfig } from "@/lib/workspace-auth"

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
