FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd -u 1000 -m exporter
COPY fishing_tide_exporter.py .
USER exporter
EXPOSE 9877 9878
CMD ["python", "fishing_tide_exporter.py"]
