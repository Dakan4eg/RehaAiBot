import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters
import redis
import requests

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")
REDIS_TOKEN = os.getenv("REDIS_TOKEN")
HF_API_TOKEN = os.getenv("HF_API_TOKEN")
MAX_CONTEXT_LENGTH = 100  # Максимальное число сообщений в контексте

# Подключение к Redis
redis_client = redis.Redis(
    host=REDIS_URL,
    password=REDIS_TOKEN,
    ssl=True,
    decode_responses=True
)

async def handle_message(update: Update, context):
    try:
        user_message = update.message.text
        chat_id = update.message.chat.id
        
        # Сохраняем сообщение в Redis
        redis_client.lpush(f"chat:{chat_id}", user_message)
        redis_client.ltrim(f"chat:{chat_id}", 0, MAX_CONTEXT_LENGTH - 1)
        
        # Получаем контекст (последние 100 сообщений)
        context_messages = redis_client.lrange(f"chat:{chat_id}", 0, MAX_CONTEXT_LENGTH - 1)
        
        # Генерация ответа через Hugging Face API
        headers = {"Authorization": f"Bearer {HF_API_TOKEN}"}
        response = requests.post(
            "https://api-inference.huggingface.co/models/microsoft/DialoGPT-large",
            headers=headers,
            json={"inputs": {"past_user_inputs": context_messages, "text": user_message}}
        )
        
        # Проверка ошибок и ограничение длины
        if response.status_code == 200:
            bot_response = response.json()["generated_text"]
            bot_response = bot_response[:4096]  # Ограничение Telegram
            await update.message.reply_text(bot_response)
        else:
            await update.message.reply_text("Ошибка генерации ответа 🤖")
            
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("Произошла ошибка 😢")

def main():
    # Инициализация бота
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == "__main__":
    main()
