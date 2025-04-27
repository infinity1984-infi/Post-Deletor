# bot.py

import logging
import time
import aiosqlite
from telegram import Update, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from config import API_TOKEN, REGISTRATION_CHAT_ID

DB_PATH = "bot.db"

# ——— Initialize logging ———
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ——— Database setup ———
async def init_db():
    """Create tables for channels and broadcasts if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                channel_id INTEGER PRIMARY KEY
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT,
                private_chat_id INTEGER,
                private_message_id INTEGER,
                channel_id INTEGER,
                channel_message_id INTEGER
            )
        """)
        await db.commit()

# ——— Handlers ———

async def register_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Auto-register a channel when you forward one of its messages
    into the registration channel.
    """
    msg: Message = update.effective_message
    if msg.chat.id != REGISTRATION_CHAT_ID:
        return
    src = msg.forward_from_chat
    if not src:
        return  # ignore non-channel forwards
    cid = src.id
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT OR IGNORE INTO channels (channel_id) VALUES (?)",
                (cid,)
            )
            await db.commit()
            await msg.reply_text(f"✅ Registered channel `{cid}`")
        except Exception as e:
            logger.error(f"Error registering {cid}: {e}")

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start — show bot status and how many channels are registered."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM channels") as cur:
            total = (await cur.fetchone())[0]
    await update.message.reply_text(
        f"🤖 Bot is online.\n📡 Registered channels: {total}"
    )

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /broadcast — reply to your own private message to send it to all channels.
    Records each copy so it can later be deleted en masse.
    """
    if not update.message.reply_to_message:
        return await update.message.reply_text("❌ Please reply to a message to broadcast.")
    orig: Message = update.message.reply_to_message
    batch_id = str(int(time.time()))
    successes = failures = 0

    # Fetch registered channels
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT channel_id FROM channels") as cur:
            channels = [row[0] for row in await cur.fetchall()]

        # Copy message into each channel & record mapping
        for ch in channels:
            try:
                sent = await context.bot.copy_message(
                    chat_id=ch,
                    from_chat_id=orig.chat.id,
                    message_id=orig.message_id
                )
                await db.execute(
                    """INSERT INTO broadcasts
                       (batch_id, private_chat_id, private_message_id, channel_id, channel_message_id)
                       VALUES (?, ?, ?, ?, ?)""",
                    (batch_id, update.effective_chat.id, orig.message_id, ch, sent.message_id)
                )
                successes += 1
            except Exception as e:
                logger.warning(f"Broadcast to {ch} failed: {e}")
                failures += 1
        await db.commit()

    await update.message.reply_text(
        f"📤 Broadcast done!\n"
        f"✅ Success: {successes}\n"
        f"❌ Failed:  {failures}\n"
        f"📋 Total channels: {len(channels)}"
    )

async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /delete — reply to your original broadcast message in private chat.
    Deletes that batch from all channels where it was posted.
    """
    if not update.message.reply_to_message:
        return await update.message.reply_text("❌ Reply to your broadcast message to delete.")
    orig_mid = update.message.reply_to_message.message_id
    async with aiosqlite.connect(DB_PATH) as db:
        # Identify batch via private message mapping
        async with db.execute(
            """SELECT batch_id FROM broadcasts
               WHERE private_chat_id = ? AND private_message_id = ?""",
            (update.effective_chat.id, orig_mid)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return await update.message.reply_text("❌ No record found for that broadcast.")

        batch_id = row[0]
        async with db.execute(
            "SELECT channel_id, channel_message_id FROM broadcasts WHERE batch_id = ?",
            (batch_id,)
        ) as cur:
            entries = await cur.fetchall()

        deleted = not_found = 0
        for ch, msg_id in entries:
            try:
                await context.bot.delete_message(chat_id=ch, message_id=msg_id)
                deleted += 1
            except Exception:
                not_found += 1

    await update.message.reply_text(
        f"🗑 Delete complete.\n"
        f"✅ Deleted:     {deleted}\n"
        f"❓ Not found:   {not_found}\n"
        f"📋 Total in batch: {len(entries)}"
    )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Log all exceptions and notify the admin."""
    logger.error("Exception while handling update:", exc_info=context.error)

def main():
    # Initialize DB & bot
    import asyncio
    asyncio.run(init_db())
    app = Application.builder().token(API_TOKEN).build()

    # Forward-watcher for dynamic registration
    app.add_handler(
        MessageHandler(
            filters.Chat(chat_id=REGISTRATION_CHAT_ID) & filters.FORWARDED,
            register_forward
        )
    )

    # Command handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))

    # Global error handler
    app.add_error_handler(error_handler)

    # Start polling
    app.run_polling()

if __name__ == "__main__":
    main()
