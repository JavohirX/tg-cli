# tg-cli

> A keyboard-first, terminal Telegram client built specifically for Windows Terminal with a clean two-thread architecture.

`tg-cli` provides an efficient, keyboard-driven interface for Telegram power users. Built on **Textual 8.2+** and **Telethon 1.42+**, it isolates the UI from network latency using a dedicated background worker thread and SQLite WAL storage.

---

## Key Highlights

- **Windowed Virtual Viewport**: Navigates 10,000+ chats and extensive message histories smoothly without creating per-row widgets.
- **Two-Thread Architecture**: Textual runs the async UI event loop on the main thread; Telethon runs its own loop on a background thread. Screens communicate with SQLite and thread-safe queues—never importing Telethon directly.
- **Vim-Inspired Motions**: `j`/`k`, count prefixes like `5j` or `12k`, half-page jumps (`d`/`u`), `g`/`G`, and visual selection (`v`, `space`, `V`).
- **Bulk Actions with 5-Second Undo**: Multi-select chats to archive (`a`), mute (`m`), mark read/unread (`r`/`R`), or delete (`d`) with an inline 5-second countdown toast (`Press 'u' to undo`).
- **Voice Transcription with Gemini**: Press `t` on voice messages to transcribe using Google Gemini. Transcriptions are cached in SQLite; press `T` to force re-transcription. Temp audio files are automatically wiped.
- **Command Palette & Contextual Help**: `Ctrl+K` opens a searchable command palette showing all actions. Destructive actions require inline `y/n` confirmation. Press `?` or `F1` for contextual cheat sheets.
- **Multi-Account Support**: Switch accounts seamlessly with `F2` / `Shift+F2`. Sessions persist authenticated between restarts.

---

## Windows Terminal Setup

`tg-cli` is tuned for **Windows Terminal** on Windows 10/11.

### Recommended Settings
1. **Font**: Use a font with full Unicode glyph and emoji coverage, such as:
   - *Cascadia Code* / *Cascadia Mono*
   - *JetBrains Mono Nerd Font*
2. **Keybindings**: Windows Terminal defaults `Ctrl+Shift+T` to new tab and `Alt+Arrow` to pane focus. `tg-cli` deliberately avoids colliding with these.
3. **Paging**: `PageUp` and `PageDown` scroll the chat/message list inside the client rather than the Windows Terminal terminal scrollback buffer.

---

## Telegram API Credentials

To connect to Telegram, obtain an `api_id` and `api_hash`:

1. Sign in with your phone number at [https://my.telegram.org](https://my.telegram.org).
2. Go to **API development tools**.
3. Create a new application (e.g. app name: `tg-cli`, platform: `Desktop`).
4. Note your numeric **`api_id`** and 32-character hex **`api_hash`**.

### Configuration

You can provide credentials via environment variables:

```powershell
$env:TG_API_ID = "1234567"
$env:TG_API_HASH = "0123456789abcdef0123456789abcdef"
$env:GEMINI_API_KEY = "AIzaSy..." # Optional, for voice transcription
```

Or run `tg-cli` directly—it will prompt for your credentials on first launch and store them safely in `%APPDATA%\tg-cli\config.json`.

---

## Filesystem Layout

Secrets, database files, and logs live strictly inside the user's roaming AppData directory and are never written to the git repository:

```text
%APPDATA%\tg-cli\
├── config.json          # api_id, api_hash, gemini_api_key, and user preferences
├── app.db               # SQLite database in WAL mode (chats, messages, drafts, transcripts)
├── app.db-wal           # SQLite write-ahead log
├── tg-cli.log           # Application logs (scrubbed of codes and message text)
└── sessions/            # Telethon SQLite session files (one per user_id)
    └── 123456789.session
```

---

## Keyboard Shortcuts

### Global
| Key | Action |
|---|---|
| `Ctrl+K` | Open Command Palette |
| `?` or `F1` | Show keyboard help for current screen |
| `F2` / `Shift+F2` | Switch to next / previous authenticated account |
| `Esc` | Back, clear selection, dismiss popup, or defocus |
| `Ctrl+C` | Cancel in-flight action; double-tap within 1s to quit |

### List Navigation (Chats & Messages)
| Key | Action |
|---|---|
| `j` / `↓` | Move cursor down |
| `k` / `↑` | Move cursor up |
| `5j` / `10k` | Jump by count prefix |
| `d` / `u` | Half-page down / up |
| `PageDown` / `PageUp` | Full-page down / up |
| `g` / `Home` | Jump to top (triggers older history load in chat) |
| `G` / `End` | Jump to bottom |
| `v` | Start visual range selection anchor |
| `Space` | Toggle selection of current item |
| `V` | Select all items in view (confirms if > 50) |

### Chat List Actions
| Key | Action |
|---|---|
| `Enter` | Open selected chat |
| `/` | Filter chats by name or preview |
| `[` / `]` | Switch folder tabs (All, Personal, Work, Archive) |
| `a` | Toggle Archive (undoable 5s) |
| `m` | Toggle Mute (undoable 5s) |
| `r` | Mark as read (undoable 5s) |
| `R` / `Shift+R` | Mark as unread (undoable 5s) |
| `d` | Delete chat for me (confirms if > 10, undoable 5s) |
| `u` | Undo last action within 5 seconds |

### Messages & Composer
| Key | Action |
|---|---|
| `Tab` | Toggle focus between Message List `[LIST]` and Composer `[WRITE]` |
| `Enter` | Send message (when in composer) or retry failed message (when on list) |
| `Ctrl+J` / `\`+Enter | Insert newline in composer |
| `r` | Reply to focused message |
| `e` | Edit focused message (if sent by me) |
| `d` | Delete focused message(s) |
| `t` | Transcribe voice message with Gemini (cached) |
| `T` | Force re-transcribe voice message (bypasses cache) |
| `y` | Copy message text to system clipboard |
| `z` | Toggle message clamping (expand/collapse > 4 lines) |
| `/` | Search loaded messages or query older history from Telegram |
| `Esc` | Clear reply/edit context, defocus composer, or return to chat list |

---

## Running tg-cli

### Standard Run
```powershell
py -m tg_cli
```

### In-Memory Demo Mode (10,000 Chats)
Runs immediately without network access or Telegram credentials:
```powershell
py -m tg_cli --fake
```

### Debug Logging
Raises log level to `DEBUG` and streams detailed output to `%APPDATA%\tg-cli\tg-cli.log`:
```powershell
py -m tg_cli --debug
```

---

## Running Tests

Run the complete test suite:
```powershell
py -m pytest
```

---

## Scope & Limitations (What v1 Does Not Do)

To ensure terminal reliability and high performance, v1 focuses strictly on text and voice workflows:
- **No Voice/Video Calls**: Telegram calls require WebRTC codecs outside the terminal scope.
- **No Secret Chats**: MTProto end-to-end encrypted secret chats are device-specific and not supported by Telethon.
- **No Animated Sticker/Video Playback**: Media renders as contextual placeholders (e.g. `[sticker]`, `[photo]`, `[video]`).
- **Text & Voice Only**: Sending media attachments (photos, videos, arbitrary files) is deferred to future releases.
