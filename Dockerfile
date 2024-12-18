FROM python:3.10-slim

# Установим системные зависимости
RUN apt-get update && apt-get install -y \
    libffi-dev \
    libssl-dev \
    build-essential \
    gcc \
    --no-install-recommends && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Установим рабочую директорию
WORKDIR /app

# Копируем файлы проекта
COPY . /app

# Убедимся, что pip3 обновлен
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel

# Установим зависимости проекта
RUN pip3 install --no-cache-dir -r requirements.txt

# Указываем команду для запуска приложения
CMD ["python3", "bot.py"]
