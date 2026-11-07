export const YOUTUBE_PLAYER_ORIGIN = "https://www.youtube-nocookie.com"

interface YouTubePlayer {
  getCurrentTime(): number
  getPlayerState(): number
  seekTo(seconds: number, allowSeekAhead: boolean): void
  destroy(): void
}

interface YouTubeApi {
  Player: new (element: HTMLIFrameElement, options: {
    events: { onReady(event: { target: YouTubePlayer }): void }
  }) => YouTubePlayer
}

type YouTubeWindow = Window & {
  YT?: YouTubeApi
  onYouTubeIframeAPIReady?: () => void
}

let apiReady: Promise<YouTubeApi> | undefined

export function loadYouTubeApi(): Promise<YouTubeApi> {
  const host = window as YouTubeWindow
  if (host.YT?.Player) return Promise.resolve(host.YT)
  if (apiReady) return apiReady

  apiReady = new Promise<YouTubeApi>((resolve, reject) => {
    const script = document.createElement("script")
    const previousReady = host.onYouTubeIframeAPIReady
    const timeout = window.setTimeout(fail, 12_000)

    function cleanup() {
      window.clearTimeout(timeout)
      host.onYouTubeIframeAPIReady = previousReady
      script.removeEventListener("error", fail)
    }

    function fail() {
      cleanup()
      script.remove()
      reject(new Error("The YouTube player API could not load."))
    }

    host.onYouTubeIframeAPIReady = () => {
      if (!host.YT?.Player) return
      cleanup()
      resolve(host.YT)
      previousReady?.()
    }
    script.src = "https://www.youtube.com/iframe_api"
    script.async = true
    script.addEventListener("error", fail)
    document.head.append(script)
  }).catch((error: unknown) => {
    apiReady = undefined
    throw error
  })
  return apiReady
}

export function pausedPlayheadMs(
  player: Pick<YouTubePlayer, "getCurrentTime" | "getPlayerState">,
  durationMs: number,
): number {
  if (player.getPlayerState() !== 2) {
    throw new Error("Pause the video at the moment you want to explain.")
  }
  const timestamp = Math.floor(player.getCurrentTime() * 1000)
  if (!Number.isSafeInteger(timestamp) || timestamp < 0 || timestamp >= durationMs) {
    throw new Error("The playhead is outside the imported video duration. Choose another moment.")
  }
  return timestamp
}

export type { YouTubePlayer }

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
