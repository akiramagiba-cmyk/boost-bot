import os
import threading
from flask import Flask
from bot import main as run_telegram_bot

app = Flask(__name__)

@app.route('/')
def home():
    return "Telegram Bot is Alive and Running!"

@app.route('/health')
def health():
    return "OK", 200

def start_bot():
    # Tinatawag nito ang main() function mula sa bot.py mo
    run_telegram_bot()

if __name__ == '__main__':
    # 1. Simulan ang Telegram Bot sa background thread
    bot_thread = threading.Thread(target=start_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # 2. Simulan ang Flask web server para may makitang Port ang Render
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
