# Required imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, filters, CallbackQueryHandler
)
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
RESTRICTED_TOPIC_IDS = [2, 21, 3]  # Topic IDs to restrict messages

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

# --- COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello! I'm NovelTamizh Bot. I help manage your group!\n\n"
        "Commands:\n"
        "/books - View all books\n"
        "/search <keyword> - Search\n"
        "/top_books - Top downloads\n"
        "/book <id> - View details\n"
        "/notify_on - Enable notifications\n"
        "/notify_off - Disable notifications\n"
        "/mystats - Your stats\n"
        "/scan [limit] - Scan topic"
    )

async def upload_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if user.id not in ADMINS:
        await update.message.reply_text("🚫 You are not authorized.")
        return

    if not (update.message.document or update.message.photo):
        await update.message.reply_text(
            "📎 Please send:\n1. A cover photo\n2. Then the PDF\nCaption: /upload <title> | <author> | <category>"
        )
        return

    if update.message.photo and not context.user_data.get('upload_state'):
        context.user_data['upload_state'] = 'awaiting_pdf'
        context.user_data['cover_photo_id'] = update.message.photo[-1].file_id
        context.user_data['upload_args'] = context.args
        await update.message.reply_text("✅ Cover received. Now send the PDF.")
        return

    if context.user_data.get('upload_state') == 'awaiting_pdf' and update.message.document:
        if not update.message.document.file_name.endswith(".pdf"):
            await update.message.reply_text("❌ Only PDF files allowed.")
            return

        title, author, category = parse_book_info(' '.join(context.user_data['upload_args']))
        book_id = str(books_col.count_documents({}) + 1)

        books_col.insert_one({
            "_id": book_id,
            "title": title,
            "author": author,
            "category": category,
            "file_id": update.message.document.file_id,
            "cover_id": context.user_data['cover_photo_id'],
            "downloads": 0,
            "upload_date": datetime.now(),
            "uploader": user.id
        })

        for user_data in subscribers_col.find():
            try:
                await context.bot.send_photo(
                    chat_id=user_data["_id"],
                    photo=context.user_data['cover_photo_id'],
                    caption=f"🆕 New book: {title}\nby {author}\nUse /book {book_id}"
                )
            except:
                continue

        context.user_data.clear()
        await update.message.reply_text(f"✅ Book uploaded: {title} ({category})")

async def scan_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id not in ADMINS:
        await update.message.reply_text("🚫 You are not authorized.")
        return

    limit = int(context.args[0]) if context.args and context.args[0].isdigit() else 360
    await update.message.reply_text(f"🔎 Scanning last {limit} messages...")

    try:
        messages = []
        async for message in context.bot.get_forum_topic_messages(
            chat_id=GROUP_CHAT_ID,
            message_thread_id=TAMIL_NOVELS_TOPIC_ID
        ):
            messages.append(message)
            if len(messages) >= limit:
                break
        messages.reverse()
    except Exception as e:
        logger.error("❌ Error: %s", e)
        await update.message.reply_text(f"❌ Failed to scan topic: {str(e)}")
        return

    scanned, added, skipped = 0, 0, 0
    current_cover = None

    for message in messages:
        scanned += 1
        try:
            if message.photo:
                current_cover = {
                    "file_id": message.photo[-1].file_id,
                    "caption": message.caption,
                    "date": message.date
                }
                continue

            if message.document and message.document.file_name.endswith(".pdf"):
                if books_col.find_one({"file_id": message.document.file_id}):
                    skipped += 1
                    continue

                if current_cover and current_cover["caption"]:
                    title, author, category = parse_book_info(current_cover["caption"])
                else:
                    title = os.path.splitext(message.document.file_name)[0]
                    title, author, category = parse_book_info(title)

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

                if current_cover and (message.date - current_cover["date"]).total_seconds() < 300:
                    book_data["cover_id"] = current_cover["file_id"]

                books_col.insert_one(book_data)
                added += 1
                current_cover = None
                await asyncio.sleep(0.5)

        except Exception as e:
            logger.error("Error message %s: %s", message.message_id, str(e))
            continue

    await update.message.reply_text(
        f"📊 Scan Results:\n"
        f"🔍 Scanned: {scanned}\n"
        f"📚 Added: {added}\n"
        f"⏩ Skipped: {skipped}"
    )

async def list_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    books = books_col.find().sort("_id", 1)
    message = "📚 Available Books:\n\n"
    for book in books:
        message += f"{book['_id']}. {book['title']} by {book['author']}\n"
    message += "\nUse /book <id> to view details."
    await update.message.reply_text(message)

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
        logger.error("Error displaying book %s: %s", book['_id'], str(e))
        await update.message.reply_text("❌ Error displaying book.")

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
        books_col.update_one({"_id": book_id}, {"$inc": {"downloads": 1}})
        user_downloads_col.update_one({"_id": user_id}, {"$inc": {"count": 1}}, upsert=True)
        try:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=book["file_id"],
                caption=f"📘 {book['title']}"
            )
        except Exception as e:
            logger.error("Download error %s: %s", book_id, str(e))
            await query.edit_message_text("❌ Failed to send document.")
    elif data.startswith("bookmark_"):
        book_id = data.split("_")[1]
        bookmark_data = bookmarks_col.find_one({"_id": user_id})
        if not bookmark_data:
            bookmarks_col.insert_one({"_id": user_id, "books": [book_id]})
            await query.answer("🔖 Bookmarked!")
        elif book_id not in bookmark_data["books"]:
            bookmarks_col.update_one({"_id": user_id}, {"$push": {"books": book_id}})
            await query.answer("🔖 Bookmarked!")
        else:
            await query.answer("⚠️ Already bookmarked.")

async def search_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /search <keyword>")
        return
    keyword = ' '.join(context.args)
    results = books_col.find({
        "$or": [
            {"title": {"$regex": keyword, "$options": "i"}},
            {"author": {"$regex": keyword, "$options": "i"}}
        ]
    })
    message = "🔍 Search Results:\n\n"
    found = False
    for book in results:
        message += f"{book['_id']}. {book['title']} by {book['author']}\n"
        found = True
    if not found:
        message = "❌ No books found."
    await update.message.reply_text(message)

async def top_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    books = books_col.find().sort("downloads", -1).limit(5)
    message = "🏆 Top Books:\n\n"
    for book in books:
        message += f"{book['_id']}. {book['title']} ({book['downloads']} downloads)\n"
    await update.message.reply_text(message)

async def user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    downloads = user_downloads_col.find_one({"_id": user_id})
    bookmarks = bookmarks_col.find_one({"_id": user_id})
    await update.message.reply_text(
        f"📊 Your Stats:\n\n"
        f"📥 Downloads: {downloads['count'] if downloads else 0}\n"
        f"🔖 Bookmarks: {len(bookmarks['books']) if bookmarks else 0}"
    )

async def notify_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    subscribers_col.update_one({"_id": user_id}, {"$set": {"notifications": True}}, upsert=True)
    await update.message.reply_text("🔔 Notifications enabled.")

async def notify_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    subscribers_col.delete_one({"_id": user_id})
    await update.message.reply_text("🔕 Notifications disabled.")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    user = message.from_user
    chat = message.chat
    topic_id = message.message_thread_id

    if message.new_chat_members or message.left_chat_member:
        try:
            await message.delete()
        except:
            pass
        return

    if message.text and any(link in message.text for link in ['t.me/', 'telegram.me/']):
        try:
            await message.reply_text("🚫 Telegram links not allowed.")
            await message.delete()
        except:
            pass
        return

    if topic_id and topic_id in RESTRICTED_TOPIC_IDS:
        if user.id not in ADMINS and not await is_admin(chat, user.id):
            try:
                await message.reply_text("❌ Only admins can post in this topic.")
                await message.delete()
            except:
                pass

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
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
    logger.info("Bot running...")
    app.run_polling()

if __name__ == '__main__':
    main()
