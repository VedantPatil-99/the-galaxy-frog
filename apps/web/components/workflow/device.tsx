import brandMarks from "./brand-marks.json"
import type { DeviceIcon } from "./model"

// The marks come from Simple Icons. The chassis and relief are drawn for this workflow.
const symbols: Partial<Record<DeviceIcon, string>> = {
  queue: "M6 9h28v7H6zM8 18h28v7H8zM6 27h28v7H6zM10 12.5h9m-7 9h9m-11 9h9",
  transcript: "M8 5h19l6 6v24H8zM27 5v7h6m-20 5h14m-14 6h14m-14 6h9",
  audio: "M3 20h5l3-9 5 19 5-24 4 27 5-17 3 4h4",
  frames: "M5 8h30v24H5zM11 8v24m18-24v24M5 15h6m-6 10h6m18-10h6m-6 10h6m-18-10 8 5-8 5z",
  vision: "M7 9h26v22H7zM7 26l8-8 7 7 4-4 7 7M4 12V5h8m16 0h8v7M4 28v7h8m16 0h8v-7M25 15a2 2 0 1 0 0-.1",
  question: "M6 7h28v20H21l-8 7v-7H6zM16 15c0-5 9-5 9 0 0 3-5 4-5 7m0 3h.01",
  search: "M28 17a11 11 0 1 1-22 0 11 11 0 1 1 22 0Zm-3 8 10 10M11 17h12m-6-6v12",
  route: "M5 20h12m0 0 10-10h8m-18 10h18m-18 0 10 10h8m-4-24 4 4-4 4m0 2 4 4-4 4m0 2 4 4-4 4",
  player: "M5 7h30v25H5zM17 13l9 5-9 5zM9 27h22m-17-2v4",
}

const top = "M-86-4-7-50Q0-54 7-50L86-4Q93 0 86 4L7 50Q0 54-7 50L-86 4Q-93 0-86-4Z"
const cap = "M-52-4-5-31Q0-34 5-31L52-4Q58 0 52 4L5 31Q0 34-5 31L-52 4Q-58 0-52-4Z"

function Mark({ icon, disc }: { icon: DeviceIcon; disc?: boolean }) {
  const brand = brandMarks[icon as keyof typeof brandMarks]
  return (
    <g transform={disc ? "matrix(1 0 0 .6 0 -24)" : "matrix(.866 .5 -.866 .5 0 -24)"}>
      {brand ? (
        <path d={brand} transform="translate(-17 -17) scale(1.4167)" fill="currentColor" stroke="none" />
      ) : (
        <path d={symbols[icon]} transform="translate(-19 -19) scale(.95)" fill="none" stroke="currentColor" strokeWidth="2.25" />
      )}
    </g>
  )
}

export function WorkflowDevice({ icon, disc, className }: { icon: DeviceIcon; disc?: boolean; className?: string }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="-98 -74 196 158" fill="none" strokeLinejoin="round" strokeLinecap="round">
      <g className="wf-device-case">
        <path className="wf-device-foot" d={top} transform="translate(0 31) scale(1.035 1)" />
        <path className="wf-device-wall" d="M-90 0v26q0 4 6 8l77 44q7 4 14 0l77-44q6-4 6-8V0L0 49Z" />
        <path className="wf-device-side" d="M0 49 90 0v26q0 4-6 8L7 78q-3 2-7 2Z" />
        <path className="wf-device-detail" d="M-86 13-10 57m-76-36 76 44m-76-36 76 44M54 36v12m6-15v12m6-16v13m6-16v12m6-16v12m6-15v12" />
        <path className="wf-device-vent" d="M-81 15-48 34v4l-33-19Zm0 8 26 15v4l-26-15Z" />
        <path className="wf-device-surface" d={top} />
        <path className="wf-device-rim" d="M-83 4-6 49q6 3 12 0L83 4" />
        <ellipse cx="0" cy="-42" rx="4.4" ry="2.4" className="wf-device-screw" />
        <ellipse cx="-77" cy="0" rx="4.4" ry="2.4" className="wf-device-screw" />
        <ellipse cx="77" cy="0" rx="4.4" ry="2.4" className="wf-device-screw" />
        <ellipse cx="0" cy="42" rx="4.4" ry="2.4" className="wf-device-screw" />
        <ellipse cx="19" cy="61" rx="3.2" ry="2.2" className="wf-device-led" />
      </g>
      <g className="wf-device-cap">
        {disc ? (
          <>
            <ellipse cx="0" cy="0" rx="46" ry="24" className="wf-device-recess" />
            <path className="wf-device-wall" d="M-41-24v12C-41 0-23 10 0 10s41-10 41-22v-12Z" />
            {Array.from({ length: 17 }, (_, i) => {
              const angle = (Math.PI * (i + 1)) / 18
              const x = 41 * Math.cos(angle)
              const y = -24 + 22 * Math.sin(angle)
              return <path key={i} className="wf-device-detail" d={`M${x} ${y}v11`} />
            })}
            <ellipse cx="0" cy="-24" rx="41" ry="22" className="wf-device-surface" />
            <ellipse cx="0" cy="-24" rx="34.5" ry="18" className="wf-device-rim" />
          </>
        ) : (
          <>
            <path className="wf-device-recess" d={cap} transform="scale(1.12)" />
            <path className="wf-device-wall" d="M-56-24v12L-5 19q5 3 10 0l51-31v-12L0 7Z" />
            <path className="wf-device-surface" d={cap} transform="translate(0 -24)" />
          </>
        )}
        <Mark icon={icon} disc={disc} />
      </g>
    </svg>
  )
}
