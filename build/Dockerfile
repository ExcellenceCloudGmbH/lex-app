# Use Debian 12 (bookworm) based Python 3.12 slim to keep apt packages consistent.
# (If you really need Debian 11, you can switch this to python:3.12-slim-bullseye.)
ARG PYTHON_BASE_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_BASE_IMAGE}

# Kept for manual/legacy builds: `docker build --build-arg IMAGE_VERSION=2.1.6`
# still installs that PyPI release. CI passes LEX_APP_SPEC instead.
# The empty default is load-bearing: the install step runs under `set -u`, so a
# build passing only LEX_APP_SPEC would abort on an unbound variable.
ARG IMAGE_VERSION=""

# Full pip requirement for lex-app itself. Empty (the default) falls back to the
# PyPI release named by IMAGE_VERSION. CI sets this to e.g.
#   git+https://github.com/ExcellenceCloudGmbH/lex-app@<commit-sha>
# to build an image straight from a branch or commit, with no PyPI publish.
ARG LEX_APP_SPEC=""

# Only used for git builds. lex/_version.py is stamped by the PyPI publish
# workflow, so a git checkout still carries the "0.0.0.dev0" placeholder and
# /health would report that for every branch build. Setting this rewrites the
# file after install — which reaches /health because runtime_health.py imports
# lex._version.__version__ directly rather than reading dist metadata (that is
# frozen at install time and could not be corrected this way).
ARG LEX_APP_VERSION_LABEL=""

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

RUN set -eux; \
    if [[ -z "${LEX_APP_SPEC}" && -z "${IMAGE_VERSION}" ]]; then \
        echo "ERROR: pass LEX_APP_SPEC (git+https://...@sha) or IMAGE_VERSION (2.1.6)" >&2; \
        exit 1; \
    fi; \
    SPEC="${LEX_APP_SPEC:-lex-app==${IMAGE_VERSION}}"; \
    echo "Installing lex-app from: ${SPEC}"; \
    pip install --no-cache-dir "${SPEC}"; \
    \
    if [[ -n "${LEX_APP_VERSION_LABEL}" ]]; then \
        LEX_DIR="$(python -c 'import lex, os; print(os.path.dirname(lex.__file__))')"; \
        printf '__version__ = "%s"\n' "${LEX_APP_VERSION_LABEL}" > "${LEX_DIR}/_version.py"; \
        echo "Stamped lex._version.__version__ = ${LEX_APP_VERSION_LABEL}"; \
    fi
