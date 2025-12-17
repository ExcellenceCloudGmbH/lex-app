FROM python:3.9.17-slim-bullseye
ARG IMAGE_VERSION
ENV DEBIAN_FRONTEND=noninteractive

# install all necessary packages
WORKDIR /app
RUN apt-get update && apt-get install -y \
    curl \
    gnupg2 \
    wget \
    apt-transport-https \
    ca-certificates \
    sudo && \
    echo "deb [arch=amd64] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list && \
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-linux-signing-keyring.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-linux-signing-keyring.gpg] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list && \
    echo "deb http://deb.debian.org/debian bullseye contrib" >> /etc/apt/sources.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y \
        git \
        gcc \
        g++ \
        make \
        pkg-config \
        cmake \
        meson \
        ninja-build \
        libpq-dev \
        wkhtmltopdf \
        rsync \
        locales-all \
        fontconfig \
        google-chrome-stable \
        ttf-mscorefonts-installer \
        # Cairo + friends (so pycairo / cairo-dependent packages build & run)
        libcairo2 \
        libcairo2-dev \
        libpango-1.0-0 \
        libpango1.0-dev \
        libgdk-pixbuf2.0-0 \
        libgdk-pixbuf2.0-dev \
        librsvg2-2 \
        librsvg2-dev && \
    fc-cache -fv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "lex-app==${IMAGE_VERSION}"