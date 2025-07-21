# Required imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, filters, CallbackQueryHandler
)
# MongoDB
from pymongo import MongoClient
import os
import logging
import time
import re
from datetime import datetime
import asyncio

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Set in Railway
ADMINS = [5504106603]  # Replace with your Telegram user ID
MONGO_URI = os.getenv("MONGO_URI")  # Set in Railway
GROUP_CHAT_ID = "-1002760881143"  # Your group chat ID
TAMIL_NOVELS_TOPIC_ID = 2  # Topic ID of "Tamil Novels"
RESTRICTED_TOPIC_IDS = [2, 21, 3]  # Add topic IDs if needed

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- MONGODB CONNECTION ---
try:
    client = MongoClient(MONGO_URI)
    client.admin.command('ping')
    logger.info("✅ MongoDB connected successfully")
except Exception as e:
    logger.error("❌ MongoDB connection failed: %s", e)
    raise

db = client.telegram_bot
books_col = db.books
bookmarks_col = db.bookmarks
user_downloads_col = db.user_downloads
subscribers_col = db.subscribers

# --- HELPER FUNCTIONS ---
async def is_admin(chat, user_id):
    from telegram import ChatMemberAdministrator, ChatMemberOwner
    member = await chat.get_member(user_id)
    return isinstance(member, (ChatMemberAdministrator, ChatMemberOwner))

def parse_book_info(text):
    """Parse book info from message text"""
    patterns = [
        r"(.*?)\s*by\s*(.*?)\s*-\s*(.*)",  # Title by Author - Category
        r"(.*?)\s*\|\s*(.*?)\s*\|\s*(.*)",  # Title | Author | Category
        r"(.*?)\s*-\s*(.*?)\s*-\s*(.*)"     # Title - Author - Category
    ]
    
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return match.groups()
    
    return text.strip(), "Unknown", "Tamil Novel"

async def download_file(bot, file_id, file_name):
    """Download file from Telegram servers"""
    file = await bot.get_file(file_id)
    return await file.download_to_drive(custom_path=file_name)

# --- COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello! I'm NovelTamizh Bot. I help manage your group!\n\n"
        "Here are some commands you can use:\n"
        "/books - View all books\n"
        "/search <keyword> - Search for a book\n"
        "/top_books - See most downloaded books\n"
        "/book <id> - View details of a book\n"
        "/notify_on - Get notified when new books are uploaded\n"
        "/notify_off - Stop notifications\n"
        "/mystats - View your download stats\n"
        "/scan [limit] - Scan group topic for existing PDF books"
    )

# Upload book with cover
async def upload_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔄 upload_book function triggered")
    user = update.message.from_user
    
    if user.id not in ADMINS:
        await update.message.reply_text("🚫 You are not authorized to upload books.")
        return
    
    if not (update.message.document or update.message.photo):
        await update.message.reply_text(
            "📎 Please send:\n"
            "1. A cover photo (first)\n"
            "2. The PDF file\n"
            "With caption: /upload <title> | <author> | <category>"
        )
        return
    
    # Check if we're receiving the cover photo
    if update.message.photo and not hasattr(context.user_data, 'upload_state'):
        context.user_data.upload_state = 'awaiting_pdf'
        context.user_data.cover_photo_id = update.message.photo[-1].file_id
        await update.message.reply_text("✅ Cover photo received. Now please send the PDF file.")
        return
    
    # Check if we're receiving the PDF
    if hasattr(context.user_data, 'upload_state') and update.message.document:
        if not update.message.document.file_name.endswith(".pdf"):
            await update.message.reply_text("❌ Only PDF files are allowed.")
            return
        
        if not context.args or len(context.args) < 3:
            await update.message.reply_text("📝 Usage: /upload <title> | <author> | <category>")
            return
        
        # Process the upload
        title, author, category = parse_book_info(' '.join(context.args))
        book_id = str(books_col.count_documents({}) + 1)
        pdf_file_id = update.message.document.file_id
        cover_photo_id = context.user_data.cover_photo_id
        
        # Save to database
        books_col.insert_one({
            "_id": book_id,
            "title": title,
            "author": author,
            "category": category,
            "file_id": pdf_file_id,
            "cover_id": cover_photo_id,
            "downloads": 0,
            "upload_date": datetime.now(),
            "uploader": user.id
        })
        
        # Clear upload state
        del context.user_data.upload_state
        del context.user_data.cover_photo_id
        
        # Notify subscribers
        for user_data in subscribers_col.find():
            try:
                await context.bot.send_photo(
                    chat_id=user_data["_id"],
                    photo=cover_photo_id,
                    caption=f"🆕 New book uploaded: {title}\nby {author}\nUse /book {book_id} to view"
                )
            except Exception:
                continue
        
        # Post in Tamil Novels topic
        try:
            await context.bot.send_photo(
                chat_id=GROUP_CHAT_ID,
                message_thread_id=TAMIL_NOVELS_TOPIC_ID,
                photo=cover_photo_id,
                caption=(
                    f"📚 Tamil Novel Uploaded: {title}\n"
                    f"✍️ Author: {author}\n"
                    f"📁 ID: {book_id}\n"
                    f"📘 Use /book {book_id} to view details"
                )
            )
        except Exception as e:
            logger.error("Failed to post in Tamil Novels topic: %s", e)
        
        await update.message.reply_text(f"✅ Book uploaded: {title} ({category})")
        return
    
    await update.message.reply_text("❌ Please follow the upload process correctly.")

# Improved scan function with better parsing
async def scan_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMINS:
        await update.message.reply_text("🚫 You are not authorized to run this command.")
        return

    limit = 180  # Default to scanning all 180 books
    if context.args and context.args[0].isdigit():
        limit = int(context.args[0])

    await update.message.reply_text(f"🔎 Scanning last {limit} messages in Tamil Novels topic...")

    try:
        messages = []
        async for message in context.bot.get_chat_history(
            chat_id=GROUP_CHAT_ID,
            limit=limit,
            message_thread_id=TAMIL_NOVELS_TOPIC_ID
        ):
            messages.append(message)
        
        # Process in reverse order (oldest first)
        messages.reverse()
    except Exception as e:
        logger.error("❌ Failed to fetch topic history: %s", e)
        await update.message.reply_text(f"❌ Failed to scan topic: {str(e)}")
        return

    scanned = 0
    added = 0
    skipped = 0

    for message in messages:
        scanned += 1
        try:
            # Skip non-document messages
            if not message.document or not message.document.file_name.endswith(".pdf"):
                continue
            
            # Check if already exists
            file_id = message.document.file_id
            if books_col.find_one({"file_id": file_id}):
                skipped += 1
                continue
            
            # Parse book info from message text or filename
            if message.caption:
                title, author, category = parse_book_info(message.caption)
            else:
                filename = message.document.file_name
                title = os.path.splitext(filename)[0]
                title, author, category = parse_book_info(title)
            
            # Create book entry
            book_id = str(books_col.count_documents({}) + 1)
            books_col.insert_one({
                "_id": book_id,
                "title": title,
                "author": author,
                "category": category,
                "file_id": file_id,
                "downloads": 0,
                "upload_date": message.date,
                "source_message_id": message.message_id
            })
            added += 1
            
            # Add small delay to avoid rate limiting
            await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.error(f"Error processing message {message.message_id}: {str(e)}")
            continue

    await update.message.reply_text(
        f"📊 Scan Results:\n"
        f"🔍 Scanned: {scanned} messages\n"
        f"📚 Added: {added} new books\n"
        f"⏩ Skipped: {skipped} duplicates"
    )
    logger.info(f"Scan complete - Added {added} books from {scanned} messages")

# Enhanced book view with cover image
async def view_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /book <book_id>")
        return
    
    book_id = context.args[0]
    book = books_col.find_one({"_id": book_id})
    
    if not book:
        await update.message.reply_text("❌ Book not found.")
        return
    
    buttons = [
        [InlineKeyboardButton("📥 Download PDF", callback_data=f"download_{book_id}")],
        [InlineKeyboardButton("🔖 Bookmark", callback_data=f"bookmark_{book_id}")]
    ]
    
    reply_markup = InlineKeyboardMarkup(buttons)
    caption = (
        f"📘 {book['title']}\n"
        f"✍️ Author: {book['author']}\n"
        f"🗂 Category: {book['category']}\n"
        f"📅 Uploaded: {book.get('upload_date', 'Unknown').strftime('%Y-%m-%d') if 'upload_date' in book else 'Unknown'}\n"
        f"📥 Downloads: {book['downloads']}\n"
        f"📄 ID: {book_id}"
    )
    
    try:
        if 'cover_id' in book:
            await context.bot.send_photo(
                chat_id=update.message.chat_id,
                photo=book['cover_id'],
                caption=caption,
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                caption,
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error sending book {book_id}: {str(e)}")
        await update.message.reply_text(
            "❌ Error displaying book details. Please try again."
        )

# Updated button handler for cover images
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = str(query.from_user.id)
    
    if data.startswith("download_"):
        book_id = data.split("_")[1]
        book = books_col.find_one({"_id": book_id})
        
        if not book:
            await query.edit_message_text("❌ Book not found.")
            return
        
        # Update download counts
        books_col.update_one({"_id": book_id}, {"$inc": {"downloads": 1}})
        user_downloads_col.update_one(
            {"_id": user_id},
            {"$inc": {"count": 1}},
            upsert=True
        )
        
        # Send document
        try:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=book["file_id"],
                caption=f"📘 {book['title']}"
            )
        except Exception as e:
            logger.error(f"Error sending document {book_id}: {str(e)}")
            await query.edit_message_text("❌ Failed to send document. Please try again.")
    
    elif data.startswith("bookmark_"):
        book_id = data.split("_")[1]
        bookmark_data = bookmarks_col.find_one({"_id": user_id})
        
        if not bookmark_data:
            bookmarks_col.insert_one({"_id": user_id, "books": [book_id]})
            await query.answer("🔖 Bookmarked!")
        else:
            if book_id not in bookmark_data["books"]:
                bookmarks_col.update_one(
                    {"_id": user_id},
                    {"$push": {"books": book_id}}
                )
                await query.answer("🔖 Bookmarked!")
            else:
                await query.answer("⚠️ Already bookmarked.")

# --- Other command handlers remain largely the same ---
# (list_books, search_books, top_books, user_stats, notify_on, notify_off)

# Improved message handler
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return
    
    chat = message.chat
    user = message.from_user
    
    # Delete join/leave messages
    if message.new_chat_members or message.left_chat_member:
        try:
            await message.delete()
        except Exception:
            pass
        return
    
    # Delete messages with Telegram links
    if message.text and any(link in message.text for link in ['t.me/', 'telegram.me/']):
        try:
            await message.reply_text("🚫 Telegram links are not allowed!")
            await message.delete()
        except Exception:
            pass
        return
    
    # Restrict messaging in specific topics
    topic_id = message.message_thread_id
    if topic_id and topic_id in RESTRICTED_TOPIC_IDS:
        if user.id not in ADMINS and not await is_admin(chat, user.id):
            try:
                await message.reply_text("❌ Only admins can send messages in this topic.")
                await message.delete()
            except Exception:
                pass
            return

# --- START BOT ---
def main():
    # Build and run the bot
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add handlers
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(CommandHandler("upload", upload_book))
    app_bot.add_handler(CommandHandler("books", list_books))
    app_bot.add_handler(CommandHandler("book", view_book))
    app_bot.add_handler(CommandHandler("search", search_books))
    app_bot.add_handler(CommandHandler("top_books", top_books))
    app_bot.add_handler(CommandHandler("mystats", user_stats))
    app_bot.add_handler(CommandHandler("notify_on", notify_on))
    app_bot.add_handler(CommandHandler("notify_off", notify_off))
    app_bot.add_handler(CommandHandler("scan", scan_books))
    app_bot.add_handler(CallbackQueryHandler(button_handler))
    app_bot.add_handler(MessageHandler(filters.ALL, log_all_messages), group=0)
    app_bot.add_handler(MessageHandler(filters.ALL, message_handler))

    # Start the bot with retry loop
    logger.info("Bot is running...")
    
    while True:
        try:
            app_bot.run_polling()
        except Exception as e:
            logger.error("Error: %s", e)
            logger.info("Retrying in 10 seconds...")
            time.sleep(10)

if __name__ == '__main__':
    main()
