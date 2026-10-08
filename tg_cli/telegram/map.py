"""Pure mapping functions converting Telethon objects into domain models.
Defensive attribute access allows testing without live Telethon sessions.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from tg_cli.domain.models import (
    Account,
    AuthState,
    Chat,
    ChatKind,
    Message,
    MessageKind,
    SendState,
)


def map_telethon_user_to_account(user: Any, session_path: str = "") -> Account:
    """Map a Telethon User to an Account domain model."""
    user_id = int(getattr(user, "id", 0))
    phone = str(getattr(user, "phone", "") or "")
    username = str(getattr(user, "username", "") or "")

    first_name = str(getattr(user, "first_name", "") or "").strip()
    last_name = str(getattr(user, "last_name", "") or "").strip()
    display_name = f"{first_name} {last_name}".strip() or username or phone

    return Account(
        user_id=user_id,
        phone=phone,
        username=username,
        display_name=display_name,
        session_path=session_path,
        auth_state=AuthState.OK,
    )


def determine_chat_kind(entity: Any) -> ChatKind:
    """Determine ChatKind from Telethon entity."""
    type_name = type(entity).__name__.lower()
    if "channel" in type_name:
        is_megagroup = bool(getattr(entity, "megagroup", False))
        return ChatKind.GROUP if is_megagroup else ChatKind.CHANNEL
    if "chat" in type_name:
        return ChatKind.GROUP
    if "user" in type_name:
        if bool(getattr(entity, "bot", False)):
            return ChatKind.BOT
        return ChatKind.USER
    return ChatKind.USER


# Verbs for Telegram service actions. The class name without the
# "MessageAction" prefix is the key. Unknown actions are humanized so the
# row is never a name with an empty body.
_SERVICE_VERBS = {
    "PinMessage": "pinned a message",
    "ChatJoinedByLink": "joined the group",
    "ChatJoinedByRequest": "joined the group",
    "ChatEditPhoto": "changed the group photo",
    "ChatDeletePhoto": "removed the group photo",
    "HistoryClear": "cleared the chat history",
    "ScreenshotTaken": "took a screenshot",
    "ContactSignUp": "joined Telegram",
    "BotAllowed": "allowed the bot to message",
    "SetChatTheme": "changed the chat theme",
    "SetMessagesTTL": "changed the auto-delete timer",
    "GroupCall": "started a group call",
    "InviteToGroupCall": "invited someone to the call",
    "BoostApply": "boosted the chat",
    "GiftPremium": "gifted Telegram Premium",
    "SuggestProfilePhoto": "suggested a photo",
    "WebViewDataSent": "sent web app data",
    "PaymentSent": "sent a payment",
    "GiveawayLaunch": "started a giveaway",
    "StarGift": "sent a gift",
    "RequestedPeer": "shared a chat",
    "ChatMigrateTo": "migrated the chat to a supergroup",
    "ChannelMigrateFrom": "migrated the chat from a group",
    "TopicEdit": "edited a topic",
    "SetChatWallPaper": "changed the wallpaper",
    "ConferenceCall": "started a call",
    "GeoProximityReached": "is nearby",
    "GroupCallScheduled": "scheduled a group call",
}


def _humanize_action(key: str) -> str:
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|\d+", key)
    text = " ".join(parts).strip().lower()
    return text or "sent a service message"


def describe_service_action(action: Any, *, sender_id: int = 0) -> str:
    """Plain sentence for a Telegram service action, without the actor's name."""
    key = type(action).__name__.removeprefix("MessageAction")
    if key == "ChatAddUser":
        users = list(getattr(action, "users", None) or [])
        if len(users) > 1:
            return f"added {len(users)} members"
        return "added a member"
    if key == "ChatDeleteUser":
        user_id = int(getattr(action, "user_id", 0) or 0)
        if sender_id and user_id == sender_id:
            return "left the group"
        return "removed a member"
    if key == "ChatEditTitle":
        title = str(getattr(action, "title", "") or "").strip()
        return f"changed the name to {title}" if title else "changed the group name"
    if key == "ChatCreate":
        title = str(getattr(action, "title", "") or "").strip()
        return f"created the group {title}".strip()
    if key == "ChannelCreate":
        title = str(getattr(action, "title", "") or "").strip()
        return f"created the channel {title}".strip()
    if key == "CustomAction":
        return str(getattr(action, "message", "") or "").strip() or "sent a service message"
    if key == "TopicCreate":
        title = str(getattr(action, "title", "") or "").strip()
        return f"created the topic {title}".strip()
    if key == "PhoneCall":
        reason = getattr(action, "reason", None)
        reason_name = type(reason).__name__ if reason is not None else ""
        if "Miss" in reason_name or "Busy" in reason_name:
            return "missed a call"
        if getattr(action, "duration", None):
            return "finished a call"
        return "started a call"
    if key == "GameScore":
        score = getattr(action, "score", None)
        return f"scored {score}" if score is not None else "scored in a game"
    if key in _SERVICE_VERBS:
        return _SERVICE_VERBS[key]
    return _humanize_action(key)


def service_sentence(sender_name: str, verb: str) -> str:
    """Join the actor and the verb. Skip a missing name so the row is not blank."""
    verb = (verb or "").strip() or "sent a service message"
    name = (sender_name or "").strip()
    if not name or name == "Unknown":
        return verb
    if verb.lower().startswith(name.lower()):
        return verb
    return f"{name} {verb}"


def member_role(participant: Any) -> str:
    """Return "owner", "admin", or "" from a Telethon participant object."""
    if participant is None:
        return ""
    name = type(participant).__name__
    if "Creator" in name:
        return "owner"
    if "Admin" in name:
        return "admin"
    return ""


def _message_sender_name(msg: Any) -> str:
    sender = getattr(msg, "sender", None)
    sender_name = ""
    if sender:
        first = str(getattr(sender, "first_name", "") or "")
        last = str(getattr(sender, "last_name", "") or "")
        sender_name = (
            f"{first} {last}".strip()
            or str(getattr(sender, "title", "") or "")
            or str(getattr(sender, "username", "") or "")
        )
    if not sender_name:
        sender_id = int(getattr(msg, "sender_id", 0) or 0)
        if getattr(msg, "out", False):
            sender_name = "Me"
        elif sender_id:
            sender_name = str(sender_id)
        else:
            sender_name = "Unknown"
    return sender_name


def determine_message_kind(msg: Any) -> tuple[MessageKind, str]:
    """Determine MessageKind and preview/placeholder text from Telethon Message."""
    action = getattr(msg, "action", None)
    if action is not None:
        sender_id = int(getattr(msg, "sender_id", 0) or 0)
        return (MessageKind.SERVICE, describe_service_action(action, sender_id=sender_id))

    media = getattr(msg, "media", None)
    if media is None:
        return (MessageKind.TEXT, getattr(msg, "message", "") or "")

    media_type = type(media).__name__.lower()

    if "photo" in media_type:
        caption = getattr(msg, "message", "") or ""
        return (MessageKind.PHOTO, caption)

    if "document" in media_type:
        document = getattr(media, "document", None)
        mime = getattr(document, "mime_type", "") or ""
        attrs = getattr(document, "attributes", []) or []
        attr_types = [type(a).__name__.lower() for a in attrs]

        if any("voice" in at for at in attr_types) or "audio/ogg" in mime:
            # Voice message
            duration = 0
            for a in attrs:
                if hasattr(a, "duration"):
                    duration = a.duration
                    break
            mins = duration // 60
            secs = duration % 60
            return (MessageKind.VOICE, f"[{mins}:{secs:02d}]")

        if any("sticker" in at for at in attr_types):
            return (MessageKind.STICKER, "")

        if any("animated" in at for at in attr_types) or "video/mp4" in mime:
            caption = getattr(msg, "message", "") or ""
            return (MessageKind.GIF, caption)

        if any("video" in at for at in attr_types):
            caption = getattr(msg, "message", "") or ""
            return (MessageKind.VIDEO, caption)

        # General file
        file_name = ""
        for a in attrs:
            if hasattr(a, "file_name"):
                file_name = a.file_name
                break
        return (MessageKind.FILE, file_name or "file")

    if "contact" in media_type:
        contact_name = getattr(media, "first_name", "") or ""
        return (MessageKind.CONTACT, contact_name)

    if "geo" in media_type:
        return (MessageKind.LOCATION, "")

    if "poll" in media_type:
        question = getattr(getattr(media, "poll", None), "question", "") or ""
        return (MessageKind.POLL, str(question))

    return (MessageKind.OTHER, getattr(msg, "message", "") or "")


def map_telethon_dialog_to_chat(dialog: Any, account_id: int) -> Chat:
    """Map a Telethon Dialog to a Chat domain model."""
    chat_id = int(getattr(dialog, "id", 0))
    title = str(getattr(dialog, "name", "") or getattr(dialog, "title", "") or f"Chat {chat_id}")
    entity = getattr(dialog, "entity", None)
    kind = determine_chat_kind(entity) if entity else ChatKind.USER

    unread_count = int(getattr(dialog, "unread_count", 0))
    pinned = bool(getattr(dialog, "pinned", False))
    archived = bool(getattr(dialog, "archived", False) or getattr(dialog, "folder_id", 0) == 1)

    # Dialog mute status: check notify_settings
    notify = getattr(dialog, "notify_settings", None)
    muted = bool(getattr(notify, "mute_until", None))

    msg = getattr(dialog, "message", None)
    last_message_id = int(getattr(msg, "id", 0)) if msg else 0
    last_date = getattr(msg, "date", None) if msg else None
    if last_date and last_date.tzinfo is None:
        last_date = last_date.replace(tzinfo=timezone.utc)

    last_preview = ""
    if msg:
        kind_type, text = determine_message_kind(msg)
        if kind_type == MessageKind.TEXT:
            last_preview = getattr(msg, "message", "") or ""
        elif kind_type == MessageKind.SERVICE:
            last_preview = service_sentence(_message_sender_name(msg), text)
        else:
            last_preview = f"[{kind_type.value}] {text}".strip()

    return Chat(
        account_id=account_id,
        chat_id=chat_id,
        title=title,
        kind=kind,
        unread_count=unread_count,
        muted=muted,
        pinned=pinned,
        archived=archived,
        last_message_id=last_message_id,
        last_date=last_date,
        last_preview=last_preview,
    )


def map_telethon_message_to_domain(msg: Any, account_id: int, chat_id: int) -> Message:
    """Map a Telethon Message to a Message domain model."""
    msg_id = int(getattr(msg, "id", 0))
    sender_id = int(getattr(msg, "sender_id", 0) or 0)
    sender_name = _message_sender_name(msg)

    date = getattr(msg, "date", None)
    if date and date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)

    kind, preview = determine_message_kind(msg)
    plain_text = getattr(msg, "message", "") or ""
    caption = ""
    if kind == MessageKind.SERVICE:
        plain_text = service_sentence(sender_name, preview)
    elif kind != MessageKind.TEXT:
        caption = plain_text
        plain_text = f"[{kind.value}] {preview}".strip()

    reply_to = getattr(msg, "reply_to", None)
    reply_id = int(getattr(reply_to, "reply_to_msg_id", 0)) if reply_to else None
    if reply_id == 0:
        reply_id = None

    fwd_from = getattr(msg, "fwd_from", None)
    fwd_label = ""
    if fwd_from:
        fwd_label = getattr(fwd_from, "from_name", "") or "forwarded"

    edited = bool(getattr(msg, "edit_date", None))
    outgoing = bool(getattr(msg, "out", False))

    return Message(
        account_id=account_id,
        chat_id=chat_id,
        message_id=msg_id,
        sender_id=sender_id,
        sender_name=sender_name,
        date=date,
        kind=kind,
        plain_text=plain_text,
        caption=caption,
        reply_id=reply_id,
        forward_label=fwd_label,
        edited=edited,
        outgoing=outgoing,
        send_state=SendState.SENT,
    )
