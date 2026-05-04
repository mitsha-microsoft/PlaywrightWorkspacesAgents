# Browser Use CLI Skill

Use the `browser_use` tool with only the subcommand and arguments. Do not include the `browser-use` executable name, `--session`, or `--cdp-url`; the agent runtime adds those.

## Remote browser lifecycle

1. Create a session first with `create_remote_browser_session`.
2. The runtime attaches Browser Use to the returned CDP URL once and keeps the Browser Use daemon alive.
3. Use `browser_use` for all browser interactions in the active session.
4. Do not reconnect, recreate, or close with `browser_use`.
5. When the user asks to finish or close, call `close_remote_browser_session`.

## Core workflow

1. `open <url>` navigates to a page.
2. `state` returns the current URL, title, and numbered interactive elements.
3. Use element indices from `state` for click/input/select/hover/dblclick/rightclick.
4. Re-run `state` after navigation or UI changes.
5. Use `screenshot <path>` when visual evidence is useful.
6. Use `get title`, `get html`, `get text <index>`, `get value <index>`, or `get attributes <index>` for page data.
7. Use `eval "<javascript>"` for precise DOM inspection or extraction.

## Commands

Navigation:
- `open https://example.com`
- `back`
- `scroll down`
- `scroll up`
- `scroll down --amount 1000`

Inspection:
- `state`
- `screenshot output.png`
- `screenshot --full output.png`

Interaction:
- `click <index>`
- `click <x> <y>`
- `type "text"`
- `input <index> "text"`
- `keys "Enter"`
- `keys "Control+a"`
- `select <index> "value"`
- `upload <index> <path>`
- `hover <index>`
- `dblclick <index>`
- `rightclick <index>`

Tabs:
- `switch <index>`
- `close-tab`

Wait:
- `wait selector "css"`
- `wait selector ".loading" --state hidden`
- `wait text "Success"`
- `wait selector "h1" --timeout 5000`

Get:
- `get title`
- `get html`
- `get html --selector "h1"`
- `get text <index>`
- `get value <index>`
- `get attributes <index>`
- `get bbox <index>`

JavaScript and Python:
- `eval "document.title"`
- `eval "Array.from(document.querySelectorAll('a')).map(a => a.textContent)"`
- `python "print(browser.url)"`
- `python --file script.py`

Cookies:
- `cookies get`
- `cookies get --url <url>`
- `cookies set <name> <value>`
- `cookies clear`
- `cookies export cookies.json`
- `cookies import cookies.json`

## Patterns

Fill a form:
1. `open https://example.com/contact`
2. `state`
3. `input 0 "Jane Doe"`
4. `input 1 "jane@example.com"`
5. `click 2`
6. `wait text "Success"`

Extract data:
1. `open https://news.ycombinator.com`
2. `state`
3. `eval "Array.from(document.querySelectorAll('.titleline a')).slice(0,5).map(a => a.textContent)"`

Debug a failed interaction:
1. `state`
2. `screenshot debug.png`
3. `get html --selector "body"`
4. Retry with a more precise element index or JavaScript.
