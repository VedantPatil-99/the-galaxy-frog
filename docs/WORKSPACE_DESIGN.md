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

Notebook is the default based on the user's preference for the source and question area side by side on a laptop. Frog Lab opens on demand. At intermediate widths it uses the secondary pane until the user returns to Sources or Workspace. A citation opens Source preview and seeks to the cited millisecond when the player is ready. The full transcript is collapsed by default. Answer citations and required temporal choices remain in the work area. Detailed transcript matches, frame and OCR evidence, traces, and provider settings are available through disclosures. Evidence-only search keeps its own composer control.

File import lives with the notebook's Documents list. A successful import opens the source
preview on desktop and switches to Sources on a phone. PDF pages show the image and page
number first, with extracted text available on demand. DOCX and text previews show their
content in the same Sources pane.

## Visual system and typography

Use a cool, lightly colored work surface, clear text, and a restrained teal action color drawn from the frog identity. The personality should come from Doto, the original frog mark, and well-composed evidence, not ornamental cards or animation. Dark mode uses the same hierarchy and restrained accent.

- **Light tokens:** canvas `#F4F7F8`, surface `#FFFFFF`, ink `#172E38`, secondary text `#50636C`, border `#CCD7DB`, action `#186D65` with white text.
- **Dark tokens:** canvas `#12252B`, surface `#19323A`, ink `#EBF3F4`, secondary text `#A8BEC4`, border `#3B565F`, action `#8FD1C1` with `#12252B` text.
- **Brand/display:** [Doto](https://fonts.google.com/specimen/Doto), initially weight 800, for the Galaxy Frog brand treatment and a restrained welcome heading. Use it at display sizes; never for long answers, source titles, citations, or small controls. Google's [official metadata](https://github.com/google/fonts/blob/main/ofl/doto/METADATA.pb) identifies Doto as an OFL-licensed display family. Read and retain the actual license when bundling font files.
- **Reading/interface:** Commissioner is used for headings, controls, and reading text. One variable family provides the weight range across the interface. The system monospace stack handles timestamps and code.
- **Loading:** `next/font/local` preloads the Google Fonts Latin WOFF2 files from the app. The Doto weight 800 file and variable Commissioner file are bundled with their OFL notices. Local files keep builds reproducible without a Google Fonts request. Other scripts use system fallbacks. See the [Next.js font guide](https://nextjs.org/docs/app/getting-started/fonts#local-fonts).
- **Scale:** body/answer text 16px with approximately 1.65 line height; controls and source rows 14–16px; secondary metadata 13px; workspace headings 20–28px; Doto brand approximately 22–28px. Respect browser font settings and test zoom/reflow. These values must be refined from screenshots, not used to shrink content until it fits.
- **Geometry:** spacing 4/8/12/16/24/32px; compact controls with 6px corners, overlays and larger surfaces up to 12px; structural pane dividers instead of a card around every item. Keep content edges aligned. Long reading surfaces should generally remain within 65–80 characters per line.
- **Motion:** answer user actions with brief opening/closing and state transitions. Preserve focus and honor reduced motion. Companions are optional and never the only status signal.

The palette is a starting specification, not a verified accessibility result. Measure final foreground/background pairs, selected states, keyboard focus, and text at actual rendered sizes before the shell passes acceptance.

## Implementation and verification contract

Use existing shadcn Base UI primitives, Phosphor icons, theme foundations, and the FastAPI proxy. Introduce Sidebar, Resizable, Tabs, Sheets, and document controls only where they serve a journey. Do not replace the application with a static mock or make an unfinished control look functional.

W2 used the repository's frontend-design and design-taste-frontend guidance for visual hierarchy, aligned panels, concise copy, and readable controls. The product layout and interaction requirements take priority over marketing hero, stock photography, and static landing-page gates.

In W3.3, a PDF page without extractable text shows its own OCR disclosure beneath the preview. The owner approves processing for that page. Saved OCR text opens on demand from the same page.

The local browser check used the real local workspace status and a separate synthetic answer fixture with a public YouTube embed. It checked desktop and phone screenshots, horizontal reflow at 1440, 1024, 820, 768, 390, and 320px, both themes, actual font loading, draft retention, remembered layouts, focus after clicking a citation, and keyboard access to the transcript disclosure. The fixture verifies the frontend interaction. Live model generation, hosted browser sign-in, and a full accessibility audit are separate checks.
