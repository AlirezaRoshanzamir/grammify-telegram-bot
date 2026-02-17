FROM python:3.10-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        build-essential \
        libffi-dev \
        libfontconfig1 libgl1 libegl1 libglvnd0 libgl1-mesa-dri \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY src ./src

ENV PYTHONPATH=/app/src

RUN useradd --create-home appuser
USER appuser

CMD ["python", "-m", "grammify"]
