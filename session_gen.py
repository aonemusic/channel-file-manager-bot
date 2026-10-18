"""Generate a Telethon StringSession for the configured owner account.

Run this on a trusted local machine. The resulting session string is a secret
that grants access to the Telegram account; never commit or send it in chat.
"""
import asyncio
import getpass
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    api_id_text = os.getenv("TELETHON_API_ID") or input("Telegram API ID: ").strip()
    api_hash = os.getenv("TELETHON_API_HASH") or getpass.getpass("Telegram API hash: ").strip()
    if not api_id_text.isdigit() or not api_hash:
        raise SystemExit("A numeric API ID and API hash are required.")

    client = TelegramClient(StringSession(), int(api_id_text), api_hash)
    try:
        await client.start()
        me = await client.get_me()
        session_string = client.session.save()
        print("\nAuthenticated Telegram account numeric ID (use as OWNER_ID):")
        print(me.id)
        print("\nTELETHON_SESSION value (secret; copy directly to Render environment settings):")
        print(session_string)
        print("\nDo not paste this session string into Telegram, source code, or a public log.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
