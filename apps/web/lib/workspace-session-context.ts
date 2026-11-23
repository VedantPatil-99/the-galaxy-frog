"use client"

import { createContext, useContext } from "react"

import type { WorkspaceSession } from "@/lib/api/workspace-session"

export const WorkspaceSessionContext = createContext<WorkspaceSession | null>(null)

export function useWorkspaceSession() {
  const session = useContext(WorkspaceSessionContext)
  if (!session) throw new Error("Private workspace components need a verified session.")
  return session
}
