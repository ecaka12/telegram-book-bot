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

# List all books
async def list_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = books_col.count_documents({})
    if count == 0:
        await update.message.reply_text("📭 No books available yet.")
        return
    
    books = books_col.find().sort("_id", 1)
    message = "📚 Available Books:\n\n"
    
    for book in books:
        message += f"{book['_id']}. {book['title']} by {book['author']}\n"
    
    message += "\nUse `/book <id>` to view details."
    await update.message.reply_text(message)

# Search books
async def search_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /search <keyword>")
        return
    
    keyword = ' '.join(context.args).lower()
    results = books_col.find({
        "$or": [
            {"title": {"$regex": keyword, "$options": "i"}},
            {"author": {"$regex": keyword, "$options": "i"}}
        ]
    })
    
    if results.count() == 0:
        await update.message.reply_text("❌ No books found.")
        return
    
    message = "🔍 Search Results:\n\n"
    for book in results:
        message += f"{book['_id']}. {book['title']} by {book['author']}\n"
    
    await update.message.reply_text(message)

# Top downloaded books
async def top_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top_books = books_col.find().sort("downloads", -1).limit(5)
    
    if top_books.count() == 0:
        await update.message.reply_text("📭 No books available.")
        return
    
    message = "🏆 Top Downloaded Books:\n\n"
    for book in top_books:
        message += f"{book['_id']}. {book['title']} ({book['downloads']} downloads)\n"
    
    await update.message.reply_text(message)

# User stats
async def user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    downloads = user_downloads_col.find_one({"_id": user_id})
    bookmarks = bookmarks_col.find_one({"_id": user_id})
    
    message = "📊 Your Stats:\n\n"
    message += f"📥 Downloads: {downloads['count'] if downloads else 0}\n"
    message += f"🔖 Bookmarks: {len(bookmarks['books']) if bookmarks else 0}"
    
    await update.message.reply_text(message)

# Notifications
async def notify_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    subscribers_col.update_one(
        {"_id": user_id},
        {"$set": {"notifications": True}},
        upsert=True
    )
    await update.message.reply_text("🔔 You will now receive notifications for new books.")

async def notify_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    subscribers_col.delete_one({"_id": user_id})
    await update.message.reply_text("🔕 You will no longer receive notifications.")

# [Previous functions: upload_book, scan_books, view_book, button_handler, message_handler]

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("books", list_books))
    app.add_handler(CommandHandler("search", search_books))
    app.add_handler(CommandHandler("top_books", top_books))
    app.add_handler(CommandHandler("mystats", user_stats))
    app.add_handler(CommandHandler("notify_on", notify_on))
    app.add_handler(CommandHandler("notify_off", notify_off))
    app.add_handler(CommandHandler("upload", upload_book))
    app.add_handler(CommandHandler("scan", scan_books))
    app.add_handler(CommandHandler("book", view_book))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.ALL, message_handler))
    
    logger.info("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
