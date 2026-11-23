import { describe, expect, mock, test } from "bun:test"

import { pausedPlayheadMs, seekToMs, YOUTUBE_PLAYER_ORIGIN } from "@/lib/youtube-player"

describe("YouTube player commands", () => {
  test("seeks to the cited millisecond through the ready player", () => {
    const player = { seekTo: mock() }
    seekToMs(player, 90_500)

    expect(YOUTUBE_PLAYER_ORIGIN).toBe("https://www.youtube-nocookie.com")
    expect(player.seekTo).toHaveBeenCalledTimes(1)
    expect(player.seekTo).toHaveBeenCalledWith(90.5, true)
  })

  test("rejects invalid citation timestamps", () => {
    const player = { seekTo: mock() }
    expect(() => seekToMs(player, -1)).toThrow(RangeError)
    expect(() => seekToMs(player, Number.NaN)).toThrow(RangeError)
    expect(player.seekTo).not.toHaveBeenCalled()
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
