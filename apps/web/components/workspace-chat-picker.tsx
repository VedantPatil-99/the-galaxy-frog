"use client"

import { Select } from "@base-ui/react/select"
import { CaretDownIcon } from "@phosphor-icons/react/dist/csr/CaretDown"
import { PencilSimpleIcon } from "@phosphor-icons/react/dist/csr/PencilSimple"
import { PlusIcon } from "@phosphor-icons/react/dist/csr/Plus"
import { useEffect, useId, useRef, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { ApiClientError } from "@/lib/api/client"
import type { WorkspaceChat, WorkspaceChatSummary } from "@/lib/api/workspace"
import { useWorkspaceSession } from "@/lib/workspace-session-context"
import {
  readComposerLocation, readDraftRecovery, rememberComposerLocation, setWorkspaceLocation, workspaceDraftKey,
} from "@/lib/workspace-draft-recovery"

const quickQuestion = "question"

function updateComposerUrl(accountId: string, notebookId: string, chatId: string, method: "pushState" | "replaceState") {
  const url = new URL(window.location.href)
  if (url.searchParams.get("notebook") === notebookId && url.searchParams.get("chat") === chatId) {
    if (window.history.state?.galaxyFrogAccount !== accountId) setWorkspaceLocation(accountId, url, "replaceState")
    return
  }
  url.searchParams.set("notebook", notebookId)
  url.searchParams.set("chat", chatId)
  setWorkspaceLocation(accountId, url, method)
}

function withRecovery(chat: WorkspaceChatSummary, accountId: string, notebookId: string) {
  const local = readDraftRecovery(workspaceDraftKey(accountId, notebookId, chat.chat_id))
  return local ? { ...chat, has_draft: Boolean(local.text.trim()) } : chat
}

function ChatLabel({ title, hasDraft }: { title: string; hasDraft: boolean }) {
  return <span className="flex min-w-0 items-center gap-2">
    {hasDraft ? <>
      <PencilSimpleIcon data-chat-draft="" aria-hidden="true" className="size-4 shrink-0 text-primary" />
      <span className="sr-only">Draft</span>
    </> : null}
    <span className="truncate">{title}</span>
  </span>
}

export function WorkspaceChatPicker({ accountId, notebookId, draftPresence, onSelect, onExpired }: {
  accountId: string
  notebookId: string
  draftPresence: Readonly<Record<string, boolean>>
  onSelect: (chat: WorkspaceChat | null) => void
  onExpired: () => void
}) {
  const session = useWorkspaceSession()
  const { createWorkspaceChat, getWorkspaceChat, listWorkspaceChats, renameWorkspaceChat } = session.api
  const field = useId()
  const portalParent = useRef<HTMLElement | null>(null)
  const request = useRef<AbortController | null>(null)
  const newChat = useRef<string | null>(null)
  const [items, setItems] = useState<WorkspaceChatSummary[]>([])
  const [quickDraft, setQuickDraft] = useState(false)
  const [after, setAfter] = useState<string | null>(null)
  const [active, setActive] = useState("")
  const [busy, setBusy] = useState(true)
  const [attempt, setAttempt] = useState(0)
  const [name, setName] = useState<string | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    const controller = new AbortController()
    request.current = controller
    async function load() {
      try {
        const data = await listWorkspaceChats(notebookId, null, controller.signal)
        if (controller.signal.aborted || !session.isCurrent()) return
        const route = new URLSearchParams(window.location.search)
        const remembered = readComposerLocation(accountId)
        const requested = (route.get("notebook") === notebookId ? route.get("chat") : null)
          ?? (remembered.get("notebook") === notebookId ? remembered.get("chat") : null)
        const first = data.chats[0]?.chat_id
        let chat: WorkspaceChat | null = null
        if (requested && requested !== quickQuestion) {
          try {
            chat = await getWorkspaceChat(notebookId, requested, controller.signal)
          } catch (caught) {
            if (!(caught instanceof ApiClientError) || ![404, 422].includes(caught.status)) throw caught
          }
        }
        if (!chat && first && requested !== quickQuestion) {
          chat = await getWorkspaceChat(notebookId, first, controller.signal)
        }
        if (controller.signal.aborted || !session.isCurrent()) return
        const all = chat && !data.chats.some(item => item.chat_id === chat.chat_id)
          ? [chat, ...data.chats] : data.chats
        setItems(all.map(item => withRecovery(item, accountId, notebookId)))
        const localQuick = readDraftRecovery(workspaceDraftKey(accountId, notebookId))
        setQuickDraft(Boolean(localQuick?.text.trim()))
        setAfter(data.next_after)
        setActive(chat?.chat_id ?? "")
        setName(null)
        updateComposerUrl(accountId, notebookId, chat?.chat_id ?? quickQuestion, "replaceState")
        rememberComposerLocation(accountId, notebookId, chat?.chat_id ?? quickQuestion)
        onSelect(chat)
      } catch (caught) {
        if (controller.signal.aborted) return
        if (caught instanceof ApiClientError && caught.status === 401) onExpired()
        else {
          setError(caught instanceof Error ? caught.message : "Chats could not load.")
          onSelect(null)
        }
      } finally {
        if (!controller.signal.aborted) {
          request.current = null
          setBusy(false)
        }
      }
    }
    void load()
    return () => request.current?.abort()
  }, [accountId, attempt, getWorkspaceChat, listWorkspaceChats, notebookId, onExpired, onSelect, session])

  useEffect(() => {
    function restoreChat() {
      if (new URLSearchParams(window.location.search).get("notebook") !== notebookId) return
      request.current?.abort()
      setBusy(true)
      setError("")
      setAttempt(current => current + 1)
    }
    window.addEventListener("popstate", restoreChat)
    return () => window.removeEventListener("popstate", restoreChat)
  }, [notebookId])

  async function act(action: (signal: AbortSignal) => Promise<void>) {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    setError("")
    try {
      await action(controller.signal)
    } catch (caught) {
      if (controller.signal.aborted) return
      if (caught instanceof ApiClientError && caught.status === 401) onExpired()
      else setError(caught instanceof Error ? caught.message : "The chat could not be updated.")
    } finally {
      if (!controller.signal.aborted) {
        request.current = null
        setBusy(false)
      }
    }
  }

  function select(chat: WorkspaceChat | null) {
    if (!session.isCurrent()) return
    updateComposerUrl(accountId, notebookId, chat?.chat_id ?? quickQuestion, "pushState")
    rememberComposerLocation(accountId, notebookId, chat?.chat_id ?? quickQuestion)
    setActive(chat?.chat_id ?? "")
    setName(null)
    onSelect(chat)
  }

  function create() {
    void act(async signal => {
      const chatId = newChat.current ?? crypto.randomUUID()
      newChat.current = chatId
      const chat = await createWorkspaceChat(notebookId, { chat_id: chatId, title: "New chat" }, signal)
      if (signal.aborted) return
      newChat.current = null
      setItems(current => [chat, ...current.filter(item => item.chat_id !== chat.chat_id)])
      select(chat)
    })
  }

  function rename(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const title = name?.trim()
    if (!title || !active) return
    void act(async signal => {
      const chat = await renameWorkspaceChat(notebookId, active, title, signal)
      if (signal.aborted) return
      const updated = withRecovery(chat, accountId, notebookId)
      setItems(current => current.map(item => item.chat_id === chat.chat_id ? updated : item))
      setName(null)
    })
  }

  const activeChat = items.find(chat => chat.chat_id === active)
  const hasQuickDraft = draftPresence[""] ?? quickDraft
  const activeHasDraft = activeChat ? draftPresence[activeChat.chat_id] ?? activeChat.has_draft : hasQuickDraft
  const rowClass = "flex min-h-11 cursor-default items-center rounded-md px-2 text-sm outline-none data-[highlighted]:bg-accent"

  return (
    <section ref={portalParent} className="space-y-2 border-b pb-4" aria-labelledby={`${field}-heading`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`${field}-heading`} className="font-semibold">Chats</h2>
        <Button type="button" variant="secondary" className="min-h-11" disabled={busy} onClick={create}>
          <PlusIcon aria-hidden="true" /> New chat
        </Button>
      </div>
      {items.length ? <div className="flex flex-wrap items-center gap-2">
        <Select.Root<string> value={activeChat?.chat_id ?? quickQuestion} disabled={busy} onValueChange={id => {
          if (id === quickQuestion) select(null)
          else if (id) void act(async signal => {
            const chat = await getWorkspaceChat(notebookId, id, signal)
            if (!signal.aborted) select(chat)
          })
        }}>
          <Select.Trigger aria-label="Open chat" className="flex min-h-11 min-w-32 max-w-full flex-1 items-center justify-between gap-2 rounded-md border border-input bg-card px-3 text-sm focus-visible:outline-2 focus-visible:outline-ring">
            <Select.Value className="min-w-0"><ChatLabel title={activeChat?.title ?? "Quick question"} hasDraft={activeHasDraft} /></Select.Value>
            <Select.Icon><CaretDownIcon aria-hidden="true" className="size-4" /></Select.Icon>
          </Select.Trigger>
          <Select.Portal container={portalParent}>
            <Select.Positioner sideOffset={5} align="start" alignItemWithTrigger={false} className="z-50 w-[var(--anchor-width)] max-w-[var(--available-width)]">
              <Select.Popup className="max-h-[var(--available-height)] overflow-auto rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md">
                <Select.Item value={quickQuestion} label="Quick question" className={rowClass}>
                  <Select.ItemText className="min-w-0"><ChatLabel title="Quick question" hasDraft={hasQuickDraft} /></Select.ItemText>
                </Select.Item>
                {items.map(chat => (
                  <Select.Item key={chat.chat_id} value={chat.chat_id} label={chat.title} className={rowClass}>
                    <Select.ItemText className="min-w-0"><ChatLabel title={chat.title} hasDraft={draftPresence[chat.chat_id] ?? chat.has_draft} /></Select.ItemText>
                  </Select.Item>
                ))}
              </Select.Popup>
            </Select.Positioner>
          </Select.Portal>
        </Select.Root>
        {active ? <Button type="button" variant="ghost" className="min-h-11" disabled={busy} onClick={() => setName(items.find(chat => chat.chat_id === active)?.title ?? "")}>Rename</Button> : null}
        {after ? <Button type="button" variant="ghost" className="min-h-11" disabled={busy} onClick={() => {
          void act(async signal => {
            const data = await listWorkspaceChats(notebookId, after, signal)
            if (signal.aborted) return
            const older = data.chats.map(chat => withRecovery(chat, accountId, notebookId))
            setItems(current => [...current, ...older.filter(chat => !current.some(item => item.chat_id === chat.chat_id))])
            setAfter(data.next_after)
          })
        }}>Load older chats</Button> : null}
      </div> : null}
      {name !== null ? <form onSubmit={rename} className="flex flex-wrap items-end gap-2">
        <label className="min-w-32 flex-1 space-y-1 text-sm">
          <span className="block">Chat name</span>
          <input required maxLength={120} value={name} onChange={event => setName(event.target.value)} className="min-h-11 w-full rounded-md border border-input bg-card px-2" />
        </label>
        <Button type="submit" className="min-h-11" disabled={busy || !name.trim()}>Save name</Button>
        <Button type="button" variant="ghost" className="min-h-11" disabled={busy} onClick={() => setName(null)}>Cancel</Button>
      </form> : null}
      {busy && !active ? <p role="status" className="text-sm text-muted-foreground">Loading chats…</p> : null}
      {error ? <div>
        <p role="alert" className="text-sm text-destructive">{error}</p>
        {!active ? <Button type="button" variant="ghost" className="min-h-11" onClick={() => { setError(""); setBusy(true); setAttempt(attempt + 1) }}>Reload chats</Button> : null}
      </div> : null}
    </section>
  )
}
