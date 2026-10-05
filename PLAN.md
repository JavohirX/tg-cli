# Telegram CLI — implementation plan

Keyboard-first Telegram client for Windows Terminal. Text only. No mouse required. The UX from the previous pass is the spec: list-mode letter commands, `/` to filter, `v` for range selection, Archive pinned at the top but not focused, folders on `[` `]`, accounts on F2, transcripts under `[voice]`, undo toasts, inline `y/n` for irreversible deletes.

Do not write the UI against live Telegram. Build the interaction on a fake gateway, then connect Telethon behind that boundary.

## Locked decisions

- Python 3.12+, Textual, Telethon 1.x (PyPI `telethon`, currently 1.45, developed on Codeberg). Our own SQLite cache. Telethon’s session file stays separate from that cache.
- TDLib is out of v1. `python-telegram` does not ship Windows wheels. A dedicated Telethon thread gives us a responsive UI without a C++ library.
- One process. Textual owns the main asyncio loop. Telethon runs on a second thread with its own loop. They talk through queues. This sidesteps a real bug: Textual runs `create_task` immediately, and Telethon’s send/recv loops then exit before `_user_connected` is set (Telethon issue 4721, Textual issue 6271).
- Screens never import Telethon. They talk to a `Gateway` protocol and the SQLite store.
- Chat and message lists are one windowed list widget. Textual `ListView` builds a widget per row and gets sluggish in the hundreds. Ten thousand chats need a cursor index plus a handful of rendered lines.
- Supported host is Windows Terminal. Tests that prove key delivery there are manual. Headless Textual tests prove the state machine.
- Secrets live in `%APPDATA%\tg-cli\`. Never in the repo. The user supplies their own `api_id` and `api_hash` from my.telegram.org.

## What v1 includes

Persisted accounts (phone, code, 2FA). Chat list with archive, pins, folders, mute, unread, read/unread, delete-for-me. Message view with textual placeholders, replies, edits, drafts, send, in-chat search, palette, help, on-demand voice transcription.

## What v1 leaves out

Image rendering, calls, stories, secret chats, inline bots, sending reactions, forwarding, global message search, QR login, a packaged `.exe`. QR and forward are the first follow-ups. Mouse may work where Textual does it for free. No mouse-only action.

Existing terminal clients (`tele` in Go, `tg` on TDLib) are reference points, not the codebase. Their UX is not this one.

## Architecture

```text
Textual screens  -->  store (SQLite)  -->  painted rows
       |                   ^
       | commands          | upserts
       v                   |
   command queue  -->  Telethon thread  -->  Telegram
                            |
                            +--> event queue --> UI (call_from_thread)
```

Rules:

- The UI thread never waits on the network and never touches a `TelegramClient`.
- The Telethon thread never touches Textual widgets. It upserts SQLite, then posts a small event (`chats_changed`, `messages_changed`, `status`, `auth`).
- UI refresh is coalesced to about 20 Hz. A burst of updates becomes one repaint.
- Flood waits are caught in the gateway and posted as `rate limited 23s`. The library must not `sleep` on the UI thread. A sleep on the Telethon thread is acceptable.
- Shutdown flushes pending undoable deletes, disconnects clients, and stops the thread.

Command objects are plain data: `Send`, `Edit`, `Delete`, `Archive`, `Mute`, `Read`, `LoadHistory`, `Transcribe`, `SwitchAccount`. The UI enqueues them and paints the optimistic result immediately.

## Repository layout

```text
tg_cli/
  __main__.py              # python -m tg_cli
  app.py                   # Textual app, focus region, esc stack, status, confirm
  paths.py                 # %APPDATA%\tg-cli
  commands.py              # command table: id, title, keys, contexts, undoable
  domain/models.py         # Account, Chat, Message, Draft, Transcript
  domain/render.py         # Message -> lines (placeholders, entities, width)
  store/db.py              # schema + migrations
  store/repo.py            # queries used by screens
  telegram/gateway.py      # Protocol
  telegram/fake.py         # in-memory gateway for tests and the first UI milestone
  telegram/telethon_gw.py  # only module allowed to import telethon
  telegram/worker.py       # thread, loop, queue, N clients
  telegram/map.py          # Telethon objects -> domain models
  ui/window.py             # windowed list
  ui/chrome.py             # account strip, folder strip, footer, status
  ui/screens/              # accounts, login, chats, messages, help, palette
  ui/composer.py
  transcribe/base.py
  transcribe/gemini.py
tests/
```

`commands.py` is the only shortcut list. The footer, the help screen, and the palette all read it. A shortcut string does not get copied into a second place.

## Data model

One SQLite file, WAL mode, `app.db`. Telethon sessions live in `sessions\<user_id>.session`.

- `accounts` — user id, phone, username, display name, session path, auth state (`ok`, `expired`, `needs_2fa`).
- `folders` — account, folder id, title, position. `All` and `Unread` are synthetic and not stored.
- `chats` — account, chat id, title, kind (`user`, `group`, `channel`, `bot`), unread, muted, pinned, pin rank, archived, last message id, last date, last preview.
- `chat_folders` — which stored folders contain the chat.
- `messages` — account, chat, message id, sender id, sender name, date, kind, plain text, caption, entity spans (JSON), reply id, forward label, edited flag, outgoing flag, send state (`sent`, `queued`, `failed`).
- `transcripts` — account, chat, message id, text, model, error, created at.
- `drafts` — account, chat, text, reply-to id, edit message id.
- `history_window` — account, chat, oldest loaded id, newest loaded id.

Media bytes are not stored. A voice file is downloaded to a temp path, sent to the transcriber, then deleted.

Sort for the main list: the Archive entry is a fake row painted above the query. Then pinned chats by pin rank. Then everyone else by last-message date. Unread does not float. Muted rows are dim. The cursor’s first stop is the first real chat.

Message identity is `(account_id, chat_id, message_id)`. Selection stores those ids, so scrolling does not drop it. Changing folder or filter clears the selection.

Cache policy: keep every chat row (titles are small). Keep a window of messages per open chat, about 300 in memory. SQLite may keep everything fetched. The UI never loads a million rows into widgets.

## Windowed list

This is the first thing to build, and the riskiest UI code.

- Backing data is a sequence of row models, not widgets.
- The widget measures the viewport and renders visible rows plus a few lines of overscan.
- Chat rows are one line, truncated by display columns (emoji and Cyrillic count as two).
- Message rows are variable height, clamped to four lines until `z` expands the focused message. Heights are recomputed on resize.
- Cursor is an integer. Date separators and the unread divider are rows the cursor skips.
- Keys, all in list focus: `j`/`k` and arrows (1), Ctrl+U / Ctrl+D (half page), PageUp / PageDown (page), Home / End, a digit prefix then `j`/`k`.
- `v` sets an anchor; the next motions extend a range. Space toggles one id. `V` selects the current filter, and asks `y/n` when that would select more than 50 chats. Esc clears selection before it navigates back.
- `10_000` fake chats must stay responsive. That test is the gate for phase 0.

Do not bind Alt+Arrow, Shift+Arrow, Ctrl+Tab, Ctrl+S, or Alt+Enter. Windows Terminal already uses them.

## Focus, Esc, composer

Two regions, `LIST` and `WRITE`. Tab moves between them. The footer names the region.

Letter commands run only in list focus. The composer is a Textual text area: letters type, Enter sends, Ctrl+J inserts a newline. Also honor Shift+Enter when the host delivers it as a distinct key. A paste inserts text and does not send. Empty Enter does nothing. Over 4096 characters, Enter refuses and the counter says why.

Esc pops one layer: mention popup, palette, help, filter, selection, reply/edit mode, composer focus, parent screen. Draft text is kept. Esc quits only on the account screen. Ctrl+C cancels the in-flight command; a second Ctrl+C within a second quits and flushes state.

`y` copies the logical text via Textual’s clipboard API. `o` opens the focused link. `F2` / `Shift+F2` switch accounts in place.

Phase 0 includes one manual Windows Terminal check for Ctrl+J, Ctrl+K, F2, PageUp, and paste. If Ctrl+J arrives as Enter, the fallback is a trailing `\` then Enter, and the footer says so. Do not discover this after the composer is finished.

## Telethon worker

- Start the thread in `on_mount`. Stop it on shutdown.
- One `TelegramClient` per authenticated account, all on that thread’s loop.
- On launch, connect every saved account. Paint cached chats at once. Status shows `syncing` until the first dialog page lands, then stays usable.
- Dialog sync pages through `iter_dialogs`, upserting about 100 chats at a time, posting `chats_changed` after each page. The list is never replaced by a spinner.
- Opening a chat loads the newest page (about 50) if the window is cold, then older pages when the cursor hits the top. The cursor does not jump when older rows are inserted above.
- Events handled: new message, edit, delete, read state, dialog pin/mute/archive. Map them in `telegram/map.py` and upsert.
- `flood_sleep_threshold` stays small so `FloodWaitError` becomes a status line.
- Session expired: mark that account, leave the others connected. Enter on that row starts login again.
- Login is phone, then code, then 2FA password if required. Errors stay under the field. QR is a later command on the same screen, not part of the v1 acceptance bar.

Optimistic send: insert a local row with a negative temporary id and state `queued`, enqueue `Send`, replace the row with the real id on success, or mark `failed` and keep the text. Enter on a failed row retries.

Delete-for-me: remove the rows in the UI immediately, and delay the API call by 5 seconds. `u` restores them and cancels the call. Quitting inside that window flushes the delete. More than 10 chats, and any “delete for everyone”, ask `y/n` on the status line first. There is no undo for delete-for-everyone. Archive, mute, and mark-read apply immediately and are undoable with the same 5 second toast.

## Rendering messages

`domain/render.py` is pure and tested without Textual.

| Kind | Lines |
|---|---|
| text | entity-rendered body (bold, italic, reverse code, dim code block, spoiler hidden until `z`) |
| photo, video, gif, sticker | `[photo]`, `[video]`, `[gif]`, `[sticker]` plus caption if any |
| voice | `[voice m:ss]`, then the transcript line when one exists |
| document | `[file] name` |
| location, contact, poll | `[location]`, `[contact] name`, `[poll] question` |
| anything else | `[message]` |

A reply is one dim quoted line. A forward is one dim `fwd from …` line. `edited` is a dim suffix. A message deleted while on screen becomes a dim `deleted` row.

Transcription state is local: missing, `transcribing…`, text, or `transcription failed`. `t` starts it. `T` runs it again. The `[voice]` line stays. Nothing is written back to Telegram. The transcriber is an interface; Gemini is the first implementation, model name in config, API key in config, neither logged.

Mentions in v1 complete against senders already present in the loaded window. A full member list is a follow-up.

## Screens and the order to build them

Each phase leaves a running program. Do not start a phase by stubbing the next one.

### Phase 0 — Shell and the list

Fake gateway with 10k chats and a few hundred messages. Account strip, folder strip, status, footer. Windowed list, selection, Esc stack, dummy composer, palette and help reading `commands.py`.

Done when: pilot tests cover `j`/`k`, page and half-page, digit prefix, `v` then `j`, Space, Esc order, Tab into the composer, Enter versus newline. A manual Windows Terminal pass confirms the keys above. The 10k-row list scrolls without building 10k widgets.

### Phase 1 — Login and persisted accounts

Config file for `api_id` / `api_hash` on first launch. Account screen. Worker thread. Phone, code, 2FA. Relaunch opens the same accounts without asking again. A bad code stays on the form. An expired session is a label on that row.

Done when: a real account connects, `get_me` succeeds, the process restarts still authenticated, and a second account can be added. Log file contains no code and no message text.

### Phase 2 — Chat list from Telegram

Schema and dialog sync. Compact row: pin, kind glyph, title, preview, time, unread. Archive row, pins, dim muted rows, folders, `/` filter, F2 switch. Cursor starts on the first real chat. Cached rows show before sync finishes.

Done when: a large account paints the first page immediately and keeps accepting `j`/`k` while later pages arrive. Filter and folder changes clear the selection.

### Phase 3 — Messages

Open a chat. Variable-height rows, separators, unread divider, history when scrolling up, live inserts. If the viewport is pinned to the bottom, new messages append and the chat is marked read. If the user has scrolled up, show `↓ N new` and do not move the cursor. End jumps there.

Done when: holding `k` at the top loads older messages once, without duplicating rows or jumping the cursor. A new message in another chat changes its unread count and does not steal focus.

### Phase 4 — Send, reply, edit, drafts

Composer rules from the focus section. Drafts restored per chat and shown in the preview column. Reply and edit context line. Failed send retry. 4096 limit.

Done when: pilot tests cover send, newline, paste-does-not-send, and draft retention, and a manual send to Saved Messages round-trips.

### Phase 5 — Bulk actions

`a`, `m`, `r` / `R`, `d`, `u`, status-line `y/n`. Operations hit the selection when it is non-empty, otherwise the cursor row.

Done when: deleting four chats can be undone inside 5 seconds, deleting 11 asks first, and a muted archived chat survives a restart.

### Phase 6 — Search, palette, help

`/` filters the current chat list or the loaded messages. In-chat search also queries Telegram for messages outside the window and jumps the cursor to the hit. Palette is contextual and shows shortcuts. `?` and F1 open help for this screen. First visit shows `press ? for keys` until help has been opened.

Done when: every command in `commands.py` appears in the palette for its context, and a destructive palette entry asks `y/n` when the command says it is irreversible.

### Phase 7 — Transcription

Download the voice, call Gemini, store the transcript, paint the states. Failures stay on the row and `t` retries.

Done when: a second `t` on the same message does not call the network again. `T` does. The temp audio file is gone afterward.

### Phase 8 — Failure paths

Offline status and queued sends. Rate-limit status. Database open failure as one full-screen message with the path. Unavailable chat as a status line. `--debug` raises log level. README: Windows Terminal, how to create `api_id`, where files live, what v1 does not do.

## Testing

- Pure tests: render table, chat sort, selection set, undo deadline with a fake clock, 4096 check, Esc-stack reducer.
- Store tests: temp SQLite, upsert, restart, folder filter.
- Textual pilot tests against `FakeGateway` for every phase’s keymap.
- One optional live test, skipped unless an env var is set: login is manual, the test only checks `get_me` and fetching one dialog page.

No test should require a real phone. The live check is a checklist, not CI.

## Manual checklist (Windows Terminal)

Run after phase 0 and again after phase 4.

- Ctrl+J inserts a newline. Enter sends.
- Ctrl+K opens the palette. A typed query narrows it. Esc closes it.
- F2 switches accounts and does not move the Windows Terminal tab.
- PageUp moves the list, not the terminal scrollback.
- Paste of several lines sits in the composer.
- A Cyrillic title and an emoji do not wrap the row.
- Alt+Arrow still moves Terminal panes. We do not bind it.

## Risks

| Risk | What we do |
|---|---|
| Textual and Telethon on one loop | Separate thread and loop. Phase 1 proves `get_me` before any list work depends on it. |
| Telethon’s GitHub repo is archived | Pin `telethon` from PyPI. Development continues on Codeberg. A Telegram layer break is an upstream bump, not a rewrite. |
| Windowed list takes longer than the screens | It is phase 0. Later screens reuse it. |
| Initial sync of a huge account | Page the upserts. Paint cache first. Never block the UI thread. |
| Ctrl+J indistinguishable on some hosts | Phase 0 manual check. Fallback is `\` then Enter. |
| Session files are full account keys | `%APPDATA%` only. Gitignore config, `*.session`, and `app.db`. Logs omit message bodies and login codes. |

## First implementation session

Phase 0 only: project metadata, the command table, the windowed list, the fake gateway, the account and chat screens with fake rows, pilot tests, and the Windows Terminal key checklist. No Telethon import until phase 1.
