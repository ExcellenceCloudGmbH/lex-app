FROM python:3.9.17-slim-bullseye
ARG IMAGE_VERSION
# install all necessary packages
WORKDIR /app
RUN apt-get update && apt-get install -y curl gnupg2 wget apt-transport-https ca-certificates gnupg sudo&& \
    echo "deb [arch=amd64]  http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list && \
    wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    sh -c 'echo "deb [arch=amd64] https://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y \
        git \
        gcc \
        libpq-dev \
        wkhtmltopdf \
        rsync \
        g++ \
        wget \
        locales-all && \
    apt-get install -y google-chrome-stable && \
    wget http://ftp.de.debian.org/debian/pool/contrib/m/msttcorefonts/ttf-mscorefonts-installer_3.7_all.deb -P /tmp && \
    ACCEPT_EULA=Y apt install -y \
        /tmp/ttf-mscorefonts-installer_3.7_all.deb && \
    fc-cache -fv && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "git+https://github.com/LundIT/lex-app@$IMAGE_VERSION"