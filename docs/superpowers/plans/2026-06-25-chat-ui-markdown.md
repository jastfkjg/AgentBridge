# AgentBridge Chat UI and Markdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a reading-first AgentBridge chat page with secure, offline Markdown rendering including tables.

**Architecture:** Vendor `markdown-it` under `src/agentbridge/assets`, serve it from the existing standard-library HTTP server, and initialize it with raw HTML disabled. Keep the single-page application in `web.py`, but change its layout to a navigation rail, central chat workspace, and one toggleable drawer; sanitize rendered Markdown DOM before insertion.

**Tech Stack:** Python standard-library HTTP server, vanilla HTML/CSS/JavaScript, locally bundled `markdown-it`, `unittest`, Playwright browser verification.

---

### Task 1: Package and serve the Markdown runtime

**Files:**
- Create: `src/agentbridge/assets/markdown-it.min.js`
- Create: `src/agentbridge/assets/markdown-it.LICENSE`
- Modify: `pyproject.toml`
- Modify: `src/agentbridge/web.py`
- Test: `tests/test_chat_web.py`

- [ ] **Step 1: Write failing tests**

Add tests that request `/assets/markdown-it.min.js`, assert JavaScript content and cache headers, and assert `render_index()` references only the local asset path.

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_chat_web.WebChatTests.test_web_serves_local_markdown_runtime
```

Expected: failure because the route and packaged asset do not exist.

- [ ] **Step 3: Add the packaged asset and route**

Copy the official `markdown-it` distribution and license into `src/agentbridge/assets`, include `assets/*.js` and `assets/*.LICENSE` as package data, and add a GET route that reads the asset with `importlib.resources`.

- [ ] **Step 4: Verify the targeted test passes**

Run the targeted unittest command again and expect `OK`.

### Task 2: Replace the partial Markdown renderer

**Files:**
- Modify: `src/agentbridge/web.py`
- Test: `tests/test_chat_web.py`

- [ ] **Step 1: Write failing rendering-contract tests**

Assert the page initializes `window.markdownit` with `html: false`, wraps tables in `.table-scroll`, sanitizes allowed tags and link attributes, and renders user messages through a plain-text path.

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_chat_web.WebChatTests.test_rendered_web_ui_supports_safe_offline_markdown
```

Expected: failure against the current hand-written paragraph/list renderer.

- [ ] **Step 3: Implement secure rendering**

Create a local Markdown instance, render into a detached template, remove non-allowlisted elements and attributes, enforce safe link schemes plus `rel="noopener noreferrer"`, wrap tables, and insert the sanitized fragment. Keep user messages escaped and preserve line breaks.

- [ ] **Step 4: Verify the targeted test passes**

Run the targeted unittest command again and expect `OK`.

### Task 3: Implement the reading-first workspace

**Files:**
- Modify: `src/agentbridge/web.py`
- Test: `tests/test_chat_web.py`

- [ ] **Step 1: Write failing layout tests**

Assert the generated page contains a compact navigation rail, one contextual drawer, a centered reading column, an accessible mobile drawer control, a fixed composer dock, and no permanently visible right tool rail.

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_chat_web.WebChatTests.test_rendered_web_ui_uses_reading_first_workspace
```

Expected: failure because the current page uses three permanent columns.

- [ ] **Step 3: Implement layout and drawer state**

Replace the three-column shell with rail/workspace/drawer structure, add conversation/context/tools drawer views, preserve all existing element IDs used by behavior, and add desktop/mobile responsive rules.

- [ ] **Step 4: Verify the targeted test passes**

Run the targeted unittest command again and expect `OK`.

### Task 4: Regression and visual verification

**Files:**
- Modify if defects are found: `src/agentbridge/web.py`
- Test: `tests/test_chat_web.py`

- [ ] **Step 1: Run the complete offline suite**

```bash
PYTHONPATH=src python -m unittest discover -s tests
PYTHONPATH=src python -m compileall src tests
```

Expected: all tests pass and compilation exits zero.

- [ ] **Step 2: Run browser verification**

Start a local web chat with a temporary kit and verify desktop and 375px mobile layouts. Inject representative Markdown containing `###` headings, lists, code, links, quotes, and a table; confirm semantic elements, horizontal table scrolling, drawer operation, composer visibility, and no console errors.

- [ ] **Step 3: Review the final diff**

Confirm required kit protocol paths are untouched, no CDN URLs are present, only intended files changed, and pre-existing duplicate-submit changes remain intact.
