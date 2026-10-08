\# Task: Design the complete UI/UX for a keyboard-first Telegram CLI



We are designing a \*\*Windows terminal-based Telegram client\*\*.



Your task is to think through and specify the \*\*entire UI/UX system\*\* before implementation begins.



Do not focus on implementation details yet. Think like a senior terminal UX designer / interaction architect designing a real product that must remain usable when the user has thousands of chats and messages.



\## Core product philosophy



This is \*\*not Telegram Desktop recreated in a terminal\*\*.



The product should be:



> A fast, keyboard-first Telegram client where Telegram content is reduced to a textual representation and every important operation can be performed without a mouse.



The mouse should be completely unnecessary. Keyboard navigation is the primary interaction model.



The interface should feel fast, predictable, information-dense, and consistent with good terminal/TUI applications.



\---



\# Decisions already made



Do not redesign these fundamental decisions unless you identify a serious UX problem and explicitly explain it.



\## 1. Multiple Telegram accounts



On startup, the user sees their authenticated Telegram accounts.



Example:



```text

Telegram CLI



Accounts



> @alice

&#x20; @work

&#x20; @business



Enter   Open account

A       Add account

D       Remove account

Esc     Exit

```



The user selects an account and enters that account's chat list.



Accounts should remain authenticated between launches.



\---



\# 2. Chat list



After selecting an account, show the chat list.



The \*\*Archive entry is always at the top\*\*.



Example:



```text

@alice



> \[Archive]

&#x20; Alice                         2

&#x20; Development Team             14

&#x20; Bob

&#x20; News Channel                  3

&#x20; Family                        7

```



The user navigates through chats primarily with the keyboard.



\---



\# 3. Keyboard-first navigation



Mouse interaction is not required.



Normal navigation:



```text

↑ / ↓

```



moves one item.



Fast navigation should exist because moving through hundreds/thousands of chats one at a time is inefficient.



For example:



```text

Alt + ↑ / Alt + ↓

```



moves several chats at once.



Other useful navigation primitives should be considered:



```text

Home

End

PageUp

PageDown

Ctrl + ...

Alt + ...

```



Determine the most coherent complete navigation scheme.



The jump size should ideally be configurable rather than arbitrarily hardcoded.



\---



\# 4. Range selection



The user must be able to select multiple chats using the keyboard.



For example:



```text

Shift + ↓

Shift + ↓

Shift + ↓

```



selects a range.



The selected chats can then receive bulk operations.



Required bulk operations include:



\* Delete

\* Archive

\* Mark as read

\* Mute

\* Unmute



Consider whether other bulk operations make sense.



Selection should be visually obvious.



The interaction should feel similar to selecting files in a file manager or selecting lines in an editor.



\---



\# 5. Message representation



The application is fundamentally \*\*text-first\*\*.



Telegram message types should be normalized into textual representations.



Examples:



```text

Text       → actual text

Photo      → \[photo]

Video      → \[video]

Voice      → \[voice]

Document   → \[file]

Sticker    → \[sticker]

GIF        → \[gif]

Location   → \[location]

Contact    → \[contact]

Other      → appropriate textual placeholder

```



The UI should not attempt to render images, video, stickers, etc.



The original Telegram message type should still exist internally, but the UI primarily deals with its textual representation.



\---



\# 6. Voice transcription



Voice messages are NOT automatically transcribed.



Initially:



```text

\[voice]

```



When the user activates/selects the voice message, the application can download the audio and send it to a transcription service such as Gemini.



Then:



```text

\[voice]

```



becomes something like:



```text

I'll probably be home around eight.

```



or otherwise displays the transcription associated with the message.



The exact interaction needs to be designed carefully.



Consider:



\* What key activates transcription?

\* Should Enter trigger it?

\* Should there be a dedicated `T` shortcut?

\* What happens while transcription is running?

\* What happens if transcription fails?

\* Should the transcription replace `\[voice]`, appear underneath it, or toggle?

\* Should the transcription be cached?

\* How does the user know it has already been transcribed?

\* Can the user re-transcribe?

\* Should transcription be a local annotation rather than changing the original Telegram message?



\---



\# 7. Chat/message hierarchy



The basic navigation is:



```text

Account List

&#x20;    ↓

Chat List

&#x20;    ↓

Message View

```



`Esc` should generally move back one level.



Opening a chat should show a message-oriented interface.



Example:



```text

Development Team



Alice    14:20

Does anyone know how this works?



Bob      14:21

I think it's in the documentation.



Alice    14:22

\[photo]



Bob      14:23

\[voice]



> \_

```



Messages must also be keyboard navigable.



\---



\# 8. Fast message navigation



The same principle used in the chat list should apply to messages.



Consider:



```text

↑ / ↓

Alt + ↑ / Alt + ↓

PageUp

PageDown

Home

End

```



The user may be navigating through thousands of messages, so the UX must remain efficient.



\---



\# 9. Context-sensitive commands



Do not create one enormous universal keyboard shortcut list.



Commands should depend on the current context.



For example:



\### Account list



```text

Enter   Open

A       Add account

D       Remove account

Esc     Exit

```



\### Chat list



Potential operations:



```text

Enter   Open chat

A       Archive

D       Delete

R       Mark read

M       Mute

U       Unmute

S       Search

```



\### Message view



Potential operations:



```text

Enter   Activate/select

R       Reply

E       Edit

D       Delete

T       Transcribe

```



These are examples, not necessarily final decisions.



Evaluate the complete shortcut system and make it internally consistent.



Avoid shortcuts that create excessive conflicts.



\---



\# 10. Command palette



A command palette is strongly desired.



Something like:



```text

Ctrl + K

```



opens:



```text

> archive



Archive selected chats

Archive current chat

```



The user should be able to search commands rather than memorizing every shortcut.



Design how this interacts with:



\* current context

\* selected items

\* search

\* keyboard navigation

\* destructive actions

\* command discovery



\---



\# Your task



Now take the above product concept and design the \*\*complete UI/UX specification\*\*.



Think deeply about the interaction model rather than simply expanding the examples.



\## Specifically design:



\### A. Global navigation model



Define:



\* all major application states

\* how users move between states

\* Back behavior

\* focus behavior

\* selection behavior

\* modal behavior

\* loading states

\* error states



\---



\### B. Keyboard system



Create a coherent keyboard vocabulary.



Cover:



\* movement

\* fast movement

\* selection

\* multi-selection

\* opening

\* closing

\* sending

\* editing

\* deleting

\* replying

\* forwarding if appropriate

\* marking read/unread

\* mute/unmute

\* archive/unarchive

\* search

\* account switching

\* command palette

\* help

\* quitting



Avoid arbitrary shortcuts.



Prefer patterns that users can learn.



Explain conflicts and how you resolve them.



\---



\### C. Chat list UX



Think through:



\* unread counts

\* muted chats

\* archived chats

\* pinned chats

\* folders

\* channels

\* groups

\* private chats

\* bots

\* search results

\* sorting

\* selection

\* bulk actions

\* context menus/palettes

\* chat previews

\* last-message previews

\* timestamps

\* extremely large chat lists



Determine what information should be visible without overwhelming the terminal.



\---



\### D. Message view UX



Design:



\* message navigation

\* sender display

\* timestamps

\* replies

\* edited messages

\* deleted messages

\* unread boundaries

\* date separators

\* long messages

\* links

\* mentions

\* code blocks

\* formatting

\* forwarded messages

\* media placeholders

\* voice messages

\* transcription

\* message selection

\* bulk operations where appropriate



Consider how Telegram's rich formatting should be represented in a terminal.



\---



\### E. Composition/input UX



Design the message composer.



Think about:



\* multiline messages

\* Enter behavior

\* sending

\* newlines

\* editing

\* canceling

\* drafts

\* replies

\* mentions

\* commands

\* very long messages

\* pasted text

\* Unicode

\* terminal-specific keyboard conflicts



This is especially important because the product is keyboard-first.



\---



\### F. Search



Design a powerful search system.



Consider separate or unified search for:



\* chats

\* users

\* messages

\* current chat

\* all chats



Think about:



\* keyboard navigation

\* filters

\* dates

\* sender

\* unread

\* media type

\* search result preview

\* jumping directly to a message



\---



\### G. Bulk operations



Fully design the selection model.



Determine:



\* range selection

\* individual toggling

\* select all

\* deselect all

\* reversing selection

\* selection across pages/screens

\* bulk archive

\* bulk delete

\* bulk mute

\* bulk read/unread



Make sure the model remains intuitive.



\---



\### H. Notifications



Design how new messages should behave while the user is:



\* in the chat list

\* inside another chat

\* searching

\* typing

\* inside a modal

\* away from the application



Consider terminal notifications, unread indicators, and whether notifications should interrupt the current interaction.



\---



\### I. Account switching



Design fast account switching.



The user should not have to repeatedly return to the startup screen just to switch accounts.



Consider a shortcut such as:



```text

Ctrl + ...

```



and a quick account switcher.



\---



\### J. Performance UX



Assume:



\* 10,000+ chats

\* millions of messages

\* slow network

\* temporary Telegram API failures

\* large message history

\* long-running synchronization



The UI should never feel frozen.



Design loading indicators, synchronization states, and partial results.



\---



\### K. Error handling



Define UX for:



\* Telegram authentication failure

\* expired session

\* network failure

\* rate limits

\* message send failure

\* delete failure

\* transcription failure

\* database failure

\* invalid command

\* unavailable chat

\* permission errors



Errors should be useful without becoming noisy.



\---



\### L. Discoverability



A keyboard-first application has a major UX problem: users don't know the shortcuts.



Design:



\* help screen

\* contextual help

\* command palette

\* shortcut hints

\* status bar

\* first-run experience



The UI should be learnable without reading a manual.



\---



\# Important design constraints



1\. \*\*Keyboard first.\*\*

2\. Mouse must never be required.

3\. Avoid modal dialogs whenever an inline interaction can work better.

4\. Keep the UI information-dense but readable.

5\. Optimize for fast navigation through very large lists.

6\. Avoid excessive decoration.

7\. Do not copy Telegram Desktop's UI literally.

8\. Design for terminal limitations.

9\. Keep interaction patterns consistent across chat lists and message lists.

10\. Prefer composable keyboard primitives over dozens of unrelated shortcuts.

11\. Destructive actions must have appropriate confirmation or undo behavior.

12\. Loading states must never make the application feel frozen.

13\. The interface should work well on ordinary Windows Terminal dimensions, not just very large terminals.



\# Deliverable



Produce a \*\*complete UI/UX design document\*\*, not code.



Structure it as:



1\. Product interaction philosophy

2\. Information architecture

3\. Application states

4\. Navigation model

5\. Keyboard specification

6\. Account selector

7\. Chat list

8\. Selection system

9\. Message view

10\. Message composition

11\. Search

12\. Command palette

13\. Voice transcription

14\. Notifications

15\. Account switching

16\. Loading/error states

17\. Help/discoverability

18\. Large-data/performance UX

19\. Accessibility/terminal constraints

20\. Complete example user flows

21\. Recommended final keymap

22\. Open UX questions / decisions that should be made before implementation



For every important interaction, provide concrete terminal examples.



Do not write implementation code unless a tiny pseudocode example is necessary to explain an interaction.



Be critical. If one of the existing design decisions is likely to cause UX problems, identify the problem and propose an alternative, but do not silently change the product concept.



The goal is to produce a specification that another engineering agent could use as the authoritative UX reference when implementing the application.





---

# Session Summary & Continuation Context (Date: 2026-10-07)

## 1. Overview & Context
This session focused on fixing column misalignments in the chat list, adjusting timestamps to the user's local system clock, implementing quoted replies in the message view, and diagnosing/solving the root cause of emoji-induced layout drift on Windows.

---

## 2. Key Features & Fixes Implemented

### A. Local System Clock Adjustment
- **Problem**: Message and chat timestamps were rendered in raw UTC (`+00:00`).
- **Fix**: Added `to_local_datetime()` in `tg_cli/domain/render.py` using `.astimezone()` to automatically adjust datetimes to the user's local system timezone. Naive datetimes from Telethon are localized cleanly.

### B. Quoted Message Duplication (`>` prefix)
- **Problem**: When a message was a reply to another message, it only showed a small generic label `↳ reply to #id`.
- **Fix**: In `render_message()` (`tg_cli/domain/render.py`), when `replied_message` is provided, the message body is duplicated, indented, prefixed with `> [Sender Name]: [Body]`, and styled in dim cyan, matching terminal email/quote conventions. Falls back gracefully if the message is uncached.

### C. Restricted Preview Panel & Dynamic Space Padding
- **Problem**: Previews extended directly up to the timestamp, causing previews with emojis or media tags (`[photo]`, `[file]`) to visually collide with and push the timestamp rightward.
- **Fix**:
  - Implemented user-suggested panel restriction: `preview_max_width = max(6, total_middle - title_width - buffer_gap)`, reserving a guaranteed whitespace buffer before the right columns.
  - Appended dynamic space padding `line.append(" " * pad_needed)` so all variable blank space is filled with spaces.
  - Hours column (`time_width = 5`) and unread badge (`unread_width = 5`) are rigidly anchored to the exact right edge of the window.

### D. The Emoji & Chat Title Width Drift Mystery (Root Cause & Solution)
- **User Discovery**: 
  - 3 prayer hands (`🙏🙏🙏`) in previews or emojis in chat names (`Mom ❤️`, `Crypto 🚀`) caused timestamps to shift rightward or leftward across different rows despite lots of spaces between them.
  - Realized that **chat titles and UI kind glyphs** also contain emojis and symbols, which were compounding the misalignment across the entire row from left to right.
- **Root Cause Identified via Windows Console API**:
  - In terminal emulators, there are no fixed horizontal coordinates in a line string. A row is drawn sequentially:
    `[Prefix] -> [Glyph] -> [Title] -> [Preview] -> [Spaces] -> [Time] -> [Unread]`
  - If the terminal font rasterizer and Python's cell-width engine disagree by even 1 cell on any emoji, that error offsets the cursor before spaces are drawn, pushing everything to the right of it.
  - Probed the live Windows console via `CONOUT$` and `GetConsoleScreenBufferInfo`:
    - Classic **Windows Console Host (`conhost.exe` / `cmd.exe` / classic PowerShell)** advances the cursor by **1 CELL** for SMP emojis (`🙏`, `🚀`, `👤`, `📢`).
    - BMP presentation emojis (`❤️`, `⚡`, `⭐`) advance by **2 CELLS**.
    - Modern terminals (Windows Terminal `wt.exe`, VS Code) advance by **2 CELLS** for all emojis.
- **User Discovery & Bug Report**: 
  - User reported that replacing the chat type markings broke the aesthetic ("it actually broke everything, the problem is not our marking of that chat type, bring that back").
  - The real culprit was chat names with compound emojis like `"students 👨‍🎓"` or `"students 👩‍🎓"`, which broke the message preview offset and consequently the timing alignment.
- **Root Cause Identified (ZWJ Compound Emojis Mismatch)**:
  - The student emoji `👨‍🎓` is a Zero-Width Joiner (ZWJ) sequence: Man (`👨`) + ZWJ (`\u200d`) + Graduation Cap (`🎓`).
  - Our custom cluster width calculation was treating the whole sequence as **1 cell**.
  - Meanwhile, Rich and Textual's layout engine (`cell_len`) calculates each emoji component separately: $2 + 0 + 2 = \mathbf{4\text{ cells}}$.
  - Because our code undercounted the title by 3 cells, it added 3 extra spaces of padding into the title column!
  - This pushed the preview column from column 27 to column 30, and pushed the timestamp column from column 69 to column 72!
- **Solution Implemented**:
  1. **Restored Original Chat Glyphs**:
     - Kind glyphs restored to: User (`👤`), Channel (`📢`), Group (`👥`), Bot (`🤖`).
     - Status icons restored to: Pinned (`📌`), Muted (`🔇`).
  2. **Harmonized cell_width with Rich/Textual (`cell_len`)**:
     - Switched `cell_width(text)` to delegate directly to `cell_len(text)`.
     - Ensures 100% synchronization between our title/preview space padding and Textual's widget layout engine.
     - Titles with compound student emojis (`students 🎓`, `students 👨‍🎓`, `students 👩‍🎓`, `students 👥`, `Bob 🚀`, `Mom ❤️`) now start their preview at exactly column 27 and their timestamp at column 69 across all rows.

---

## 3. Verification & Test Suite Status
- **Full Test Suite**: `py -3.12 -m pytest` -> **88 passed in 26.06s** (100% green).
- **Domain Render Tests**: `py -3.12 -m pytest tests/test_domain_render.py` -> **17 passed in 0.22s**.
- Added dedicated test `test_render_chat_row_student_emoji_titles_alignment` verifying preview and time column coordinates across all student emoji variants.

---

## 4. Key Files Modified
- `tg_cli/domain/render.py`:
  - `cell_width()` synchronized with `cell_len()` for 100% Textual layout agreement.
  - `get_kind_glyph()` restored with `👤`, `📢`, `👥`, `🤖`.
  - `status_icons` restored with `📌` and `🔇`.
  - Panel restriction with `buffer_gap` and dynamic space padding.
  - `format_time()` local timezone adjustment.
  - `render_message()` quoted reply formatting.
- `tests/test_domain_render.py`:
  - Restored assertions for original glyphs (`👤`, `📢`, `👥`, `🤖`).
  - Added test coverage for compound ZWJ student emojis in chat titles.

---

## 5. Next Steps for Continuation
- Run the app via `run-fake.bat` (`py -3.12 -m tg_cli --fake`) or `run.bat` (`py -3.12 -m tg_cli`) to see the restored `👤`, `📢`, `👥`, `🤖`, `📌`, `🔇` icons with perfectly aligned preview and timing columns even on chats with student emojis (`students 👨‍🎓`).
