from pymongo import MongoClient
import os

# MongoDB connection
client = MongoClient(os.getenv("MONGO_URI"))
db = client.telegram_bot

books_col = db.books
bookmarks_col = db.bookmarks
user_downloads_col = db.user_downloads
subscribers_col = db.subscribers


# Required imports
from telegram import Update, ChatMemberAdministrator, ChatMemberOwner, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler, CallbackQueryHandler

# Flask for dummy web server
from flask import Flask
from threading import Thread

# Replace with your bot token
import os
BOT_TOKEN = os.getenv("BOT_TOKEN")

# List of admins (replace with your Telegram user ID)
ADMINS = [550283475456603]  # Replace with your Telegram user ID


# Helper function to check if user is an admin
async def is_admin(chat, user_id):
    member = await chat.get_member(user_id)
    return isinstance(member, (ChatMemberAdministrator, ChatMemberOwner))

# --- COMMANDS ---

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! I'm NovelTamizh Bot. I help manage your group!\n\n"
                                    "Here are some commands you can use:\n"
                                    "/books - View all books\n"
                                    "/search <keyword> - Search for a book\n"
                                    "/top_books - See most downloaded books\n"
                                    "/book <id> - View details of a book\n"
                                    "/notify_on - Get notified when new books are uploaded\n"
                                    "/notify_off - Stop notifications\n"
                                    "/mystats - View your download stats")

# Upload book via DM
async def upload_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    if user.id not in ADMINS:
        await update.message.reply_text("🚫 You are not authorized to upload books.")
        return

    if not update.message.document:
        await update.message.reply_text("📎 Please send a PDF file with the following format:\n\n"
                                        "/upload <title> | <author> | <category>")
        return

    document = update.message.document
    if not document.file_name.endswith(".pdf"):
        await update.message.reply_text("❌ Only PDF files are allowed.")
        return

    if len(context.args) < 4:
        await update.message.reply_text("📝 Usage: /upload <title> | <author> | <category>")
        return

    title = context.args[0]
    author = context.args[2]
    category = context.args[4]

    book_id = str(len(books) + 1)
    file_id = document.file_id

    book_id = str(books_col.count_documents({}) + 1)

books_col.insert_one({
    "_id": book_id,
    "title": title,
    "author": author,
    "category": category,
    "file_id": file_id,
    "downloads": 0,
})

    # Notify subscribers
    for user_id in subscribers:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🆕 New book uploaded: `{title}` by `{author}`\nUse `/book {book_id}` to view."
            )
        except Exception:
            pass  # Ignore users who have blocked the bot

    await update.message.reply_text(f"✅ Book uploaded: `{title}` ({category})")

# List all books
async def list_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not books:
        await update.message.reply_text("📭 No books available yet.")
        return

    msg = "📚 Available Books:\n\n"
    for book_id, data in books.items():
        msg += f"{book_id}. {data['title']} by {data['author']} ({data['category']})\n"

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
        book = books.get(book_id)
        if not book:
            await query.edit_message_text("❌ Book not found.")
            return

        books_col.update_one(
    {"_id": book_id},
    {"$inc": {"downloads": 1}}
)
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
else:
    if book_id not in bookmark_data["books"]:
        bookmarks_col.update_one(
            {"_id": user_id},
            {"$push": {"books": book_id}}
        )
            await query.answer("🔖 Book bookmarked!")
        else:
            await query.answer("⚠️ Already bookmarked.")

# Search books
async def search_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("UsageId: /search <keyword>")
        return

    keyword = context.args[0].lower()
    results = []

    for book_id, book in books.items():
        if keyword in book["title"].lower() or keyword in book["author"].lower():
            results.append(f"{book_id}. {book['title']} by {book['author']}")

    if not results:
        await update.message.reply_text("❌ No books found.")
    else:
        await update.message.reply_text("🔍 Search Results:\n" + "\n".join(results))

# Top downloaded books
async def top_books(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sorted_books = sorted(books.items(), key=lambda x: x[1]["downloads"], reverse=True)
    if not sorted_books:
        await update.message.reply_text("📭 No books available.")
        return

    msg = "🏆 Top Downloaded Books:\n\n"
    for book_id, book in sorted_books[:5]:
        msg += f"{book_id}. {book['title']} ({book['downloads']} downloads)\n"

    await update.message.reply_text(msg)

# User stats
async def user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    count = user_downloads.get(user_id, 0)
    bks = bookmarks.get(user_id, [])
    bookmarked = ", ".join(bks) if bks else "None"

    await update.message.reply_text(f"📊 **Your Stats**\n"
                                    f"📥 Books downloaded: {count}\n"
                                    f"🔖 Bookmarked books: {bookmarked}")

# Notifications
async def notify_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    subscribers_col.update_one(
    {"_id": user_id},
    {"$set": {"subscribed": True}},
    upsert=True
)
    await update.message.reply_text("🔔 You will be notified when new books are uploaded.")

async def notify_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    subscribers_col.delete_one({"_id": user_id})
    await update.message.reply_text("🔕 You will no longer receive notifications.")

# Shareable link
async def share_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("UsageId: /share <book_id>")
        return

    book_id = context.args[0]
    if book_id not in books:
        await update.message.reply_text("❌ Book not found.")
        return

    link = f"https://t.me/noveltamizh_bot?start=book_{book_id}"
    await update.message.reply_text(f"🔗 Share this link: {link}")

# --- OTHER HANDLERS ---

# Command to assign roles (e.g., /assign admin @username)
async def assign_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat = message.chat
    user = message.from_user

    if chat.type != "supergroup":
        await message.reply_text("This command only works in supergroups.")
        return

    if not await is_admin(chat, user.id):
        await message.reply_text("You are not an admin.")
        return

    if len(context.args) < 2:
        await message.reply_text("Usage: /assign <role> <@username>")
        return

    role = context.args[0].lower()
    mention = context.args[1]

    if not mention.startswith("@"):
        await message.reply_text("Please mention a user with @username")
        return

    await message.reply_text(f"Assigned role '{role}' to {mention}")

# Command to get current topic ID
async def get_topic_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    topic_id = message.message_thread_id
    if topic_id:
        await message.reply_text(f"📌 This topic ID is: `{topic_id}`")
    else:
        await message.reply_text("⚠️ This is not a topic (or it's the General topic).")

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

# Dummy web server to keep the bot alive
app = Flask('')

@app.route('/')
def home():
    return "Bot is running"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    server = Thread(target=run)
    server.daemon = True
    server.start()

# Build and run the bot
app_bot = ApplicationBuilder().token(BOT_TOKEN).build()

# Add handlers
app_bot.add_handler(CommandHandler("start", start))
app_bot.add_handler(CommandHandler("assign", assign_role))
app_bot.add_handler(CommandHandler("topicid", get_topic_id))
app_bot.add_handler(MessageHandler(filters.ALL, message_handler))

# New handlers
app_bot.add_handler(CommandHandler("upload", upload_book))
app_bot.add_handler(CommandHandler("books", list_books))
app_bot.add_handler(CommandHandler("book", view_book))
app_bot.add_handler(CommandHandler("search", search_books))
app_bot.add_handler(CommandHandler("top_books", top_books))
app_bot.add_handler(CommandHandler("mystats", user_stats))
app_bot.add_handler(CommandHandler("notify_on", notify_on))
app_bot.add_handler(CommandHandler("notify_off", notify_off))
app_bot.add_handler(CommandHandler("share", share_book))
app_bot.add_handler(CallbackQueryHandler(button_handler))

# Start dummy web server and bot
keep_alive()
print("Bot is running...")
app_bot.run_polling()
