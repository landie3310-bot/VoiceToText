# Используем официальный легковесный образ Python
FROM python:3.11-slim

# Устанавливаем ffmpeg из репозитория Debian (единственная системная зависимость)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Рабочая директория внутри контейнера
WORKDIR /app

# Сначала копируем только requirements для кэширования слоя зависимостей
COPY requirements.txt .

# Устанавливаем Python-зависимости без кэша pip (экономия места)
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY config.py transcriber.py main.py ./

# Render передаёт PORT через переменную окружения — aiohttp слушает его
# BOT_TOKEN и GROQ_API_KEY задаются через Environment Variables в панели Render
ENV PORT=8080

# Открываем порт (документация, фактически Render сам управляет сетью)
EXPOSE 8080

# Точка входа
CMD ["python", "main.py"]
