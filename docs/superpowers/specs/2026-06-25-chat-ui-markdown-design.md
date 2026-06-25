# AgentBridge Chat UI and Markdown Design

## Goal

Make AgentBridge Web a reading-first AI agent chat workspace and render common Markdown, including tables, correctly without CDN access.

## Layout

- Use a compact 64px navigation rail on desktop.
- Keep the chat thread as the dominant workspace with a readable maximum width.
- Open recent conversations, context settings, and tools in one secondary drawer instead of keeping two wide sidebars visible.
- Keep the composer fixed to the bottom of the chat workspace without covering messages.
- On mobile, replace the navigation rail and drawer with a compact top bar and full-width overlay drawer.

## Messages

- Assistant responses use an unboxed document-style layout with a small AgentBridge mark.
- User messages remain right-aligned, content-sized bubbles.
- Long assistant content is constrained to a readable text measure.
- Loading, empty, pending-confirmation, and request-error states remain visible and accessible.

## Markdown

- Bundle `markdown-it` with the Python package and serve it from AgentBridge Web.
- Do not load scripts, styles, fonts, or icons from a CDN.
- Disable raw HTML in Markdown.
- Sanitize rendered DOM to an explicit allowlist before inserting it into the page.
- Support headings, paragraphs, emphasis, links, blockquotes, ordered and unordered lists, inline code, fenced code blocks, horizontal rules, and tables.
- Wrap tables in a horizontally scrollable container on narrow viewports.
- Render user text as plain escaped text with line breaks; Markdown rendering is reserved for assistant and system output.

## Interaction

- Navigation rail buttons open one drawer at a time for conversations, context, or tools.
- The new-chat action remains immediately available.
- The composer keeps file attachment, slash-command suggestions, Enter-to-send, and duplicate-submit prevention.
- All icon buttons have accessible labels, visible focus states, and at least 44px hit targets.
- Motion is limited to short drawer and message-entry transitions and respects reduced-motion preferences.

## Verification

- Unit tests assert local Markdown asset serving, parser configuration, table wrapping, safe rendering, and reading-first layout structure.
- Existing offline unit tests and Python compile checks must pass.
- Browser verification covers desktop and 375px mobile widths, Markdown headings and tables, drawer interaction, composer visibility, and console errors.
