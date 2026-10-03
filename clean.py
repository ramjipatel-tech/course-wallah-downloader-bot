import os
import glob
from pathlib import Path
from pyrogram import Client, filters
from vars import ADMINS, TEMP_DIR, DOWNLOADS_DIR, PROJECT_ROOT
from db import db
from datetime import datetime
from pyrogram.handlers import MessageHandler
from utils import cleanup_uploaded_file, cleanup_job_temp_dir

def clean_downloads():
    """Clean everything in downloads and temp directories safely"""
    try:
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        os.makedirs(TEMP_DIR, exist_ok=True)
        
        for p in [DOWNLOADS_DIR, TEMP_DIR]:
            for root, dirs, files in os.walk(p, topdown=False):
                for f in files:
                    cleanup_uploaded_file(os.path.join(root, f))
                for d in dirs:
                    cleanup_job_temp_dir(os.path.join(root, d))
    except Exception as e:
        print(f"Error cleaning downloads: {e}")

def clean_media_files():
    """Clean images and videos in temp directory except persistent assets"""
    try:
        image_formats = ["*.jpg", "*.jpeg", "*.png"]
        video_formats = ["*.mp4", "*.mkv", "*.webm"]
        temp_formats = ["*.part", "*.ytdl", "*.pdf"]
        formats_to_clean = image_formats + video_formats + temp_formats
        
        for target_dir in [TEMP_DIR, DOWNLOADS_DIR]:
            for format_pattern in formats_to_clean:
                for file in glob.glob(os.path.join(str(target_dir), "**", format_pattern), recursive=True):
                    if os.path.basename(file) in ("wm.png", "watermark.png", "start.png", "start.jpg"):
                        continue
                    cleanup_uploaded_file(file)
    except Exception as e:
        print(f"Error cleaning media files: {e}")

def clean_all():
    """Clean all temporary media files"""
    clean_downloads()
    clean_media_files()

async def clean_expired_users(client: Client):
    """Clean expired users from all bots"""
    try:
        # Get all users from all bots
        all_users = []
        for bot_username in db.list_bot_usernames():
            users = db.list_users(bot_username)
            all_users.extend([(user, bot_username) for user in users])
        
        removed_count = 0
        now = datetime.now()
        
        # Check each user
        for user, bot_username in all_users:
            expiry_val = user.get('expiry_date')
            if not expiry_val:
                continue
            if isinstance(expiry_val, str):
                try:
                    expiry = datetime.fromisoformat(expiry_val)
                except Exception:
                    try:
                        expiry = datetime.strptime(expiry_val, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        continue
            elif isinstance(expiry_val, datetime):
                expiry = expiry_val
            else:
                continue
                
            if expiry <= now:
                # User is expired
                try:
                    # Send expiry notification
                    await client.send_message(
                        user['user_id'],
                        "**⚠️ Your subscription has expired**\n\n"
                        "Your access has been revoked. Contact admin to renew."
                    )
                except Exception as e:
                    print(f"Failed to notify user {user['user_id']}: {e}")
                
                # Remove user
                if db.remove_user(user['user_id'], bot_username):
                    removed_count += 1
                    
        return removed_count
        
    except Exception as e:
        print(f"Error cleaning expired users: {e}")
        return 0

# Command handler for /clean
async def handle_clean_command(client: Client, message):
    """Handle the /clean command"""
    try:
        # Only allow admins to use this command
        if message.from_user.id not in ADMINS:
            await message.reply_text("⚠️ You are not authorized to use this command.")
            return
            
        # Send initial message
        status_msg = await message.reply_text("🧹 Cleaning files and expired users...")
        
        # Clean all files
        clean_all()
        
        # Clean expired users
        removed_users = await clean_expired_users(client)
        
        # Update status message
        await status_msg.edit_text(
            "✅ Cleanup completed!\n"
            "- Cleaned downloads directory\n"
            "- Removed media files (except wm.png)\n"
            "- Removed .part and .ytdl files\n"
            f"- Removed {removed_users} expired users"
        )
        
    except Exception as e:
        await message.reply_text(f"❌ Error during cleanup: {str(e)}")

# Register command handler
def register_clean_handler(bot: Client):
    """Register the clean command handler"""
    bot.add_handler(MessageHandler(handle_clean_command, filters.command("clean") & filters.private))
