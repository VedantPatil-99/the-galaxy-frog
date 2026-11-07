import { describe, expect, test } from "bun:test"

import { pausedPlayheadMs, seekCommands, YOUTUBE_PLAYER_ORIGIN } from "@/lib/youtube-player"

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

  test("samples the current paused playhead on every request", () => {
    let seconds = 12.3456
    const player = { getCurrentTime: () => seconds, getPlayerState: () => 2 }
    expect(pausedPlayheadMs(player, 60_000)).toBe(12_345)
    seconds = 39.8765
    expect(pausedPlayheadMs(player, 60_000)).toBe(39_876)
    expect(() => pausedPlayheadMs(player, 39_000)).toThrow("outside the imported video duration")
    expect(() => pausedPlayheadMs({ ...player, getPlayerState: () => 1 }, 60_000)).toThrow("Pause the video")
  })
})
