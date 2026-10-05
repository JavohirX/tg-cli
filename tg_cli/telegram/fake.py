"""In-memory fake gateway for tests and Phase 0 development.
Generates 10,000 chats and sample messages without touching the network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence
from tg_cli.domain.models import (
    Account,
    AuthState,
    Chat,
    ChatKind,
    Draft,
    Folder,
    Message,
    MessageKind,
    SendState,
)


class FakeGateway:
    """In-memory gateway adhering to the Gateway protocol."""

    def __init__(self, chat_count: int = 10_000) -> None:
        self.accounts: list[Account] = [
            Account(
                user_id=1001,
                phone="+12025550101",
                username="alice",
                display_name="Alice Liddell",
                auth_state=AuthState.OK,
            ),
            Account(
                user_id=1002,
                phone="+12025550102",
                username="work",
                display_name="Alice @ Acme Corp",
                auth_state=AuthState.OK,
            ),
            Account(
                user_id=1003,
                phone="+12025550103",
                username="business",
                display_name="Alice Ventures",
                auth_state=AuthState.OK,
            ),
        ]

        self.folders: dict[int, list[Folder]] = {
            1001: [
                Folder(account_id=1001, folder_id=1, title="Personal", position=1),
                Folder(account_id=1001, folder_id=2, title="Projects", position=2),
                Folder(account_id=1001, folder_id=3, title="Channels", position=3),
            ],
            1002: [
                Folder(account_id=1002, folder_id=10, title="Team", position=1),
                Folder(account_id=1002, folder_id=11, title="Alerts", position=2),
            ],
            1003: [],
        }

        self.chats: dict[int, dict[int, Chat]] = {acc.user_id: {} for acc in self.accounts}
        self.messages: dict[tuple[int, int], list[Message]] = {}
        self.drafts: dict[tuple[int, int], Draft] = {}

        self._seed_data(chat_count)

    def _seed_data(self, chat_count: int) -> None:
        now = datetime.now(timezone.utc)
        alice_id = 1001

        # Handcrafted initial chats for Alice
        handcrafted = [
            Chat(
                account_id=alice_id,
                chat_id=1,
                title="Bob 🚀",
                kind=ChatKind.USER,
                unread_count=2,
                pinned=True,
                pin_rank=1,
                last_message_id=104,
                last_date=now - timedelta(minutes=2),
                last_preview="Sure, let's catch up later today!",
                folder_ids={1},
            ),
            Chat(
                account_id=alice_id,
                chat_id=2,
                title="Development Team (Core)",
                kind=ChatKind.GROUP,
                unread_count=14,
                pinned=True,
                pin_rank=2,
                last_message_id=210,
                last_date=now - timedelta(minutes=5),
                last_preview="Bob: [voice 0:42]",
                folder_ids={2},
            ),
            Chat(
                account_id=alice_id,
                chat_id=3,
                title="Новости и обновления (Cyrillic)",
                kind=ChatKind.CHANNEL,
                unread_count=3,
                muted=True,
                last_message_id=305,
                last_date=now - timedelta(hours=1),
                last_preview="Релиз новой версии готов к тестированию",
                folder_ids={3},
            ),
            Chat(
                account_id=alice_id,
                chat_id=4,
                title="Family Group",
                kind=ChatKind.GROUP,
                unread_count=7,
                last_message_id=402,
                last_date=now - timedelta(hours=3),
                last_preview="Mom: [photo] Birthday photos are ready!",
                folder_ids={1},
            ),
            Chat(
                account_id=alice_id,
                chat_id=5,
                title="Archived Newsletter",
                kind=ChatKind.CHANNEL,
                archived=True,
                last_message_id=501,
                last_date=now - timedelta(days=10),
                last_preview="Weekly tech digest #142",
            ),
        ]

        for chat in handcrafted:
            self.chats[alice_id][chat.chat_id] = chat

        # Messages for Development Team (chat_id=2)
        dev_messages = [
            Message(
                account_id=alice_id,
                chat_id=2,
                message_id=201,
                sender_id=2001,
                sender_name="Alice",
                date=now - timedelta(minutes=30),
                kind=MessageKind.TEXT,
                plain_text="Does anyone know how the windowed list widget works?",
                outgoing=True,
            ),
            Message(
                account_id=alice_id,
                chat_id=2,
                message_id=202,
                sender_id=2002,
                sender_name="Bob",
                date=now - timedelta(minutes=28),
                kind=MessageKind.TEXT,
                plain_text="I think it's documented in PLAN.md. It measures viewport height and renders only visible rows.",
                reply_id=201,
            ),
            Message(
                account_id=alice_id,
                chat_id=2,
                message_id=203,
                sender_id=2001,
                sender_name="Alice",
                date=now - timedelta(minutes=20),
                kind=MessageKind.PHOTO,
                plain_text="[photo]",
                caption="Architecture diagram from the spec",
                outgoing=True,
            ),
            Message(
                account_id=alice_id,
                chat_id=2,
                message_id=204,
                sender_id=2002,
                sender_name="Bob",
                date=now - timedelta(minutes=5),
                kind=MessageKind.VOICE,
                plain_text="[voice 0:42]",
            ),
        ]
        self.messages[(alice_id, 2)] = dev_messages

        # Messages for Bob (chat_id=1)
        bob_messages = [
            Message(
                account_id=alice_id,
                chat_id=1,
                message_id=101,
                sender_id=2002,
                sender_name="Bob",
                date=now - timedelta(hours=2),
                kind=MessageKind.TEXT,
                plain_text="Hey! Are you working on the keyboard shortcuts today?",
            ),
            Message(
                account_id=alice_id,
                chat_id=1,
                message_id=102,
                sender_id=alice_id,
                sender_name="Alice",
                date=now - timedelta(hours=1, minutes=45),
                kind=MessageKind.TEXT,
                plain_text="Yes, starting with Phase 0 shell and windowed list.",
                outgoing=True,
            ),
            Message(
                account_id=alice_id,
                chat_id=1,
                message_id=103,
                sender_id=2002,
                sender_name="Bob",
                date=now - timedelta(minutes=10),
                kind=MessageKind.TEXT,
                plain_text="Nice! Remember: no mouse needed at all.",
            ),
            Message(
                account_id=alice_id,
                chat_id=1,
                message_id=104,
                sender_id=2002,
                sender_name="Bob",
                date=now - timedelta(minutes=2),
                kind=MessageKind.TEXT,
                plain_text="Sure, let's catch up later today!",
            ),
        ]
        self.messages[(alice_id, 1)] = bob_messages

        # Bulk generate remaining chats up to chat_count to test 10k scalability
        base_id = 100
        remaining = chat_count - len(handcrafted)
        for i in range(remaining):
            cid = base_id + i
            minutes_ago = 10 + i * 2
            c_date = now - timedelta(minutes=minutes_ago)
            # Mix kinds and folders
            kind = ChatKind.USER if i % 3 == 0 else (ChatKind.GROUP if i % 3 == 1 else ChatKind.CHANNEL)
            f_ids: set[int] = set()
            if i % 4 == 0:
                f_ids.add(1)
            elif i % 4 == 1:
                f_ids.add(2)
            elif i % 4 == 2:
                f_ids.add(3)

            chat = Chat(
                account_id=alice_id,
                chat_id=cid,
                title=f"Chat #{cid} - Contact {i}",
                kind=kind,
                unread_count=1 if (i % 7 == 0) else 0,
                muted=(i % 5 == 0),
                pinned=False,
                archived=False,
                last_message_id=cid * 10,
                last_date=c_date,
                last_preview=f"Latest message in chat {cid}",
                folder_ids=f_ids,
            )
            self.chats[alice_id][cid] = chat

    def get_accounts(self) -> list[Account]:
        return list(self.accounts)

    def add_account(self, account: Account) -> None:
        self.accounts.append(account)
        self.chats.setdefault(account.user_id, {})

    def remove_account(self, user_id: int) -> None:
        self.accounts = [a for a in self.accounts if a.user_id != user_id]
        self.chats.pop(user_id, None)

    def get_folders(self, account_id: int) -> list[Folder]:
        return list(self.folders.get(account_id, []))

    def get_chats(
        self,
        account_id: int,
        folder_id: int | None = None,
        filter_query: str = "",
    ) -> list[Chat]:
        acc_chats = self.chats.get(account_id, {})
        result: list[Chat] = []

        q = filter_query.strip().lower()

        for chat in acc_chats.values():
            if folder_id is not None:
                if folder_id == -1:  # Synthetic Unread
                    if chat.unread_count == 0:
                        continue
                elif folder_id == -2:  # Archive
                    if not chat.archived:
                        continue
                else:
                    if folder_id not in chat.folder_ids:
                        continue
            else:
                # Main list excludes archived chats unless archive view requested
                if chat.archived:
                    continue

            if q and (q not in chat.title.lower() and q not in chat.last_preview.lower()):
                continue

            result.append(chat)

        # Sort according to spec:
        # Pinned first (by pin_rank ascending), then by last_date descending
        def sort_key(c: Chat) -> tuple[int, int, datetime]:
            pin_group = 0 if c.pinned else 1
            rank = c.pin_rank if c.pinned else 0
            dt = c.last_date or datetime.min.replace(tzinfo=timezone.utc)
            return (pin_group, rank, dt)

        # Python sort is stable; sort descending by date first, then ascending by pin
        result.sort(key=lambda c: c.last_date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        result.sort(key=lambda c: (0 if c.pinned else 1, c.pin_rank))
        return result

    def get_chat(self, account_id: int, chat_id: int) -> Chat | None:
        return self.chats.get(account_id, {}).get(chat_id)

    def get_messages(
        self,
        account_id: int,
        chat_id: int,
        limit: int = 50,
        before_id: int | None = None,
    ) -> list[Message]:
        msgs = self.messages.get((account_id, chat_id), [])
        if not msgs:
            # Generate placeholder messages on the fly if needed
            now = datetime.now(timezone.utc)
            msgs = [
                Message(
                    account_id=account_id,
                    chat_id=chat_id,
                    message_id=m_id,
                    sender_id=1001 if (m_id % 2 == 0) else 2002,
                    sender_name="Alice" if (m_id % 2 == 0) else "Contact",
                    date=now - timedelta(minutes=(50 - m_id)),
                    kind=MessageKind.TEXT,
                    plain_text=f"Message {m_id} in chat {chat_id}",
                    outgoing=(m_id % 2 == 0),
                )
                for m_id in range(1, 40)
            ]
            self.messages[(account_id, chat_id)] = msgs

        filtered = [m for m in msgs if before_id is None or m.message_id < before_id]
        return filtered[-limit:]

    def send_message(
        self,
        account_id: int,
        chat_id: int,
        text: str,
        reply_to_id: int | None = None,
    ) -> Message:
        msgs = self.messages.setdefault((account_id, chat_id), [])
        new_id = (msgs[-1].message_id + 1) if msgs else 1
        msg = Message(
            account_id=account_id,
            chat_id=chat_id,
            message_id=new_id,
            sender_id=account_id,
            sender_name="Me",
            date=datetime.now(timezone.utc),
            kind=MessageKind.TEXT,
            plain_text=text,
            reply_id=reply_to_id,
            outgoing=True,
            send_state=SendState.SENT,
        )
        msgs.append(msg)

        # Update chat preview
        if chat_id in self.chats.get(account_id, {}):
            chat = self.chats[account_id][chat_id]
            chat.last_message_id = new_id
            chat.last_date = msg.date
            chat.last_preview = text
        return msg

    def edit_message(
        self,
        account_id: int,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> Message:
        msgs = self.messages.get((account_id, chat_id), [])
        for m in msgs:
            if m.message_id == message_id:
                m.plain_text = text
                m.edited = True
                return m
        raise KeyError(f"Message {message_id} not found")

    def delete_messages(
        self,
        account_id: int,
        chat_id: int,
        message_ids: Sequence[int],
    ) -> None:
        target_set = set(message_ids)
        msgs = self.messages.get((account_id, chat_id), [])
        for m in msgs:
            if m.message_id in target_set:
                m.is_deleted = True
                m.plain_text = "[deleted]"

    def archive_chats(
        self,
        account_id: int,
        chat_ids: Sequence[int],
        archived: bool = True,
    ) -> None:
        for cid in chat_ids:
            if cid in self.chats.get(account_id, {}):
                self.chats[account_id][cid].archived = archived

    def mute_chats(
        self,
        account_id: int,
        chat_ids: Sequence[int],
        muted: bool = True,
    ) -> None:
        for cid in chat_ids:
            if cid in self.chats.get(account_id, {}):
                self.chats[account_id][cid].muted = muted

    def mark_chats_read(
        self,
        account_id: int,
        chat_ids: Sequence[int],
        read: bool = True,
    ) -> None:
        for cid in chat_ids:
            if cid in self.chats.get(account_id, {}):
                self.chats[account_id][cid].unread_count = 0 if read else 1

    def delete_chats(
        self,
        account_id: int,
        chat_ids: Sequence[int],
    ) -> None:
        for cid in chat_ids:
            self.chats.get(account_id, {}).pop(cid, None)

    def get_draft(self, account_id: int, chat_id: int) -> Draft | None:
        return self.drafts.get((account_id, chat_id))

    def save_draft(self, draft: Draft) -> None:
        self.drafts[(draft.account_id, draft.chat_id)] = draft
        if draft.chat_id in self.chats.get(draft.account_id, {}):
            self.chats[draft.account_id][draft.chat_id].last_preview = f"Draft: {draft.text}"

    def clear_draft(self, account_id: int, chat_id: int) -> None:
        self.drafts.pop((account_id, chat_id), None)
        chat = self.chats.get(account_id, {}).get(chat_id)
        if chat:
            msgs = self.messages.get((account_id, chat_id), [])
            chat.last_preview = msgs[-1].plain_text if msgs else ""

    def retry_failed_message(self, account_id: int, chat_id: int, message_id: int) -> None:
        msgs = self.messages.get((account_id, chat_id), [])
        for m in msgs:
            if m.message_id == message_id:
                m.send_state = SendState.SENT
                return
