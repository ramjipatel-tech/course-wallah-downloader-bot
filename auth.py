from datetime import datetime
from typing import Optional
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from db import db
from vars import OWNER_ID, ADMINS, AUTH_MESSAGES, BOT_USERNAME


async def handle_subscription_end(client: Client, user_id: int):
    try:
        await client.send_message(
            user_id,
            "<b>⚠️ Subscription Ended</b>\n\n<blockquote>Your access has expired. Contact admin to renew your plan.</blockquote>"
        )
    except Exception:
        pass


# ==============================================================================
# 👥 ADMIN USER COMMANDS
# ==============================================================================

async def add_user_cmd(client: Client, message: Message):
    """Add a new authorized user: /add <user_id> <days>"""
    try:
        sender_id = message.from_user.id if message.from_user else 0
        if not db.is_admin(sender_id):
            await message.reply_text(AUTH_MESSAGES["not_admin"])
            return

        args = message.text.split()[1:]
        if len(args) != 2:
            await message.reply_text(
                AUTH_MESSAGES["invalid_format"].format(format="/add <user_id> <days>\n\nExample:\n/add 123456789 30")
            )
            return

        user_id = int(args[0])
        days = int(args[1])
        bot_uname = client.me.username if getattr(client, "me", None) and client.me.username else BOT_USERNAME

        try:
            user = await client.get_users(user_id)
            name = user.first_name
            if user.last_name:
                name += f" {user.last_name}"
        except Exception:
            name = f"User {user_id}"

        success, expiry_date = db.add_user(user_id, name, days, bot_uname)

        if success and expiry_date:
            expiry_str = expiry_date.strftime("%d-%m-%Y %H:%M:%S")
            await message.reply_text(
                AUTH_MESSAGES["user_added"].format(name=name, user_id=user_id, expiry_date=expiry_str)
            )
            try:
                await client.send_message(
                    user_id,
                    AUTH_MESSAGES["subscription_active"].format(expiry_date=expiry_str)
                )
            except Exception:
                pass
        else:
            await message.reply_text("❌ Failed to add user. Please check arguments.")

    except ValueError:
        await message.reply_text("❌ Invalid user ID or days. Please use numbers only.")
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


async def renew_user_cmd(client: Client, message: Message):
    """Renew an existing user: /renew <user_id> <days>"""
    try:
        sender_id = message.from_user.id if message.from_user else 0
        if not db.is_admin(sender_id):
            await message.reply_text(AUTH_MESSAGES["not_admin"])
            return

        args = message.text.split()[1:]
        if len(args) != 2:
            await message.reply_text("❌ Format: <code>/renew &lt;user_id&gt; &lt;days&gt;</code>")
            return

        user_id = int(args[0])
        days = int(args[1])
        success, expiry_date = db.renew_user(user_id, days)

        if success and expiry_date:
            expiry_str = expiry_date.strftime("%d-%m-%Y %H:%M:%S")
            await message.reply_text(f"✅ User <code>{user_id}</code> renewed until <b>{expiry_str}</b> (+{days} days)")
            try:
                await client.send_message(
                    user_id,
                    f"🎉 <b>Subscription Renewed!</b>\n\n<blockquote>Your access has been extended until {expiry_str}.</blockquote>"
                )
            except Exception:
                pass
        else:
            await message.reply_text("❌ User not found in database. Use /add first.")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")


async def remove_user_cmd(client: Client, message: Message):
    """Remove a user from authorized users: /remove <user_id>"""
    try:
        sender_id = message.from_user.id if message.from_user else 0
        if not db.is_admin(sender_id):
            await message.reply_text(AUTH_MESSAGES["not_admin"])
            return

        args = message.text.split()[1:]
        if len(args) != 1:
            await message.reply_text("❌ Format: <code>/remove &lt;user_id&gt;</code>")
            return

        user_id = int(args[0])
        bot_uname = client.me.username if getattr(client, "me", None) and client.me.username else BOT_USERNAME

        if db.remove_user(user_id, bot_uname):
            await message.reply_text(AUTH_MESSAGES["user_removed"].format(user_id=user_id))
            try:
                await client.send_message(
                    user_id,
                    AUTH_MESSAGES["subscription_expired"]
                )
            except Exception:
                pass
        else:
            await message.reply_text(f"❌ User <code>{user_id}</code> not found.")
    except ValueError:
        await message.reply_text("❌ Invalid user ID. Use numbers only.")
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


async def ban_user_cmd(client: Client, message: Message):
    """Ban a user: /ban <user_id>"""
    try:
        sender_id = message.from_user.id if message.from_user else 0
        if not db.is_admin(sender_id):
            await message.reply_text(AUTH_MESSAGES["not_admin"])
            return

        args = message.text.split()[1:]
        if len(args) != 1:
            await message.reply_text("❌ Format: <code>/ban &lt;user_id&gt;</code>")
            return

        user_id = int(args[0])
        if db.ban_user(user_id):
            await message.reply_text(f"🚫 User <code>{user_id}</code> has been <b>BANNED</b>.")
        else:
            await message.reply_text("❌ Failed to ban user.")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")


async def unban_user_cmd(client: Client, message: Message):
    """Unban a user: /unban <user_id>"""
    try:
        sender_id = message.from_user.id if message.from_user else 0
        if not db.is_admin(sender_id):
            await message.reply_text(AUTH_MESSAGES["not_admin"])
            return

        args = message.text.split()[1:]
        if len(args) != 1:
            await message.reply_text("❌ Format: <code>/unban &lt;user_id&gt;</code>")
            return

        user_id = int(args[0])
        if db.unban_user(user_id):
            await message.reply_text(f"✅ User <code>{user_id}</code> has been <b>UNBANNED</b>.")
        else:
            await message.reply_text("❌ Failed to unban user.")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")


async def list_users_cmd(client: Client, message: Message):
    """List all users: /users"""
    try:
        sender_id = message.from_user.id if message.from_user else 0
        if not db.is_admin(sender_id):
            await message.reply_text(AUTH_MESSAGES["not_admin"])
            return

        bot_uname = client.me.username if getattr(client, "me", None) and client.me.username else BOT_USERNAME
        users = db.list_users(bot_uname)

        if not users:
            await message.reply_text("📝 No authorized users in database.")
            return

        user_list = f"<b>👥 Authorized Users ({len(users)})</b>\n\n"
        for u in users:
            expiry_str = u.get("expiry_date")
            days_left = "N/A"
            if expiry_str:
                try:
                    exp = datetime.fromisoformat(expiry_str)
                    days_left = max(0, (exp - datetime.now()).days)
                except Exception:
                    pass

            banned_flag = " 🚫 [BANNED]" if u.get("banned") else ""
            user_list += (
                f"• <b>{u['name']}</b> ({u['user_id']}){banned_flag}\n"
                f"  ⏳ Days Left: {days_left} | Exp: {str(expiry_str)[:10]}\n"
                f"────────────────\n"
            )

        # Truncate if message exceeds Telegram limit
        if len(user_list) > 4000:
            user_list = user_list[:3900] + "\n\n<i>... [Truncated due to Telegram limit]</i>"

        await message.reply_text(user_list)
    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


async def inspect_user_cmd(client: Client, message: Message):
    """Inspect user details: /user <user_id>"""
    try:
        sender_id = message.from_user.id if message.from_user else 0
        if not db.is_admin(sender_id):
            await message.reply_text(AUTH_MESSAGES["not_admin"])
            return

        args = message.text.split()[1:]
        if len(args) != 1:
            await message.reply_text("❌ Format: <code>/user &lt;user_id&gt;</code>")
            return

        user_id = int(args[0])
        info = db.get_user_expiry_info(user_id)
        if not info:
            await message.reply_text(f"❌ User <code>{user_id}</code> not found in database.")
            return

        cookies_info = db.get_user_cookies_info(user_id)
        ck_status = "Configured" if cookies_info.get("configured") else "Not configured"

        text = (
            f"<b>👤 User Information</b>\n\n"
            f"• <b>Name:</b> {info['name']}\n"
            f"• <b>User ID:</b> <code>{info['user_id']}</code>\n"
            f"• <b>Active:</b> {'✅ Yes' if info['is_active'] else '❌ No'}\n"
            f"• <b>Banned:</b> {'🚫 Yes' if info.get('banned') else 'No'}\n"
            f"• <b>Days Left:</b> {info['days_left']} days\n"
            f"• <b>Expires:</b> {info['expiry_date']}\n"
            f"• <b>YouTube Cookies:</b> {ck_status}\n"
        )
        await message.reply_text(text)
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")


# ==============================================================================
# 📱 USER PLAN COMMAND
# ==============================================================================

async def my_plan_cmd(client: Client, message: Message):
    """View current plan: /plan or /plan <user_id> (admin)"""
    try:
        sender_id = message.from_user.id if message.from_user else 0
        target_id = sender_id

        args = message.text.split()[1:]
        if args and db.is_admin(sender_id):
            try:
                target_id = int(args[0])
            except ValueError:
                pass

        bot_uname = client.me.username if getattr(client, "me", None) and client.me.username else BOT_USERNAME
        info = db.get_user_expiry_info(target_id, bot_uname)

        if not info or not info.get("is_active"):
            if db.is_admin(target_id):
                await message.reply_text("👑 <b>Admin Account</b>\n\n<blockquote>You have lifetime unlimited administrator access.</blockquote>")
                return
            await message.reply_text(
                "❌ <b>No Active Plan</b>\n\n"
                "<blockquote>You do not have an active subscription.\n"
                "Please contact the admin to purchase or renew your plan.</blockquote>"
            )
            return

        ck_info = db.get_user_cookies_info(target_id)
        ck_str = "✅ Active" if ck_info.get("configured") else "❌ Not configured (/cookies)"

        await message.reply_text(
            f"📱 <b>Your Subscription Details</b>\n\n"
            f"• <b>Name:</b> {info['name']}\n"
            f"• <b>User ID:</b> <code>{info['user_id']}</code>\n"
            f"• <b>Status:</b> {'✅ Active' if info['is_active'] else '⚠️ Expired'}\n"
            f"• <b>Days Remaining:</b> {info['days_left']} days\n"
            f"• <b>Expiry Date:</b> {info['expiry_date']}\n"
            f"• <b>YouTube Cookies:</b> {ck_str}"
        )

    except Exception as e:
        await message.reply_text(f"❌ Error: {str(e)}")


# Decorator for checking user authorization
def check_auth():
    def decorator(func):
        async def wrapper(client, message, *args, **kwargs):
            if not message.from_user:
                return await func(client, message, *args, **kwargs)

            bot_uname = client.me.username if getattr(client, "me", None) and client.me.username else BOT_USERNAME
            if not db.is_user_authorized(message.from_user.id, bot_uname):
                return await message.reply(
                    "<b>❌ Access Denied</b>\n\n<blockquote>You need an active subscription to use this bot. Contact admin for access.</blockquote>"
                )
            return await func(client, message, *args, **kwargs)
        return wrapper
    return decorator
