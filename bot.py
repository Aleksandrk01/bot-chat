import logging
import os
import time
import asyncio
import re
import json
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler, ContextTypes,
    ChatMemberHandler, filters
)

# Загрузка переменных окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO  # Уровень INFO для основных сообщений
)
logger = logging.getLogger(__name__)

# Установка более высокого уровня логирования для внешних библиотек
logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('telegram.ext').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)

# Этапы регистрации (изменён порядок)
NAME, CITY, CAR_TYPE, YEAR, PURPOSE = range(5)

# Время ожидания регистрации и блокировок (в секундах)
REGISTRATION_TIMEOUT = 120  # Время для регистрации
BAN_DURATION = 30          # Время блокировки (в секундах)

# Списки для отслеживания пользователей
registered_users = {}  # Словарь для хранения данных зарегистрированных пользователей
pending_users = {}

# Словарь для хранения message_ids отправленных ботом пользователям
user_messages = {}

# Правила чата
CHAT_RULES = """
**Правила чата:**
1. Торгівля (відкрита/закрита) будь-якого виду
2. Реклама (відкрита / закрита) своїх послуг будь-якого виду
3. Пропаганда BMW
4. Розмови про те, що VAG ламається )))
5. Грошові збори
6. Інше ...
"""

# Получение постоянной ссылки приглашения из .env
INVITE_LINK = os.getenv('INVITE_LINK')  # Добавьте эту переменную в ваш .env файл

if not INVITE_LINK:
    logger.critical("INVITE_LINK не установлена. Добавьте INVITE_LINK в ваш .env файл.")
    exit(1)

# Получение списка администраторов из .env
ADMIN_IDS_ENV = os.getenv('ADMIN_IDS', '')
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_ENV.split(',') if admin_id.strip().isdigit()]

if not ADMIN_IDS:
    logger.warning("ADMIN_IDS не установлены или некорректны. Добавьте ADMIN_IDS в ваш .env файл, если хотите использовать глобальных администраторов.")
    # Можно продолжить работу без глобальных администраторов

# Список российских городов (пример, дополните по необходимости)
RUSSIAN_CITIES = {
    "Москва", "Санкт-Петербург", "Новосибирск", "Екатеринбург", "Казань",
    "Нижний Новгород", "Челябинск", "Самара", "Омск", "Ростов-на-Дону",
    "Уфа", "Красноярск", "Воронеж", "Пермь", "Волгоград",
    # Добавьте остальные города
}

# Функция для проверки имени
def is_valid_name(name: str) -> bool:
    """
    Проверяет, что имя состоит из одного и более слов, состоящих только из букв.
    """
    parts = name.strip().split()
    if len(parts) < 1:
        return False
    for part in parts:
        if not re.fullmatch(r"[A-Za-zА-Яа-яЁё]+", part):
            return False
    return True

# Функция для проверки города
def is_valid_city(city: str) -> bool:
    """
    Проверяет, что город не является российским.
    """
    return city.strip().title() not in RUSSIAN_CITIES

# Функция для проверки типа машины
def is_valid_car_type(car_type: str) -> bool:
    """
    Проверяет, что тип машины не содержит 'bmw', 'бмв' или 'беха' в любом регистре.
    """
    return not re.search(r'\bbmw\b|\bбмв\b|\bбеха\b', car_type, re.IGNORECASE)

# Функция для бановки пользователя, если он не зарегистрировался
async def ban_user_if_not_registered(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_id = job.data['user_id']
    group_id = job.data['group_id']

    logger.info(f"Выполнение задачи бановки пользователя ID={user_id} в группе ID={group_id}")

    if user_id not in registered_users:
        try:
            # Проверка, не является ли user_id ботом
            bot = await context.bot.get_me()
            bot_id = bot.id
            if user_id == bot_id:
                logger.warning(f"Попытка заблокировать бота самого себя (ID={user_id}). Операция отменена.")
                return

            # Баним пользователя на BAN_DURATION секунд
            until_date = int(time.time()) + BAN_DURATION
            await context.bot.ban_chat_member(
                chat_id=group_id,
                user_id=user_id,
                until_date=until_date
            )
            logger.info(f"Пользователь ID={user_id} временно забанен в группе ID={group_id} за отсутствие регистрации.")

            # Отправляем уведомление в группу (опционально)
            await context.bot.send_message(
                chat_id=group_id,
                text=f"Пользователь был временно забанен за отсутствие регистрации. Он сможет снова присоединиться через {BAN_DURATION} секунд."
            )

        except Exception as e:
            logger.error(f"Ошибка при бановке участника ID={user_id} из группы ID={group_id}: {e}")
    else:
        logger.info(f"Пользователь ID={user_id} уже зарегистрирован. Бан не требуется.")

# Функция для отправки сообщения и хранения message_id
async def send_message_and_store_id(user_id, context, text, reply_markup=None):
    try:
        message = await context.bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup, parse_mode='HTML')
        if user_id not in user_messages:
            user_messages[user_id] = []
        user_messages[user_id].append(message.message_id)
        logger.info(f"Отправлено сообщение ID={message.message_id} пользователю ID={user_id}.")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения пользователю ID={user_id}: {e}")

# Функция для удаления сообщений пользователя
async def delete_user_messages(user_id, context):
    user_msgs = user_messages.get(user_id, [])
    for message_id in user_msgs:
        try:
            await context.bot.delete_message(chat_id=user_id, message_id=message_id)
            logger.info(f"Удалено сообщение ID={message_id} пользователю ID={user_id}.")
        except Exception as e:
            logger.error(f"Ошибка удаления сообщения ID={message_id} пользователю ID={user_id}: {e}")
    # Очистка списка сообщений после удаления
    if user_id in user_messages:
        del user_messages[user_id]
        logger.debug(f"Очистка списка сообщений пользователя ID={user_id}.")

# Обработчик покидания группы (MessageHandler)
async def handle_left_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    left_member = message.left_chat_member

    if not left_member:
        return  # Не обработка, если нет информации о покинувшем

    user_id = left_member.id
    group_id = message.chat.id

    logger.info(f"Пользователь ID={user_id} покинул группу ID={group_id}.")

    # Проверяем, был ли пользователь ранее зарегистрирован
    if user_id in registered_users:
        registered_users.pop(user_id, None)
        logger.info(f"Пользователь ID={user_id} удалён из registered_users.")
        save_registered_users()  # Сохранение изменений
    else:
        logger.debug(f"Пользователь ID={user_id} покинул группу, но не был зарегистрирован.")

    # Удаление личных сообщений
    await delete_user_messages(user_id, context)

    # Удаляем из pending_users, если находится
    if user_id in pending_users:
        pending_users.pop(user_id, None)
        logger.debug(f"Пользователь ID={user_id} удалён из pending_users.")

# Отслеживание новых и покидающих участников группы (ChatMemberHandler)
async def monitor_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_member = update.chat_member
    old_status = chat_member.old_chat_member.status
    new_status = chat_member.new_chat_member.status
    user = chat_member.new_chat_member.user
    user_id = user.id
    group_id = chat_member.chat.id

    logger.debug(f"Обновление статуса участника: ID={user_id}, старый статус={old_status}, новый статус={new_status}")

    # Обработка присоединения пользователя к группе
    if new_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED]:
        # Если пользователь уже зарегистрирован или в процессе регистрации, ничего не делаем
        if user_id in registered_users or user_id in pending_users:
            logger.debug(f"Пользователь ID={user_id} уже зарегистрирован или находится в процессе регистрации.")
            return

        # Ограничиваем возможности пользователя
        try:
            restrict_permissions = ChatPermissions(
                can_send_messages=False,
                can_send_polls=False,
                can_add_web_page_previews=False
            )
            await context.bot.restrict_chat_member(
                chat_id=group_id,
                user_id=user_id,
                permissions=restrict_permissions
            )
            logger.info(f"Пользователь ID={user_id} ограничен в группе ID={group_id}.")
        except Exception as e:
            logger.error(f"Ошибка ограничения участника ID={user_id} в группе ID={group_id}: {e}")

        # Отправляем сообщение о необходимости регистрации с отметкой пользователя
        keyboard = [
            [InlineKeyboardButton("📋 Зарегистрироваться", url=f"https://t.me/{context.bot.username}?start=registration")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await context.bot.send_message(
                chat_id=group_id,
                text=f"Добро пожаловать, <a href='tg://user?id={user_id}'>{user.first_name}</a>! Чтобы остаться в группе, пожалуйста, зарегистрируйтесь через нашего бота.",
                reply_markup=reply_markup,
                parse_mode='HTML'  # Включаем HTML-разметку для упоминания пользователя
            )
            logger.info(f"Отправлено сообщение о регистрации пользователю ID={user_id} в группу ID={group_id}.")
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения о регистрации: {e}")

        # Добавляем пользователя в pending_users для регистрации
        pending_users[user_id] = group_id
        logger.debug(f"Пользователь ID={user_id} добавлен в pending_users.")

        # Планируем бан пользователя через REGISTRATION_TIMEOUT секунд, если он не зарегистрируется
        context.job_queue.run_once(
            ban_user_if_not_registered,
            REGISTRATION_TIMEOUT,
            data={'user_id': user_id, 'group_id': group_id},
            name=f"ban_user_if_not_registered_{user_id}"
        )
        logger.debug(f"Запланирована задача бановки пользователя ID={user_id} через {REGISTRATION_TIMEOUT} секунд.")

# Регистрация через бота
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    logger.debug(f"Пользователь ID={user_id} начал регистрацию.")

    # Проверяем, находится ли пользователь в pending_users
    if user_id not in pending_users:
        await update.message.reply_text('Вы должны присоединиться к группе, чтобы начать регистрацию.')
        logger.warning(f"Пользователь ID={user_id} попытался зарегистрироваться без присоединения к группе.")
        return ConversationHandler.END

    # Отправляем приветственное сообщение и сохраняем message_id
    await send_message_and_store_id(user_id, context, 'Добро пожаловать! Давайте начнём регистрацию.\n\nВопрос 1: Как вас зовут? (псевдоним)')
    logger.debug(f"Пользователь ID={user_id} получил вопрос 1.")
    return NAME

# Обработчик Вопроса 1: Как вас зовут? (псевдоним)
async def name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text
    user_id = update.message.from_user.id
    logger.debug(f"Пользователь ID={user_id} ответил на Вопрос 1: {name}")

    if not is_valid_name(name):
        await update.message.reply_text(
            "Пожалуйста, введите ваше полное имя (имя и фамилию). Имя должно состоять только из букв."
        )
        logger.warning(f"Пользователь ID={user_id} ввёл некорректное имя: {name}")
        return NAME  # Повторный запрос
    context.user_data['name'] = name.strip()
    await send_message_and_store_id(user_id, context, 'Вопрос 2: Из какого вы города?')
    return CITY

# Обработчик Вопроса 2: Из какого вы города?
async def city_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text
    user_id = update.message.from_user.id
    logger.debug(f"Пользователь ID={user_id} ответил на Вопрос 2: {city}")

    if not is_valid_city(city):
        await update.message.reply_text(
            "Извините, эта группа предназначена для пользователей из Украины. Пожалуйста, убедитесь, что вы находитесь за пределами России."
        )
        logger.warning(f"Пользователь ID={user_id} ввёл город из России: {city}")
        return CITY  # Повторный запрос

    context.user_data['city'] = city.strip()
    await send_message_and_store_id(user_id, context, 'Вопрос 3: Какая у вас модель автомобиля?')
    return CAR_TYPE

# Обработчик Вопроса 3: Какая у вас модель автомобиля?
async def car_type_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    car_type = update.message.text
    user_id = update.message.from_user.id
    logger.debug(f"Пользователь ID={user_id} ответил на Вопрос 3: {car_type}")

    if not is_valid_car_type(car_type):
        await update.message.reply_text(
            "Извините, мы не принимаем пользователей с машиной марки BMW. Пожалуйста, укажите другой тип автомобиля, чтобы войти в чат."
        )
        logger.warning(f"Пользователь ID={user_id} ввёл запрещённый тип машины: {car_type}")
        return CAR_TYPE  # Повторный запрос

    context.user_data['car_type'] = car_type.strip()
    await send_message_and_store_id(user_id, context, 'Вопрос 4: Какой год выпуска вашей машины?')
    return YEAR

# Обработчик Вопроса 4: Какой год выпуска вашей машины?
async def year_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    year_input = update.message.text
    user_id = update.message.from_user.id
    logger.debug(f"Пользователь ID={user_id} ответил на Вопрос 4: {year_input}")

    # Убрана проверка на корректность года
    context.user_data['year'] = year_input.strip()
    await send_message_and_store_id(user_id, context, 'Вопрос 5: Какова цель вашего визита?')
    return PURPOSE

# Обработчик Вопроса 5: Какова цель вашего визита?
async def purpose_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    purpose = update.message.text
    user_id = update.message.from_user.id
    logger.debug(f"Пользователь ID={user_id} ответил на Вопрос 5: {purpose}")

    context.user_data['purpose'] = purpose.strip()
    # Сохраняем данные пользователя
    registered_users[user_id] = {
        'name': context.user_data.get('name'),
        'city': context.user_data.get('city'),
        'car_type': context.user_data.get('car_type'),
        'year': context.user_data.get('year'),
        'purpose': context.user_data.get('purpose')
    }
    logger.info(f"Данные пользователя ID={user_id} сохранены в registered_users.")
    
    save_registered_users()  # Сохранение данных после регистрации

    # Удаляем из ожидающих регистрации
    group_id = pending_users.pop(user_id, None)
    logger.debug(f"Пользователь ID={user_id} удалён из pending_users.")

    # Отправляем сообщение с правилами и ссылкой-приглашением
    keyboard = [
        [InlineKeyboardButton("📢 Перейти в чат", url=INVITE_LINK)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"Спасибо за регистрацию! Теперь вы можете отправлять сообщения в группе.\n\n**Правила чата:**\n{CHAT_RULES}",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        logger.info(f"Отправлено сообщение с правилами и приглашением пользователю ID={user_id}.")
    except Exception as e:
        logger.error(f"Ошибка отправки личного сообщения пользователю ID={user_id}: {e}")
        # Если не удалось отправить в личные сообщения, отправляем в группу
        if group_id:
            try:
                await context.bot.send_message(
                    chat_id=group_id,
                    text=f"Пользователь <a href='tg://user?id={user_id}'>{context.user_data.get('name')}</a> успешно зарегистрирован и теперь может отправлять сообщения.",
                    parse_mode='HTML'
                )
                # Также можно добавить кнопку с приглашением
                await context.bot.send_message(
                    chat_id=group_id,
                    text="Если вы хотите проверить свои настройки или повторно присоединиться, используйте кнопку ниже.",
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
                logger.info(f"Отправлено сообщение с приглашением в группу для пользователя ID={user_id}.")
            except Exception as ex:
                logger.error(f"Ошибка отправки сообщения в группу для пользователя ID={user_id}: {ex}")

    # Снимаем ограничения с пользователя
    if group_id:
        try:
            # Разрешаем пользователю отправлять сообщения
            unrestrict_permissions = ChatPermissions(
                can_send_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False
            )
            await context.bot.restrict_chat_member(
                chat_id=group_id,
                user_id=user_id,
                permissions=unrestrict_permissions,
                until_date=None  # Снятие ограничений
            )
            logger.info(f"Ограничения сняты с пользователя ID={user_id} в группе ID={group_id}.")
        except Exception as e:
            logger.error(f"Ошибка снятия ограничений с участника ID={user_id} в группе ID={group_id}: {e}")

    # Отправляем пользователю ссылку приглашения в группу
    if group_id:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"Спасибо за регистрацию! Вы можете вернуться в группу по ссылке: {INVITE_LINK}",
                parse_mode='HTML'
            )
            logger.info(f"Отправлено приглашение пользователю ID={user_id} в группу.")
        except Exception as e:
            logger.error(f"Ошибка отправки приглашения пользователю ID={user_id}: {e}")
            # Попробуем отправить в группу
            try:
                await context.bot.send_message(
                    chat_id=group_id,
                    text=f"Пользователь <a href='tg://user?id={user_id}'>{context.user_data.get('name')}</a>, пожалуйста, используйте ссылку ниже для повторного присоединения к чату.",
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
                logger.info(f"Отправлено приглашение в группу для пользователя ID={user_id}.")
            except Exception as ex:
                logger.error(f"Ошибка отправки приглашения в группу для пользователя ID={user_id}: {ex}")
    else:
        logger.error(f"Неизвестный group_id для пользователя ID={user_id}.")

    # **Удаление личных сообщений после регистрации убрано, чтобы сохранить переписку**
    # await delete_user_messages(user_id, context)  # Удалено

    # Очищаем данные пользователя из context.user_data
    context.user_data.clear()

    return ConversationHandler.END

# Обработчик Отмены Регистрации
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await update.message.reply_text('Регистрация отменена.')
    logger.info(f"Пользователь ID={user_id} отменил регистрацию.")

    # Удаляем из ожидающих и зарегистрированных, если необходимо
    if user_id in pending_users:
        pending_users.pop(user_id, None)
        logger.debug(f"Пользователь ID={user_id} удалён из pending_users.")
    if user_id in registered_users:
        registered_users.pop(user_id, None)
        logger.debug(f"Пользователь ID={user_id} удалён из registered_users.")
        save_registered_users()  # Сохранение изменений

    # Отменяем запланированную задачу бановки
    job_name = f"ban_user_if_not_registered_{user_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    if current_jobs:
        for job in current_jobs:
            job.schedule_removal()
            logger.debug(f"Запланированная задача бановки пользователя ID={user_id} отменена.")
    else:
        logger.warning(f"Запланированные задачи бановки для пользователя ID={user_id} не найдены.")

    # Удаляем личные сообщения
    await delete_user_messages(user_id, context)

    # Очищаем данные пользователя из context.user_data
    context.user_data.clear()

    return ConversationHandler.END

# Обработчик команды /list_users для администраторов
# Обработчик команды /list_users для администраторов группы
async def list_users_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Проверяем, была ли команда отправлена в личных сообщениях
    if chat_id != user_id:
        await update.message.reply_text("Эту команду можно использовать только в личных сообщениях боту.")
        logger.warning(f"Пользователь ID={user_id} попытался использовать /list_users в чате ID={chat_id}.")
        return

    try:
        # Проверяем, является ли пользователь администратором указанной группы
        admins = await context.bot.get_chat_administrators(int(os.getenv('GROUP_ID')))
        admin_ids = {admin.user.id for admin in admins}

        if user_id not in admin_ids:
            await update.message.reply_text("У вас нет прав для выполнения этой команды. Только администраторы группы могут использовать её.")
            logger.warning(f"Пользователь ID={user_id} попытался использовать /list_users без прав.")
            return

        if not registered_users:
            await update.message.reply_text("Нет зарегистрированных пользователей.")
            logger.info("Запрос списка пользователей, но список пуст.")
            return

        # Форматируем список пользователей
        message_lines = ["**Список зарегистрированных пользователей:**\n"]
        for uid, data in registered_users.items():
            try:
                user = await context.bot.get_chat(uid)
                name = data.get('name', 'Не указано')
                city = data.get('city', 'Не указано')
                car_type = data.get('car_type', 'Не указано')
                year = data.get('year', 'Не указано')
                purpose = data.get('purpose', 'Не указано')

                message_lines.append(
                    f"**Пользователь:** {user.full_name} (ID: {uid})\n"
                    f"• Имя (псевдоним): {name}\n"
                    f"• Город: {city}\n"
                    f"• Модель автомобиля: {car_type}\n"
                    f"• Год выпуска: {year}\n"
                    f"• Цель визита: {purpose}\n"
                    "-----"
                )
            except Exception as e:
                logger.error(f"Ошибка получения информации о пользователе ID={uid}: {e}")
                message_lines.append(
                    f"**Пользователь ID={uid}:** Не удалось получить информацию.\n"
                    "-----"
                )

        message_text = "\n".join(message_lines)

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=message_text,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            logger.info(f"Отправлен список пользователей администратору ID={user_id}.")
        except Exception as e:
            logger.error(f"Ошибка отправки списка пользователей администратору ID={user_id}: {e}")
            await update.message.reply_text("Произошла ошибка при отправке списка пользователей.")
    except Exception as e:
        logger.error(f"Ошибка проверки администратора группы: {e}")
        await update.message.reply_text("Произошла ошибка при проверке администратора группы.")
# Обработка ошибок
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# Функция для сохранения данных о зарегистрированных пользователях в JSON
def save_registered_users():
    try:
        with open('registered_users.json', 'w', encoding='utf-8') as f:
            json.dump({str(k): v for k, v in registered_users.items()}, f, ensure_ascii=False, indent=4)
        logger.info("Данные зарегистрированных пользователей сохранены.")
    except Exception as e:
        logger.error(f"Ошибка сохранения зарегистрированных пользователей: {e}")

# Функция для загрузки данных о зарегистрированных пользователях из JSON
def load_registered_users():
    global registered_users
    if os.path.exists('registered_users.json'):
        try:
            with open('registered_users.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Преобразуем ключи обратно в int
                registered_users = {int(k): v for k, v in data.items()}
            logger.info("Данные зарегистрированных пользователей загружены.")
        except json.JSONDecodeError:
            logger.error("Ошибка декодирования JSON-файла с зарегистрированными пользователями.")
            registered_users = {}
        except Exception as e:
            logger.error(f"Ошибка загрузки зарегистрированных пользователей: {e}")
            registered_users = {}
    else:
        registered_users = {}
        logger.info("JSON-файл с зарегистрированными пользователями не найден. Создаётся новый.")

def main():
    load_registered_users()  # Загрузка данных о зарегистрированных пользователях

    BOT_TOKEN = os.getenv('BOT_TOKEN')  # Использование переменной окружения для токена

    if not BOT_TOKEN:
        logger.critical("Токен бота не установлен. Установите переменную окружения BOT_TOKEN.")
        return

    # Создаём приложение
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Обработчик новых и покидающих участников (ChatMemberHandler)
    application.add_handler(ChatMemberHandler(monitor_new_members, ChatMemberHandler.CHAT_MEMBER))

    # Обработчик покидающих участников (MessageHandler)
    # Этот обработчик обрабатывает сообщения, содержащие 'left_chat_member'
    left_member_filter = filters.StatusUpdate.LEFT_CHAT_MEMBER
    left_member_handler = MessageHandler(left_member_filter, handle_left_chat_member)
    application.add_handler(left_member_handler)

    # Обработчики команд регистрации (ConversationHandler)
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name_handler)],
            CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, city_handler)],
            CAR_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_type_handler)],
            YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, year_handler)],
            PURPOSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, purpose_handler)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(conv_handler)

    # Обработчик команды /list_users для администраторов
    list_users_command = CommandHandler('list_users', list_users_handler)
    application.add_handler(list_users_command)

    # Обработчик ошибок
    application.add_error_handler(error_handler)

    # Запуск бота
    logger.info("Запуск бота...")
    application.run_polling()

if __name__ == '__main__':
    main()
