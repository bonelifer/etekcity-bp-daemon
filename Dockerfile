FROM python:3.11-slim

# git is needed at install time only, to fetch the etekcity-bp-ble
# dependency's git+https URL (it isn't published to PyPI).
RUN apt-get update && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENTRYPOINT ["etekcity-bp-daemon"]
CMD ["--config", "/etc/etekcity-bp-daemon/config.ini"]
