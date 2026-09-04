#!/usr/bin/env python3
"""
Bridge Bot: MAX Messenger -> Telegram Group
--------------------------------------------
Пересылает все сообщения из группы в MAX Messenger в группу Telegram.

Автор: Antigravity Assistant
"""

import os
import sys
import json
import time
import html
import signal
import logging
import argparse
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests
import urllib3

# Отключаем предупреждения об отсутствии системных сертификатов Минцифры
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("max-tg-bridge")

# Базовая папка скрипта
BASE_DIR = Path(__file__).resolve().parent
MARKER_FILE = BASE_DIR / "marker.json"
ENV_FILE = BASE_DIR / ".env"


def load_env_file(filepath: Path) -> None:
    """Загружает переменные из .env файла вручную (без обязательного python-dotenv)."""
    if not filepath.exists():
        return
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as e:
        logger.warning("Не удалось прочитать .env файл: %s", e)


# Загружаем переменные окружения
load_env_file(ENV_FILE)

# Конфигурационные параметры
MAX_BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "").strip()
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID", "").strip()
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
MAX_API_BASE = os.getenv("MAX_API_BASE", "https://platform-api2.max.ru").rstrip("/")
SOURCE_CHAT_NAME = os.getenv("SOURCE_CHAT_NAME", "MAX").strip()
VERIFY_SSL = os.getenv("MAX_VERIFY_SSL", "False").lower() in ("true", "1", "yes")

TG_API_BASE = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"

# Флаг для корректной остановки процесса
RUNNING = True


def signal_handler(signum, frame):
    """Обработчик сигналов завершения работы (Ctrl+C, SIGTERM)."""
    global RUNNING
    logger.info("Получен сигнал завершения. Остановка моста...")
    RUNNING = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def load_marker() -> Optional[int]:
    """Загружает последний сохраненный маркер (курсор) обновлений MAX."""
    if MARKER_FILE.exists():
        try:
            with open(MARKER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("marker")
        except Exception as e:
            logger.warning("Ошибка чтения marker.json: %s", e)
    return None


def save_marker(marker: Optional[int]) -> None:
    """Сохраняет маркер обновлений в файл."""
    if marker is None:
        return
    try:
        with open(MARKER_FILE, "w", encoding="utf-8") as f:
            json.dump({"marker": marker, "updated_at": time.time()}, f)
    except Exception as e:
        logger.error("Не удалось сохранить маркер: %s", e)


def check_tokens() -> bool:
    """Проверяет подключение к MAX и Telegram."""
    logger.info("Проверка конфигурации и токенов...")
    success = True

    # 1. Проверка Telegram
    if not TG_BOT_TOKEN:
        logger.error("❌ TG_BOT_TOKEN не задан в .env!")
        success = False
    else:
        try:
            r = requests.get(f"{TG_API_BASE}/getMe", timeout=10)
            if r.status_code == 200 and r.json().get("ok"):
                bot_data = r.json()["result"]
                logger.info("✅ Telegram Bot подключен: @%s (%s)", bot_data.get("username"), bot_data.get("first_name"))
            else:
                logger.error("❌ Ошибка Telegram токена: HTTP %d: %s", r.status_code, r.text)
                success = False
        except Exception as e:
            logger.error("❌ Ошибка соединения с Telegram API: %s", e)
            success = False

    # 2. Проверка MAX
    if not MAX_BOT_TOKEN:
        logger.error("❌ MAX_BOT_TOKEN не задан в .env!")
        success = False
    else:
        try:
            headers = {"Authorization": MAX_BOT_TOKEN}
            r = requests.get(f"{MAX_API_BASE}/me", headers=headers, verify=VERIFY_SSL, timeout=10)
            if r.status_code == 200:
                bot_data = r.json()
                logger.info("✅ MAX Bot подключен: ID=%s, Имя='%s'", bot_data.get("user_id"), bot_data.get("first_name"))
            else:
                logger.error("❌ Ошибка токена MAX Bot: HTTP %d: %s", r.status_code, r.text)
                success = False
        except Exception as e:
            logger.error("❌ Ошибка соединения с MAX API (%s): %s", MAX_API_BASE, e)
            success = False

    # 3. Проверка параметров групп
    if not TG_CHAT_ID:
        logger.warning("⚠️ TG_CHAT_ID не задан в .env! Сообщения не смогут отправляться в Telegram.")
    else:
        logger.info("🎯 Целевая группа Telegram: %s", TG_CHAT_ID)

    if not MAX_CHAT_ID:
        logger.info("ℹ️ MAX_CHAT_ID не задан. Бот будет слушать все чаты и выводить Chat ID при получении сообщений.")
    else:
        logger.info("🎯 Фильтр группы MAX: %s", MAX_CHAT_ID)

    return success


def send_to_telegram(text: str, photo_url: Optional[str] = None, file_url: Optional[str] = None) -> bool:
    """Отправляет отформатированное сообщение в группу Telegram."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        logger.warning("Невозможно отправить в Telegram: не настроен TG_BOT_TOKEN или TG_CHAT_ID")
        return False

    try:
        if photo_url:
            payload = {
                "chat_id": TG_CHAT_ID,
                "photo": photo_url,
                "caption": text,
                "parse_mode": "HTML"
            }
            r = requests.post(f"{TG_API_BASE}/sendPhoto", json=payload, timeout=15)
        elif file_url:
            payload = {
                "chat_id": TG_CHAT_ID,
                "document": file_url,
                "caption": text,
                "parse_mode": "HTML"
            }
            r = requests.post(f"{TG_API_BASE}/sendDocument", json=payload, timeout=15)
        else:
            payload = {
                "chat_id": TG_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            r = requests.post(f"{TG_API_BASE}/sendMessage", json=payload, timeout=15)

        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            return True
        else:
            logger.error("Ошибка отправки в Telegram: %s (код %d)", data.get("description"), r.status_code)
            # Если не удалось отправить с HTML форматированием (например, некорректный тег), шлем plain text
            if "can't parse entities" in data.get("description", "").lower():
                payload["parse_mode"] = ""
                requests.post(f"{TG_API_BASE}/sendMessage", json={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
            return False
    except Exception as e:
        logger.error("Исключение при отправке в Telegram: %s", e)
        return False


def format_sender_name(sender: Dict[str, Any]) -> str:
    """Форматирует отображаемое имя автора сообщения в MAX."""
    first_name = sender.get("first_name") or ""
    last_name = sender.get("last_name") or ""
    full_name = f"{first_name} {last_name}".strip()

    username = sender.get("username")
    if username:
        if full_name:
            return f"{full_name} (@{username})"
        return f"@{username}"

    if full_name:
        return full_name

    user_id = sender.get("user_id")
    return f"Пользователь #{user_id}" if user_id else "Аноним"


def process_max_message(message: Dict[str, Any]) -> None:
    """Обрабатывает одно входящее сообщение из MAX и пересылает его в Telegram."""
    sender = message.get("sender", {})
    if sender.get("is_bot"):
        # Игнорируем сообщения других ботов во избежание циклов
        return

    recipient = message.get("recipient", {})
    incoming_chat_id = recipient.get("chat_id") or message.get("chat_id") or (message.get("chat") or {}).get("id")

    # Если задан фильтр по Chat ID группы в MAX
    if MAX_CHAT_ID:
        target_id = str(MAX_CHAT_ID).strip()
        current_id = str(incoming_chat_id).strip()
        if current_id != target_id and current_id.lstrip("-") != target_id.lstrip("-"):
            logger.info("⏭️ Пропуск сообщения из другого чата [Chat ID: %s] (ожидается: %s)", incoming_chat_id, target_id)
            return
    else:
        logger.info(
            "💡 Получено сообщение из чата MAX [Chat ID: %s]. "
            "Чтобы пересылать сообщения ТОЛЬКО из этой группы, укажите MAX_CHAT_ID=%s в файле .env",
            incoming_chat_id, incoming_chat_id
        )

    author = format_sender_name(sender)
    body = message.get("body", {})
    msg_text = body.get("text") or ""
    attachments = body.get("attachments") or message.get("attachments") or []

    # Экранируем спецсимволы HTML для безопасной отправки в Telegram
    safe_author = html.escape(author)
    safe_text = html.escape(msg_text)

    # Ищем фото или вложения
    photo_url = None
    file_url = None
    links_text = []

    for att in attachments:
        att_type = att.get("type")
        payload = att.get("payload") or {}
        url = payload.get("url")

        if not url:
            continue

        if att_type in ("image", "photo") and not photo_url:
            photo_url = url
        elif att_type in ("file", "document") and not file_url:
            file_url = url
        else:
            links_text.append(f"📎 <a href=\"{url}\">Вложение ({att_type or 'файл'})</a>")

    # Формируем заголовок сообщения
    header = f"💬 <b>[{SOURCE_CHAT_NAME}] {safe_author}</b>:\n"

    # Если есть дополнительный текст или вложения
    full_message = header + (safe_text if safe_text else "<i>(без текста)</i>")
    if links_text:
        full_message += "\n" + "\n".join(links_text)

    logger.info("Пересылка сообщения от '%s' (длина текста: %d)", author, len(msg_text))
    send_to_telegram(full_message, photo_url=photo_url, file_url=file_url)


def delete_webhook_if_exists() -> None:
    """Удаляет существующие вебхуки в MAX, если они были ранее настроены (чтобы работал Long Polling)."""
    try:
        headers = {"Authorization": MAX_BOT_TOKEN}
        r = requests.delete(f"{MAX_API_BASE}/subscriptions", headers=headers, verify=VERIFY_SSL, timeout=10)
        if r.status_code in (200, 204):
            logger.info("Существующие Webhook-подписки в MAX очищены. Long Polling активен.")
    except Exception as e:
        logger.debug("Проверка вебхуков MAX: %s", e)


def run_bridge() -> None:
    """Основной цикл опроса (Long Polling) MAX и пересылки в Telegram."""
    global RUNNING
    marker = load_marker()
    if marker:
        logger.info("Возобновление с маркера: %s", marker)
    else:
        logger.info("Маркер не найден. Запуск с последних сообщений.")

    headers = {
        "Authorization": MAX_BOT_TOKEN
    }

    delete_webhook_if_exists()

    logger.info("🚀 Мост MAX -> Telegram запущен и ожидает сообщений...")

    while RUNNING:
        try:
            params = {
                "timeout": 25,
                "types": "message_created"
            }
            if marker is not None:
                params["marker"] = marker

            # Отправляем запрос Long Polling
            response = requests.get(
                f"{MAX_API_BASE}/updates",
                headers=headers,
                params=params,
                verify=VERIFY_SSL,
                timeout=35
            )

            if response.status_code == 200:
                data = response.json()
                updates: List[Dict[str, Any]] = data.get("updates", [])
                new_marker = data.get("marker")

                if updates:
                    logger.info("Получено %d новых событий из MAX", len(updates))

                for upd in updates:
                    upd_type = upd.get("update_type")
                    if upd_type == "message_created":
                        msg = upd.get("message")
                        if msg:
                            process_max_message(msg)

                if new_marker is not None and new_marker != marker:
                    marker = new_marker
                    save_marker(marker)

            elif response.status_code == 401:
                logger.error("❌ Ошибка 401 Unauthorized: неверный MAX_BOT_TOKEN. Проверьте .env")
                time.sleep(10)
            elif response.status_code == 429:
                logger.warning("Превышен лимит запросов (HTTP 429). Ожидание 5 секунд...")
                time.sleep(5)
            elif response.status_code == 409:
                logger.warning("Конфликт обновлений (HTTP 409). Возможно, активен Webhook. Сбрасываем подписки...")
                delete_webhook_if_exists()
                time.sleep(3)
            else:
                logger.warning("Неожиданный ответ от MAX API: HTTP %d: %s", response.status_code, response.text)
                time.sleep(3)

        except requests.exceptions.Timeout:
            # Обычный таймаут long polling, сервер не прислал ничего за 25 сек, повторяем цикл
            continue
        except requests.exceptions.ConnectionError as e:
            logger.warning("Ошибка соединения с MAX API: %s. Повтор через 5 секунд...", e)
            time.sleep(5)
        except Exception as e:
            logger.error("Непредвиденная ошибка в цикле опроса: %s", e, exc_info=True)
            time.sleep(3)

    logger.info("Мост MAX -> Telegram корректно остановлен.")


def main():
    parser = argparse.ArgumentParser(description="MAX to Telegram Group Bridge Bot")
    parser.add_argument("--check", action="store_true", help="Проверить токены и подключение без запуска цикла")
    parser.add_argument("--reset-marker", action="store_true", help="Сбросить сохраненный маркер (читать только новые)")
    args = parser.parse_args()

    if args.reset_marker:
        if MARKER_FILE.exists():
            MARKER_FILE.unlink()
            logger.info("Файл marker.json удален. Курсор сброшен.")
        else:
            logger.info("Файл marker.json не найден.")

    if args.check:
        valid = check_tokens()
        sys.exit(0 if valid else 1)

    if not check_tokens():
        logger.warning("Проверка конфигурации выявила ошибки. Пожалуйста, проверьте файл .env перед запуском.")

    run_bridge()


if __name__ == "__main__":
    main()
