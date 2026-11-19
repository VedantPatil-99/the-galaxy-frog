export type Point = readonly [number, number]

export type DeviceIcon =
  | "youtube" | "nextjs" | "fastapi" | "queue" | "transcript" | "audio"
  | "ollama" | "frames" | "vision" | "database" | "question" | "search" | "route" | "player"

type Device = {
  label: string
  technology: string
  icon: DeviceIcon
  detail: string
  output: string
  position: Point
  compact: Point
  disc?: boolean
}

type Cable = {
  from: string
  to: string
  via?: readonly Point[]
  compactVia?: readonly Point[]
  conditional?: boolean
}

type Stage = {
  name: string
  title: string
  summary: string
  devices: Record<string, Device>
  cables: readonly Cable[]
  tour: readonly string[]
}

export const board = { wide: [840, 438], compact: [380, 340] } as const

export const stages: readonly Stage[] = [
  {
    name: "Import",
    title: "Import the video",
    summary: "A YouTube link becomes a transcript with timestamps.",
    devices: {
      video: {
        label: "YouTube", technology: "Video source", icon: "youtube", position: [84, 165], compact: [63, 80],
        detail: "Paste a supported YouTube link into the workspace to start an import.",
        output: "Video URL",
      },
      web: {
        label: "Workspace", technology: "Next.js", icon: "nextjs", position: [251, 165], compact: [190, 80],
        detail: "The browser submits the link and checks for progress while the video is prepared.",
        output: "Import request",
      },
      api: {
        label: "FastAPI", technology: "Python API", icon: "fastapi", position: [418, 165], compact: [317, 80],
        detail: "The API validates the link and creates or reuses an import job. It returns the job ID to the workspace.",
        output: "Queued job",
      },
      worker: {
        label: "Import worker", technology: "Python · PostgreSQL jobs", icon: "queue", position: [585, 165], compact: [317, 234],
        detail: "The worker picks up the saved job and looks for usable captions. If it can’t use them, it sends the audio to Whisper.",
        output: "Caption cues or a request to transcribe audio",
      },
      transcript: {
        label: "Transcript", technology: "Caption or speech cues", icon: "transcript", position: [752, 165], compact: [63, 234],
        detail: "Every caption or transcribed line keeps its original source and its start and end times.",
        output: "Timed transcript for indexing",
      },
      whisper: {
        label: "Whisper", technology: "faster-whisper small · when needed", icon: "audio", position: [668, 311], compact: [190, 234], disc: true,
        detail: "If captions are unavailable or unusable, the worker extracts bounded audio and transcribes it locally by default.",
        output: "Speech cues with timestamps",
      },
    },
    cables: [
      { from: "video", to: "web" }, { from: "web", to: "api" }, { from: "api", to: "worker" },
      { from: "worker", to: "transcript", compactVia: [[317, 311], [63, 311]] },
      { from: "worker", to: "whisper", via: [[585, 254], [668, 254]], conditional: true },
      { from: "whisper", to: "transcript", via: [[752, 311]], conditional: true },
    ],
    tour: ["video", "web", "api", "worker", "transcript"],
  },
  {
    name: "Index",
    title: "Make the video searchable",
    summary: "Store transcript sections and selected frames with their original times.",
    devices: {
      transcript: {
        label: "Timed cues", technology: "Transcript from Import", icon: "transcript", position: [104, 104], compact: [63, 80],
        detail: "The worker groups nearby lines into small sections while preserving the links to the original lines and video times.",
        output: "Searchable transcript sections",
      },
      embedding: {
        label: "BGE-M3", technology: "Ollama · text embeddings", icon: "ollama", position: [322, 104], compact: [190, 80], disc: true,
        detail: "BGE-M3 helps search find similar ideas even when the question uses different words from the video.",
        output: "1,024-dimensional text vectors",
      },
      index: {
        label: "Evidence index", technology: "PostgreSQL · full-text search · pgvector", icon: "database", position: [735, 201], compact: [317, 80], disc: true,
        detail: "PostgreSQL stores searchable text, vectors, frame references, and the times and source IDs needed to find each result again.",
        output: "Searchable evidence linked to the video",
      },
      video: {
        label: "Video stream", technology: "YouTube · yt-dlp", icon: "youtube", position: [104, 298], compact: [63, 234],
        detail: "Vision indexing can acquire a limited video stream to extract representative images.",
        output: "Video for frame sampling",
      },
      frames: {
        label: "Frame sampler", technology: "PySceneDetect · OpenCV", icon: "frames", position: [322, 298], compact: [190, 234],
        detail: "The worker samples scene boundaries and periodic moments. It identifies near duplicates and keeps frame images on local disk.",
        output: "Selected JPEGs and exact capture times",
      },
      visual: {
        label: "Optional vision", technology: "LlamaParse OCR · SigLIP", icon: "vision", position: [530, 298], compact: [317, 234], disc: true,
        detail: "With the right settings, LlamaParse can read on-screen text and SigLIP can make images searchable. Cloud OCR needs permission.",
        output: "On-screen text or 768-dimensional frame vectors",
      },
    },
    cables: [
      { from: "transcript", to: "embedding" }, { from: "embedding", to: "index", via: [[625, 104], [735, 165]] },
      { from: "video", to: "frames" },
      { from: "frames", to: "index", via: [[322, 215], [675, 215]], compactVia: [[190, 173], [274, 173]] },
      { from: "frames", to: "visual", conditional: true },
      { from: "visual", to: "index", via: [[735, 298]], conditional: true },
    ],
    tour: ["transcript", "embedding", "index"],
  },
  {
    name: "Answer",
    title: "Find, answer, and seek",
    summary: "Find matching evidence, answer from it, and jump to the source.",
    devices: {
      query: {
        label: "Search or ask", technology: "Workspace request", icon: "question", position: [84, 197], compact: [63, 80],
        detail: "Enter a question or search query. If a time reference is ambiguous, select a matching timestamp before continuing.",
        output: "Query and any selected time range",
      },
      retrieval: {
        label: "Find evidence", technology: "BGE-M3 · BGE reranker v2 M3", icon: "search", position: [258, 197], compact: [190, 80],
        detail: "FastAPI reads the evidence index for this video. It finds and ranks matching transcript sections and can include screen text or frames.",
        output: "Ranked evidence with source times",
      },
      routing: {
        label: "Choose a path", technology: "FastAPI · evidence routing", icon: "route", position: [428, 197], compact: [317, 80],
        detail: "Search returns the ranked evidence directly. A question can use matching screen text or send the selected evidence to an answer model.",
        output: "Results, text context, or selected frames",
      },
      text: {
        label: "Qwen3 4B", technology: "Ollama · text answers", icon: "ollama", position: [588, 70], compact: [190, 234], disc: true,
        detail: "Qwen writes an answer from the retrieved text. FastAPI checks that the citations refer to the supplied evidence.",
        output: "Answer with checked citations",
      },
      visual: {
        label: "Qwen3-VL", technology: "Ollama · optional visual reasoning", icon: "vision", position: [588, 312], compact: [317, 234], disc: true,
        detail: "When visual reasoning is enabled and the question needs it, the model can inspect selected frames and cite the relevant moment.",
        output: "Visual answer with frame citations",
      },
      player: {
        label: "Jump to source", technology: "Next.js · YouTube player", icon: "player", position: [748, 197], compact: [63, 234],
        detail: "The workspace shows results with timestamps. Select a citation to move the player to the original moment and check it yourself.",
        output: "Playback at the cited time",
      },
    },
    cables: [
      { from: "query", to: "retrieval" }, { from: "retrieval", to: "routing" },
      { from: "routing", to: "player", compactVia: [[317, 176], [63, 176]] },
      { from: "routing", to: "text", via: [[478, 197], [478, 70]], compactVia: [[255, 125], [190, 190]], conditional: true },
      { from: "text", to: "player", via: [[704, 70], [748, 127]], conditional: true },
      { from: "routing", to: "visual", via: [[478, 197], [478, 312]], conditional: true },
      { from: "visual", to: "player", via: [[704, 312], [748, 267]], compactVia: [[317, 310], [63, 310]], conditional: true },
    ],
    tour: ["query", "retrieval", "routing", "text", "player"],
  },
]
