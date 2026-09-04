# 🌉 Мост сообщений: MAX Messenger ➡️ Telegram

Готовое решение для автоматической пересылки всех сообщений из группового чата в **MAX Messenger** в группу в **Telegram**.

---

## 📋 Как это работает

1. **Бот в MAX** находится в вашей группе MAX. Платформа MAX передает боту сообщения через официальный API (`platform-api2.max.ru`).
2. **Скрипт-мост (`bot.py`)** принимает сообщения через Long Polling, форматирует их (указывает имя автора, текст, фото/вложения).
3. **Бот в Telegram** отправляет сообщение в вашу группу Telegram от своего имени.

```
[Группа в MAX] 
      │ (сообщение от пользователя)
      ▼
 [Бот в MAX] 
      │ (Long Polling updates)
      ▼
 [Скрипт-мост bot.py] 
      │ (Telegram Bot API sendMessage/sendPhoto)
      ▼
[Бот в Telegram]
      │
      ▼
[Группа в Telegram]
```

---

## 🚀 Пошаговая инструкция по настройке

### Шаг 1. Создание и настройка бота в MAX Messenger

1. Перейдите в кабинет разработчика MAX: **[dev.max.ru](https://dev.max.ru)** или **[business.max.ru](https://business.max.ru)**.
2. Авторизуйтесь (для создания ботов в MAX требуется профиль ИП, юрлица или самозанятого через Госуслуги).
3. Создайте нового бота и скопируйте его **API токен** (`MAX_BOT_TOKEN`).
4. **КРИТИЧЕСКИ ВАЖНО:** Добавьте бота в нужную группу в MAX и **сделайте его администратором** с правом:
   - `read_all_messages` (**«Чтение всех сообщений»**).
   > ⚠️ *Если не выдать боту это право, по правилам платформы MAX он будет видеть только те сообщения, где его тегнули через `@`, а не всю переписку группы.*

---

### Шаг 2. Создание и настройка бота в Telegram

1. В Telegram найдите официального бота **[@BotFather](https://t.me/BotFather)**.
2. Отправьте команду `/newbot`, задайте имя и юзернейм (например, `MyMaxRelayBot`).
3. Скопируйте полученный токен (`TG_BOT_TOKEN`), он выглядит примерно так: `1234567890:ABCdefGhIJKlmNoPQRsTUVwxyZ`.
4. Добавьте созданного бота в вашу группу в Telegram и дайте ему право писать сообщения.
5. **Как узнать ID группы в Telegram (`TG_CHAT_ID`)**:
   - Добавьте в эту же группу бота **`@getmyid_bot`** или **`@userinfobot`** — он пришлет `Current chat ID` (число с минусом, например: `-1002345678901`).
   - Либо напишите любое сообщение в группу и откройте в браузере:  
     `https://api.telegram.org/bot<ВАШ_ТЕЛЕГРАМ_ТОКЕН>/getUpdates` — в ответе найдите поле `"chat": {"id": -100...}`.

---

### Шаг 3. Конфигурация проекта

Перейдите в папку проекта:
```bash
cd /home/root2506/.gemini/antigravity-ide/scratch/max_tg_bridge
```

Скопируйте файл примера конфигурации в рабочий `.env`:
```bash
cp .env.example .env
```

Откройте `.env` в текстовом редакторе и заполните данные:
```env
# Токен бота MAX
MAX_BOT_TOKEN=ваш_токен_макс

# Токен бота Telegram
TG_BOT_TOKEN=ваш_токен_телеграм

# ID группы в Telegram (обязательно с минусом!)
TG_CHAT_ID=-1001234567890

# ID группы MAX (можно пока оставить пустым)
MAX_CHAT_ID=
```

> 💡 **Как узнать ID группы в MAX (`MAX_CHAT_ID`):**  
> Если вы не знаете ID группы в MAX, оставьте поле `MAX_CHAT_ID` пустым. Запустите скрипт и отправьте любое тестовое сообщение в группу MAX. Скрипт выведет в консоль:  
> `💡 Получено сообщение из чата MAX [Chat ID: 987654321]...`  
> После этого просто скопируйте этот ID в `.env` в параметр `MAX_CHAT_ID`, чтобы бот пересылал сообщения только из этой конкретной группы!

---

### Шаг 4. Проверка и запуск

1. **Проверьте корректность подключения к обеим платформам:**
   ```bash
   python3 bot.py --check
   ```
   Если все настроено верно, вы увидите:
   ```
   [INFO] ✅ Telegram Bot подключен: @MyMaxRelayBot
   [INFO] ✅ MAX Bot подключен: ID=...
   ```

2. **Запустите мост:**
   ```bash
   python3 bot.py
   ```

Теперь напишите сообщение в группе MAX — оно моментально появится в группе Telegram с именем автора!

---

## ⚙️ Полезные команды

* **Сбросить маркер (историю):**  
  Если вы хотите сбросить позицию и читать только новые сообщения:
  ```bash
  python3 bot.py --reset-marker
  ```
* **Запуск в фоновом режиме через nohup:**
  ```bash
  nohup python3 bot.py > bridge.log 2>&1 &
  ```
* **Автозапуск через systemd (для Linux-сервера):**  
  Создайте файл `/etc/systemd/system/max-bridge.service`:
  ```ini
  [Unit]
  Description=MAX to Telegram Bridge Service
  After=network.target

  [Service]
  Type=simple
  User=root
  WorkingDirectory=/home/root2506/.gemini/antigravity-ide/scratch/max_tg_bridge
  ExecStart=/usr/bin/python3 /home/root2506/.gemini/antigravity-ide/scratch/max_tg_bridge/bot.py
  Restart=always
  RestartSec=5

  [Install]
  WantedBy=multi-user.target
  ```
  Затем включите и запустите сервис:
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl enable --now max-bridge
  ```
