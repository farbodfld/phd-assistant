FROM python:3.12-slim-bookworm
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
COPY seed ./seed
COPY sops ./sops
COPY knowledge/profile.yaml knowledge/institutions.yaml ./knowledge/
ENV PYTHONPATH=/srv PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir .
RUN apt-get update && apt-get install -y --no-install-recommends xvfb xauth x11vnc \
    && if [ "$(dpkg --print-architecture)" = amd64 ]; then patchright install --with-deps chrome; \
       else apt-get install -y --no-install-recommends chromium; fi \
    && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["xvfb-run", "-a"]
CMD ["phd", "serve"]
