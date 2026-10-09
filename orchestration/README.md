# Orchestration and browser testing

RIVA routes a request to a worker, or creates a validated sequential plan of up to
eight steps. Scheduling is deterministic. Each worker receives its assigned task,
the original goal, earlier outputs, and review feedback. A failed step stops its
dependents. Complex tasks are reviewed; malformed reviews fail, and one text-only
repair is allowed without replaying external actions.

## Start the local chat

From the repository root in PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-browser.txt
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD\.riva\browsers"
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe chat.py
```

The current workspace already has a local environment prepared during development.
Browser binaries under `.riva/browsers` are detected automatically when present.
An externally configured `PLAYWRIGHT_BROWSERS_PATH` takes precedence.

Set `GEMINI_API_KEY` in your local `.env` for natural-language requests. Do not
commit credentials. The model defaults to `gemini-3.5-flash-lite`; configure
`GEMINI_TEXT_MODEL`, optional comma-separated `GEMINI_FALLBACK_MODELS`, or an override
such as `GEMINI_MODEL_BROWSER`. Fallbacks are limited to three model candidates.
The CLI does not overwrite model settings. Keys from one Google project share
quota; the runtime no longer rotates keys to attempt to increase that quota.

Voice can independently use `GEMINI_LIVE_MODEL`. Existing `GEMINI_MODEL` settings
remain supported: text chat ignores a shared Live/audio model and uses its text
default. An explicit audio model in `GEMINI_TEXT_MODEL` or an agent override is
rejected locally with a configuration message instead of an API 400.

## Commands

- `status`: show key availability and model names without key values.
- `agents` / `tools`: inspect actual registered capabilities.
- `tools test`: run offline regression tests, including mocked orchestration.
- `browser test`: run real headless Chromium against local test pages.
- `browser close`: close this chat's browser.
- `clear`: clear chat context and close its browser.
- `voice`: toggle the existing optional Windows speech readout.
- `exit`: close the CLI and browser resources.

Run tests directly:

```powershell
.venv\Scripts\python.exe chat.py --self-test
.venv\Scripts\python.exe chat.py --browser-test
```

Both test commands work without a real Gemini key. The browser suite needs
Playwright and its Chromium binary. Tests authorize actions only on their own
loopback fixture and use isolated temporary workspaces.

Try a natural-language request:

> Open https://example.com in the browser and tell me the page heading.

Or call tools explicitly using JSON arguments:

```text
tool browser_start {"headed": true}
tool browser_tabs {}
tool browser_navigate {"tab_id":"ID_FROM_TABS","url":"https://example.com"}
tool browser_snapshot {"tab_id":"ID_FROM_TABS"}
tool browser_screenshot {"tab_id":"ID_FROM_TABS"}
tool browser_close {}
```

Use the actual returned IDs, not the placeholders. Browser tools share a session
across turns of the same chat. Snapshot element refs expire after an action or
navigation. Take another snapshot before interacting again.

## Execution behavior

- Tool arguments are validated. Agent capability lists are enforced and checked
  against registered tools at startup.
- Every LLM tool call retains its correlated result, success/error, and duration.
  The CLI reports failures rather than displaying an earlier successful step as
  the final answer. Tool arguments are omitted from routine progress output.
- Model requests have a timeout, SDK automatic retries are disabled, and the
  runtime never replays a prompt after a tool has been invoked. Uncertain outcomes
  require observation and reconciliation in a new task.
- The per-request default budget is 40 tool calls and 300 seconds, with a 20-call
  SDK function-calling limit. Timeouts are also applied to HTTP and browser actions.
- Files are confined to `RIVA_WORKSPACE` (repository root by default), with
  credential/control-directory paths blocked. Reads/writes are capped at 2 MB.
  Overwriting requires an explicit `overwrite: true` argument.
- Shell execution requires an authorization callback; CLI users see the exact
  command before approving it. Browser interactions that can modify a page also
  require authorization. Navigation, observation, and extraction do not prompt.
- The library defaults to denying privileged operations when no authorization
  callback is supplied. An embedding application can supply a scoped policy using
  `ExecutionPolicy` and `execution_scope`; it must make authorization decisions
  independently of model-generated instructions.
- HTTP fetches validate destinations and redirects and cap responses at 2 MB.
  Browser HTTP requests use the same destination policy; service workers and
  WebSockets are blocked in this initial version. To test a local application,
  explicitly set `RIVA_LOCAL_HOSTS=localhost,127.0.0.1` before starting the CLI.

## Browser capabilities and limits

Implemented: tabs, navigation/history, DOM snapshots including frames, click,
fill, select, keyboard, hover, scroll, text waits, extraction, screenshots,
workspace uploads, and verified download saving. The worker uses DOM observations;
screenshots are artifacts for human inspection, not visual model inputs.

This is a browser automation foundation, not a guarantee of every website task.
Passwords, MFA, and CAPTCHA require manual interaction in the visible browser.
Dialogs are dismissed automatically; dialog acceptance, canvas-only interaction,
drag-and-drop, and WebSocket-dependent apps need additional support. Downloads
must be initiated with `browser_download`; ordinary clicks do not save them.

The browser uses a fresh isolated context, not the user's personal Chrome profile.
Cookies persist within a chat and disappear when its browser closes. Task history
is bounded in memory, with no process-restart recovery or durable checkpoints yet.
Voice streaming still has its separate dispatcher; this change integrates the
new execution path into `chat.py`, not the Gemini Live audio loop.

Application path/network checks are defense in depth, not an OS sandbox. An
approved shell command runs with the current user's permissions. Browser download
sizes are not yet capped, and destination checks alone do not eliminate DNS
rebinding. Use an isolated machine/container and network egress controls before
exposing this service to untrusted remote users or hostile websites. Cancellation
is cooperative; an in-flight browser/HTTP operation may finish before its timeout.
