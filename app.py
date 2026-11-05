import threading
import asyncio
import logging
from flask import Flask
from bot import main as run_telegram_bot

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("flask-app")

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def start_bot():
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        logger.info("Starting Telegram bot in background thread...")
        run_telegram_bot()
    except Exception as e:
        logger.error(f"Bot thread crashed: {type(e).__name__}: {e}")

if __name__ == "__main__":
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()
    logger.info("Telegram bot thread started.")
    logger.info("Starting Flask server...")
    app.run(host="0.0.0.0", port=10000)
