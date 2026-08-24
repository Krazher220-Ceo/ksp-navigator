#!/usr/bin/env bash
#
# scripts/setup_mac.sh — подготовка macOS для проекта «Учебный навигатор».
#
# Ставит через Homebrew: python@3.11, libreoffice (конвертация .doc → .docx —
# pandoc для этого не подходит, он не читает бинарный формат .doc, только
# .docx), cloudflared (публичный HTTPS-туннель для Mini App, блок Б11).
# Создаёт виртуальное окружение и ставит зависимости из requirements.txt.
#
# Скрипт идемпотентный: повторный запуск ничего не переустанавливает
# и не ломает уже настроенное окружение.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "==> Проект: $PROJECT_ROOT"

if ! command -v brew >/dev/null 2>&1; then
    echo "ОШИБКА: Homebrew не найден. Установите его: https://brew.sh" >&2
    exit 1
fi

install_brew_formula() {
    local formula="$1"
    if brew list "$formula" >/dev/null 2>&1; then
        echo "==> $formula уже установлен, пропускаю"
    else
        echo "==> Устанавливаю $formula"
        brew install "$formula"
    fi
}

install_brew_cask() {
    local cask="$1"
    if brew list --cask "$cask" >/dev/null 2>&1; then
        echo "==> $cask уже установлен, пропускаю"
    else
        echo "==> Устанавливаю $cask"
        brew install --cask "$cask"
    fi
}

echo "==> Проверяю системные зависимости"
install_brew_formula "python@3.11"
install_brew_cask "libreoffice"
install_brew_formula "cloudflared"

PYTHON_BIN="$(brew --prefix python@3.11)/bin/python3.11"
if [ ! -x "$PYTHON_BIN" ]; then
    echo "ОШИБКА: не нашёл $PYTHON_BIN после установки python@3.11" >&2
    exit 1
fi

echo "==> Проверяю виртуальное окружение"
if [ ! -d "venv" ]; then
    echo "==> Создаю venv"
    "$PYTHON_BIN" -m venv venv
else
    echo "==> venv уже существует, пропускаю создание"
fi

echo "==> Ставлю зависимости из requirements.txt"
"$PROJECT_ROOT/venv/bin/pip" install --upgrade pip
"$PROJECT_ROOT/venv/bin/pip" install -r requirements.txt

echo "==> Проверяю .env"
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "==> Создан .env из .env.example — впишите в него реальные значения"
else
    echo "==> .env уже существует, пропускаю"
fi

echo "==> Готово. Активировать окружение: source venv/bin/activate"
