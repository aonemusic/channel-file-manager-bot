"""Owner-controlled Telegram channel file manager.

The Bot API is used for the interface and for checking the bot's channel rights.
Telethon uses the authorized owner's account to read historical channel messages
and forward them. The owner's Telegram account must itself have access to the
source and posting rights in the destination.
"""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import BadRequest, RetryAfter, TelegramError, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from waitress import serve

from database import Database


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


BOT_TOKEN = required_env("BOT_TOKEN")
OWNER_ID = int(required_env("OWNER_ID"))
API_ID = int(required_env("TELETHON_API_ID"))
API_HASH = required_env("TELETHON_API_HASH")
TELETHON_SESSION = required_env("TELETHON_SESSION")
DATABASE_PATH = os.getenv("DATABASE_PATH", "/var/data/channelbot.sqlite3")
BOT_LOCK_PATH = os.getenv("BOT_LOCK_PATH", "/var/data/channelbot.lock")
BATCH_SIZE = max(1, min(100, int(os.getenv("BATCH_SIZE", "20"))))
PROGRESS_EVERY = max(1, int(os.getenv("PROGRESS_EVERY", "20")))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("channel_file_manager")


class SecretRedactionFilter(logging.Filter):
    """Defensively redact configured credentials from any log message."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [secret for secret in secrets if secret]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
            for secret in self.secrets:
                rendered = rendered.replace(secret, "[REDACTED]")
            record.msg = rendered
            record.args = ()
        except Exception:
            record.msg = "[log message redacted]"
            record.args = ()
        return True


_redactor = SecretRedactionFilter([BOT_TOKEN, API_HASH, TELETHON_SESSION])
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_redactor)
for _name in ("telegram", "telegram.ext", "httpx", "telethon"):
    logging.getLogger(_name).setLevel(logging.WARNING)


db = Database(DATABASE_PATH)
telethon_client = TelegramClient(StringSession(TELETHON_SESSION), API_ID, API_HASH)
health_app = Flask(__name__)


@health_app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "service": "channel-file-manager-bot"}), 200


@health_app.get("/")
def index():
    return "Channel File Manager Bot is running.", 200


def start_health_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    serve(health_app, host="0.0.0.0", port=port, threads=4)


async def safe_edit(query, text: str, reply_markup=None) -> None:
    """Edit a callback's message while honoring Bot API retry-after responses."""
    for attempt in range(3):
        try:
            await query.edit_message_text(text=text, reply_markup=reply_markup)
            return
        except RetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 1)
        except BadRequest as exc:
            if "Message is not modified" in str(exc):
                return
            raise
        except TimedOut:
            if attempt == 2:
                raise
            await asyncio.sleep(1 + attempt)


async def safe_callback_answer(query, text: str | None = None) -> None:
    try:
        await query.answer(text=text, show_alert=bool(text))
    except RetryAfter as exc:
        await asyncio.sleep(float(exc.retry_after) + 1)
        try:
            await query.answer(text=text, show_alert=bool(text))
        except TelegramError:
            pass
    except TelegramError:
        pass


def is_owner(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == OWNER_ID)


def owner_denied_text() -> str:
    return "This bot is restricted to its configured owner."


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Forward Now", callback_data="forward")],
        [InlineKeyboardButton("📡 Connected Channels", callback_data="channels:0")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
    ])


def available_permissions(member: Any) -> list[str]:
    if getattr(member, "status", "") == "creator":
        return ["owner (all channel permissions)"]
    labels = [
        ("can_post_messages", "post messages"),
        ("can_edit_messages", "edit messages"),
        ("can_delete_messages", "delete messages"),
        ("can_invite_users", "invite users"),
        ("can_manage_chat", "manage channel"),
        ("can_manage_video_chats", "manage video chats"),
        ("can_promote_members", "promote members"),
        ("can_change_info", "change channel info"),
    ]
    return [label for attribute, label in labels if getattr(member, attribute, False)]


async def inspect_bot_channel(bot, chat_id: int):
    """Return (chat, member, status, permissions) from a fresh Bot API check."""
    me = await bot.get_me()
    chat = await bot.get_chat(chat_id)
    member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
    status = getattr(member, "status", "unknown")
    rights = available_permissions(member)
    if status not in ("administrator", "creator"):
        return chat, member, status, rights
    return chat, member, status, rights


async def register_channel(bot, chat_id: int) -> tuple[bool, str]:
    try:
        chat, member, status, _rights = await inspect_bot_channel(bot, chat_id)
        title = getattr(chat, "title", None) or str(chat_id)
        username = getattr(chat, "username", None)
        active = status in ("administrator", "creator")
        db.upsert_channel(chat_id, title, username, status) if active else db.update_channel_status(chat_id, status, False)
        return active, title
    except TelegramError as exc:
        logger.warning("Channel registration check failed for %s (%s)", chat_id, type(exc).__name__)
        return False, ""


def pretty_channel_status(active: bool, status: str, rights: list[str]) -> str:
    state = "Connected" if active else f"Not active (bot status: {status})"
    perms = ", ".join(rights) if rights else "no channel permissions reported"
    return f"Status: {state}\nBot permissions: {perms}"


async def verify_channel_for_action(bot, chat_id: int, destination: bool = False):
    row = db.get_channel(chat_id)
    if row is None or not row["is_active"]:
        raise ValueError("That channel is not registered as connected.")
    chat, member, status, rights = await inspect_bot_channel(bot, chat_id)
    if status not in ("administrator", "creator"):
        db.update_channel_status(chat_id, status, False)
        raise ValueError(f"The bot is no longer an administrator in {getattr(chat, 'title', chat_id)}.")
    if destination and status != "creator" and not getattr(member, "can_post_messages", False):
        raise ValueError("The bot must have the channel permission to post messages in the destination.")
    db.upsert_channel(chat_id, getattr(chat, "title", row["title"]), getattr(chat, "username", row["username"]), status)
    return chat, member, rights


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.effective_message.reply_text(owner_denied_text())
        return
    context.user_data.clear()
    await update.effective_message.reply_text(
        "Channel File Manager\nChoose an action below. Only the configured owner can run bulk operations.",
        reply_markup=main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.effective_message.reply_text(owner_denied_text())
        return
    await update.effective_message.reply_text(
        "How it works\n"
        "1. Add this bot as an administrator to each channel you want to use.\n"
        "2. Register channels by adding the bot (new admin updates are registered automatically), posting /register in the channel, or using /addchannel CHAT_ID in this private chat.\n"
        "3. Select a source and destination with Forward Now. The owner's Telethon account scans the full accessible history, including older messages.\n"
        "4. Review the eligible-file count and confirm before forwarding.\n\n"
        "The bot does not discover every channel automatically. Only registered channels are listed. The bot must remain an admin; the destination bot admin needs post permission. The Telethon owner account must be the configured OWNER_ID, have access to the source, and have posting rights in the destination. Protected/no-forward content is skipped, and prior successful forwards are not repeated."
    )


async def addchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.effective_message.reply_text(owner_denied_text())
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("Use /addchannel CHAT_ID in a private chat with the bot.")
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Usage: /addchannel -1001234567890")
        return
    try:
        chat_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("The channel ID must be a numeric Telegram chat ID.")
        return
    active, title = await register_channel(context.bot, chat_id)
    if active:
        await update.effective_message.reply_text(f"Registered: {title} ({chat_id}).")
    else:
        await update.effective_message.reply_text(
            "I could not register that channel. Confirm the ID and that this bot is already an administrator there."
        )


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if not chat or chat.type != ChatType.CHANNEL:
        return
    # Register only when the channel explicitly posts /register; otherwise newly
    # added administrator updates below provide the authorized discovery event.
    message = update.channel_post
    text = (message.text or message.caption or "").strip() if message else ""
    if text.split(maxsplit=1)[0].split("@", 1)[0] == "/register" if text else False:
        active, title = await register_channel(context.bot, chat.id)
        if active:
            logger.info("Channel registered by authorized channel command: %s", chat.id)
        else:
            logger.info("Channel registration requested but bot is not an administrator: %s", chat.id)


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    change = update.my_chat_member
    if not change or change.chat.type != ChatType.CHANNEL:
        return
    new_status = change.new_chat_member.status
    if new_status in ("administrator", "creator"):
        active, _title = await register_channel(context.bot, change.chat.id)
        if active:
            logger.info("Registered channel after bot admin update: %s", change.chat.id)
    else:
        db.update_channel_status(change.chat.id, new_status, False)
        logger.info("Bot channel access changed: %s status=%s", change.chat.id, new_status)


async def render_channel_list(query, bot, page: int) -> None:
    rows = db.list_channels()
    page_size = 6
    page_count = max(1, (len(rows) + page_size - 1) // page_size)
    page = max(0, min(page, page_count - 1))
    selected = rows[page * page_size:(page + 1) * page_size]
    if not rows:
        await safe_edit(query, "No channels are registered yet. Add the bot as a channel admin, then use /register in that channel or /addchannel CHAT_ID here.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]))
        return
    lines = [f"Connected Channels — page {page + 1}/{page_count}"]
    for row in selected:
        active = bool(row["is_active"])
        status = row["bot_status"]
        rights: list[str] = []
        try:
            _chat, member, status, rights = await inspect_bot_channel(bot, row["chat_id"])
            active = status in ("administrator", "creator")
            db.update_channel_status(row["chat_id"], status, active)
        except TelegramError:
            active = False
            status = "unavailable"
            db.update_channel_status(row["chat_id"], status, False)
        lines.append(
            f"\n{row['title']}\nID: {row['chat_id']}\n" + pretty_channel_status(active, status, rights)
        )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"channels:{page-1}"))
    if page + 1 < page_count:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"channels:{page+1}"))
    keyboard = [nav] if nav else []
    keyboard.append([InlineKeyboardButton("⬅️ Menu", callback_data="menu")])
    await safe_edit(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


def registered_active_rows() -> list[Any]:
    return db.list_channels(active_only=True)


def channel_picker(rows: list[Any], action: str, exclude_id: int | None = None) -> InlineKeyboardMarkup:
    buttons = []
    for row in rows:
        if exclude_id is not None and int(row["chat_id"]) == exclude_id:
            continue
        title = str(row["title"])
        if len(title) > 48:
            title = title[:45] + "..."
        buttons.append([InlineKeyboardButton(f"{title} ({row['chat_id']})", callback_data=f"{action}:{row['chat_id']}")])
    buttons.append([InlineKeyboardButton("⬅️ Menu", callback_data="menu")])
    return InlineKeyboardMarkup(buttons)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    if not is_owner(update):
        await safe_callback_answer(query, "Owner access only.")
        return
    await safe_callback_answer(query)
    data = query.data or ""
    try:
        if data == "menu":
            await safe_edit(query, "Channel File Manager\nChoose an action below.", main_keyboard())
            return
        if data == "help":
            text = (
                "Help\nRegister channels with /register in the channel or /addchannel CHAT_ID here.\n"
                "Forward Now scans the accessible historical source messages using the configured owner's Telethon session.\n"
                "The bot must be an admin in both registered channels; it must have post permission in the destination. The owner's Telegram account must be able to read the source and post to the destination. Protected content is never copied or re-uploaded."
            )
            await safe_edit(query, text, InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]))
            return
        if data.startswith("channels:"):
            await render_channel_list(query, context.bot, int(data.split(":", 1)[1]))
            return
        if data == "forward":
            rows = registered_active_rows()
            if len(rows) < 2:
                await safe_edit(query, "At least two registered active channels are needed (one source and one destination).", InlineKeyboardMarkup([[InlineKeyboardButton("📡 Connected Channels", callback_data="channels:0")], [InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]))
                return
            await safe_edit(query, "Choose a registered source channel:", channel_picker(rows, "src"))
            return
        if data.startswith("src:"):
            source_id = int(data.split(":", 1)[1])
            if db.get_channel(source_id) is None:
                raise ValueError("That source channel is not registered.")
            context.user_data["source_id"] = source_id
            rows = registered_active_rows()
            await safe_edit(query, "Choose a registered destination channel:", channel_picker(rows, "dst", exclude_id=source_id))
            return
        if data.startswith("dst:"):
            source_id = int(context.user_data.get("source_id", 0))
            destination_id = int(data.split(":", 1)[1])
            if not source_id or source_id == destination_id:
                raise ValueError("Choose a valid source and a different destination.")
            await verify_channel_for_action(context.bot, source_id)
            await verify_channel_for_action(context.bot, destination_id, destination=True)
            if not telethon_client.is_connected():
                raise ValueError("The owner's Telegram session is not connected. Check the Render logs and TELETHON_SESSION configuration.")
            active_tasks = context.application.bot_data.setdefault("active_tasks", {})
            current = active_tasks.get(OWNER_ID)
            if current and not current[1].done():
                raise ValueError("Another scan or forwarding task is already running. Wait for it to finish or cancel it first.")
            plan_id = uuid.uuid4().hex
            db.create_plan(plan_id, OWNER_ID, source_id, destination_id)
            context.user_data["active_plan"] = plan_id
            await safe_edit(query, "⏳ Scanning the complete accessible source history for eligible media files…\nThis may take a while; the bot remains responsive.", InlineKeyboardMarkup([[InlineKeyboardButton("Cancel scan", callback_data=f"cancel:{plan_id}")]]))
            task = asyncio.create_task(scan_history(plan_id, source_id, destination_id, query))
            active_tasks[OWNER_ID] = (plan_id, task)
            return
        if data.startswith("go:"):
            plan_id = data.split(":", 1)[1]
            plan = db.get_plan(plan_id)
            if not plan or plan["owner_id"] != OWNER_ID or plan["status"] != "ready":
                raise ValueError("This scan is no longer available. Please scan again.")
            await verify_channel_for_action(context.bot, plan["source_chat_id"])
            await verify_channel_for_action(context.bot, plan["destination_chat_id"], destination=True)
            await validate_telethon_destination(plan["destination_chat_id"])
            active_tasks = context.application.bot_data.setdefault("active_tasks", {})
            current = active_tasks.get(OWNER_ID)
            if current and current[0] != plan_id and not current[1].done():
                raise ValueError("Another forwarding task is already running. Wait for it to finish or cancel it first.")
            db.set_plan_status(plan_id, "running")
            await safe_edit(query, "Starting the confirmed forwarding operation…", InlineKeyboardMarkup([[InlineKeyboardButton("Cancel forwarding", callback_data=f"cancel:{plan_id}")]]))
            task = asyncio.create_task(run_forwarding(plan_id, query))
            active_tasks[OWNER_ID] = (plan_id, task)
            return
        if data.startswith("cancel:"):
            plan_id = data.split(":", 1)[1]
            plan = db.get_plan(plan_id)
            if not plan or plan["owner_id"] != OWNER_ID:
                raise ValueError("That operation is no longer active.")
            current = context.application.bot_data.setdefault("active_tasks", {}).get(OWNER_ID)
            if current and current[0] == plan_id and not current[1].done():
                current[1].cancel()
            db.set_plan_status(plan_id, "cancelled")
            await safe_edit(query, "Operation cancelled. Messages already forwarded remain in the destination.", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]))
            return
    except ValueError as exc:
        await safe_edit(query, f"⚠️ {exc}", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]))
    except TelegramError as exc:
        logger.warning("Telegram operation check failed (%s)", type(exc).__name__)
        await safe_edit(query, "Telegram could not verify the channel right now. Confirm bot access and try again.", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]))
    except Exception as exc:
        logger.error("Callback failed (%s)", type(exc).__name__)
        await safe_edit(query, "Something went wrong. Check the Render logs for a safe error summary and try again.", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]))


def is_supported_file(message) -> bool:
    media = getattr(message, "media", None)
    return isinstance(media, (MessageMediaPhoto, MessageMediaDocument))


def is_protected(message, source_entity) -> bool:
    return bool(
        getattr(message, "noforwards", False)
        or getattr(message, "protected", False)
        or getattr(source_entity, "noforwards", False)
    )


async def scan_history(plan_id: str, source_id: int, destination_id: int, query) -> None:
    scanned = found = 0
    pending: list[int] = []
    try:
        source = await telethon_client.get_entity(source_id)
        if getattr(source, "noforwards", False):
            db.set_plan_status(plan_id, "protected")
            await safe_edit(query, "This source channel protects its content from forwarding. No messages were copied.", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]))
            return
        async for message in telethon_client.iter_messages(source, reverse=True):
            scanned += 1
            if is_supported_file(message) and not is_protected(message, source):
                pending.append(message.id)
                found += 1
            if len(pending) >= 500:
                db.add_candidates(plan_id, pending)
                pending.clear()
            if scanned % 5000 == 0:
                db.update_plan_counts(plan_id, scanned, found)
                await safe_edit(query, f"⏳ Scanning history…\nMessages checked: {scanned:,}\nEligible files found: {found:,}", InlineKeyboardMarkup([[InlineKeyboardButton("Cancel scan", callback_data=f"cancel:{plan_id}")]]))
        if pending:
            db.add_candidates(plan_id, pending)
        db.update_plan_counts(plan_id, scanned, found)
        if found == 0:
            db.set_plan_status(plan_id, "empty")
            await safe_edit(query, f"Scan complete. Checked {scanned:,} historical messages; no eligible, unprotected media files were found.", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]))
            return
        db.set_plan_status(plan_id, "ready")
        await safe_edit(
            query,
            f"Scan complete.\nMessages checked: {scanned:,}\nEligible media files found: {found:,}\n\nConfirm to start forwarding. Previously forwarded messages are skipped; protected content is never copied or re-uploaded.",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm and forward", callback_data=f"go:{plan_id}")],
                [InlineKeyboardButton("Cancel", callback_data=f"cancel:{plan_id}")],
            ]),
        )
    except asyncio.CancelledError:
        db.set_plan_status(plan_id, "cancelled")
        raise
    except errors.FloodWaitError as exc:
        db.set_plan_status(plan_id, "failed")
        logger.warning("History scan was rate limited for %s seconds", exc.seconds)
        await safe_edit(query, f"Telegram rate-limited the scan for {exc.seconds} seconds. No bulk forwarding was started; try again later.", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]))
    except Exception as exc:
        db.set_plan_status(plan_id, "failed")
        logger.error("History scan failed (%s)", type(exc).__name__)
        await safe_edit(query, "Could not scan this channel history. Verify that the owner's Telegram account can access it, then try again.", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]))


async def validate_telethon_destination(destination_id: int) -> None:
    me = await telethon_client.get_me()
    if not me or me.id != OWNER_ID:
        raise ValueError("The Telethon session must belong to the Telegram account whose numeric ID is OWNER_ID.")
    entity = await telethon_client.get_entity(destination_id)
    if getattr(entity, "noforwards", False):
        # Destination no-forwards setting is not a reason to bypass it; Telegram may
        # still accept posting, but refuse rather than risk moving protected content.
        raise ValueError("The destination channel has protected-content mode enabled; forwarding is disabled for safety.")
    permissions = await telethon_client.get_permissions(entity, me)
    rights = getattr(permissions, "admin_rights", None)
    can_post = bool(getattr(permissions, "is_creator", False) or getattr(rights, "post_messages", False))
    if not can_post:
        raise ValueError("The owner's Telegram account must be a channel creator or have post_messages admin permission in the destination.")


async def run_forwarding(plan_id: str, query) -> None:
    plan = db.get_plan(plan_id)
    if not plan:
        return
    source_id = plan["source_chat_id"]
    destination_id = plan["destination_chat_id"]
    completed = skipped = failed = 0
    total = int(plan["found_count"])
    try:
        # Recheck the user's ID and destination posting rights immediately before
        # each bulk operation; Bot API checks were repeated in the confirmation path.
        await validate_telethon_destination(destination_id)
        source = await telethon_client.get_entity(source_id)
        destination = await telethon_client.get_entity(destination_id)
        processed = 0
        for batch in db.candidate_batches(plan_id, BATCH_SIZE):
            for message_id in batch:
                processed += 1
                if db.already_forwarded(source_id, destination_id, message_id):
                    skipped += 1
                    continue
                attempt = 0
                while True:
                    try:
                        await telethon_client.forward_messages(
                            destination, message_id, from_peer=source
                        )
                        db.record_forward(source_id, destination_id, message_id)
                        completed += 1
                        break
                    except errors.FloodWaitError as exc:
                        attempt += 1
                        wait_seconds = int(exc.seconds) + 1
                        logger.warning("Forward rate limited; sleeping %s seconds", wait_seconds)
                        await safe_edit(
                            query,
                            f"⏳ Telegram rate limit: waiting {wait_seconds}s before continuing.\nForwarded: {completed:,}/{total:,} · skipped: {skipped:,} · errors: {failed:,}",
                            InlineKeyboardMarkup([[InlineKeyboardButton("Cancel forwarding", callback_data=f"cancel:{plan_id}")]]),
                        )
                        await asyncio.sleep(wait_seconds)
                        if attempt >= 5:
                            failed += 1
                            break
                    except (errors.ChatForwardsRestrictedError, errors.MessageIdInvalidError):
                        # Never work around Telegram's content-protection or message rules.
                        skipped += 1
                        logger.info("Telegram refused protected/invalid message %s; skipped", message_id)
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        failed += 1
                        logger.warning("Could not forward source message %s (%s)", message_id, type(exc).__name__)
                        break
            if processed % PROGRESS_EVERY == 0 or processed == total:
                await safe_edit(
                    query,
                    f"Forwarding in progress…\nProcessed: {processed:,}/{total:,}\nForwarded: {completed:,} · skipped/duplicate: {skipped:,} · errors: {failed:,}",
                    InlineKeyboardMarkup([[InlineKeyboardButton("Cancel forwarding", callback_data=f"cancel:{plan_id}")]]),
                )
            # Yield between batches to keep the bot responsive to callbacks.
            await asyncio.sleep(0)
        db.set_plan_status(plan_id, "completed")
        await safe_edit(
            query,
            f"Forwarding complete.\nForwarded: {completed:,}\nSkipped (duplicates/protected): {skipped:,}\nErrors: {failed:,}",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]),
        )
    except asyncio.CancelledError:
        db.set_plan_status(plan_id, "cancelled")
        raise
    except Exception as exc:
        db.set_plan_status(plan_id, "failed")
        logger.error("Forwarding task failed (%s)", type(exc).__name__)
        try:
            await safe_edit(query, "Forwarding stopped because the channel or session became unavailable. Check logs and channel permissions.", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Menu", callback_data="menu")]]))
        except TelegramError:
            pass


async def post_init(application: Application) -> None:
    me = await application.bot.get_me()
    logger.info("Bot started; bot ID=%s. Owner ID is configured.", me.id)
    await telethon_client.connect()
    if not await telethon_client.is_user_authorized():
        raise RuntimeError("The configured Telethon session is not authorized. Generate and set TELETHON_SESSION before starting the service.")
    telethon_me = await telethon_client.get_me()
    if not telethon_me or telethon_me.id != OWNER_ID:
        raise RuntimeError("The Telethon session account does not match OWNER_ID; refusing to start.")
    logger.info("Owner Telethon session authenticated; credential values are not logged.")


async def post_shutdown(application: Application) -> None:
    tasks = application.bot_data.get("active_tasks", {})
    running = [item[1] for item in tasks.values() if item]
    for task in running:
        if task and not task.done():
            task.cancel()
    if running:
        await asyncio.gather(*running, return_exceptions=True)
    if telethon_client.is_connected():
        await telethon_client.disconnect()


def acquire_single_instance_lock() -> int:
    Path(BOT_LOCK_PATH).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(BOT_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RuntimeError("Another bot polling process already holds the instance lock.")
    return fd


async def run_bot() -> None:
    lock_fd = acquire_single_instance_lock()
    threading.Thread(target=start_health_server, daemon=True, name="health-server").start()
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("addchannel", addchannel_command))
    application.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.COMMAND, handle_channel_post))
    application.add_handler(CallbackQueryHandler(callback_handler))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        await application.initialize()
        await post_init(application)
        await application.start()
        if application.updater is None:
            raise RuntimeError("Telegram polling updater was not created.")
        await application.updater.start_polling(
            allowed_updates=["message", "channel_post", "my_chat_member", "callback_query"],
            drop_pending_updates=False,
        )
        logger.info("Telegram polling started; keep Render instances at one.")
        await stop_event.wait()
    finally:
        if application.updater and application.updater.running:
            await application.updater.stop()
        if application.running:
            await application.stop()
        await post_shutdown(application)
        await application.shutdown()
        os.close(lock_fd)


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        logger.error("Service startup/shutdown failed (%s)", type(exc).__name__)
        raise
