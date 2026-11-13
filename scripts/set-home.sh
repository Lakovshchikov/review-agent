#!/usr/bin/env bash
# Настройка машины (macOS) для запуска review-agent из любой консоли:
# ставит команду `review-agent` и сохраняет этот репозиторий как «дом» агента
# в переменной REVIEW_AGENT_HOME (маркерный блок `export` в ~/.zshrc).
#
# Дом — родитель папки, где лежит скрипт, поэтому путь не спрашивается и не
# передаётся. review-agent ищет config.yaml в этой папке, а все относительные
# пути конфига (work_dir, отчёты, skills, ...) считает от неё — откуда бы ни
# запустили команду.
#
# Скрипт:
#   1. проверяет, что есть `pipx` (нет — останавливается, ничего не меняя);
#   2. `pipx install --force -e <дом>` (editable: код берётся из репозитория)
#      и `pipx ensurepath` (команда попадает в PATH);
#   3. записывает в rc-файл блок с `export REVIEW_AGENT_HOME=...`.
#
# Повторный запуск безопасен: блок заменяется, а не дублируется, установка
# обновляется. Запустите снова после переноса репозитория.
# Уже открытые терминалы новую переменную и PATH не увидят — откройте новый.
#
# Использование:
#   scripts/set-home.sh [--dry-run] [--remove] [--rc-file FILE] [--python VERSION|PATH]
#     --dry-run      только показать, что будет сделано
#     --remove       убрать блок из rc-файла и удалить команду
#     --python X     интерпретатор для окружения pipx (версия, например 3.13, или путь);
#                    нужен, если Python по умолчанию слишком новый для зависимостей
#     --rc-file FILE куда писать блок (по умолчанию ~/.zshrc; для bash —
#                    ~/.bash_profile)

set -eu

VAR_NAME="REVIEW_AGENT_HOME"
PACKAGE_NAME="review-agent"
BEGIN_MARK="# >>> review-agent >>>"
END_MARK="# <<< review-agent <<<"

DRY_RUN=0
REMOVE=0
RC_FILE="$HOME/.zshrc"
PYTHON_ARG=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --remove) REMOVE=1 ;;
        --python)
            [ $# -ge 2 ] || { echo "--python требует аргумент: версия или путь" >&2; exit 2; }
            PYTHON_ARG="$2"
            shift
            ;;
        --rc-file)
            [ $# -ge 2 ] || { echo "--rc-file требует аргумент: путь к файлу" >&2; exit 2; }
            RC_FILE="$2"
            shift
            ;;
        -h|--help)
            sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Неизвестный параметр: $1 (см. --help)" >&2; exit 2 ;;
    esac
    shift
done

HOME_DIR="$(cd "$(dirname "$0")/.." && pwd)"

step() {
    if [ "$DRY_RUN" -eq 1 ]; then echo "[dry-run] $1"; else echo "$1"; fi
}

# Содержимое rc-файла без нашего блока (если блока нет — файл как есть).
without_block() {
    [ -f "$RC_FILE" ] || return 0
    awk -v b="$BEGIN_MARK" -v e="$END_MARK" '
        $0 == b { skip = 1; next }
        $0 == e { skip = 0; next }
        !skip { print }
    ' "$RC_FILE"
}

# -- pipx: проверяется до любых изменений --------------------------------------

# `pipx` как команда, если она в PATH; иначе `python3 -m pipx`: после
# `pip install --user pipx` исполняемый файл может быть ещё не в PATH.
PIPX_PYTHON=""
pipx_run() {
    if [ -n "$PIPX_PYTHON" ]; then "$PIPX_PYTHON" -m pipx "$@"; else pipx "$@"; fi
}

HAVE_PIPX=1
if ! command -v pipx >/dev/null 2>&1; then
    if command -v python3 >/dev/null 2>&1 && python3 -m pipx --version >/dev/null 2>&1; then
        PIPX_PYTHON="$(command -v python3)"
    else
        HAVE_PIPX=0
    fi
fi
if [ "$HAVE_PIPX" -eq 0 ]; then
    HINT="pipx не найден (ни как команда, ни как python3 -m pipx). Установите его: brew install pipx, затем pipx ensurepath, откройте новый терминал и запустите скрипт снова."
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] ВНИМАНИЕ: $HINT Настоящий запуск остановится здесь."
    else
        echo "$HINT" >&2
        exit 1
    fi
fi

# -- удаление -------------------------------------------------------------------

if [ "$REMOVE" -eq 1 ]; then
    step "Убираю блок из $RC_FILE"
    if [ "$DRY_RUN" -eq 0 ] && [ -f "$RC_FILE" ]; then
        TMP_FILE="$(mktemp)"
        without_block > "$TMP_FILE"
        cat "$TMP_FILE" > "$RC_FILE"
        rm -f "$TMP_FILE"
    fi
    if [ "$HAVE_PIPX" -eq 1 ]; then
        if pipx_run list --short | grep -q "^$PACKAGE_NAME "; then
            step "Удаляю команду: pipx uninstall $PACKAGE_NAME"
            [ "$DRY_RUN" -eq 1 ] || pipx_run uninstall "$PACKAGE_NAME"
        else
            echo "Команды $PACKAGE_NAME среди установленных pipx нет — удалять нечего."
        fi
    fi
    [ "$DRY_RUN" -eq 1 ] || echo "Готово. Откройте новый терминал, чтобы изменения вступили в силу."
    exit 0
fi

# -- установка ------------------------------------------------------------------

step "Дом агента: $HOME_DIR"
step "Устанавливаю команду: pipx install --force -e \"$HOME_DIR\"${PYTHON_ARG:+ --python $PYTHON_ARG}"
if [ "$DRY_RUN" -eq 0 ]; then
    if [ -n "$PYTHON_ARG" ]; then
        pipx_run install --force -e "$HOME_DIR" --python "$PYTHON_ARG"
    else
        pipx_run install --force -e "$HOME_DIR"
    fi
fi
step "Добавляю каталог pipx в PATH: pipx ensurepath"
if [ "$DRY_RUN" -eq 0 ]; then
    pipx_run ensurepath
fi

# Одинарные кавычки внутри пути экранируются как '\''.
QUOTED_HOME="'$(printf '%s' "$HOME_DIR" | sed "s/'/'\\\\''/g")'"
step "Записываю в $RC_FILE: export $VAR_NAME=$QUOTED_HOME"
if [ "$DRY_RUN" -eq 0 ]; then
    TMP_FILE="$(mktemp)"
    without_block > "$TMP_FILE"
    {
        echo "$BEGIN_MARK"
        echo "export $VAR_NAME=$QUOTED_HOME"
        echo "$END_MARK"
    } >> "$TMP_FILE"
    cat "$TMP_FILE" > "$RC_FILE"
    rm -f "$TMP_FILE"
fi

if [ ! -f "$HOME_DIR/config.yaml" ]; then
    echo "Подсказка: в $HOME_DIR нет config.yaml — скопируйте config.example.yaml в config.yaml и отредактируйте."
fi
if [ "$DRY_RUN" -eq 0 ]; then
    echo
    echo "Готово. Откройте НОВЫЙ терминал (уже открытые не видят переменную и PATH) и проверьте: review-agent --help"
fi
