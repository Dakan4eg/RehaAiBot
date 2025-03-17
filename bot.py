import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import requests

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")   
REDIS_TOKEN = os.getenv("REDIS_TOKEN") 
HF_API_TOKEN = os.getenv("HF_API_TOKEN")
MAX_CONTEXT_LENGTH = 100  # Максимальное число сообщений в контексте

# Заголовки для Upstash Redis REST API
REDIS_HEADERS = {
    "Authorization": f"Bearer {REDIS_TOKEN}",  # Используем REDIS_TOKEN
    "Content-Type": "application/json"
}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_message = update.message.text
        chat_id = update.message.chat.id
        
        # Сохраняем сообщение в Redis через REST API
        redis_key = f"chat:{chat_id}"
        lpush_url = f"{os.getenv('REDIS_URL')}/lpush/{redis_key}"
        response = requests.post(
            lpush_url,
            headers=REDIS_HEADERS,
            json=[user_message]  # Upstash требует список значений
        )
        
        if response.status_code not in [200, 201]:
            logger.error(f"Redis LPUSH error: {response.text}")
            raise Exception("Redis error")

        # Обрезаем список до MAX_CONTEXT_LENGTH элементов
        ltrim_url = f"{os.getenv('REDIS_URL')}/ltrim/{redis_key}/0/{MAX_CONTEXT_LENGTH-1}"
        requests.post(ltrim_url, headers=REDIS_HEADERS)

        # Получаем контекст (последние MAX_CONTEXT_LENGTH сообщений)
        lrange_url = f"{os.getenv('REDIS_URL')}/lrange/{redis_key}/0/{MAX_CONTEXT_LENGTH}"
        context_response = requests.get(lrange_url, headers=REDIS_HEADERS)
        
        if context_response.status_code != 200:
            logger.error(f"Redis LRANGE error: {context_response.text}")
            context_messages = []
        else:
            context_messages = context_response.json()["result"] or []

        # Формируем запрос к Hugging Face API
        hf_headers = {"Authorization": f"Bearer {HF_API_TOKEN}"}
        payload = {
            "inputs": {
                "text": user_message,
                "past_user_inputs": context_messages,
                "generated_responses": []
            }
        }

        try:
            hf_response = requests.post(
                "https://api-inference.huggingface.co/models/microsoft/DialoGPT-large",
                headers=hf_headers,
                json=payload,
                timeout=15  # Таймаут для Hugging Face
            )
        except requests.exceptions.Timeout:
            logger.error("Hugging Face API timeout")
            await update.message.reply_text("Сервис перегружен, попробуйте позже ⏳")
            return

        if hf_response.status_code == 200:
            response_data = hf_response.json()
            if "generated_text" in response_data:
                bot_response = response_data["generated_text"][:4096]  # Ограничение Telegram
            else:
                logger.error(f"Некорректный ответ API: {response_data}")
                bot_response = "Не понимаю ваш запрос 😕"
        else:
            logger.error(f"API error {hf_response.status_code}: {hf_response.text}")
            bot_response = "Ошибка генерации ответа 🤖"

        await update.message.reply_text(bot_response)

    except Exception as e:
        logger.exception(f"Critical error: {e}")
        await update.message.reply_text("Произошла внутренняя ошибка 😢")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    logger.error(f"Global error handler: {error}", exc_info=error)
    
    if isinstance(error, ConnectionError):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Проблемы с подключением 🔌 Повторяю попытку..."
        )

def main():
    # Инициализация бота с таймаутами
    application = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )

    # Регистрация обработчиков
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    # Запуск с интервалом опроса
    application.run_polling(
        poll_interval=5,
        timeout=25,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
