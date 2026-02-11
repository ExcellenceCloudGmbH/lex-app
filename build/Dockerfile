# Use Debian 12 (bookworm) based Python 3.12 slim to keep apt packages consistent.
# (If you really need Debian 11, you can switch this to python:3.12-slim-bullseye.)
ARG PYTHON_BASE_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE_IMAGE}

ARG IMAGE_VERSION
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN set -eux; \
    \
    # Enable contrib/non-free (needed for ttf-mscorefonts-installer on Debian bookworm)
    if [[ -f /etc/apt/sources.list.d/debian.sources ]]; then \
        sed -i 's/^Components: main$/Components: main contrib non-free non-free-firmware/' /etc/apt/sources.list.d/debian.sources; \
    else \
        sed -i 's/ main$/ main contrib non-free/' /etc/apt/sources.list; \
    fi; \
    \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        wget \
        gnupg \
        sudo \
        debconf-utils; \
    \
    # Add Google Chrome apt repo (signed-by keyring)
    install -m 0755 -d /usr/share/keyrings; \
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
      | gpg --dearmor -o /usr/share/keyrings/google-linux-signing-keyring.gpg; \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux-signing-keyring.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
      > /etc/apt/sources.list.d/google-chrome.list; \
    \
    apt-get update; \
    \
    # Pre-accept the Microsoft Core Fonts EULA to avoid interactive prompt
    echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula select true" | debconf-set-selections; \
    echo "ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula seen true"   | debconf-set-selections; \
    \
    # Install everything else (build deps + wkhtmltopdf + chrome + fonts + cairo stack)
    apt-get install -y --no-install-recommends \
        git \
        gcc \
        g++ \
        make \
        pkg-config \
        cmake \
        meson \
        ninja-build \
        libpq-dev \
        rsync \
        locales-all \
        fontconfig \
        google-chrome-stable \
        ttf-mscorefonts-installer \
        wkhtmltopdf \
        xvfb \
        xfonts-75dpi \
        xfonts-base \
        libcairo2 \
        libcairo2-dev \
        libpango-1.0-0 \
        libpango1.0-dev \
        librsvg2-2 \
        librsvg2-dev; \
    \
    # gdk-pixbuf package naming differs across Debian lines; prefer the main packages if present
    if apt-cache show libgdk-pixbuf-2.0-0 >/dev/null 2>&1; then \
        apt-get install -y --no-install-recommends libgdk-pixbuf-2.0-0 libgdk-pixbuf-2.0-dev; \
    else \
        apt-get install -y --no-install-recommends libgdk-pixbuf2.0-0 libgdk-pixbuf2.0-dev; \
    fi; \
    \
    fc-cache -f -v; \
    \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "lex-app==${IMAGE_VERSION}"
