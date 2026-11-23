# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Learners studying lectures and tutorials and researchers comparing sources are equally
important. Both need to find the relevant passage or moment, examine it in context, and
return to it as their understanding develops. The private workspace starts with invited creators.

## Product Purpose

Galaxy Frog helps people investigate source material, ask grounded questions, and produce
study material they can trace back to the evidence. Success means a reader can open the
original source from a result and judge whether it supports the claim.

## Positioning

Galaxy Frog starts with temporal multimodal retrieval. For video, it can connect spoken words,
on-screen text, and selected frames to the moment they came from. The workspace extends that
approach to mixed sources, using exact pages or stable, versioned text blocks where applicable.
[Product scope](../../docs/mvp-scope.md) · [Workspace plan](../../docs/WORKSPACE_REDESIGN_PLAN.md)

## Operating Context

Conversation is the main reading surface. A **Sources** action near the end of an answer lists
its citations, each linked to the original location. The intended default searches eligible
notebook sources automatically. Readers can narrow the scope to specific sources or passages.

The current workspace supports mixed-source search, one-shot answers from selected passages,
and recoverable chat drafts. Saved grounded turns and recoverable generation runs are planned.
[Workspace checkpoints](../../PLANS.md#active-program--workspace-redesign)

## Capabilities and Constraints

- Preserve notebook ownership, source versions, original citation coordinates, and caption or
  transcription lineage. Keep cloud processing and provider changes explicit and preserve consent
  boundaries. [Repository invariants](../../AGENTS.md) · [Workspace plan](../../docs/WORKSPACE_REDESIGN_PLAN.md)
- Require an explicit selection to resolve an ambiguous temporal event and keep evidence search
  available independently of answer generation. [Temporal evidence decision](../../docs/adr/0005-hybrid-transcript-retrieval.md)
- Validate citation references and exact quotes against the evidence supplied to the model.
  Give readers access to the source so they can assess the claim itself.
  [Selected-passage answer decision](../../docs/adr/0013-answers-from-selected-passages.md)
- The redesigned conversation must support local Ollama and configured Gemini answer-generation
  models. Put the model choice and common run controls in the composer. Keep connection settings,
  processing models, and provider administration in advanced settings.
- When a provider call may have started, explain the uncertain outcome and require an explicit
  decision before a new call. Reconnecting resumes observation of the existing run.
- Keep source removal and permanent deletion distinct. Show the deletion impact before
  confirmation and remove or invalidate private content that depends on deleted material.
  [Domain glossary](../../GLOSSARY.md)

## Brand Commitments

Use the Galaxy Frog name and retain the original frog mark. Use familiar labels for actions
people need to find quickly. Prior layout and visual decisions are design context for future
work. [Frog mark](public/logo.png) · [Workspace design record](../../docs/WORKSPACE_DESIGN.md)

## Evidence on Hand

Use the checkpoint records to check what passed. The measurements identify the workloads,
environments, and limits of those checks.
[Workspace verification](../../PLANS.md#active-program--workspace-redesign) · [Measurements](../../docs/WORKSPACE_MEASUREMENTS.md)

## Product Principles

- Keep readers oriented when they open a source. Let them inspect the evidence while preserving
  their place in the question or result that brought them there.
- Help people resume work. Keep the relationship between the draft, selected evidence, and
  source location understandable as people move between tasks or return after an interruption.
- Make partial coverage, waiting, and failures actionable at the point where they affect the
  reader's decision.

## Accessibility & Inclusion

The workspace targets WCAG 2.2 AA. Preserve keyboard access, visible focus, readable reflow,
and text status cues. [Accessibility and acceptance criteria](../../docs/WORKSPACE_REDESIGN_PLAN.md#companions-and-accessibility)
