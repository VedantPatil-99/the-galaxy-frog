"use client"

import Link from "next/link"
import { useEffect, useId, useRef, useState, type CSSProperties } from "react"
import { ArrowRightIcon } from "@phosphor-icons/react/dist/csr/ArrowRight"
import { PauseIcon } from "@phosphor-icons/react/dist/csr/Pause"
import { PlayIcon } from "@phosphor-icons/react/dist/csr/Play"

import { WorkflowDevice } from "./device"
import { board, stages, type Point } from "./model"
import "./workflow.css"

type Stage = (typeof stages)[number]
type Selection = { stage: number; device: string }

const beginning: Selection = { stage: 0, device: stages[0].tour[0] }

function nextTourStop({ stage, device }: Selection): Selection | null {
  const tour = stages[stage].tour
  const next = tour[tour.indexOf(device) + 1]
  if (next) return { stage, device: next }

  const nextStage = stages[stage + 1]
  return nextStage ? { stage: stage + 1, device: nextStage.tour[0] } : null
}

function distance(a: Point, b: Point) {
  return Math.hypot(b[0] - a[0], b[1] - a[1])
}

function moveToward(a: Point, b: Point, amount: number): Point {
  const ratio = amount / distance(a, b)
  return [a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio]
}

function port(center: Point, toward: Point, compact: boolean) {
  const [dx, dy] = [toward[0] - center[0], toward[1] - center[1]]
  const length = Math.hypot(dx, dy)
  const radiusX = compact ? 46 : 54
  const radiusY = compact ? 29 : 38
  const reach = length / Math.hypot(dx / radiusX, dy / radiusY)
  return moveToward(center, toward, Math.min(reach, length * 0.75))
}

function cablePath(start: Point, end: Point, via: readonly Point[], compact: boolean) {
  const points = [start, ...via, end]
  points[0] = port(start, points[1], compact)
  points[points.length - 1] = port(end, points[points.length - 2], compact)
  let path = `M${points[0].join(" ")}`
  for (let i = 1; i < points.length - 1; i++) {
    const [previous, corner, next] = [points[i - 1], points[i], points[i + 1]]
    const radius = Math.min(12, distance(previous, corner) / 2, distance(corner, next) / 2)
    if (radius === 0) continue
    path += `L${moveToward(corner, previous, radius).join(" ")}Q${corner.join(" ")} ${moveToward(corner, next, radius).join(" ")}`
  }
  return `${path}L${points.at(-1)!.join(" ")}`
}

function Cables({ stage, selected, nextInTour, compact }: { stage: Stage; selected: string; nextInTour: string | null | undefined; compact: boolean }) {
  const arrowId = useId()
  const [width, height] = compact ? board.compact : board.wide
  const position = compact ? "compact" : "position"

  return (
    <svg aria-hidden="true" className={`wf-cables ${compact ? "wf-cables-compact" : "wf-cables-wide"}`} viewBox={`0 0 ${width} ${height}`}>
      <defs>
        <marker id={arrowId} markerUnits="userSpaceOnUse" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
          <path d="m0 0 7 3.5L0 7Z" fill="context-stroke" />
        </marker>
      </defs>
      {stage.cables.map((cable) => {
        const active = cable.from === selected || cable.to === selected
        const travels = cable.from === selected && (nextInTour === undefined || cable.to === nextInTour)
        const d = cablePath(stage.devices[cable.from][position], stage.devices[cable.to][position], (compact ? cable.compactVia : cable.via) ?? [], compact)
        return (
          <g key={`${cable.from}-${cable.to}`} className="wf-cable" data-active={active} data-conditional={Boolean(cable.conditional)}>
            <path d={d} className="wf-cable-jacket" />
            <path d={d} className="wf-cable-center" markerEnd={`url(#${arrowId})`} />
            {travels && <path key={selected} d={d} pathLength={100} className="wf-signal" />}
          </g>
        )
      })}
    </svg>
  )
}

function Circuit({ stage, selected, nextInTour, onSelect }: { stage: Stage; selected: string; nextInTour: string | null | undefined; onSelect: (device: string) => void }) {
  return (
    <div className="wf-board" role="group" aria-label={`${stage.name} components`}>
      <Cables stage={stage} selected={selected} nextInTour={nextInTour} compact={false} />
      <Cables stage={stage} selected={selected} nextInTour={nextInTour} compact />
      {Object.entries(stage.devices).map(([id, device]) => (
        <button
          key={id}
          type="button"
          className="wf-node"
          aria-pressed={id === selected}
          onClick={() => onSelect(id)}
          style={{
            "--wide-x": `${device.position[0] / board.wide[0] * 100}%`,
            "--wide-y": `${device.position[1] / board.wide[1] * 100}%`,
            "--compact-x": `${device.compact[0] / board.compact[0] * 100}%`,
            "--compact-y": `${device.compact[1] / board.compact[1] * 100}%`,
          } as CSSProperties}
        >
          <WorkflowDevice icon={device.icon} disc={device.disc} className="wf-device" />
          <span className="wf-node-label">{device.label}</span>
        </button>
      ))}
    </div>
  )
}

export function WorkflowExplorer() {
  const [selection, setSelection] = useState(beginning)
  const [playing, setPlaying] = useState(false)
  const stageHeading = useRef<HTMLHeadingElement>(null)
  const stage = stages[selection.stage]
  const device = stage.devices[selection.device]
  const nextStage = stages[selection.stage + 1]
  const nextStop = playing ? nextTourStop(selection) : null
  const nextInTour = playing ? (nextStop?.stage === selection.stage ? nextStop.device : null) : undefined

  useEffect(() => {
    if (!playing) return

    const timeout = window.setTimeout(() => {
      const next = nextTourStop(selection)
      if (next) setSelection(next)
      else setPlaying(false)
    }, 2_800)
    return () => window.clearTimeout(timeout)
  }, [playing, selection])

  function selectStage(index: number) {
    setPlaying(false)
    setSelection({ stage: index, device: stages[index].tour[0] })
  }

  function selectDevice(id: string) {
    setPlaying(false)
    setSelection({ stage: selection.stage, device: id })
  }

  function toggleTour() {
    if (playing) {
      setPlaying(false)
      return
    }
    if (!nextTourStop(selection) || !stage.tour.includes(selection.device)) setSelection(beginning)
    setPlaying(true)
  }

  function continueToNextStage() {
    selectStage(selection.stage + 1)
    stageHeading.current?.focus({ preventScroll: true })
    stageHeading.current?.scrollIntoView({ block: "nearest" })
  }

  return (
    <section className="wf-explorer" aria-label="Video workflow explorer">
      <div className="wf-toolbar">
        <nav className="wf-stage-nav" aria-label="Workflow stages">
          {stages.map((item, index) => (
            <button key={item.name} type="button" onClick={() => selectStage(index)} aria-current={selection.stage === index ? "step" : undefined}>
              <span aria-hidden="true">0{index + 1}</span> {item.name}
            </button>
          ))}
        </nav>
        <button className="wf-tour-button" type="button" onClick={toggleTour}>
          {playing ? <PauseIcon size={15} weight="fill" aria-hidden="true" /> : <PlayIcon size={15} weight="fill" aria-hidden="true" />}
          {playing ? "Pause walkthrough" : "Play walkthrough"}
        </button>
      </div>

      <div className="wf-panel">
        <section className="wf-scene" aria-labelledby="workflow-stage-title">
          <div className="wf-stage-heading">
            <h2 id="workflow-stage-title" ref={stageHeading} tabIndex={-1}>{stage.title}</h2>
            <p>{stage.summary}</p>
          </div>
          <Circuit key={stage.name} stage={stage} selected={selection.device} nextInTour={nextInTour} onSelect={selectDevice} />
          <div className="wf-scene-footer">
            <span><i className="wf-key-line" aria-hidden="true" /> Main path</span>
            <span><i className="wf-key-line wf-key-dashed" aria-hidden="true" /> When needed</span>
          </div>
        </section>
        <aside className="wf-readout" aria-label="Selected component">
          <p className="wf-readout-label"><span aria-hidden="true" /> Selected component</p>
          <div className="wf-readout-content" aria-live="polite" aria-atomic="true">
            <h3>{device.label}</h3>
            <p className="wf-technology">{device.technology}</p>
            <p className="wf-detail">{device.detail}</p>
            <div className="wf-handoff">
              <p>Passes on</p>
              <p>{device.output}</p>
            </div>
          </div>
          {nextStage ? (
            <button className="wf-continue" type="button" onClick={continueToNextStage}>
              Next: {nextStage.name} <ArrowRightIcon size={16} aria-hidden="true" />
            </button>
          ) : (
            <Link className="wf-continue" href="/">
              Open workspace <ArrowRightIcon size={16} aria-hidden="true" />
            </Link>
          )}
        </aside>
      </div>
    </section>
  )
}
