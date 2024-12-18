FROM python:3.10-slim

# Устанавливаем системные зависимости для сборки
RUN apt-get update && apt-get install -y \
    libffi-dev \
    gcc \
    build-essential \
    python3-dev \
    --no-install-recommends && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файлы проекта
COPY . /app

# Обновляем pip
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Устанавливаем зависимости из requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Указываем команду для запуска приложения
CMD ["python", "bot.py"]
