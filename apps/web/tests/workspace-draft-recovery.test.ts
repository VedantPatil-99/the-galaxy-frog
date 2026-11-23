import { expect, test } from "bun:test"

import {
  readComposerLocation, readDraftRecovery, readPendingTurn, rememberComposerLocation, workspaceDraftKey, writeDraftRecovery, writePendingTurn,
} from "@/lib/workspace-draft-recovery"

function storageFixture() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    removeItem: (key: string) => { values.delete(key) },
  }
}

test("recovers exact text with separate keys for accounts, notebooks, and chats", () => {
  const storage = storageFixture()
  const key = workspaceDraftKey("owner", "first", "chat")
  const edit = { text: "  Where is café shown?\n", revision: 4 }
  const question = { text: "A quick question", revision: null }
  expect(writeDraftRecovery(key, edit, { storage })).toBe(true)
  expect(writeDraftRecovery(workspaceDraftKey("owner", "first"), question, { storage })).toBe(true)
  expect(readDraftRecovery(key, storage)).toEqual(edit)
  expect(readDraftRecovery(workspaceDraftKey("owner", "first"), storage)).toEqual(question)
  expect(readDraftRecovery(workspaceDraftKey("other", "first", "chat"), storage)).toBeNull()
  expect(readDraftRecovery(workspaceDraftKey("owner", "second", "chat"), storage)).toBeNull()
  expect(readDraftRecovery(workspaceDraftKey("owner", "first", "other"), storage)).toBeNull()
})

test("an unconfirmed Send can be checked after refresh only from its owning composer", () => {
  const storage = storageFixture()
  const id = "79e450bd-b542-4218-9f3b-82574abf9fe7"
  const intent = { submission_key: id, question: "What moves?", draft_revision: 3,
    passage: { source_id: id, version_id: id, block_id: id } }
  writePendingTurn("owner", "notebook", "chat", intent, storage)
  expect(readPendingTurn("owner", "notebook", "chat", storage)).toEqual(intent)
  expect(readPendingTurn("other", "notebook", "chat", storage)).toBeNull()
  expect(readPendingTurn("owner", "notebook", "different", storage)).toBeNull()
  writePendingTurn("owner", "notebook", "chat", null, storage)
  expect(readPendingTurn("owner", "notebook", "chat", storage)).toBeNull()
})

test("an acknowledgement cannot remove a newer local edit, including a cleared question", () => {
  const storage = storageFixture()
  const key = workspaceDraftKey("owner", "notebook", "chat")
  const starting = { text: "A question", revision: 1 }
  const cleared = { text: "", revision: 1 }
  writeDraftRecovery(key, starting, { storage })
  writeDraftRecovery(key, cleared, { storage })
  expect(writeDraftRecovery(key, null, { storage, previous: starting })).toBe(false)
  expect(readDraftRecovery(key, storage)).toEqual(cleared)
  const rebased = { ...cleared, revision: 2 }
  expect(writeDraftRecovery(key, rebased, { storage, previous: cleared })).toBe(true)
  expect(writeDraftRecovery(key, null, { storage, previous: cleared })).toBe(false)
  expect(writeDraftRecovery(key, null, { storage, previous: rebased })).toBe(true)
  expect(readDraftRecovery(key, storage)).toBeNull()
})

test("restores the last composer only for the matching account", () => {
  const storage = storageFixture()
  rememberComposerLocation("owner", "notebook", "chat", storage)
  expect(readComposerLocation("owner", storage).get("chat")).toBe("chat")
  expect(readComposerLocation("owner", storage).get("notebook")).toBe("notebook")
  expect(readComposerLocation("other", storage).get("chat")).toBeNull()
})

test("ignores invalid records and tolerates blocked browser storage", () => {
  const storage = storageFixture()
  const key = workspaceDraftKey("owner", "first", "chat")
  for (const payload of ["broken JSON", "[]", '{"text":"bad","revision":-1}', '{"text":"bad"}']) {
    storage.setItem(key, payload)
    expect(readDraftRecovery(key, storage)).toBeNull()
  }
  const deny = () => { throw new Error("Storage disabled") }
  const blocked = { getItem: deny, setItem: deny, removeItem: deny }
  expect(readDraftRecovery(key, blocked)).toBeNull()
  expect(writeDraftRecovery(key, { text: "draft", revision: 3 }, { storage: blocked })).toBe(false)
  expect(readComposerLocation("owner", blocked).get("chat")).toBeNull()
})
