FROM python:3.11-slim
WORKDIR /app
# Keep PDF rendering independent of apt/Docker DNS on the deployment host.
COPY fonts/DejaVuSans.ttf /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
COPY fonts/DejaVuSans-Bold.ttf /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Копируем backend и frontend в единый контейнер приложения
COPY ./backend /app/backend
COPY ./frontend /app/frontend
# Запускаем сервер
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
