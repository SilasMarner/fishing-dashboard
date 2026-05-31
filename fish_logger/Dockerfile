FROM python:3.12-slim
WORKDIR /app
# tesseract-ocr: free, on-device OCR for the handwritten-log import (no API key needed)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 9879
CMD ["python", "app.py"]
