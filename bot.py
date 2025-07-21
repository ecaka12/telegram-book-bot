# Required imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, filters, CallbackQueryHandler
)
# MongoDB
from pymongo import MongoClient
import os
import logging
import time

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Set in Railway
ADMINS = [5504106603]  # Replace with your Telegram user ID
MONGO_URI = os.getenv("MONGO_URI")  # Set in Railway
GROUP_CHAT_ID = "-1002760881143"  # Your group chat ID
TAMIL_NOVELS_TOPIC_ID = 2  # Topic ID of "Tamil Novels"
RESTRICTED_TOPIC_IDS = [2, 21, 3]  # Add topic IDs if needed

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)
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

# --- COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! I'm NovelTamizh Bot. I help manage your group!\n\n"
                                    "Here are some commands you can use:\n"
                                    "/books - View all books\n"
                                    "/search <keyword> - Search for a book\n"
                                    "/top_books - See most downloaded books\n"
                                    "/book <id> - View details of a book\n"
                                    "/notify_on - Get notified when new books are uploaded\n"
                                    "/notify_off - Stop notifications\n"
                                    "/mystats - View your download stats\n"
                                    "/scan [limit] - Scan group topic for existing PDF books")

# Upload book via DM
async def upload_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔄 upload_book function triggered")
    user = update.message.from_user
    logger.info(f"User ID: {user.id}")
    if user.id not in ADMINS:
        logger.warning("🚫 User not in ADMINS")
        await update.message.reply_text("🚫 You are not authorized to upload books.")
        return
    if not update.message.document:
        logger.warning("📎 No document attached")
        await update.message.reply_text("📎 Please send a PDF file with the following format:\n"
                                        "/upload <title> | <author> | <category>")
        return
    document = update.message.document
    logger.info(f"📄 Document file name: {document.file_name}")
    if not document.file_name.endswith(".pdf"):
        logger.warning("❌ Invalid file type")
        await update.message.reply_text("❌ Only PDF files are allowed.")
        return
    if len(context.args) < 4:
        logger.warning("📝 Invalid command format")
        await update.message.reply_text("📝 Usage: /upload <title> | <author> | <category>")
        return

    title = context.args[0]
    author = context.args[2]
    category = context.args[4]
    logger.info(f"📖 Parsed book info: {title}, {author}, {category}")
    book_id = str(books_col.count_documents({}) + 1)
    file_id = document.file_id

    books_col.insert_one({
        "_id": book_id,
        "title": title,
        "author": author,
        "category": category,
        "file_id": file_id,
        "downloads": 0,
    })

    logger.info("✅ Book saved to MongoDB")

    # Notify subscribers
    for user_data in subscribers_col.find():
        user_id = user_data["_id"]
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🆕 New book uploaded: `{title}` by `{author}`\nUse `/book {book_id}` to view."
            )
        except Exception:
            pass  # Ignore users who have blocked the bot

    # 📢 Send to Tamil Novels topic
    try:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            message_thread_id=TAMIL_NOVELS_TOPIC_ID,
            text=f"📚 Tamil Novel Uploaded: `{title}` by `{author}`\n"
                 f"📁 ID: {book_id}\n"
                 f"📘 Use `/book {book_id}` to view details."
        )
    except Exception as e:
        logger.error("Failed to post in Tamil Novels topic: %s", e)

    await update.message.reply_text(f"✅ Book uploaded: `{title}` ({category})")
    logger.info("✅ Upload complete")

# Scan group topic for existing PDFs
async def scan_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("🔄 scan_books function triggered")
    user = update.message.from_user
    if user.id not in ADMINS:
        logger.warning("🚫 User not in ADMINS")
        await update.message.reply_text("🚫 You are not authorized to run this command.")
        return

    limit = 50  # Default scan limit
    if context.args and context.args[0].isdigit():
        limit = int(context.args[0])

    await update.message.reply_text(f"🔎 Scanning last {limit} messages in Tamil Novels topic...")

    try:
        # ✅ Uses get_forum_topic_messages (available in 20.2)
        messages = context.bot.get_forum_topic_messages(
            chat_id=GROUP_CHAT_ID,
            message_thread_id=TAMIL_NOVELS_TOPIC_ID,
            limit=limit
        )
    except Exception as e:
        logger.error("❌ Failed to fetch topic history: %s", e)
        await update.message.reply_text("❌ Failed to scan topic: " + str(e))
        return

    scanned = 0
    added = 0

    async for message in messages:
        scanned += 1
        if message.document and message.document.file_name.endswith(".pdf"):
            file_id = message.document.file_id
            file_name = message.document.file_name
            title = os.path.splitext(file_name)[0]
            author = "Unknown"
            category = "Tamil Novel"

            if " by " in title:
                parts = title.split(" by ")
                title = parts[0]
                author = parts[1]

            existing = books_col.find_one({"file_id": file_id})
            if existing:
                continue

            book_id = str(books_col.count_documents({}) + 1)
            books_col.insert_one({
                "_id": book_id,
                "title": title,
                "author": author,
                "category": category,
                "file_id": file_id,
                "downloads": 0,
            })
            added += 1

    await update.message.reply_text(f"✅ Scanned {scanned} messages\n📚 Found and added {added} new books.")
    logger.info(f"✅ Scanned {scanned} messages, added {added} books")

# List all books
async def list_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = books_col.count_documents({})
    if count == 0:
        await update.message.reply_text("📭 No books available yet.")
        return
    msg = "📚 Available Books:\n"
    for book in books_col.find().sort("_id", 1):
        msg += f"{book['_id']}. {book['title']} by {book['author']} ({book['category']})\n"
    msg += "\nUse `/book <id>` to view details."
    await update.message.reply_text(msg)

# View book details
async def view_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("UsageId: /book <book_id>")
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
    await update.message.reply_text(
        f"📘 **{book['title']}**\n"
        f"✍️ Author: {book['author']}\n"
        f"🗂 Category: {book['category']}\n"
        f"📄 ID: {book_id}",
        reply_markup=reply_markup
    )

# Handle button clicks
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
        user_downloads_col.update_one(
            {"_id": user_id},
            {"$inc": {"count": 1}},
            upsert=True
        )
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=book["file_id"],
            caption=f"📘 {book['title']}"
        )
    elif data.startswith("bookmark_"):
        book_id = data.split("_")[1]
        bookmark_data = bookmarks_col.find_one({"_id": user_id})
        if not bookmark_data:
            bookmarks_col.insert_one({"_id": user_id, "books": [book_id]})
            await query.answer("🔖 Bookmarked!")
        else:
            if book_id not in bookmark_data["books"]:
                bookmarks_col.update_one({"_id": user_id}, {"$push": {"books": book_id}})
                await query.answer("🔖 Bookmarked!")
            else:
                await query.answer("⚠️ Already bookmarked.")

# Search books
async def search_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("UsageId: /search <keyword>")
        return
    keyword = context.args[0].lower()
    results = []
    for book in books_col.find():
        if keyword in book["title"].lower() or keyword in book["author"].lower():
            results.append(f"{book['_id']}. {book['title']} by {book['author']}")
    if not results:
        await update.message.reply_text("❌ No books found.")
    else:
        await update.message.reply_text("🔍 Search Results:\n" + "\n".join(results))

# Top downloaded books
async def top_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top_books_list = books_col.find().sort("downloads", -1).limit(5)
    msg = "🏆 Top Downloaded Books:\n"
    count = 0
    for book in top_books_list:
        count += 1
        msg += f"{book['_id']}. {book['title']} ({book['downloads']} downloads)\n"
    if count == 0:
        await update.message.reply_text("📭 No books available.")
    else:
        await update.message.reply_text(msg)

# User stats
async def user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    downloads = user_downloads_col.find_one({"_id": user_id})
    count = downloads["count"] if downloads else 0
    bookmark_data = bookmarks_col.find_one({"_id": user_id})
    bks = bookmark_data["books"] if bookmark_data else []
    bookmarked = ", ".join(bks) if bks else "None"
    await update.message.reply_text(f"📊 **Your Stats**\n"
                                    f"📥 Books downloaded: {count}\n"
                                    f"🔖 Bookmarked books: {bookmarked}")

# Notifications
async def notify_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    subscribers_col.update_one({"_id": user_id}, {"$set": {"subscribed": True}}, upsert=True)
    await update.message.reply_text("🔔 You will be notified when new books are uploaded.")

async def notify_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    subscribers_col.delete_one({"_id": user_id})
    await update.message.reply_text("🔕 You will no longer receive notifications.")

# Log all incoming messages
async def log_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        logger.info("📨 Incoming message: %s", update.message.text or update.message.document)
        logger.info("User ID: %s", update.message.from_user.id)
        logger.info("Chat Type: %s", update.message.chat.type)
        logger.info("Document: %s", bool(update.message.document))

# Main message handler
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat = message.chat
    if not message or not chat:
        return
    # Delete join/leave messages
    if message.new_chat_members or message.left_chat_member:
        await message.delete()
        return
    # Delete messages with Telegram links
    if message.text and ('t.me/' in message.text or 'telegram.me/' in message.text):
        await message.reply_text("🚫 Telegram links are not allowed!")
        await message.delete()
    # Restrict messaging in specific topics
    topic_id = message.message_thread_id
    if topic_id and topic_id in RESTRICTED_TOPIC_IDS:
        if message.from_user.id not in ADMINS and not await is_admin(chat, message.from_user.id):
            await message.reply_text("❌ Only admins can send messages in this topic.")
            await message.delete()

# --- START BOT ---
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
