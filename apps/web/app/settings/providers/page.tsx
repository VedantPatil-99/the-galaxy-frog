import Link from "next/link"

import { ProviderSettings } from "@/components/provider-settings"

export default function Page() {
  return (
    <main className="mx-auto max-w-7xl space-y-6 px-5 py-8 sm:px-8">
      <Link href="/" className="text-sm text-primary underline underline-offset-4">Back to workspace</Link>
      <h1 className="font-heading text-3xl font-semibold tracking-tight">Provider settings</h1>
      <ProviderSettings />
    </main>
  )
}
