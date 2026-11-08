FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
COPY seed ./seed
RUN pip install --no-cache-dir .
CMD ["phd", "serve"]
