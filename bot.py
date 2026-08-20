import asyncio
import json
import logging
import os
import random
import re
import string
import time
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime

import aiohttp
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
    ConversationHandler,
    filters,
)

# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "8818345094:AAGw9CA1acbXZPgOzoPNu33tEnnMG9hcVPI")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5728569894"))
ADMIN_IDS = {ADMIN_ID}

STATE_DIR = Path("data")
STATE_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE = STATE_DIR / "users.json"
KEYS_FILE = STATE_DIR / "keys.json"
SETTINGS_FILE = STATE_DIR / "settings.json"

# API Endpoints
ZEFAME_API = 'https://app.zefame.com/api_free.php'
TIKFAMES_API = 'https://tikfames.com/api'

# Conversation states
AWAITING_KEY = 1
AWAITING_KEY_DURATION = 2
AWAITING_BOOST_URL = 3
AWAITING_BROADCAST_MESSAGE = 4
AWAITING_REVOKE_KEY = 5

# ============================================================
# DATA MODELS
# ============================================================

@dataclass
class User:
    user_id: int
    username: str = ""
    first_name: str = ""
    total_boosts: int = 0
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
    total_boosts_done: int = 0
    daily_boost_limit: int = 10
    premium_boost_limit: int = 50
    key_required: bool = True

# ============================================================
# SERVICE CONFIGURATION
# ============================================================

SERVICES = {
    "tiktok_views": {"name": "TikTok Views", "icon": "◉", "api": "tikfames"},
    "tiktok_likes": {"name": "TikTok Likes", "icon": "♥", "api": "tikfames"},
    "tiktok_followers": {"name": "TikTok Followers", "icon": "◆", "api": "tikfames"},
    "tiktok_favorites": {"name": "TikTok Favorites", "icon": "★", "api": "tikfames"},
    "tiktok_shares": {"name": "TikTok Shares", "icon": "↗", "api": "tikfames"},
    "instagram_likes": {"name": "Instagram Likes", "icon": "♥", "api": "zefame", "service_id": "234"},
    "instagram_views": {"name": "Instagram Views", "icon": "◉", "api": "zefame", "service_id": "237"},
    "instagram_followers": {"name": "Instagram Followers", "icon": "◆", "api": "zefame", "service_id": "233"},
    "facebook_followers": {"name": "Facebook Followers", "icon": "◆", "api": "zefame", "service_id": "244"},
}

TIKFAMES_SERVICE_MAP = {
    "tiktok_views": "video_views",
    "tiktok_likes": "video_likes",
    "tiktok_followers": "followers",
    "tiktok_favorites": "video_favorites",
    "tiktok_shares": "video_shares",
}

# ============================================================
# GLOBAL STATE
# ============================================================

USERS: Dict[int, User] = {}
KEYS: Dict[str, AccessKey] = {}
SETTINGS = BotSettings()
BOOST_COOLDOWNS: Dict[int, float] = {}
DAILY_BOOSTS: Dict[int, Dict[str, int]] = {}

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("boost-bot")

# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state():
    global USERS, KEYS, SETTINGS, DAILY_BOOSTS
    
    if USERS_FILE.exists():
        try:
            with USERS_FILE.open("r") as f:
                data = json.load(f)
                for uid, user_data in data.items():
                    USERS[int(uid)] = User(**user_data)
        except Exception as e:
            logger.error(f"Failed to load users: {e}")
    
    if KEYS_FILE.exists():
        try:
            with KEYS_FILE.open("r") as f:
                data = json.load(f)
                for key, key_data in data.items():
                    KEYS[key] = AccessKey(**key_data)
        except Exception as e:
            logger.error(f"Failed to load keys: {e}")
    
    if SETTINGS_FILE.exists():
        try:
            with SETTINGS_FILE.open("r") as f:
                data = json.load(f)
                SETTINGS = BotSettings(**data)
        except Exception as e:
            logger.error(f"Failed to load settings: {e}")

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
    if not has_access(user_id):
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

# ============================================================
# KEY MANAGEMENT
# ============================================================

def generate_key() -> str:
    chars = string.ascii_uppercase + string.digits
    while True:
        key = "CLOUT-" + "".join(random.choices(chars, k=12))
        if key not in KEYS:
            return key

def create_access_key(
    duration_days: int = 0,
    duration_hours: int = 0,
    is_lifetime: bool = False,
    created_by: int = 0,
    note: str = ""
) -> AccessKey:
    key = generate_key()
    key_expires = time.time() + (7 * 86400)
    
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
    
    if key.is_lifetime:
        duration_msg = "LIFETIME ACCESS"
    else:
        duration_msg = f"{key.duration_days}D {key.duration_hours}H"
    
    return {
        "success": True,
        "message": (
            "▸ ACCESS ACTIVATED SUCCESSFULLY\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"◈ Duration : {duration_msg}\n"
            f"◈ Key      : {key_string}\n\n"
            "▸ You can now use the boost services."
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
    
    save_keys()
    save_users()
    
    return {"success": True, "message": f"✓ Key {key_string} has been revoked."}

# ============================================================
# API FUNCTIONS
# ============================================================

def generate_token() -> str:
    chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
    token = '0cAFcWeA4'
    for _ in range(2000):
        token += chars[random.randint(0, len(chars)-1)]
    return token

def generate_session_id() -> str:
    chars = 'abcdef0123456789'
    session = ''
    for _ in range(40):
        session += chars[random.randint(0, len(chars)-1)]
    return session

REFERER_MAP = {
    'video_views': 'https://tikfames.com/free-tiktok-video-views',
    'video_likes': 'https://tikfames.com/free-tiktok-likes',
    'followers': 'https://tikfames.com/free-tiktok-followers',
    'video_favorites': 'https://tikfames.com/free-tiktok-favorites',
    'video_shares': 'https://tikfames.com/free-tiktok-shares',
}

def get_tikfames_headers(boost_type: str, cookie: Optional[str] = None) -> Dict[str, str]:
    referer = REFERER_MAP.get(boost_type, REFERER_MAP['video_views'])
    session_cookie = cookie or f"ci_session={generate_session_id()}"
    
    return {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Mobile Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
        'Origin': 'https://tikfames.com',
        'Referer': referer,
        'Cookie': session_cookie,
    }

async def get_tiktok_video_details(session: aiohttp.ClientSession, video_url: str, boost_type: str) -> Optional[Dict]:
    payload = {
        'input': video_url,
        'type': 'videoDetails',
        'recaptchaToken': generate_token(),
    }
    
    try:
        async with session.post(
            f'{TIKFAMES_API}/search',
            headers=get_tikfames_headers(boost_type),
            json=payload
        ) as response:
            text = await response.text()
            if response.status != 200:
                return None
            data = json.loads(text)
            if data.get('error') or not data.get('success'):
                return None
            return data
    except Exception as e:
        logger.error(f'TikFames search error: {e}')
        return None

async def process_tikfames_boost(session: aiohttp.ClientSession, video_details: Dict, boost_type: str) -> Dict[str, Any]:
    payload = {
        'username': video_details.get('username', video_details.get('author', {}).get('uniqueId', '')),
        'aweme_id': video_details.get('aweme_id', video_details.get('id', '')),
        'user_id': video_details.get('user_id', video_details.get('author', {}).get('id', '')),
        'sec_uid': video_details.get('sec_uid', video_details.get('author', {}).get('secUid', '')),
        'aweme_avatar': video_details.get('aweme_avatar', video_details.get('author', {}).get('avatarThumb', '')),
        'stats': video_details.get('stats', {}),
        'type': boost_type,
        'success': True,
        'recaptchaToken': generate_token(),
    }
    
    try:
        async with session.post(
            f'{TIKFAMES_API}/process',
            headers=get_tikfames_headers(boost_type),
            json=payload
        ) as response:
            text = await response.text()
            if response.status != 200:
                return {'success': False, 'message': f'API error: {response.status}'}
            
            data = json.loads(text)
            if data.get('success') is True:
                return {'success': True, 'message': data.get('message', 'Boost submitted successfully')}
            return {'success': False, 'message': data.get('message', 'Boost failed')}
    except Exception as e:
        logger.error(f'TikFames process error: {e}')
        return {'success': False, 'message': 'Processing error'}

async def handle_tiktok_boost(session: aiohttp.ClientSession, url: str, service: str) -> Dict[str, Any]:
    boost_type = TIKFAMES_SERVICE_MAP[service]
    video_details = await get_tiktok_video_details(session, url, boost_type)
    if not video_details:
        return {'success': False, 'message': 'Failed to fetch video details'}
    return await process_tikfames_boost(session, video_details, boost_type)

async def handle_zefame_boost(session: aiohttp.ClientSession, url: str, service: str) -> Dict[str, Any]:
    service_id = SERVICES[service].get('service_id')
    uuid = f"{random.randint(10000000, 99999999)}-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}-{random.randint(1000, 9999)}-{random.randint(100000000000, 999999999999)}"
    
    form_data = {
        'service': service_id,
        'link': url,
        'uuid': uuid,
    }
    
    if service == 'instagram_followers':
        username = url.replace('@', '')
        form_data['username'] = username
        form_data['link'] = f'https://www.instagram.com/{username}'
    
    headers = {
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Origin': 'https://zefame.com',
        'Referer': 'https://zefame.com/',
        'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Mobile Safari/537.36',
    }
    
    try:
        async with session.post(
            f'{ZEFAME_API}?action=order',
            headers=headers,
            data=form_data
        ) as response:
            text = await response.text()
            try:
                result = json.loads(text)
            except:
                return {'success': False, 'message': 'Invalid response from service'}
            
            success = result.get('success') == 1 or result.get('success') is True
            message = result.get('message', 'Order submitted successfully' if success else 'Failed to process order')
            return {'success': success, 'message': message}
    except Exception as e:
        logger.error(f'Zefame error: {e}')
        return {'success': False, 'message': 'Service error'}

# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    if is_admin(user_id):
        rows = [
            ["▸ Boost Services", "▸ My Statistics"],
            ["▸ Generate Key", "▸ Admin Panel"],
            ["▸ Help"]
        ]
    elif has_access(user_id):
        rows = [
            ["▸ Boost Services", "▸ My Statistics"],
            ["▸ Redeem Key", "▸ Help"]
        ]
    else:
        rows = [
            ["▸ Get Access", "▸ Help"],
            ["▸ My Statistics"]
        ]
    
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def service_keyboard() -> InlineKeyboardMarkup:
    keyboard = []
    row = []
    
    for service_id, service in SERVICES.items():
        button = InlineKeyboardButton(
            f"{service['icon']} {service['name']}",
            callback_data=f"service:{service_id}"
        )
        row.append(button)
        
        if len(row) == 2:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    keyboard.append([
        InlineKeyboardButton("« Main Menu", callback_data="main_menu")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def admin_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        ["▸ Generate Key", "▸ List Keys"],
        ["▸ Global Stats", "▸ User List"],
        ["▸ Revoke Key", "▸ Broadcast"],
        ["▸ Settings", "▸ Main Menu"]
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def key_type_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("∞ Lifetime Access", callback_data="keytype:lifetime")],
        [InlineKeyboardButton("◈ Custom Duration", callback_data="keytype:custom")],
        [InlineKeyboardButton("× Cancel", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ============================================================
# HANDLERS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if is_banned(user.id):
        await update.message.reply_text("× You are banned from this bot.")
        return
    
    get_or_create_user(user.id, user.username or "", user.first_name or "")
    
    access_status = get_access_status(user.id)
    
    welcome_text = (
        "╔════════════════════════════╗\n"
        "║   CLOUT PREMIUM BOOST      ║\n"
        "╚════════════════════════════╝\n\n"
        f"▸ Welcome, {user.first_name}!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"◈ Access : {access_status}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "▸ AVAILABLE SERVICES\n"
        "  ◈ TikTok Views\n"
        "  ◈ TikTok Likes\n"
        "  ◈ TikTok Followers\n"
        "  ◈ Instagram Likes\n"
        "  ◈ Instagram Views\n"
        "  ◈ Instagram Followers\n"
        "  ◈ Facebook Followers\n\n"
    )
    
    if not has_access(user.id) and not is_admin(user.id):
        welcome_text += (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "▸ GET ACCESS\n"
            "  Contact admin to purchase.\n\n"
            "▸ PRICING\n"
            "  ◈ 3 Days    : $1.99\n"
            "  ◈ 7 Days    : $3.99\n"
            "  ◈ 30 Days   : $9.99\n"
            "  ◈ Lifetime  : $29.99\n\n"
            "Use '▸ Get Access' to redeem!"
        )
    else:
        welcome_text += (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "▸ Use '▸ Boost Services' to start!"
        )
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=main_keyboard(user.id)
    )

async def get_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if has_access(user.id):
        access_status = get_access_status(user.id)
        await update.message.reply_text(
            f"✓ You already have access!\n\n"
            f"◈ Status : {access_status}"
        )
        return ConversationHandler.END
    
    context.user_data['awaiting_key'] = True
    
    await update.message.reply_text(
        "╔════════════════════════════╗\n"
        "║     REDEEM ACCESS KEY      ║\n"
        "╚════════════════════════════╝\n\n"
        "▸ Enter your access key below.\n\n"
        "▸ Format : CLOUT-XXXXXXXXXXXX\n\n"
        "Type /cancel to go back."
    )
    
    return AWAITING_KEY

async def handle_key_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key_string = update.message.text.strip()
    context.user_data['awaiting_key'] = False
    
    result = redeem_key(update.effective_user.id, key_string)
    
    if result['success']:
        await update.message.reply_text(
            result['message'],
            reply_markup=main_keyboard(update.effective_user.id)
        )
        
        user = update.effective_user
        if ADMIN_ID:
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"◈ KEY REDEEMED\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"▸ User : {user.first_name}\n"
                    f"▸ ID   : {user.id}\n"
                    f"▸ Key  : {key_string}",
                    parse_mode="Markdown"
                )
            except:
                pass
    else:
        await update.message.reply_text(result['message'])
        return AWAITING_KEY
    
    return ConversationHandler.END

async def boost_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if is_banned(user.id):
        await update.message.reply_text("× You are banned from this bot.")
        return ConversationHandler.END
    
    if SETTINGS.maintenance_mode and not is_admin(user.id):
        await update.message.reply_text("× Bot is under maintenance. Try again later.")
        return ConversationHandler.END
    
    if not has_access(user.id):
        await update.message.reply_text(
            "× You need access to use boost services.\n\n"
            "▸ Get your key from admin\n"
            "▸ Use '▸ Get Access' to redeem!",
            reply_markup=main_keyboard(user.id)
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "╔════════════════════════════╗\n"
        "║    BOOST SERVICES MENU     ║\n"
        "╚════════════════════════════╝\n\n"
        "▸ Select a service below :",
        reply_markup=service_keyboard()
    )
    
    return ConversationHandler.END

async def handle_service_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "main_menu":
        await query.edit_message_text("▸ Returning to main menu...")
        await context.bot.send_message(
            query.message.chat_id,
            "▸ Main Menu :",
            reply_markup=main_keyboard(query.from_user.id)
        )
        return ConversationHandler.END
    
    if data == "cancel":
        await query.edit_message_text("× Operation cancelled.")
        return ConversationHandler.END
    
    if data.startswith("keytype:"):
        key_type = data.split(":")[1]
        context.user_data['key_type'] = key_type
        
        if key_type == "lifetime":
            key = create_access_key(is_lifetime=True, created_by=query.from_user.id)
            
            await query.edit_message_text(
                "╔════════════════════════════╗\n"
                "║    KEY CREATED SUCCESS     ║\n"
                "╚════════════════════════════╝\n\n"
                f"▸ Key      : {key.key}\n"
                f"▸ Duration : LIFETIME\n"
                f"▸ Status   : Available\n\n"
                "Send this key to your customer."
            )
            return ConversationHandler.END
        
        elif key_type == "custom":
            context.user_data['awaiting_key_duration'] = True
            
            await query.edit_message_text(
                "╔════════════════════════════╗\n"
                "║    CUSTOM KEY DURATION     ║\n"
                "╚════════════════════════════╝\n\n"
                "▸ Enter duration in format :\n"
                "▸ days,hours\n\n"
                "Examples :\n"
                "  ◈ 3,0   = 3 days\n"
                "  ◈ 0,12  = 12 hours\n"
                "  ◈ 7,12  = 7 days 12 hours\n\n"
                "Type /cancel to cancel."
            )
            return AWAITING_KEY_DURATION
    
    if data.startswith("service:"):
        service_id = data.split(":")[1]
        
        if service_id not in SERVICES:
            await query.edit_message_text("× Invalid service selected.")
            return ConversationHandler.END
        
        service = SERVICES[service_id]
        context.user_data['selected_service'] = service_id
        context.user_data['awaiting_boost_url'] = True
        
        await query.edit_message_text(
            f"{service['icon']} {service['name']}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "▸ Send the URL to boost.\n\n"
            "Example :\n"
            "https://www.tiktok.com/@user/video/123456\n\n"
            "Type /cancel to cancel."
        )
        
        return AWAITING_BOOST_URL
    
    return ConversationHandler.END

async def handle_key_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data['awaiting_key_duration'] = False
    
    try:
        days_str, hours_str = text.split(',')
        days = int(days_str.strip())
        hours = int(hours_str.strip())
        
        if days < 0 or hours < 0:
            raise ValueError("Negative values not allowed")
        if days == 0 and hours == 0:
            raise ValueError("Duration cannot be zero")
        if days > 365:
            raise ValueError("Days cannot exceed 365")
        if hours > 23:
            raise ValueError("Hours cannot exceed 23")
        
    except ValueError as e:
        await update.message.reply_text(
            f"× Invalid input : {e}\n\n"
            f"▸ Use format : days,hours\n"
            f"▸ Example : 3,0 for 3 days\n\n"
            "Type /cancel to cancel."
        )
        context.user_data['awaiting_key_duration'] = True
        return AWAITING_KEY_DURATION
    
    key = create_access_key(
        duration_days=days,
        duration_hours=hours,
        created_by=update.effective_user.id
    )
    
    await update.message.reply_text(
        "╔════════════════════════════╗\n"
        "║    KEY CREATED SUCCESS     ║\n"
        "╚════════════════════════════╝\n\n"
        f"▸ Key      : {key.key}\n"
        f"▸ Duration : {days}D {hours}H\n"
        f"▸ Status   : Available\n\n"
        "Send this key to your customer."
    )
    
    return ConversationHandler.END

async def handle_boost_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    url = update.message.text.strip()
    service_id = context.user_data.get('selected_service')
    context.user_data['awaiting_boost_url'] = False
    
    if not service_id or service_id not in SERVICES:
        await update.message.reply_text("× Please select a service first.")
        return ConversationHandler.END
    
    # Validate URL
    if not (url.startswith("http://") or url.startswith("https://")):
        await update.message.reply_text(
            "× Invalid URL format.\n"
            "▸ URL must start with http:// or https://",
            reply_markup=main_keyboard(user.id)
        )
        return ConversationHandler.END
    
    # Check cooldown
    cooldown_time = BOOST_COOLDOWNS.get(user.id, 0)
    if time.time() - cooldown_time < 30:
        remaining = int(30 - (time.time() - cooldown_time))
        await update.message.reply_text(
            f"× Please wait {remaining} seconds before next boost.",
            reply_markup=main_keyboard(user.id)
        )
        return ConversationHandler.END
    
    # Check daily limit
    today = datetime.now().strftime("%Y-%m-%d")
    if user.id not in DAILY_BOOSTS:
        DAILY_BOOSTS[user.id] = {}
    
    daily_count = DAILY_BOOSTS[user.id].get(today, 0)
    limit = SETTINGS.premium_boost_limit if has_access(user.id) else SETTINGS.daily_boost_limit
    
    if daily_count >= limit:
        await update.message.reply_text(
            f"× Daily boost limit reached ({limit}/day).",
            reply_markup=main_keyboard(user.id)
        )
        return ConversationHandler.END
    
    processing_msg = await update.message.reply_text(
        "▸ Processing your request...\n"
        "▸ Please wait..."
    )
    
    try:
        async with aiohttp.ClientSession() as session:
            if service_id.startswith('tiktok_'):
                result = await handle_tiktok_boost(session, url, service_id)
            else:
                result = await handle_zefame_boost(session, url, service_id)
        
        if result['success']:
            USERS[user.id].total_boosts += 1
            DAILY_BOOSTS[user.id][today] = daily_count + 1
            BOOST_COOLDOWNS[user.id] = time.time()
            SETTINGS.total_boosts_done += 1
            
            save_users()
            save_settings()
            
            await processing_msg.edit_text(
                "╔════════════════════════════╗\n"
                "║    BOOST SUBMITTED         ║\n"
                "╚════════════════════════════╝\n\n"
                f"▸ Service : {SERVICES[service_id]['name']}\n"
                f"▸ URL     : {url}\n"
                f"▸ Status  : SUCCESS\n"
                f"▸ Message : {result['message']}\n\n"
                "Your boost will be processed shortly."
            )
        else:
            await processing_msg.edit_text(
                "╔════════════════════════════╗\n"
                "║      BOOST FAILED          ║\n"
                "╚════════════════════════════╝\n\n"
                f"▸ Service : {SERVICES[service_id]['name']}\n"
                f"▸ Reason  : {result['message']}\n\n"
                "Please try again later."
            )
            
    except Exception as e:
        logger.error(f"Boost error: {e}")
        await processing_msg.edit_text(
            "× An error occurred while processing.\n"
            "× Please try again later."
        )
    
    await context.bot.send_message(
        chat_id=user.id,
        text="▸ Main Menu :",
        reply_markup=main_keyboard(user.id)
    )
    
    return ConversationHandler.END

async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_or_create_user(user_id, update.effective_user.username or "", update.effective_user.first_name or "")
    
    today = datetime.now().strftime("%Y-%m-%d")
    today_boosts = DAILY_BOOSTS.get(user_id, {}).get(today, 0)
    access_status = get_access_status(user_id)
    limit = SETTINGS.premium_boost_limit if has_access(user_id) else SETTINGS.daily_boost_limit
    
    stats_text = (
        "╔════════════════════════════╗\n"
        "║     YOUR STATISTICS        ║\n"
        "╚════════════════════════════╝\n\n"
        f"▸ Name       : {user.first_name}\n"
        f"▸ ID         : {user.user_id}\n"
        f"▸ Access     : {access_status}\n"
        f"▸ Keys Used  : {user.total_keys_used}\n"
        f"▸ Total Boost: {user.total_boosts}\n"
        f"▸ Today      : {today_boosts}/{limit}\n"
        f"▸ Joined     : {datetime.fromtimestamp(user.joined_at).strftime('%Y-%m-%d')}"
    )
    
    await update.message.reply_text(stats_text)

# ============================================================
# ADMIN HANDLERS
# ============================================================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("× Admin only.")
        return
    
    await update.message.reply_text(
        "╔════════════════════════════╗\n"
        "║      ADMIN PANEL           ║\n"
        "╚════════════════════════════╝\n\n"
        "▸ Select an option below :",
        reply_markup=admin_keyboard()
    )

async def generate_key_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    await update.message.reply_text(
        "╔════════════════════════════╗\n"
        "║   GENERATE ACCESS KEY      ║\n"
        "╚════════════════════════════╝\n\n"
        "▸ Choose key type :",
        reply_markup=key_type_keyboard()
    )

async def list_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    if not KEYS:
        await update.message.reply_text("× No keys generated yet.")
        return
    
    keys_list = sorted(KEYS.values(), key=lambda x: x.created_at, reverse=True)[:20]
    
    message = (
        "╔════════════════════════════╗\n"
        "║      RECENT KEYS           ║\n"
        "╚════════════════════════════╝\n\n"
    )
    
    for key in keys_list:
        if key.revoked:
            status = "REVOKED"
        elif key.used_by:
            user = USERS.get(key.used_by)
            username = user.username if user else "Unknown"
            status = f"USED BY {username}"
        elif time.time() > key.expires_at:
            status = "EXPIRED"
        else:
            status = "AVAILABLE"
        
        if key.is_lifetime:
            duration = "LIFETIME"
        else:
            duration = f"{key.duration_days}D {key.duration_hours}H"
        
        message += (
            f"▸ Key      : {key.key}\n"
            f"▸ Duration : {duration}\n"
            f"▸ Status   : {status}\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
        )
    
    await update.message.reply_text(message)

async def global_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    total_users = len(USERS)
    active_users = sum(1 for u in USERS.values() if has_access(u.user_id))
    total_keys = len(KEYS)
    used_keys = sum(1 for k in KEYS.values() if k.used_by)
    
    stats_text = (
        "╔════════════════════════════╗\n"
        "║     GLOBAL STATISTICS      ║\n"
        "╚════════════════════════════╝\n\n"
        f"▸ Total Users  : {total_users}\n"
        f"▸ Active Users : {active_users}\n"
        f"▸ Total Keys   : {total_keys}\n"
        f"▸ Used Keys    : {used_keys}\n"
        f"▸ Total Boosts : {SETTINGS.total_boosts_done}\n"
        f"▸ Maintenance  : {'ON' if SETTINGS.maintenance_mode else 'OFF'}"
    )
    
    await update.message.reply_text(stats_text)

async def revoke_key_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    context.user_data['awaiting_revoke_key'] = True
    
    await update.message.reply_text(
        "╔════════════════════════════╗\n"
        "║      REVOKE KEY            ║\n"
        "╚════════════════════════════╝\n\n"
        "▸ Enter the key to revoke :\n"
        "▸ Format : CLOUT-XXXXXXXXXXXX\n\n"
        "Type /cancel to cancel."
    )
    
    return AWAITING_REVOKE_KEY

async def handle_revoke_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key_string = update.message.text.strip().upper()
    context.user_data['awaiting_revoke_key'] = False
    
    result = revoke_key(key_string)
    await update.message.reply_text(result['message'])
    return ConversationHandler.END

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    context.user_data['awaiting_broadcast'] = True
    
    await update.message.reply_text(
        "╔════════════════════════════╗\n"
        "║     BROADCAST MESSAGE      ║\n"
        "╚════════════════════════════╝\n\n"
        "▸ Send the message to broadcast :\n"
        "Type /cancel to cancel."
    )
    
    return AWAITING_BROADCAST_MESSAGE

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text
    context.user_data['awaiting_broadcast'] = False
    
    success = 0
    failed = 0
    
    status_msg = await update.message.reply_text(
        f"▸ Sending broadcast to {len(USERS)} users..."
    )
    
    for user_id in USERS:
        try:
            await context.bot.send_message(chat_id=user_id, text=message)
            success += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    
    await status_msg.edit_text(
        "╔════════════════════════════╗\n"
        "║    BROADCAST COMPLETE      ║\n"
        "╚════════════════════════════╝\n\n"
        f"▸ Success : {success}\n"
        f"▸ Failed  : {failed}"
    )
    
    return ConversationHandler.END

async def toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    
    SETTINGS.maintenance_mode = not SETTINGS.maintenance_mode
    save_settings()
    
    status = "ON" if SETTINGS.maintenance_mode else "OFF"
    await update.message.reply_text(f"▸ Maintenance mode : {status}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "× Operation cancelled.",
        reply_markup=main_keyboard(update.effective_user.id)
    )
    return ConversationHandler.END

# ============================================================
# MESSAGE HANDLER
# ============================================================

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle menu navigation and pending operations"""
    text = update.message.text.strip()
    
    # Check for pending operations first
    if context.user_data.get('awaiting_boost_url'):
        await handle_boost_url(update, context)
        return
    
    if context.user_data.get('awaiting_key'):
        await handle_key_input(update, context)
        return
    
    if context.user_data.get('awaiting_key_duration'):
        await handle_key_duration(update, context)
        return
    
    if context.user_data.get('awaiting_broadcast'):
        await handle_broadcast(update, context)
        return
    
    if context.user_data.get('awaiting_revoke_key'):
        await handle_revoke_key(update, context)
        return
    
    # Regular menu navigation
    if text == "▸ Boost Services":
        await boost_now(update, context)
    elif text == "▸ Get Access":
        await get_access(update, context)
    elif text == "▸ Redeem Key":
        await get_access(update, context)
    elif text == "▸ My Statistics":
        await my_stats(update, context)
    elif text == "▸ Help":
        await start(update, context)
    elif text == "▸ Admin Panel" and is_admin(update.effective_user.id):
        await admin_panel(update, context)
    elif text == "▸ Generate Key" and is_admin(update.effective_user.id):
        await generate_key_admin(update, context)
    elif text == "▸ List Keys" and is_admin(update.effective_user.id):
        await list_keys(update, context)
    elif text == "▸ Global Stats" and is_admin(update.effective_user.id):
        await global_stats(update, context)
    elif text == "▸ Revoke Key" and is_admin(update.effective_user.id):
        await revoke_key_admin(update, context)
    elif text == "▸ Broadcast" and is_admin(update.effective_user.id):
        await broadcast(update, context)
    elif text == "▸ Settings" and is_admin(update.effective_user.id):
        await toggle_maintenance(update, context)
    elif text == "▸ Main Menu":
        await start(update, context)
    else:
        await update.message.reply_text(
            "× Invalid option.\n"
            "▸ Please use the menu buttons below.",
            reply_markup=main_keyboard(update.effective_user.id)
        )

# ============================================================
# MAIN
# ============================================================

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return
    
    load_state()
    
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation handlers
    key_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r'^▸ Get Access$'), get_access),
            MessageHandler(filters.Regex(r'^▸ Redeem Key$'), get_access),
        ],
        states={
            AWAITING_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_key_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    key_gen_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r'^▸ Generate Key$'), generate_key_admin),
        ],
        states={
            AWAITING_KEY_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_key_duration)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    boost_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r'^▸ Boost Services$'), boost_now),
        ],
        states={
            AWAITING_BOOST_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_boost_url)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    admin_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r'^▸ Revoke Key$'), revoke_key_admin),
            MessageHandler(filters.Regex(r'^▸ Broadcast$'), broadcast),
        ],
        states={
            AWAITING_REVOKE_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_revoke_key)],
            AWAITING_BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", my_stats))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(key_conv)
    application.add_handler(key_gen_conv)
    application.add_handler(boost_conv)
    application.add_handler(admin_conv)
    
    # Callback handler
    application.add_handler(CallbackQueryHandler(handle_service_selection))
    
    # Menu handler (should be last)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu))
    
    logger.info("Clout Premium Boost Bot started!")
    application.run_polling()

if __name__ == "__main__":
    main()
