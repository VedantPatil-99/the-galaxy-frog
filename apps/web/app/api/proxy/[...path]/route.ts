import { proxyRequest } from "@/lib/api/proxy"

export const runtime = "nodejs"

type ProxyContext = {
  params: Promise<{ path: string[] }>
}

async function handle(request: Request, context: ProxyContext): Promise<Response> {
  const { path } = await context.params
  return proxyRequest(request, path)
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
