# Required imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, ChatMemberAdministrator, ChatMemberOwner
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
    member = await chat.get_member(user_id)
    return isinstance(member, (ChatMemberAdministrator, ChatMemberOwner))

def parse_book_info(text):
    patterns = [
        r"(.*?)\s*by\s*(.*?)\s*-\s*(.*)",
        r"(.*?)\s*\|\s*(.*?)\s*\|\s*(.*)",
        r"(.*?)\s*-\s*(.*?)\s*-\s*(.*)"
    ]
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return match.groups()
    return text.strip(), "Unknown", "Tamil Novel"

# --- COMMAND HANDLERS ---
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

async def books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    books = list(books_col.find().sort("upload_date", -1).limit(10))
    if not books:
        await update.message.reply_text("No books found.")
        return

    msg = "📚 Recent Books:\n\n"
    for b in books:
        msg += f"{b['_id']}. {b['title']} by {b['author']}\n"
    await update.message.reply_text(msg)

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /search <keyword>")
        return
    query = " ".join(context.args)
    books = books_col.find({"$text": {"$search": query}})
    results = list(books)
    if not results:
        await update.message.reply_text("No results found.")
        return

    msg = f"🔍 Results for '{query}':\n\n"
    for b in results[:10]:
        msg += f"{b['_id']}. {b['title']} by {b['author']}\n"
    await update.message.reply_text(msg)

async def top_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    books = books_col.find().sort("downloads", -1).limit(10)
    msg = "🔥 Top Books:\n\n"
    for b in books:
        msg += f"{b['_id']}. {b['title']} ({b['downloads']} downloads)\n"
    await update.message.reply_text(msg)

async def book_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /book <id>")
        return
    book_id = context.args[0]
    book = books_col.find_one({"_id": book_id})
    if not book:
        await update.message.reply_text("Book not found.")
        return

    caption = f"📖 *{book['title']}*\n👤 {book['author']}\n🏷 {book['category']}\n📥 {book['downloads']} downloads"
    await update.message.reply_document(
        book["file_id"], caption=caption, parse_mode="Markdown"
    )
    books_col.update_one({"_id": book_id}, {"$inc": {"downloads": 1}})

    user_id = str(update.message.from_user.id)
    user_downloads_col.update_one(
        {"user_id": user_id},
        {"$inc": {"downloads": 1}},
        upsert=True
    )

async def notify_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    subscribers_col.update_one({"user_id": user_id}, {"$set": {"notify": True}}, upsert=True)
    await update.message.reply_text("✅ You will now receive notifications for new books.")

async def notify_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    subscribers_col.update_one({"user_id": user_id}, {"$set": {"notify": False}}, upsert=True)
    await update.message.reply_text("🔕 You have unsubscribed from notifications.")

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    stats = user_downloads_col.find_one({"user_id": user_id})
    count = stats["downloads"] if stats else 0
    await update.message.reply_text(f"📊 You have downloaded {count} books.")

async def scan_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMINS:
        await update.message.reply_text("🚫 You are not authorized to run this command.")
        return

    limit = int(context.args[0]) if context.args and context.args[0].isdigit() else 360
    await update.message.reply_text(f"🔎 Scanning last {limit} messages in Tamil Novels topic...")

    scanned = added = skipped = 0
    current_cover = None
    last_id = 0

    while scanned < limit:
        try:
            response = await context.bot._requester.post(
                "getChatHistory",
                data={
                    "chat_id": GROUP_CHAT_ID,
                    "message_thread_id": TAMIL_NOVELS_TOPIC_ID,
                    "limit": min(100, limit - scanned),
                    "offset": last_id
                }
            )
            msgs = response.get("result", [])
            if not msgs:
                break

            for m in msgs:
                scanned += 1
                last_id = m["message_id"] + 1

                if "photo" in m:
                    current_cover = {
                        "file_id": m["photo"][-1]["file_id"],
                        "caption": m.get("caption"),
                        "date": datetime.fromtimestamp(m["date"])
                    }
                    continue

                doc = m.get("document")
                if doc and doc.get("file_name", "").endswith(".pdf"):
                    if books_col.find_one({"file_id": doc["file_id"]}):
                        skipped += 1
                        continue

                    if current_cover and current_cover["caption"]:
                        title, author, category = parse_book_info(current_cover["caption"])
                    else:
                        fn = doc["file_name"].rsplit(".", 1)[0]
                        title, author, category = parse_book_info(fn)

                    book_id = str(books_col.count_documents({}) + 1)
                    data = {
                        "_id": book_id,
                        "title": title,
                        "author": author,
                        "category": category,
                        "file_id": doc["file_id"],
                        "downloads": 0,
                        "upload_date": datetime.fromtimestamp(m["date"]),
                        "source_message_id": m["message_id"]
                    }
                    if current_cover and (datetime.fromtimestamp(m["date"]) - current_cover["date"]).total_seconds() < 300:
                        data["cover_id"] = current_cover["file_id"]

                    books_col.insert_one(data)
                    added += 1
                    current_cover = None

            if len(msgs) < min(100, limit - scanned):
                break
            await asyncio.sleep(0.2)

        except Exception as e:
            logger.error("❌ Failed to fetch topic history: %s", e)
            await update.message.reply_text(f"❌ Failed to scan topic: {str(e)}")
            return

    await update.message.reply_text(
        f"📊 Scan Results:\n"
        f"🔍 Scanned: {scanned} messages\n"
        f"📚 Added: {added} new books\n"
        f"⏩ Skipped: {skipped} duplicates"
    )

# --- FILTER HANDLERS ---
async def auto_delete_joins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except:
        pass

async def block_telegram_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if re.search(r"t\.me|telegram\.me|joinchat", update.message.text):
        try:
            await update.message.delete()
        except:
            pass

# --- MAIN ENTRYPOINT ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan_books))
    app.add_handler(CommandHandler("books", books))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("top_books", top_books))
    app.add_handler(CommandHandler("book", book_detail))
    app.add_handler(CommandHandler("notify_on", notify_on))
    app.add_handler(CommandHandler("notify_off", notify_off))
    app.add_handler(CommandHandler("mystats", my_stats))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, auto_delete_joins))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, auto_delete_joins))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, block_telegram_links))

    logger.info("Bot running...")
    app.run_polling()

if __name__ == '__main__':
    main()
