import logging
import sqlite3
from telegram import Update, MessageOriginType
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import config

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize SQLite database
conn = sqlite3.connect('bot.db')
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS broadcasts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        original_message_id INTEGER,
        broadcast_message_ids TEXT
    )
''')
conn.commit()

async def register_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    # Check if the message is forwarded
    if message.forward_origin and message.forward_origin.type == MessageOriginType.CHANNEL:
        original_channel_id = message.forward_origin.chat.id
        logger.info(f"Registered channel ID: {original_channel_id}")
        await message.reply_text(f"Channel ID {original_channel_id} registered.")
    else:
        logger.warning("Forwarded message lacks origin information.")
        await message.reply_text("Unable to register channel: no origin information found.")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message.reply_to_message:
        await message.reply_text("Please reply to the message you want to broadcast.")
        return

    original_message = message.reply_to_message
    broadcast_message_ids = []

    for channel_id in config.BROADCAST_CHANNEL_IDS:
        try:
            sent_message = await context.bot.copy_message(
                chat_id=channel_id,
                from_chat_id=original_message.chat.id,
                message_id=original_message.message_id
            )
            broadcast_message_ids.append(str(sent_message.message_id))
        except Exception as e:
            logger.error(f"Failed to broadcast to {channel_id}: {e}")

    # Store the broadcast information in the database
    cursor.execute(
        'INSERT INTO broadcasts (original_message_id, broadcast_message_ids) VALUES (?, ?)',
        (original_message.message_id, ','.join(broadcast_message_ids))
    )
    conn.commit()

    await message.reply_text("Broadcast completed.")

async def delete_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message.reply_to_message:
        await message.reply_text("Please reply to the broadcasted message you want to delete.")
        return

    original_message_id = message.reply_to_message.message_id

    # Retrieve the broadcast message IDs from the database
    cursor.execute(
        'SELECT broadcast_message_ids FROM broadcasts WHERE original_message_id = ?',
        (original_message_id,)
    )
    result = cursor.fetchone()

    if not result:
        await message.reply_text("No broadcast record found for this message.")
        return

    broadcast_message_ids = result[0].split(',')

    for channel_id, msg_id in zip(config.BROADCAST_CHANNEL_IDS, broadcast_message_ids):
        try:
            await context.bot.delete_message(chat_id=channel_id, message_id=int(msg_id))
        except Exception as e:
            logger.error(f"Failed to delete message {msg_id} in channel {channel_id}: {e}")

    # Optionally, remove the record from the database
    cursor.execute(
        'DELETE FROM broadcasts WHERE original_message_id = ?',
        (original_message_id,)
    )
    conn.commit()

    await message.reply_text("Broadcast messages deleted.")

def main():
    application = ApplicationBuilder().token(config.BOT_TOKEN).build()

    # Handler to register channels via forwarded messages
    application.add_handler(MessageHandler(
        filters.Chat(config.REGISTRATION_CHANNEL_ID) & filters.FORWARDED,
        register_channel
    ))

    # Handler for /broadcast command
    application.add_handler(CommandHandler("broadcast", broadcast))

    # Handler for /delete command
    application.add_handler(CommandHandler("delete", delete_broadcast))

    application.run_polling()

if __name__ == '__main__':
    main()
