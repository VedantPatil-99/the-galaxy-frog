"use client"

import { PlusIcon } from "@phosphor-icons/react/dist/csr/Plus"
import { useEffect, useId, useRef, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { ApiClientError } from "@/lib/api/client"
import {
  createWorkspaceChat, getWorkspaceChat, listWorkspaceChats, renameWorkspaceChat,
  type WorkspaceChat, type WorkspaceChatSummary,
} from "@/lib/api/workspace"

export function WorkspaceChatPicker({ notebookId, onSelect, onExpired }: {
  notebookId: string
  onSelect: (chat: WorkspaceChat | null) => void
  onExpired: () => void
}) {
  const field = useId()
  const request = useRef<AbortController | null>(null)
  const newChat = useRef<string | null>(null)
  const [items, setItems] = useState<WorkspaceChatSummary[]>([])
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
        if (controller.signal.aborted) return
        const requested = new URLSearchParams(window.location.search).get("chat")
        const first = data.chats[0]?.chat_id
        let chat: WorkspaceChat | null = null
        if (requested) {
          try {
            chat = await getWorkspaceChat(notebookId, requested, controller.signal)
          } catch (caught) {
            if (!(caught instanceof ApiClientError) || ![404, 422].includes(caught.status)) throw caught
          }
        }
        if (!chat && first) chat = await getWorkspaceChat(notebookId, first, controller.signal)
        if (controller.signal.aborted) return
        const all = chat && !data.chats.some(item => item.chat_id === chat.chat_id)
          ? [chat, ...data.chats] : data.chats
        setItems(all)
        setAfter(data.next_after)
        setActive(chat?.chat_id ?? "")
        onSelect(chat)
      } catch (caught) {
        if (controller.signal.aborted) return
        if (caught instanceof ApiClientError && caught.status === 401) onExpired()
        else setError(caught instanceof Error ? caught.message : "Chats could not load.")
      } finally {
        if (!controller.signal.aborted) {
          request.current = null
          setBusy(false)
        }
      }
    }
    void load()
    return () => request.current?.abort()
  }, [attempt, notebookId, onExpired, onSelect])

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

  function select(chat: WorkspaceChat) {
    const url = new URL(window.location.href)
    url.searchParams.set("notebook", notebookId)
    url.searchParams.set("chat", chat.chat_id)
    window.history.replaceState(window.history.state, "", url)
    setActive(chat.chat_id)
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
      setItems(current => current.map(item => item.chat_id === chat.chat_id ? chat : item))
      setName(null)
    })
  }

  return (
    <section className="space-y-2 border-b pb-4" aria-labelledby={`${field}-heading`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={`${field}-heading`} className="font-semibold">Chats</h2>
        <Button type="button" variant="secondary" className="min-h-11" disabled={busy} onClick={create}>
          <PlusIcon aria-hidden="true" /> New chat
        </Button>
      </div>
      {active ? <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={field} className="sr-only">Open chat</label>
        <select id={field} value={active} disabled={busy} className="min-h-11 min-w-32 max-w-full flex-1 rounded-md border border-input bg-card px-2 text-sm" onChange={event => {
          const id = event.target.value
          void act(async signal => {
            const chat = await getWorkspaceChat(notebookId, id, signal)
            if (!signal.aborted) select(chat)
          })
        }}>
          {items.map(chat => <option key={chat.chat_id} value={chat.chat_id}>{chat.title}</option>)}
        </select>
        <Button type="button" variant="ghost" className="min-h-11" disabled={busy} onClick={() => setName(items.find(chat => chat.chat_id === active)?.title ?? "")}>Rename</Button>
        {after ? <Button type="button" variant="ghost" className="min-h-11" disabled={busy} onClick={() => {
          void act(async signal => {
            const data = await listWorkspaceChats(notebookId, after, signal)
            if (signal.aborted) return
            setItems(current => [...current, ...data.chats.filter(chat => !current.some(item => item.chat_id === chat.chat_id))])
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
