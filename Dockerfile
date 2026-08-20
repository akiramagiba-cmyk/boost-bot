FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory
RUN mkdir -p /app/data

# Set data directory as volume
VOLUME ["/app/data"]

CMD ["python", "bot.py"]
