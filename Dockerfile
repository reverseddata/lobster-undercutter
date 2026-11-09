FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY undercutter ./undercutter
# Runs as root because Railway volumes are mounted root-owned.
CMD ["python", "-m", "undercutter"]
