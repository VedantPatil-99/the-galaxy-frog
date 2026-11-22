"use client"

import { Collapsible } from "@base-ui/react/collapsible"
import { CaretDownIcon } from "@phosphor-icons/react/dist/csr/CaretDown"
import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"

export function Disclosure({ label, children, details, keepMounted = true, defaultOpen = false }: {
  label: string
  children: ReactNode
  details?: string
  keepMounted?: boolean
  defaultOpen?: boolean
}) {
  return (
    <Collapsible.Root className="border-t" defaultOpen={defaultOpen}>
      <Collapsible.Trigger render={
        <Button variant="ghost" className="group/disclosure h-auto min-h-11 w-full justify-between rounded-md px-2 py-2 text-left whitespace-normal" />
      }>
        <span>{label}</span>
        <span className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
          {details}
          <CaretDownIcon aria-hidden="true" className="transition-transform group-data-[panel-open]/disclosure:rotate-180 motion-reduce:transition-none" />
        </span>
      </Collapsible.Trigger>
      <Collapsible.Panel keepMounted={keepMounted} className="pb-3">
        {children}
      </Collapsible.Panel>
    </Collapsible.Root>
  )
}
