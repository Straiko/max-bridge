#!/usr/bin/env python3
"""
MAX Messenger -> Telegram Userbot (Юзербот)
--------------------------------------------
Подключается напрямую к API MAX под вашей личной учётной записью,
слушает группу "ИСиП-2-23" и автоматически пересылает новые сообщения в Telegram.

Работает 24/7 автономно на любом бот-хостинге или сервере без открытого браузера.
"""

import os
import sys
import json
import time
import html
import ssl
import struct
import signal
import socket
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any, Set

import requests
import msgpack
import lz4.block

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("max-userbot")

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
SEEN_FILE = BASE_DIR / "userbot_seen.json"


def load_env_file(filepath: Path) -> None:
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
        logger.warning("Ошибка чтения .env: %s", e)


load_env_file(ENV_FILE)

MAX_USER_TOKEN = os.getenv("MAX_USER_TOKEN", "").strip()
MAX_DEVICE_ID = os.getenv("MAX_DEVICE_ID", "").strip()
MAX_CHAT_ID = os.getenv("MAX_CHAT_ID", "-72161178330527").strip()
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
SOURCE_CHAT_NAME = os.getenv("SOURCE_CHAT_NAME", "ИСиП-2-23").strip()

TG_API_BASE = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"
HOST = "api.oneme.ru"
PORT = 443

RUNNING = True
_seq = 1
_seq_lock = threading.Lock()
USER_NAMES: Dict[int, str] = {}


def signal_handler(signum, frame):
    global RUNNING
    logger.info("Завершение работы юзербота...")
    RUNNING = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def get_seq() -> int:
    global _seq
    with _seq_lock:
        _seq = (_seq + 1) & 0xFF
        if _seq == 0:
            _seq = 1
        return _seq


def load_seen_ids() -> Set[str]:
    if SEEN_FILE.exists():
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("seen", []))
        except Exception:
            pass
    return set()


def save_seen_ids(seen: Set[str]) -> None:
    try:
        data = {"seen": list(seen)[-2000:]}
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error("Ошибка сохранения seen_ids: %s", e)


def send_to_telegram(text: str) -> bool:
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        logger.warning("Не настроен TG_BOT_TOKEN или TG_CHAT_ID! Перейдите в Telegram и укажите ID группы в .env")
        return False

    try:
        payload = {
            "chat_id": TG_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        r = requests.post(f"{TG_API_BASE}/sendMessage", json=payload, timeout=12)
        res = r.json()
        if r.status_code == 200 and res.get("ok"):
            return True
        else:
            logger.error("Ошибка Telegram API: %s", res.get("description"))
            # Попытка без HTML форматирования
            requests.post(f"{TG_API_BASE}/sendMessage", json={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)
            return False
    except Exception as e:
        logger.error("Исключение при отправке в Telegram: %s", e)
        return False


def send_frame(sock: ssl.SSLSocket, ver: int, cmd: int, seq: int, opcode: int, payload_dict: Dict[str, Any]):
    body = msgpack.packb(payload_dict)
    header = struct.pack(">BBHHI", ver, cmd, seq, opcode, len(body))
    sock.sendall(header + body)


import select

def recv_exact(sock: ssl.SSLSocket, n: int) -> bytes:
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionResetError("Соединение закрыто сервером")
        data.extend(chunk)
    return bytes(data)


def recv_frame(sock: ssl.SSLSocket, timeout: float = 4.0):
    r, _, _ = select.select([sock], [], [], timeout)
    if not r:
        return None, 0, None, None

    hdr = recv_exact(sock, 10)
    ver, cmd, seq, opcode, packed_len = struct.unpack(">BBHHI", hdr)
    body_len = packed_len & 0x00FFFFFF
    flags = (packed_len >> 24) & 0xFF
    body = recv_exact(sock, body_len)

    # Декомпрессия LZ4 при наличии флага
    if flags & 2 or (flags & 1 and len(body) > 2 and body[:2] not in (b"\x85", b"\x84", b"\x83", b"\x82", b"\x81")):
        try:
            body = lz4.block.decompress(body, uncompressed_size=4 * 1024 * 1024)
        except Exception:
            pass

    # Пропуск ведущих ints
    offset = 0
    if flags & 1:
        while offset < min(8, len(body)):
            b = body[offset]
            if b <= 0x7F or (0xE0 <= b <= 0xFF):
                offset += 1
            elif b == 0xD0:
                offset += 2
            elif b == 0xD1:
                offset += 3
            elif b == 0xD2:
                offset += 5
            else:
                break

    unpacker = msgpack.Unpacker(raw=False, strict_map_key=False)
    unpacker.feed(body[offset:])
    for obj in unpacker:
        return cmd, seq, opcode, obj
    return cmd, seq, opcode, {}


def extract_chat_id(data: Dict[str, Any]) -> Optional[int]:
    """Извлекает ID чата из входящего пакета или словаря сообщения."""
    if not isinstance(data, dict):
        return None
    cid = data.get("chatId") or data.get("chat_id") or data.get("chat")
    if cid is not None:
        try:
            return int(cid)
        except (ValueError, TypeError):
            pass

    msg = data.get("message")
    if isinstance(msg, dict):
        cid = msg.get("chatId") or msg.get("chat_id") or msg.get("chat")
        if cid is not None:
            try:
                return int(cid)
            except (ValueError, TypeError):
                pass
    return None


def is_target_chat(chat_id: Optional[int], target_id: Optional[int]) -> bool:
    """Проверяет, совпадает ли ID чата с целевым (с учетом возможного знака минус)."""
    if target_id is None or chat_id is None:
        return False
    return chat_id == target_id or abs(chat_id) == abs(target_id)

NAMES_FILE = BASE_DIR / "user_names.json"


def load_names() -> Dict[int, str]:
    if NAMES_FILE.exists():
        try:
            with open(NAMES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception:
            pass
    return {}


def save_names():
    try:
        with open(NAMES_FILE, "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in USER_NAMES.items()}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Ошибка сохранения имен: %s", e)


USER_NAMES = load_names()


def resolve_unknown_senders(sock: ssl.SSLSocket, sender_ids: Set[int]):
    unknown = [sid for sid in sender_ids if sid and sid not in USER_NAMES]
    if not unknown:
        return
    try:
        send_frame(sock, 10, 0, get_seq(), 32, {"contactIds": unknown})
        cmd, seq, op, data = recv_frame(sock, timeout=4.0)
        if op == 32 and isinstance(data, dict):
            for c in data.get("contacts", []):
                cid = c.get("id")
                names = c.get("names") or []
                if cid and names:
                    USER_NAMES[cid] = names[0].get("name", f"User_{cid}")
            save_names()
    except Exception as e:
        logger.debug("Ошибка получения имен контактов: %s", e)


def parse_and_forward_message(msg: Dict[str, Any], seen_ids: Set[str], initial_warmup: bool = False) -> None:
    msg_id = str(msg.get("id") or "")
    if not msg_id:
        return

    if msg_id in seen_ids:
        return

    if initial_warmup:
        # Во время первого запуска сохраняем существующие сообщения без спама в Telegram
        seen_ids.add(msg_id)
        return

    text = msg.get("text") or ""
    sender_id = msg.get("sender")
    attaches = msg.get("attaches") or []

    # Поддержка пересланных сообщений (Forward)
    fwd_msg = (msg.get("link") or {}).get("message")
    if isinstance(fwd_msg, dict):
        if not text and fwd_msg.get("text"):
            text = fwd_msg.get("text")
        if not attaches and fwd_msg.get("attaches"):
            attaches = fwd_msg.get("attaches")
        if not sender_id and fwd_msg.get("sender"):
            sender_id = fwd_msg.get("sender")

    if not text and not attaches:
        seen_ids.add(msg_id)
        return

    author_name = USER_NAMES.get(sender_id) or f"Участник #{sender_id}" if sender_id else "Сообщение"
    safe_author = html.escape(str(author_name))
    safe_text = html.escape(str(text)) if text else "<i>(без текста / вложение)</i>"

    links = []
    for att in attaches:
        att_type = att.get("_type") or att.get("type") or "файл"
        url = att.get("url") or (att.get("payload") or {}).get("url")
        name = att.get("name") or att_type
        if url:
            links.append(f"📎 <a href=\"{url}\">{html.escape(str(name))}</a>")

    header = f"💬 <b>[{SOURCE_CHAT_NAME}] {safe_author}</b>:\n{safe_text}"
    if links:
        header += "\n" + "\n".join(links)

    logger.info("📩 Новое сообщение от %s: %s", author_name, text[:60])
    if send_to_telegram(header):
        seen_ids.add(msg_id)
        save_seen_ids(seen_ids)


def run_userbot():
    global RUNNING

    if not MAX_USER_TOKEN or not MAX_DEVICE_ID:
        logger.error("❌ MAX_USER_TOKEN или MAX_DEVICE_ID не указаны в .env!")
        return

    seen_ids = load_seen_ids()
    try:
        target_chat_id = int(MAX_CHAT_ID)
    except (ValueError, TypeError):
        logger.error("❌ MAX_CHAT_ID '%s' некорректен! Укажите числовой ID группы в .env", MAX_CHAT_ID)
        return

    chat_titles: Dict[int, str] = {target_chat_id: SOURCE_CHAT_NAME}

    logger.info("🚀 Запуск MAX Userbot...")
    logger.info("🎯 Отслеживаемый чат: %s (%s)", SOURCE_CHAT_NAME, target_chat_id)

    # Если список seen_ids пуст, запоминаем текущие сообщения, чтобы не пересылать старую историю
    first_run = len(seen_ids) == 0

    while RUNNING:
        sock = None
        try:
            logger.info("Подключение к серверу MAX (%s:%d)...", HOST, PORT)
            raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            raw_sock.settimeout(15)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            sock = ctx.wrap_socket(raw_sock, server_hostname=HOST)
            sock.connect((HOST, PORT))

            # 1. INIT
            send_frame(sock, 10, 0, get_seq(), 6, {
                "userAgent": {
                    "deviceType": "WEB", "locale": "ru", "deviceLocale": "ru",
                    "osVersion": "Windows", "deviceName": "Chrome",
                    "headerUserAgent": "Mozilla/5.0", "appVersion": "26.6.17",
                    "screen": "1920x1080 1.0x", "timezone": "Europe/Moscow"
                },
                "deviceId": MAX_DEVICE_ID
            })
            cmd, seq, op, _ = recv_frame(sock)

            # 2. LOGIN
            send_frame(sock, 10, 0, get_seq(), 19, {
                "interactive": True,
                "token": MAX_USER_TOKEN,
                "chatsCount": 20,
                "chatsSync": 20
            })
            cmd, seq, op, login_data = recv_frame(sock)

            if cmd == 3:
                logger.error("❌ Ошибка авторизации токена! Возможно, сессия была завершена.")
                time.sleep(15)
                continue

            # Запоминаем свое имя и ID
            my_contact = login_data.get("profile", {}).get("contact", {})
            my_id = my_contact.get("id")
            if my_id and my_contact.get("names"):
                USER_NAMES[my_id] = my_contact["names"][0].get("name", "Gengro")

            # Сохраняем известные имена пользователей
            for u in login_data.get("users", []):
                uid = u.get("id")
                names = u.get("names") or []
                if uid and names:
                    USER_NAMES[uid] = names[0].get("name", f"User_{uid}")
            save_names()

            # Сохраняем названия чатов для красивого логгирования
            for c in login_data.get("chats", []):
                cid = c.get("id")
                title = c.get("title") or c.get("type")
                if cid and title:
                    chat_titles[cid] = title

            profile_name = USER_NAMES.get(my_id) or "Gengro"
            logger.info("✅ Успешный вход под аккаунтом: %s (ID: %s)", profile_name, my_id)

            # Прогрев: запоминаем существующие сообщения только целевого чата
            now_ms = int(time.time() * 1000)
            send_frame(sock, 10, 0, get_seq(), 49, {
                "chatId": target_chat_id,
                "from": now_ms,
                "backward": 10,
                "forward": 0,
                "getMessages": True
            })
            cmd, seq, op, hist_data = recv_frame(sock, timeout=10.0)
            if hist_data and isinstance(hist_data, dict):
                msgs = hist_data.get("messages", [])
                unknown = {m.get("sender") for m in msgs if m.get("sender") and m.get("sender") not in USER_NAMES}
                if unknown:
                    resolve_unknown_senders(sock, unknown)
                for m in msgs:
                    parse_and_forward_message(m, seen_ids, initial_warmup=first_run)
            first_run = False
            save_seen_ids(seen_ids)
            logger.info("👀 Мониторинг группы '%s' активен! Ожидаем новых сообщений...", SOURCE_CHAT_NAME)

            last_ping = time.time()

            # Рабочий цикл прослушивания и периодического опроса
            while RUNNING:
                # Отправка клиентского PING каждые 25 секунд
                if time.time() - last_ping > 25:
                    send_frame(sock, 10, 0, get_seq(), 1, {"interactive": True})
                    last_ping = time.time()

                # Периодический опрос истории только целевого чата
                now_ms = int(time.time() * 1000)
                send_frame(sock, 10, 0, get_seq(), 49, {
                    "chatId": target_chat_id,
                    "from": now_ms,
                    "backward": 5,
                    "forward": 0,
                    "getMessages": True
                })

                cmd, seq, op, data = recv_frame(sock, timeout=3.0)
                if cmd is not None and isinstance(data, dict):
                    # 1. Серверный PING (cmd == 0, op == 1) - подтверждаем
                    if cmd == 0 and op == 1:
                        send_frame(sock, 10, 1, seq, 1, {})
                        continue

                    # 2. Ответ на опрос истории сообщений (op == 49)
                    if op == 49:
                        hist_cid = extract_chat_id(data)
                        if hist_cid is not None and not is_target_chat(hist_cid, target_chat_id):
                            continue
                        msgs = data.get("messages", [])
                        unknown = {m.get("sender") for m in msgs if m.get("sender") and m.get("sender") not in USER_NAMES}
                        if unknown:
                            resolve_unknown_senders(sock, unknown)
                        for m in reversed(msgs):
                            parse_and_forward_message(m, seen_ids, initial_warmup=False)

                    # 3. Серверное push-уведомление о новом сообщении в реальном времени
                    elif "message" in data:
                        incoming_chat_id = extract_chat_id(data)
                        msg_obj = data["message"]
                        msg_id = msg_obj.get("id")

                        # Подтверждаем серверу прием пакета (ACK)
                        if cmd == 0 and op == 128 and incoming_chat_id and msg_id:
                            send_frame(sock, 10, 1, seq, 128, {
                                "chatId": incoming_chat_id,
                                "messageId": msg_id
                            })

                        # СТРОГИЙ ФИЛЬТР: проверяем, что сообщение пришло именно из целевого чата!
                        if not is_target_chat(incoming_chat_id, target_chat_id):
                            chat_title = chat_titles.get(incoming_chat_id, f"ID {incoming_chat_id}")
                            logger.info("⏭️ Пропуск сообщения из другого чата '%s' (не '%s')", chat_title, SOURCE_CHAT_NAME)
                            continue

                        sender = msg_obj.get("sender")
                        if sender and sender not in USER_NAMES:
                            resolve_unknown_senders(sock, {sender})
                        parse_and_forward_message(msg_obj, seen_ids, initial_warmup=False)

                time.sleep(1.0)

        except (ConnectionResetError, BrokenPipeError, socket.error) as e:
            logger.warning("Обрыв соединения (%s). Переподключение через 5 секунд...", e)
            time.sleep(5)
        except Exception as e:
            logger.error("Непредвиденная ошибка: %s", e, exc_info=True)
            time.sleep(5)
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    logger.info("Юзербот завершил работу.")


if __name__ == "__main__":
    run_userbot()

