# Galaxy Frog workspace design direction

Date: 2026-11-19. Branch: `feat/workspace`. Baseline: merged `origin/main` at `4299de6`.

This is the W0 design specification for [the workspace plan](WORKSPACE_REDESIGN_PLAN.md), not a claim that the new interface is implemented. The user confirmed Phase 8 was merged; this packet does not repeat its acceptance audit.

## Product and design brief

Galaxy Frog is a workspace for reading, questioning, and producing study material from time- and source-grounded evidence. Its distinctive material is the relationship between a question, the supporting passage or moment, and the output the user keeps. Make that relationship easier to follow than it is in the existing vertically stacked transcript workspace.

The current application provides behavior to preserve: imports, durable progress, transcript search, the player, citation seeking, moment explanations, Study materials, and explicit provider/consent controls. Existing layout and typography are open to replacement. The existing frog logo remains a brand asset.

Apply `.agents/skills/frontend-design/SKILL.md` for product reasoning and critique, and `.agents/skills/design-taste-frontend/SKILL.md` for applicable composition, copy, consistency, accessibility, and performance guidance. The workspace plan's application-specific overrides apply. Marketing heroes, random palette/font deals, forced screen-height sections, stock photos, shaders, static export, and limits on evidence captions do not apply. Their marketing-page gates cannot establish that a private application works.

## Journeys and the information they need

- **Import and select:** choose a Galaxy, add a source, see its processing/coverage state, and select it for the next question. Keep selection separate from ingestion. Show errors beside the affected source.
- **Ask and investigate:** compose a question with visible source scope, follow generation progress, read validated answer blocks, and open a citation alongside the answer. Preserve the original interval or document location.
- **Create and revise:** start a Frog Lab action from selected evidence or an answer, open the resulting document, edit it, and see its save/version state. The document can become the central working surface.
- **Resume:** return to a Galaxy and its chat, restore the draft and panels, and recover a running job without starting a second request.
- **Change context:** switch layout or source inspector without losing the player's position, open output, source selection, or unsent prompt. Phone navigation changes the primary pane rather than shrinking four columns.

## Two layout concepts

### A. Persistent evidence desk

```text
Galaxy / chats | Source Pond | Conversation or document | Frog Lab
              | player      |                         | outputs
```

Keep sources, conversation, and tools present on desktop. This supports comparison and learning the available actions. Its cost is reading width: four full columns become cramped on a laptop, and an always-visible player competes with document editing. It works as Notebook mode with a collapsible rail and optional source preview, rather than as an invariant at every width.

### B. One working surface with contextual panels — selected shell concept

```text
Galaxy / chats | Conversation or document              | Inspector
              | answer + evidence links               | source / Lab / Gallery
              | composer or document controls         | selected context
```

Give the current question or document most of the width. A contextual inspector opens evidence without replacing that work. This supports longer answers, focused reading, and a clearer mobile transition. Its cost is discoverability: inspector switches need visible names and source scope must stay visible near the composer.

Use concept B as the common shell/state model. **Notebook mode** expands a dedicated Source Pond and Frog Lab around the same working surface, incorporating concept A's comparison strengths. **Focus mode** keeps the navigation and one contextual inspector. Neither mode creates a separate conversation or job. At intermediate widths, collapse secondary panels before narrowing readable content. On phones, show one pane and labeled Sources, Chat/Document, and Frog Lab navigation.

## Visual system and typography

Use a cool, lightly colored work surface, clear text, and a restrained teal action color drawn from the frog identity. The personality should come from Doto, the original frog mark, and well-composed evidence, not ornamental cards or animation. Dark mode uses the same hierarchy and restrained accent.

- **Light tokens:** canvas `#F4F7F8`, surface `#FFFFFF`, ink `#172E38`, secondary text `#50636C`, border `#CCD7DB`, action `#186D65` with white text.
- **Dark tokens:** canvas `#12252B`, surface `#19323A`, ink `#EBF3F4`, secondary text `#A8BEC4`, border `#3B565F`, action `#8FD1C1` with `#12252B` text.
- **Brand/display:** [Doto](https://fonts.google.com/specimen/Doto), initially weight 800, for the Galaxy Frog brand treatment and a restrained welcome heading. Use it at display sizes; never for long answers, source titles, citations, or small controls. Google's [official metadata](https://github.com/google/fonts/blob/main/ofl/doto/METADATA.pb) identifies Doto as an OFL-licensed display family. Read and retain the actual license when bundling font files.
- **Reading/interface candidate:** Commissioner for headings and UI text, pending a real browser specimen of paragraphs, tables, equations, and source lists. Evaluate its distinction and legibility before replacing the current fonts. Reuse one family across those roles rather than adding unrelated display faces. Use the system monospace stack for code initially.
- **Loading:** use the installed Next.js `next/font` facilities to self-host Google Fonts. Doto does not require an npm font package or a machine font installation. No browser call to Google is needed when using this loading path. See the installed `next/dist/docs/01-app/01-getting-started/13-fonts.md`.
- **Scale:** body/answer text 16px with approximately 1.65 line height; controls and source rows 14–16px; secondary metadata 13px; workspace headings 20–28px; Doto brand approximately 22–28px. Respect browser font settings and test zoom/reflow. These values must be refined from screenshots, not used to shrink content until it fits.
- **Geometry:** spacing 4/8/12/16/24/32px; compact controls with 6px corners, overlays and larger surfaces up to 12px; structural pane dividers instead of a card around every item. Keep content edges aligned. Long reading surfaces should generally remain within 65–80 characters per line.
- **Motion:** answer user actions with brief opening/closing and state transitions. Preserve focus and honor reduced motion. Companions are optional and never the only status signal.

The palette is a starting specification, not a verified accessibility result. Measure final foreground/background pairs, selected states, keyboard focus, and text at actual rendered sizes before the shell passes acceptance.

## Implementation and verification contract

Use existing shadcn Base UI primitives, Phosphor icons, theme foundations, and the FastAPI proxy. Introduce Sidebar, Resizable, Tabs, Sheets, and document controls only where they serve a journey. Do not replace the application with a static mock or make an unfinished control look functional.

Before shipping the shell, compare screenshots at 1440, 1024, 820/768, 390, and 320px in both themes. Exercise import/select/ask/cite/save/resume and layout switching; test keyboard resizing, focus restoration, mobile navigation, loading/error states, actual font loading, and reduced motion. Use applicable taste-skill checks and record exclusions honestly. This packet changes backend ownership foundations and documentation only, so no new UI screenshots or browser acceptance are claimed yet.
