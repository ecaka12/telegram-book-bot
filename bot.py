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
        "/scan [limit] - Scan group topic for existing books"
    )

# Upload book with cover
async def upload_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    # Handle cover photo
    if update.message.photo and not hasattr(context.user_data, 'upload_state'):
        context.user_data.upload_state = 'awaiting_pdf'
        context.user_data.cover_photo_id = update.message.photo[-1].file_id
        context.user_data.upload_args = context.args
        await update.message.reply_text("✅ Cover photo received. Now please send the PDF file.")
        return
    
    # Handle PDF document
    if hasattr(context.user_data, 'upload_state') and update.message.document:
        if not update.message.document.file_name.endswith(".pdf"):
            await update.message.reply_text("❌ Only PDF files are allowed.")
            return
        
        title, author, category = parse_book_info(' '.join(context.user_data.upload_args))
        book_id = str(books_col.count_documents({}) + 1)
        
        books_col.insert_one({
            "_id": book_id,
            "title": title,
            "author": author,
            "category": category,
            "file_id": update.message.document.file_id,
            "cover_id": context.user_data.cover_photo_id,
            "downloads": 0,
            "upload_date": datetime.now(),
            "uploader": user.id
        })
        
        # Clear upload state
        del context.user_data.upload_state
        del context.user_data.cover_photo_id
        del context.user_data.upload_args
        
        # Notify subscribers
        for user_data in subscribers_col.find():
            try:
                await context.bot.send_photo(
                    chat_id=user_data["_id"],
                    photo=context.user_data.cover_photo_id,
                    caption=f"🆕 New book: {title}\nby {author}\nUse /book {book_id}"
                )
            except Exception:
                continue
        
        await update.message.reply_text(f"✅ Book uploaded: {title} ({category})")

# Enhanced scanning for 360 messages (180 covers + 180 PDFs)
async def scan_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMINS:
        await update.message.reply_text("🚫 You are not authorized to run this command.")
        return

    limit = 360  # Default to scanning all 360 messages
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
    current_cover = None

    for message in messages:
        scanned += 1
        try:
            # Store cover photos temporarily
            if message.photo:
                current_cover = {
                    "file_id": message.photo[-1].file_id,
                    "caption": message.caption,
                    "date": message.date
                }
                continue
            
            # Process PDF documents
            if message.document and message.document.file_name.endswith(".pdf"):
                # Check if already exists
                if books_col.find_one({"file_id": message.document.file_id}):
                    skipped += 1
                    continue
                
                # Parse book info
                if current_cover and current_cover["caption"]:
                    title, author, category = parse_book_info(current_cover["caption"])
                else:
                    filename = message.document.file_name
                    title = os.path.splitext(filename)[0]
                    title, author, category = parse_book_info(title)
                
                # Create book entry
                book_id = str(books_col.count_documents({}) + 1)
                book_data = {
                    "_id": book_id,
                    "title": title,
                    "author": author,
                    "category": category,
                    "file_id": message.document.file_id,
                    "downloads": 0,
                    "upload_date": message.date,
                    "source_message_id": message.message_id
                }
                
                # Add cover if available (within 5 minutes of PDF)
                if current_cover and (message.date - current_cover["date"]).total_seconds() < 300:
                    book_data["cover_id"] = current_cover["file_id"]
                
                books_col.insert_one(book_data)
                added += 1
                current_cover = None  # Reset cover after pairing
                
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

# View book with cover image
async def view_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /book <book_id>")
        return
    
    book = books_col.find_one({"_id": context.args[0]})
    if not book:
        await update.message.reply_text("❌ Book not found.")
        return
    
    buttons = [
        [InlineKeyboardButton("📥 Download PDF", callback_data=f"download_{book['_id']}")],
        [InlineKeyboardButton("🔖 Bookmark", callback_data=f"bookmark_{book['_id']}")]
    ]
    
    caption = (
        f"📘 {book['title']}\n"
        f"✍️ Author: {book['author']}\n"
        f"🗂 Category: {book['category']}\n"
        f"📅 Uploaded: {book.get('upload_date', 'Unknown').strftime('%Y-%m-%d') if 'upload_date' in book else 'Unknown'}\n"
        f"📥 Downloads: {book['downloads']}"
    )
    
    try:
        if 'cover_id' in book:
            await context.bot.send_photo(
                chat_id=update.message.chat_id,
                photo=book['cover_id'],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        else:
            await update.message.reply_text(
                caption,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
    except Exception as e:
        logger.error(f"Error displaying book {book['_id']}: {str(e)}")
        await update.message.reply_text("❌ Error displaying book details.")

# --- Other handlers (list_books, search_books, etc.) remain the same ---

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("upload", upload_book))
    app.add_handler(CommandHandler("scan", scan_books))
    app.add_handler(CommandHandler("book", view_book))
    # Add other command handlers...
    
    logger.info("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
