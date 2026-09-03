export const YOUTUBE_PLAYER_ORIGIN = "https://www.youtube-nocookie.com"

export function seekCommands(milliseconds: number): string[] {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) {
    throw new RangeError("seek timestamp must be a non-negative finite number")
  }
  return [
    JSON.stringify({
      event: "command",
      func: "seekTo",
      args: [milliseconds / 1000, true],
    }),
    JSON.stringify({ event: "command", func: "playVideo", args: [] }),
  ]
}
