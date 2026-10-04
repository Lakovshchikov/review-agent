# Как устроен review-agent

Тонкий оркестратор на Python поверх трёх внешних инструментов: `git`
(изолированный worktree), OpenCode (агентный харнесс с моделью) и `glab`
(GitLab). Принципы и их обоснование — в [AGENTS.md](../AGENTS.md) (п.3–5),
технические решения — в [decisions.md](decisions.md).

```
                ┌──────────── review-agent poll ────────────┐
 GitLab  ◄─glab─┤ поиск MR → claim → fetch → ревью → отчёт  │
                └───────────────────┬───────────────────────┘
                                    │ (тот же движок, что у ручного режима)
                ┌───────────────────▼───────────────────────┐
 git     ◄──────┤ worktree на head в <work_dir>/tmp/<run_id>│
 OpenCode◄──────┤ opencode.json (read-only агент) + промпт  │
                │ → opencode run → stdout = отчёт          │
                └───────────────────────────────────────────┘
```

## Один прогон ревью (движок)

Общий для ручного режима и `poll` (`pipeline.py`):

1. Создаётся изолированный `git worktree` на head в
   `<work_dir>/tmp/<run_id>/worktree`. Репозиторий-источник не меняется.
   Если head не найден локально, движок пробует `git fetch <sha>`.
2. В worktree ищутся инструкции целевого репозитория — первый найденный из
   `AGENTS.md` → `CLAUDE.md` → `.github/copilot-instructions.md` — и папка
   документации (`docs/` или `documentation/`). Агенту передаются только
   **пути**, не содержимое.
3. Рендерится промпт (`prompt.py`): роль, worktree, base SHA, title и
   description MR, пути из п.2, пути к skill'ам, методология ревью (что
   проверять, SEV Blocker/Major/Minor, допустимы находки с неполной
   уверенностью, ничего не изменять и не запускать). Это единственное
   место, где живёт методология. Заранее подготовленного diff, списка
   файлов или карты рисков нет: агент строит это сам через `git`.
4. В корень worktree пишется `opencode.json` с агентом `harness.agent_name`
   (`harness_config.py`): `bash` — denylist из
   `safety.denied_bash_patterns`; `edit`, `webfetch`, `websearch` —
   запрещены. Рядом в `tmp/<run_id>/` пишется `safety-note.md` — запись
   ограничений для человека.
5. Харнесс вызывается подпроцессом (`harness.py`) по шаблону
   `harness.command` из папки worktree. Первый элемент команды ищется
   через PATH явно: на Windows npm-инструменты ставятся как `.CMD`/`.ps1`-шимы,
   которые `subprocess` без `shell=True` по голому имени не находит. Промпт передаётся файлом, stdout
   становится отчётом, stderr сохраняется в `harness-stderr.log`.
6. Собирается учёт расхода (`usage_source.py`, `review_stats.py`), и в
   `usage/ledger.jsonl` дописывается запись (`usage_ledger.py`) — при любом
   исходе прогона.
7. Вся `tmp/<run_id>/` удаляется — при успехе и при ошибке. С `--debug`
   файлы (кроме worktree) предварительно копируются в `debug/`.

Ручной режим (`cli.py`) берёт `poll.lock`, выполняет движок и пишет отчёт
в `report.output_path` (`report.py`).

## Проход poll

`polling.py` оркестрирует: лог прохода (`passlog.py`) → lock (`lock.py`) →
уборка (`housekeeping.py`, `repo_source.py`) → проверка `glab` и поиск MR
(`gitlab.py`) → выбор → для каждого MR: claim (`publishing.py` +
`gitlab.py`) → подготовка репозитория (`repo_source.py`) → движок →
проверка отчёта и переписывание ссылок (`file_links.py`) → публикация
отчёта вместо claim'а. Пошаговое описание с точки зрения пользователя —
[polling.md](polling.md).

Состояния «отревьюено» и «ревьюится» в локальных файлах нет: оно хранится
только в маркерах комментариев GitLab. Поэтому проход идемпотентен, и его
можно запускать откуда угодно.

## Внешние процессы

| Процесс | Кто вызывает | Зачем |
| --- | --- | --- |
| `git` | `worktree.py`, `repo_source.py`, `review_stats.py`, `file_links` (через `polling.py`) | worktree, clone/fetch кэша, размер изменения, `ls-tree` для ссылок |
| `opencode run` | `harness.py` | само ревью |
| `opencode session list/export`, `opencode --version` | `usage_source.py` | токены прогона |
| `glab api` (в т.ч. `api user` — проверка авторизации), `glab mr view` | `gitlab.py` | поиск MR, метаданные, комментарии |
| `glab auth git-credential` | `git` (credential helper кэш-клона) | авторизация fetch/clone |

Все дочерние процессы запускаются с флагами из `proc.py`: без окна, если
у самого review-agent нет консоли (`pythonw`). Харнесс, кроме того,
запускается с отвязанным stdin (`harness.py`).

## Модули `src/review_agent/`

| Модуль | Ответственность |
| --- | --- |
| `__init__.py`, `__main__.py` | пакет; `python -m review_agent` |
| `cli.py` | точки входа `review-agent`, `poll`, `usage`; разбор флагов; ручной режим |
| `config.py` | загрузка и проверка YAML-конфига, значения по умолчанию, итоговые настройки проекта |
| `pipeline.py` | движок одного ревью: worktree → промпт → `opencode.json` → харнесс → учёт → уборка |
| `worktree.py` | создание и гарантированное удаление `git worktree`, уборка осиротевших |
| `prompt.py` | промпт ревью (методология), поиск инструкций и `docs/` целевого репозитория |
| `harness_config.py` | `opencode.json` с read-only агентом и safety-note |
| `harness.py` | вызов харнесса подпроцессом: подстановка плейсхолдеров, PATH-резолвинг, UTF-8 |
| `report.py` | запись отчёта ручного режима в файл |
| `polling.py` | проход `poll`: поиск, выбор, claim, ревью, публикация, итог, коды выхода |
| `gitlab.py` | единственный модуль, который говорит с GitLab (через `glab`) |
| `publishing.py` | чистая логика: маркеры ревью и claim'а, тело комментария, проверка отчёта |
| `file_links.py` | чистая логика: переписывание путей в отчёте в ссылки GitLab |
| `repo_source.py` | откуда берётся репозиторий: `local_repo` или кэш-клон в `repos/`, fetch веток MR, уборка кэша |
| `housekeeping.py` | раскладка `work_dir`, очистка `tmp/` и устаревших файлов |
| `lock.py` | `poll.lock`: один прогон за раз на рабочую папку |
| `passlog.py` | лог прохода в консоль и в файл |
| `proc.py` | флаги дочерних процессов (без окна под `pythonw`) |
| `quota_prompt.py` | интерактивный ввод остатка лимитов подписки |
| `review_stats.py` | размер изменения и число находок для журнала (агенту не передаются) |
| `usage_source.py` | токены прогона из сессий OpenCode; устойчив к изменениям формата |
| `usage_ledger.py` | запись и чтение `usage/ledger.jsonl` |
| `usage_prices.py` | цены: справочник LiteLLM, его копия, переопределения |
| `usage_summary.py` | `review-agent usage`: группировка, оценки долей, таблица/CSV/JSON |

## Тесты

`tests/test_<модуль>.py` — по одному файлу на модуль, плюс
`test_register_script.py` (PowerShell-скрипт) и `test_docs.py`
(документация не отстаёт от CLI). Харнесс и `glab` в тестах подменяются
стабами, `git` — настоящий (временные репозитории из `conftest.py`).
Фикстуры:

- `tests/fixtures/opencode/review-session.json` — реальный экспорт сессии
  OpenCode для разбора токенов;
- `tests/fixtures/file_links/*.in.md` — отчёты с путями для переписывания
  ссылок.
