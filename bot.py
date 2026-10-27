import os
import time
import uuid
import html
import asyncio
import logging
from typing import Dict, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import config
from downloader import (
    extract_first_url,
    detect_platform,
    download_video,
    download_audio,
    cleanup_files,
    auto_cleanup_downloads,
    clear_all_downloads,
)

# Logging configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Cache for callback actions (Token -> Media Info)
MEDIA_CACHE: Dict[str, Dict[str, Any]] = {}

def cleanup_old_cache(max_age_seconds: int = 1800):
    """Remove cache entries older than 30 mins."""
    now = time.time()
    to_delete = []
    for token, item in MEDIA_CACHE.items():
        if now - item.get("timestamp", 0) > max_age_seconds:
            to_delete.append(token)
            if item.get("video_path") and os.path.exists(item["video_path"]):
                cleanup_files(item["video_path"])
    for token in to_delete:
        MEDIA_CACHE.pop(token, None)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command in Malayalam."""
    user = update.effective_user
    welcome_text = (
        f"നമസ്കാരം <b>{html.escape(user.first_name)}</b>! 👋\n\n"
        "റീലുകളും വീഡിയോകളും എളുപ്പത്തിൽ ഡൗൺലോഡ് ചെയ്യാൻ സഹായിക്കുന്ന ബോട്ടിലേക്ക് സ്വാഗതം! 🎬\n\n"
        "<b>✨ പിന്തുണയ്ക്കുന്ന പ്ലാറ്റ്‌ഫോമുകൾ:</b>\n"
        "• 📸 <b>Instagram</b> (Reels, Posts)\n"
        "• 💬 <b>ShareChat</b> (Videos, Reels)\n"
        "• 🔵 <b>Facebook</b> (Reels, Watch)\n"
        "• 🔴 <b>YouTube</b> (Shorts, Videos)\n\n"
        "<b>ഉപയോഗിക്കേണ്ട വിധം:</b>\n"
        "ഏതെങ്കിലും റീൽ/വീഡിയോ ലിങ്ക് ഇങ്ങോട്ട് അയക്കൂ.\n"
        "👉 <b>ഡിഫോൾട്ടായി നേരിട്ട് വീഡിയോ ലഭിക്കും.</b>\n"
        "👉 ഒപ്പം <b>MP3 ഓഡിയോ</b> ഡൗൺലോഡ് ചെയ്യാനുള്ള ബട്ടണും ലഭ്യമാണ്! 🎵\n\n"
        "ഓഡിയോ മാത്രം വേണമെങ്കിൽ: <code>/audio &lt;ലിങ്ക്&gt;</code> എന്ന് അയക്കാം."
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /help command in Malayalam."""
    help_text = (
        "📖 <b>സഹായം &amp; നിർദ്ദേശങ്ങൾ:</b>\n\n"
        "1. <b>വീഡിയോ ഡൗൺലോഡ് ചെയ്യാൻ:</b>\n"
        "   Instagram, ShareChat, Facebook, YouTube എന്നിവയിൽ നിന്നുള്ള ലിങ്ക് നേരിട്ട് അയക്കുക.\n\n"
        "2. <b>ഓഡിയോ (MP3) ലഭിക്കാൻ:</b>\n"
        "   വീഡിയോ ലഭിച്ച ശേഷം താഴെയുള്ള <code>🎵 MP3 ഡൗൺലോഡ് ചെയ്യുക</code> ബട്ടൺ ക്ലിക്ക് ചെയ്യുക, അല്ലെങ്കിൽ <code>/mp3 &lt;ലിങ്ക്&gt;</code> എന്ന് അയക്കുക.\n\n"
        "3. <b>സ്റ്റോറേജ് ക്ലീൻ ചെയ്യാൻ:</b>\n"
        "   സെർവറിലെ താൽക്കാലിക ഫയലുകൾ ഓരോ 6 മണിക്കൂറിലും തനിയെ നീക്കം ചെയ്യപ്പെടും. ഉടൻ തന്നെ ക്ലീൻ ചെയ്യാൻ: <code>/clean</code>\n\n"
        "⚠️ <b>ശ്രദ്ധിക്കുക:</b>\n"
        "• സ്വകാര്യ (Private) അക്കൗണ്ടുകളിൽ നിന്നുള്ള വീഡിയോകൾ ലഭിക്കില്ല.\n"
        "• വലിയ ഫയലുകൾ ടെലിഗ്രാമിന് അനുയോജ്യമായ രീതിയിൽ ഒപ്റ്റിമൈസ് ചെയ്യപ്പെടുന്നു."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


async def clean_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to clear all cached downloads immediately."""
    user_id = update.effective_user.id
    if config.ADMIN_ID and user_id != config.ADMIN_ID:
        await update.message.reply_text("⚠️ ഈ കമാൻഡ് ഉപയോഗിക്കാൻ അഡ്മിന് മാത്രമേ അധികാരമുള്ളൂ.")
        return

    status_msg = await update.message.reply_text("🧹 <b>സ്റ്റോറേജ് ക്ലീൻ ചെയ്യുന്നു...</b>", parse_mode=ParseMode.HTML)
    res = await asyncio.to_thread(clear_all_downloads)
    MEDIA_CACHE.clear()
    
    mb_freed = res["bytes_freed"] / (1024 * 1024)
    await status_msg.edit_text(
        f"🧹 <b>സ്റ്റോറേജ് വിജയകരമായി ക്ലീൻ ചെയ്തു!</b>\n\n"
        f"• നീക്കം ചെയ്ത ഫയലുകൾ: <b>{res['count']}</b>\n"
        f"• ലഭ്യമാക്കിയ സ്ഥലം: <b>{mb_freed:.2f} MB</b>\n\n"
        f"Raspberry Pi സെർവറിലെ എല്ലാ താൽക്കാലിക ഫയലുകളും നീക്കം ചെയ്തു.",
        parse_mode=ParseMode.HTML
    )


async def watermark_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to toggle or configure video watermark."""
    user_id = update.effective_user.id
    if config.ADMIN_ID and user_id != config.ADMIN_ID:
        await update.message.reply_text("⚠️ ഈ കമാൻഡ് ഉപയോഗിക്കാൻ അഡ്മിന് മാത്രമേ അധികാരമുള്ളൂ.")
        return

    args = context.args
    if args:
        sub = args[0].lower()
        if sub in ["on", "enable", "true"]:
            config.WATERMARK_ENABLED = True
            await update.message.reply_text("✅ <b>വാട്ടർമാർക്ക് സജീവമാക്കി (Enabled).</b>", parse_mode=ParseMode.HTML)
            return
        elif sub in ["off", "disable", "false"]:
            config.WATERMARK_ENABLED = False
            await update.message.reply_text("❌ <b>വാട്ടർമാർക്ക് പ്രവർത്തനരഹിതമാക്കി (Disabled).</b>", parse_mode=ParseMode.HTML)
            return
        elif sub in ["center", "centre", "top_right", "top_left", "bottom_right", "bottom_left"]:
            if sub == "centre":
                sub = "center"
            config.WATERMARK_POSITION = sub
            await update.message.reply_text(f"📍 <b>വാട്ടർമാർക്ക് സ്ഥാനം മാറ്റി:</b> <code>{sub}</code>", parse_mode=ParseMode.HTML)
            return

    status_str = "ഓൺ (Enabled) ✅" if config.WATERMARK_ENABLED else "ഓഫ് (Disabled) ❌"
    msg = (
        "🏷️ <b>വാട്ടർമാർക്ക് കോൺഫിഗറേഷൻ:</b>\n\n"
        f"• സ്റ്റാറ്റസ്: <b>{status_str}</b>\n"
        f"• സ്ഥാനം: <code>{config.WATERMARK_POSITION}</code>\n"
        f"• സുതാര്യത (Opacity): <b>{int(config.WATERMARK_OPACITY * 100)}%</b>\n"
        f"• ലോഗോ: <b>Babu Perumana (White, Centered)</b>\n\n"
        "<b>കമാൻഡുകൾ:</b>\n"
        "• <code>/watermark on</code> - ഓൺ ചെയ്യാൻ\n"
        "• <code>/watermark off</code> - ഓഫ് ചെയ്യാൻ\n"
        "• <code>/watermark center</code> (വീഡിയോയുടെ നടുവിൽ - Default)\n"
        "• <code>/watermark top_right</code> (മുകളിൽ വലതുവശം)\n"
        "• <code>/watermark bottom_right</code> (താഴെ വലതുവശം)\n"
        "• <code>/watermark top_left</code> (മുകളിൽ ഇടതുവശം)"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)




async def audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /audio or /mp3 command to download audio directly."""
    text = update.message.text
    url = extract_first_url(text)
    
    if not url:
        await update.message.reply_text(
            "⚠️ ദയവായി ഒരു ലിങ്ക് കൂടി ഉൾപ്പെടുത്തുക.\nഉദാഹരണം: <code>/audio https://instagram.com/reel/...</code>",
            parse_mode=ParseMode.HTML
        )
        return

    await process_audio_download(update, context, url)


async def handle_url_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes messages containing links (Default: Download Video)."""
    text = update.message.text
    url = extract_first_url(text)

    if not url:
        return

    cleanup_old_cache()
    platform = detect_platform(url)
    
    status_msg = await update.message.reply_text(
        f"⏳ <b>{platform} ലിങ്ക് പരിശോധിക്കുന്നു...</b>\nവീഡിയോ ഡൗൺലോഡ് ചെയ്തുവരുന്നു, ദയവായി കാത്തിരിക്കൂ...",
        parse_mode=ParseMode.HTML
    )

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.UPLOAD_VIDEO
        )

        video_data = await asyncio.to_thread(download_video, url)
        media_type = video_data.get("type", "video")
        file_path = video_data["file_path"]
        title = video_data.get("title") or f"{platform} Media"

        if media_type == "photo":
            safe_title = html.escape(title[:100])
            caption = (
                f"📸 <b>{safe_title}</b>\n\n"
                f"📌 പ്ലാറ്റ്‌ഫോം: <b>{platform} Photo</b>\n"
                "ℹ️ <i>ശ്രദ്ധിക്കുക: ഇതൊരു ഫോട്ടോ (Image) പോസ്റ്റ് ആണ്, അതിനാൽ ചിത്രം നൽകിയിരിക്കുന്നു!</i>"
            )
            await status_msg.edit_text("📤 <b>ഫോട്ടോ ടെലിഗ്രാമിലേക്ക് അപ്‌ലോഡ് ചെയ്യുന്നു...</b>", parse_mode=ParseMode.HTML)
            with open(file_path, "rb") as photo_file:
                await update.message.reply_photo(
                    photo=photo_file,
                    caption=caption,
                    parse_mode=ParseMode.HTML
                )
            await status_msg.delete()
            cleanup_files(file_path)
            return

        duration = int(video_data.get("duration") or 0)
        width = video_data.get("width")
        height = video_data.get("height")
        thumb_path = video_data.get("thumbnail")

        # Save to cache so MP3 can be requested
        token = uuid.uuid4().hex[:8]
        MEDIA_CACHE[token] = {
            "url": url,
            "video_path": file_path,
            "title": title,
            "platform": platform,
            "timestamp": time.time()
        }

        keyboard = [
            [
                InlineKeyboardButton(
                    "🎵 MP3 ഓഡിയോ ഡൗൺലോഡ് ചെയ്യുക",
                    callback_data=f"mp3:{token}"
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Clean caption in HTML
        safe_title = html.escape(title[:100])
        caption = (
            f"🎬 <b>{safe_title}</b>\n\n"
            f"📌 പ്ലാറ്റ്‌ഫോം: <b>{platform}</b>\n"
            "✨ <i>MP3 ഓഡിയോ വേണമെങ്കിൽ താഴെയുള്ള ബട്ടൺ ക്ലിക്ക് ചെയ്യുക.</i>"
        )

        await status_msg.edit_text("📤 <b>വീഡിയോ ടെലിഗ്രാമിലേക്ക് അപ്‌ലോഡ് ചെയ്യുന്നു...</b>", parse_mode=ParseMode.HTML)

        thumb_file = open(thumb_path, "rb") if thumb_path and os.path.exists(thumb_path) else None
        try:
            with open(file_path, "rb") as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    duration=duration if duration > 0 else None,
                    width=width,
                    height=height,
                    thumbnail=thumb_file,
                    supports_streaming=True,
                    reply_markup=reply_markup
                )
        finally:
            if thumb_file:
                thumb_file.close()
            if thumb_path:
                cleanup_files(thumb_path)

        await status_msg.delete()

    except Exception as e:
        logger.error(f"Download failed for {url}: {e}", exc_info=True)
        safe_err = html.escape(str(e))
        if len(safe_err) > 200:
            safe_err = safe_err[:200] + "..."
        error_msg = (
            "❌ <b>ഡൗൺലോഡ് ചെയ്യാൻ സാധിച്ചില്ല!</b>\n\n"
            f"⚠️ <b>കാരണം:</b> <code>{safe_err}</code>\n\n"
            "സാധ്യമായ കാരണങ്ങൾ:\n"
            "• അക്കൗണ്ട് പ്രൈവറ്റ് (Private) ആയിരിക്കാം.\n"
            "• ലിങ്ക് അസാധുവായതാകാം അല്ലെങ്കിൽ വീഡിയോ ഡിലീറ്റ് ചെയ്യപ്പെട്ടതാകാം.\n"
            "• ദയവായി ലിങ്ക് ശരിയാണോ എന്ന് പരിശോധിക്കുക."
        )
        try:
            await status_msg.edit_text(error_msg, parse_mode=ParseMode.HTML)
        except Exception:
            await update.message.reply_text(error_msg, parse_mode=ParseMode.HTML)


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button clicks (MP3 download)."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data or not data.startswith("mp3:"):
        return

    token = data.split(":", 1)[1]
    item = MEDIA_CACHE.get(token)

    if not item:
        await query.message.reply_text(
            "⚠️ ഈ അഭ്യർത്ഥനയുടെ കാലാവധി കഴിഞ്ഞു. ദയവായി ലിങ്ക് വീണ്ടും അയക്കുക.",
            parse_mode=ParseMode.HTML
        )
        return

    url = item["url"]
    video_path = item.get("video_path")
    platform = item.get("platform", "Unknown")
    title = item.get("title", "Audio")

    status_msg = await query.message.reply_text(
        "🎵 <b>ഓഡിയോ (MP3) വേർതിരിച്ചെടുക്കുന്നു...</b> ദയവായി കാത്തിരിക്കൂ.",
        parse_mode=ParseMode.HTML
    )

    try:
        await context.bot.send_chat_action(
            chat_id=query.message.chat_id,
            action=ChatAction.UPLOAD_VOICE
        )

        audio_data = await asyncio.to_thread(
            download_audio,
            url,
            existing_video_path=video_path if video_path and os.path.exists(video_path) else None
        )

        mp3_path = audio_data["file_path"]
        duration = int(audio_data.get("duration") or 0)

        await status_msg.edit_text("📤 <b>MP3 ഓഡിയോ അപ്‌ലോഡ് ചെയ്യുന്നു...</b>", parse_mode=ParseMode.HTML)

        safe_title = html.escape(title[:80])
        with open(mp3_path, "rb") as audio_file:
            await query.message.reply_audio(
                audio=audio_file,
                title=safe_title,
                performer=platform,
                duration=duration if duration > 0 else None,
                caption=f"🎵 <b>{safe_title}</b>\n📌 പ്ലാറ്റ്‌ഫോം: <b>{platform}</b>",
                parse_mode=ParseMode.HTML
            )

        await status_msg.delete()
        cleanup_files(mp3_path)

    except Exception as e:
        logger.error(f"Audio extraction failed for {url}: {e}", exc_info=True)
        await status_msg.edit_text(
            "❌ <b>ഓഡിയോ വേർതിരിച്ചെടുക്കാൻ സാധിച്ചില്ല.</b> ദയവായി പിന്നീട് വീണ്ടും ശ്രമിക്കുക.",
            parse_mode=ParseMode.HTML
        )


async def process_audio_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    """Direct audio download helper for /audio command."""
    platform = detect_platform(url)
    status_msg = await update.message.reply_text(
        f"🎵 <b>{platform} ഓഡിയോ (MP3) ഡൗൺലോഡ് ചെയ്യുന്നു...</b> ദയവായി കാത്തിരിക്കൂ...",
        parse_mode=ParseMode.HTML
    )

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.UPLOAD_VOICE
        )

        audio_data = await asyncio.to_thread(download_audio, url)
        mp3_path = audio_data["file_path"]
        title = audio_data.get("title") or f"{platform} Audio"
        duration = int(audio_data.get("duration") or 0)

        await status_msg.edit_text("📤 <b>MP3 ഓഡിയോ അപ്‌ലോഡ് ചെയ്യുന്നു...</b>", parse_mode=ParseMode.HTML)

        safe_title = html.escape(title[:80])
        with open(mp3_path, "rb") as audio_file:
            await update.message.reply_audio(
                audio=audio_file,
                title=safe_title,
                performer=platform,
                duration=duration if duration > 0 else None,
                caption=f"🎵 <b>{safe_title}</b>\n📌 പ്ലാറ്റ്‌ഫോം: <b>{platform}</b>",
                parse_mode=ParseMode.HTML
            )

        await status_msg.delete()
        cleanup_files(mp3_path)

    except Exception as e:
        logger.error(f"Direct audio download failed: {e}", exc_info=True)
        await status_msg.edit_text(
            "❌ <b>ഓഡിയോ ഡൗൺലോഡ് പരാജയപ്പെട്ടു.</b> ലിങ്ക് ശരിയാണോ എന്ന് പരിശോധിക്കുക.",
            parse_mode=ParseMode.HTML
        )


async def periodic_cleanup_task():
    """Background task to automatically delete downloads older than AUTO_CLEANUP_HOURS."""
    while True:
        try:
            await asyncio.sleep(3600)  # Check every hour
            res = await asyncio.to_thread(auto_cleanup_downloads, config.AUTO_CLEANUP_HOURS)
            cleanup_old_cache(max_age_seconds=config.AUTO_CLEANUP_HOURS * 3600)
            if res.get("count", 0) > 0:
                logger.info(f"Periodic Cleanup: Deleted {res['count']} files older than {config.AUTO_CLEANUP_HOURS}h")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic cleanup task: {e}")


async def post_init(application: Application):
    """Notify admin when bot comes online and start background cleanup."""
    # Launch background auto-cleanup task
    asyncio.create_task(periodic_cleanup_task())

    if config.ADMIN_ID:
        try:
            bot_info = await application.bot.get_me()
            await application.bot.send_message(
                chat_id=config.ADMIN_ID,
                text=(
                    f"🚀 <b>റീൽസ് ഡൗൺലോഡർ ബോട്ട് സജീവമായി!</b>\n\n"
                    f"🤖 ബോട്ട്: @{bot_info.username}\n"
                    f"📅 സ്റ്റാറ്റസ്: ഓൺലൈൻ (Active)\n"
                    f"🧹 ഓട്ടോ-ക്ലീനർ: ഓരോ {config.AUTO_CLEANUP_HOURS} മണിക്കൂറിലും പഴയ ഫയലുകൾ തനിയെ നീക്കം ചെയ്യപ്പെടും.\n\n"
                    f"മാനുവലായി ക്ലീൻ ചെയ്യാൻ: <code>/clean</code>"
                ),
                parse_mode=ParseMode.HTML
            )
            logger.info(f"Startup notification sent to admin {config.ADMIN_ID}")
        except Exception as e:
            logger.warning(f"Could not send startup notification to admin: {e}")


def main():
    """Main entry point with retry on connection error."""
    if not config.BOT_TOKEN:
        print("ERROR: BOT_TOKEN is missing in .env file!")
        return

    from telegram.request import HTTPXRequest

    logger.info("Starting Reels Downloader Telegram Bot...")

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=300.0,
        write_timeout=300.0,
        pool_timeout=30.0
    )

    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .request(request)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler(["audio", "mp3"], audio_command))
    application.add_handler(CommandHandler("clean", clean_command))
    application.add_handler(CommandHandler("watermark", watermark_command))
    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url_message)
    )

    application.run_polling(drop_pending_updates=True, bootstrap_retries=5)


if __name__ == "__main__":
    main()
