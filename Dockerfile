FROM python:3.11-slim

# Встановлюємо FFmpeg та утиліти
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl build-essential && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p temp_processing

CMD ["python", "bot.py"]
