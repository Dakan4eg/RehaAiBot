import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import httpx

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
REDIS_URL = os.getenv("REDIS_URL")          # REST Endpoint из Upstash
REDIS_TOKEN = os.getenv("REDIS_TOKEN")      # REST API Token из Upstash
HF_API_TOKEN = os.getenv("HF_API_TOKEN")    # Токен Hugging Face
MAX_CONTEXT_LENGTH = 100                    # Максимальная длина контекста

# Заголовки для Upstash Redis
REDIS_HEADERS = {
    "Authorization": f"Bearer {REDIS_TOKEN}",
    "Content-Type": "application/json"
}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_message = update.message.text
        chat_id = update.message.chat.id
        
        # Проверка и формирование URL Redis
        if not REDIS_URL:
            raise ValueError("REDIS_URL не задан!")
        
        redis_url = REDIS_URL.strip()
        if not redis_url.startswith(("http://", "https://")):
            redis_url = f"https://{redis_url}"
            logger.warning("Автоматически добавлен протокол к REDIS_URL")

        redis_key = f"chat:{chat_id}"
        
        # Явное отключение прокси
        async with httpx.AsyncClient(proxies={}) as client:
            # 1. Сохраняем сообщение в Redis (LPUSH)
            lpush_url = f"{redis_url}/lpush/{redis_key}"
            lpush_response = await client.post(
                lpush_url,
                headers=REDIS_HEADERS,
                json=[user_message],
                timeout=10
            )
            
            if lpush_response.status_code not in [200, 201]:
                logger.error(f"Redis LPUSH error: {lpush_response.text}")
                raise ConnectionError("Ошибка записи в Redis")

            # 2. Обрезаем список (LTRIM)
            ltrim_url = f"{redis_url}/ltrim/{redis_key}/0/{MAX_CONTEXT_LENGTH-1}"
            await client.post(ltrim_url, headers=REDIS_HEADERS, timeout=10)

            # 3. Получаем контекст (LRANGE)
            lrange_url = f"{redis_url}/lrange/{redis_key}/0/{MAX_CONTEXT_LENGTH}"
            context_response = await client.get(lrange_url, headers=REDIS_HEADERS, timeout=10)
            
            context_messages = []
            if context_response.status_code == 200:
                context_messages = context_response.json().get("result", [])
            else:
                logger.error(f"Redis LRANGE error: {context_response.text}")

            # 4. Запрос к Hugging Face
            hf_headers = {"Authorization": f"Bearer {HF_API_TOKEN}"}
            payload = {
                "inputs": {
                    "text": user_message,
                    "past_user_inputs": context_messages,
                    "generated_responses": []
                }
            }

            try:
                hf_response = await client.post(
                    "https://api-inference.huggingface.co/models/microsoft/DialoGPT-large",
                    headers=hf_headers,
                    json=payload,
                    timeout=15
                )
            except httpx.TimeoutException:
                await update.message.reply_text("Сервис перегружен ⏳")
                return

            if hf_response.status_code == 200:
                response_data = hf_response.json()
                bot_response = response_data.get("generated_text", "Не понимаю запрос 😕")[:4096]
            else:
                logger.error(f"Hugging Face API error: {hf_response.text}")
                bot_response = "Ошибка генерации 🤖"

            await update.message.reply_text(bot_response)

    except Exception as e:
        logger.exception("Critical error:")
        await update.message.reply_text("Произошла ошибка 😢")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    logger.error(f"Global error: {error}", exc_info=error)

def main():
    # Проверка переменных окружения
    required_vars = ["TELEGRAM_TOKEN", "REDIS_URL", "REDIS_TOKEN", "HF_API_TOKEN"]
    for var in required_vars:
        if not os.getenv(var):
            logger.critical(f"Переменная окружения {var} не задана!")
            raise SystemExit(1)

    # Инициализация бота
    application = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .build()
    )

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    # Запуск
    application.run_polling(
        poll_interval=5,
        timeout=25,
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()
