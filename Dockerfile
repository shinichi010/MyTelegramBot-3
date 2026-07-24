FROM python:3.11-slim

# تثبيت الأدوات الأساسية و Node.js
RUN apt-get update && apt-get install -y curl git ffmpeg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# تثبيت متطلبات بايثون
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# تثبيت وبناء مزود PO Token (يحل مشكلة حظر يوتيوب)
RUN git clone https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /pot-provider && \
    cd /pot-provider/server && npm install && npm run build

# نسخ باقي ملفات المشروع
COPY . .

RUN chmod +x start.sh

CMD ["./start.sh"]
