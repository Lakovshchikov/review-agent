# Tasks

## 1. Поиск конфига и пути от папки конфига

- [x] 1.1 В `config.py` добавить `resolve_config_path(arg)` (порядок: `--config` → `$REVIEW_AGENT_HOME/config.yaml` → `./config.yaml`) и подсказку про `REVIEW_AGENT_HOME`/`--config` в ошибку «конфиг не найден»; тесты в `test_config.py` на все сценарии поиска (через `monkeypatch.setenv/chdir`)
- [x] 1.2 Базой относительных путей сделать папку конфига: `skills`, `prompt.template` (глобальный и проектный), `storage.work_dir`, `report.output_path` (шаблон с `{run_id}`), `local_repo`, а также `best_effort_work_dir`; тесты: запуск из чужой cwd кладёт всё рядом с конфигом, конфиг в cwd даёт прежние пути; обновить существующие тесты на `_absolute`
- [x] 1.3 В `cli.py` убрать default `config.yaml` у `--config` во всех четырёх разборщиках и вызывать `resolve_config_path`; тест `test_cli.py`: подкоманды находят конфиг через переменную
- [x] 1.4 Проверить, что ручной режим печатает абсолютный путь отчёта, а итог `poll --dry-run` перечисляет путь к сохранённому комментарию; при необходимости добавить вывод; тесты в `test_cli.py`/`test_polling.py`
- [x] 1.5 Документация группы: `README.md` (таблицы флагов `--config`, пример конфига, правило путей), `docs/configuration.md`, `docs/work-dir.md`, `docs/architecture.md`, `docs/decisions.md`, `config.example.yaml` (комментарий про пути); проверить `pytest tests/test_docs.py`

## 2. Скрипты настройки машины

- [x] 2.1 `scripts/set-home.ps1`: проверка `pipx` до изменений, `pipx install --force -e <репо>`, пользовательская `REVIEW_AGENT_HOME`, `-Remove`, `-DryRun`, сообщение «откройте новую консоль»; проверка запуском `-DryRun` на Windows и статическим тестом `tests/test_set_home_scripts.py`
- [x] 2.2 `scripts/set-home.sh`: то же для macOS — маркерный блок в `~/.zshrc`, `--remove`, `--dry-run`, `--rc-file`; тест на временном rc-файле (двойной запуск даёт один блок, удаление убирает только блок), пропуск без `bash`
- [x] 2.3 Документация группы: `README.md` (раздел установки и параметры скриптов; `test_docs.py` требует упоминания каждого параметра), `docs/scheduling.md` (Task Scheduler от переменной не зависит), `docs/troubleshooting.md` (команда не найдена, конфиг не найден, консоль не видит переменную, файлы появились не там), `AGENTS.md` п.5, п.9, п.10

## 3. Живая проверка и закрытие

- [x] 3.1 На Windows: запустить `set-home.ps1`, открыть новую консоль в посторонней папке, выполнить `review-agent prompt-check --show` и `review-agent poll --dry-run`; убедиться, что в посторонней папке ничего не создано; результат записать в `validation-notes.md`
- [x] 3.2 На macOS: то же с `set-home.sh` (если машина доступна; иначе зафиксировать как непроверенное в `validation-notes.md`)
- [x] 3.3 Актуализировать `AGENTS.md` п.7 (строка 9 таблицы, бэклог), `openspec/config.yaml` (статус) и прогнать весь `pytest`
