import { describe, expect, test } from "bun:test"

import { seekCommands, YOUTUBE_PLAYER_ORIGIN } from "@/lib/youtube-player"

describe("YouTube player commands", () => {
  test("seeks to a citation interval and resumes playback", () => {
    const commands = seekCommands(90_500).map((command) => JSON.parse(command))

    expect(YOUTUBE_PLAYER_ORIGIN).toBe("https://www.youtube-nocookie.com")
    expect(commands).toEqual([
      { event: "command", func: "seekTo", args: [90.5, true] },
      { event: "command", func: "playVideo", args: [] },
    ])
  })

  test("rejects invalid citation timestamps", () => {
    expect(() => seekCommands(-1)).toThrow(RangeError)
    expect(() => seekCommands(Number.NaN)).toThrow(RangeError)
  })
})
