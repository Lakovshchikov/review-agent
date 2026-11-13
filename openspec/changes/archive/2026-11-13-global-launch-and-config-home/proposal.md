# Proposal

## Why

Сейчас `review-agent` удобно запускать только из папки репозитория: конфиг ищется как `config.yaml` в текущей папке, а относительные пути в нём (`storage.work_dir`, отчёты, skills, шаблон промпта, `local_repo`) считаются от неё же. Из другой консоли команда либо не находит конфиг, либо раскладывает worktree, логи и журнал расхода в случайной папке. Нужно запускать агента из любой папки на Windows и macOS, а всё, что он читает и пишет, хранить в одном месте — папке репозитория.

## What Changes

- Новая переменная окружения `REVIEW_AGENT_HOME` — путь к папке репозитория («дом» агента).
- Поиск конфига: `--config`, затем `$REVIEW_AGENT_HOME/config.yaml`, затем `config.yaml` в текущей папке (как сейчас). Ключ `--config` во всех командах (`review-agent`, `poll`, `usage`, `prompt-check`) теряет жёсткое значение по умолчанию `config.yaml`.
- **BREAKING (мягко):** относительные пути в конфиге (`storage.work_dir`, `report.output_path`, `skills`, `prompt.template` глобальный и проектный, `local_repo`) считаются от папки найденного конфига, а не от текущей папки процесса. Запуск из корня репозитория с `config.yaml` рядом работает как раньше; `--config` на файл в другой папке с относительными путями меняет результат.
- Два скрипта настройки машины: `scripts/set-home.ps1` (Windows, пользовательская переменная) и `scripts/set-home.sh` (macOS, маркерный блок в `~/.zshrc`). Путь к дому скрипт определяет сам — это родитель `scripts/`. Скрипт ставит команду в PATH (`pipx install -e`), выставляет переменную, поддерживает удаление и dry-run, идемпотентен.
- Ручной режим и `poll --dry-run` печатают в консоль абсолютный путь к отчёту (ручной режим уже печатает; закрепляем требованием и проверяем dry-run).

## Capabilities

### New Capabilities
- `review-agent-home`: поиск конфига через `REVIEW_AGENT_HOME`, пути от папки конфига, установка команды и переменной скриптами для Windows и macOS, путь к отчёту в выводе.

### Modified Capabilities
- `project-review-settings`: требование «Configured file paths are made absolute when loaded» — базой для относительных путей становится папка конфига, а не текущая папка процесса; правило распространяется на `work_dir`, `report.output_path` и `local_repo`.

## Impact

- Код: `cli.py` (default `--config`), `config.py` (поиск конфига, база путей, `best_effort_work_dir`), `polling.py` (вывод пути dry-run), новые `scripts/set-home.ps1`, `scripts/set-home.sh`.
- Тесты: `test_config.py`, `test_cli.py`, `test_polling.py`, новый `test_set_home_scripts.py` (статические проверки по образцу `test_register_script.py`), `test_docs.py` (параметры скриптов в README).
- Документация (AGENTS.md п.11): `README.md` (установка, переменная, `--config`, пример конфига), `docs/configuration.md` (правило путей, поиск конфига), `docs/work-dir.md`, `docs/scheduling.md` (Task Scheduler от переменной не зависит), `docs/troubleshooting.md` (команда не найдена, конфиг не найден, консоль не видит переменную), `docs/architecture.md`, `docs/decisions.md` (почему пути от папки конфига), `AGENTS.md` п.5, п.7, п.9, п.10, `config.example.yaml` (комментарий про пути), `openspec/config.yaml` (статус).
