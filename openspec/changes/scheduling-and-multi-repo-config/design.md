# Design: scheduling-and-multi-repo-config

## Context

Мотивация — `proposal.md` (Why). Что уже есть после Change 2 и на что опирается дизайн:

- `review-agent poll` (`src/review_agent/polling.py`) — один проход; весь вывод идёт через инжектируемый `output_fn` (по умолчанию `print`) и прямые `print(..., file=sys.stderr)` для ошибок до старта. Лог-файла нет.
- `poll_lock` (`<scratch>/poll.lock`) — один проход за раз; `stdin_is_interactive()` уже корректно отказывает без `--all` под планировщиком (проверено живьём, `validation-notes.md` 6.4).
- Движок (`pipeline.run_review`) сам читает конфиг по `config_path` — провайдер и skill'ы берутся только из глобальных секций.
- Мусор на диске: `managed_worktree` удаляет `<scratch>/<run_id>/worktree`, но `rmdir` папки прогона не срабатывает — там остаются `prompt.md` и safety-note (так задумано для диагностики). Ещё копятся `<scratch>/notes/<stem>.json` (тела POST-запросов) и отчёты с `.comment.md`/`.harness-stderr.log` в папке отчётов.
- `gitlab.projects[*]` сейчас — только `path`/`local_repo`/`remote`; `design.md` Change 2 прямо отложил per-project переопределения на этот change.
- Все относительные пути конфига (`report.output_path`, `local_repo`, `skills`, `--scratch-dir`) разрешаются от рабочей папки процесса.
- Среда — Windows, Task Scheduler; Linux-раннер/GitLab CI — позже (`AGENTS.md` §2, §5).

## Goals / Non-Goals

**Goals**
- Python-часть остаётся «одним вызовом без знания о вызывающем»: всё Windows-специфичное про расписание — только в PowerShell-скрипте.
- Лог прохода пишется всегда, когда известно, куда писать, — включая отказы до старта ревью; консоль под планировщиком не нужна.
- Очистка консервативная: удаляем только своё, только по возрасту, никогда — живой worktree.
- Конфиг без новых полей ведёт себя ровно как сейчас.

**Non-Goals**
- Ротация `harness-stderr.log` по размеру, сжатие логов, отправка логов куда-либо.
- Переопределение на уровне проекта чего-то кроме `enabled`/`reviewers`/`review_drafts`/`provider`/`skills` (например `safety`, `harness`, `min_report_chars`, свой GitLab-хост).
- Автотесты PowerShell-скрипта (Pester) — скрипт проверяется вживую на Windows.

## Decisions

### 1. Регистрация — `scripts/register-task.ps1` поверх модуля `ScheduledTasks`

Параметры (все необязательные):

| Параметр | По умолчанию | Смысл |
|----------|--------------|-------|
| `-TaskName` | `review-agent-poll` | имя задачи (повторный запуск обновляет её) |
| `-IntervalMinutes` | `30` | интервал повтора |
| `-ConfigPath` | `config.yaml` | передаётся как `--config` |
| `-WorkingDirectory` | папка репозитория (родитель `scripts/`) | «Start in» задачи |
| `-ReviewAgentPath` | `(Get-Command review-agent).Source` | абсолютный путь к exe (обычно `.venv\Scripts\review-agent.exe`) |
| `-ExecutionTimeLimitHours` | `4` | Task Scheduler убьёт зависший проход |
| `-DryRun` | выкл. | добавляет `--dry-run` |
| `-RunWhetherLoggedOn` | выкл. | `LogonType S4U` вместо `Interactive` (см. риски) |
| `-Remove` | выкл. | `Unregister-ScheduledTask`, идемпотентно |

Действие: `New-ScheduledTaskAction -Execute <abs exe> -Argument "poll --all --config <cfg> [--dry-run]" -WorkingDirectory <dir>`. `--include-closed` скрипт не умеет передавать вообще. Триггер: `-Once -At (Get-Date) -RepetitionInterval`; настройки: `-MultipleInstances IgnoreNew`, `-ExecutionTimeLimit`, `-StartWhenAvailable`, работа от батареи разрешена. Регистрация — `Register-ScheduledTask -Force` (обновление на месте). Перед регистрацией скрипт проверяет, что exe и конфиг (относительно `WorkingDirectory`) существуют, иначе — ошибка без регистрации. В конце печатает итоговую команду и как посмотреть результат (`Get-ScheduledTaskInfo` → `LastTaskResult`, папка логов).

Альтернативы: подкоманда `review-agent schedule install` (отклонена — тянет Windows-специфику в пакет и нарушает «entrypoint не знает, кто его вызвал»); `schtasks.exe` строкой (отклонено — нет `MultipleInstances`/`ExecutionTimeLimit` без XML, хрупкое экранирование); только инструкция в README (отклонено — шагов много, легко забыть `--all` или рабочую папку).

### 2. Рабочая папка задачи вместо переразрешения путей конфига

Относительные пути продолжают разрешаться от cwd; задача обязательно запускается с `WorkingDirectory` = папка проекта. Альтернатива — разрешать пути относительно файла конфига — отклонена: тихо меняет поведение уже провалидированных ручных запусков. Чтобы ошибка рабочей папки была видна, первая строка лога прохода — cwd, путь конфига и версия пакета.

### 3. Лог прохода — `logging` + «двойной» вывод

Новый модуль `src/review_agent/passlog.py`: контекст-менеджер `pass_log(log_dir)` создаёт `logging.Logger` с `FileHandler(encoding="utf-8")` на `logs/poll-YYYYMMDD-HHMMSS-<pid>.log` (pid — чтобы проход, упёршийся в lock в ту же секунду, не перетёр чужой файл) и формат `%(asctime)s %(levelname)s %(message)s`. Он отдаёт две функции, которые `run_poll` использует вместо `print`: `info(msg)` и `error(msg)` — каждая пишет в лог и в консоль (`sys.stdout`/`sys.stderr`), причём запись в консоль пропускается, если поток `None`, и любые её исключения (`OSError`, `UnicodeEncodeError`) глотаются — проход и лог от консоли не зависят. Инжекция `output_fn`/`input_fn` для тестов сохраняется (логгер оборачивает `output_fn`).

Порядок в `run_poll`: (1) загрузить конфиг; при `ConfigError` — лучшее усилие прочитать сырой YAML и взять `housekeeping.log_dir`, иначе `./logs`, записать ошибку и выйти с 2; (2) открыть лог; (3) проверка интерактивности; (4) lock — отказ тоже логируется; (5) под lock'ом — очистка (решение 4), затем существующий проход. В конце — итог и длительность. В интерактивном режиме в лог попадает список и выбранный номер, но не сам вопрос.

Альтернатива — `RotatingFileHandler` на один файл — отклонена по выбору пользователя: файл на проход проще читать по `LastRunTime` задачи и удалять по возрасту тем же механизмом, что и остальное.

### 4. Очистка — `src/review_agent/housekeeping.py`, по mtime, только своё

`cleanup_expired(*, log_dir, scratch_dir, report_dir, report_name_template, retention_days, now, log)` → число удалённых. Кандидаты (mtime старше `now - retention_days`):

- `log_dir`: файлы `poll-*.log`;
- `scratch_dir`: папки, чьё имя совпадает с форматом `run_id` (`^\d+-[0-9a-f]{8}$`) и внутри которых **нет** `worktree/` (живой или осиротевший worktree — забота `cleanup_orphaned_worktrees`, который знает про `git worktree prune`); файлы `notes/*.json`;
- `report_dir` (= родитель `report.output_path`): файлы poll-формата `<slug>-<iid>-<sha12>` с суффиксами `.md`, `.comment.md`, `.harness-stderr.log` и файлы, совпадающие с именем из шаблона `report.output_path`, где `{run_id}` заменён на regex `run_id`.

Ничего вне этих трёх папок, никаких рекурсивных обходов `report_dir`. Ошибка удаления любого элемента → `warning` в лог, идём дальше. Для mtime папки прогона берётся mtime самой папки (её содержимое пишется один раз при прогоне). Очистка идёт и в `--dry-run` (это локальная уборка, а dry-run про запись в GitLab). Выполняется только под lock'ом, поэтому два прохода не чистят одновременно; ручной прогон в ту же scratch-папку lock не берёт, но его папка свежая и/или содержит `worktree/` — не попадёт под удаление.

Конфиг — новая необязательная секция:

```yaml
housekeeping:
  log_dir: ./logs        # по умолчанию ./logs
  retention_days: 14     # положительное целое; null — не удалять ничего
```

Альтернатива — «оставлять последние N прогонов» — отклонена: при неравномерном потоке MR возраст предсказуемее для человека, который ищет отчёт недельной давности.

### 5. Переопределения проекта — замена целиком, разрешение до прохода

`GitLabProjectConfig` получает `enabled: bool = True`, `reviewers: list[str] | None`, `review_drafts: bool | None`, `provider: ProviderConfig | None`, `skills: list[str] | None` (`None` = «не задано, взять глобальное»; `[]` для `skills` = «явно без skill'ов»). Разбор `provider` выносится в общую функцию `_load_provider(data, section)`, чтобы правила (обязательный ключ `reasoning_effort`, `null` вместо `""`) были одни. `gitlab.reviewers` становится необязательным; после разбора каждый **включённый** проект проверяется на непустой эффективный список ревьюеров — иначе `ConfigError` с именем проекта. `gitlab.projects` по-прежнему должен быть непустым; все проекты могут быть выключены.

Функция `effective_settings(config, project)` возвращает эффективные `reviewers`, `review_drafts` и `Config` для движка (`dataclasses.replace(config, provider=..., skills=...)`). Поверхностное слияние вложенного `provider` (например, переопределить только `model`) отклонено: одно правило «замена целиком» проще объяснить и не даёт получить модель от одного провайдера с `api_key_env` другого.

### 6. Движок принимает готовый `Config`

`run_review(..., config: Config | None = None)`: если передан — используется он, иначе как сейчас `load_config(config_path)`. Ручной CLI не меняется. `polling._review_one` передаёт эффективный конфиг проекта и строит заголовок комментария через `build_model_string(effective.provider)`. Альтернатива — отдельные параметры `provider=`/`skills=` в `run_review` — отклонена: движок читает и `harness`/`safety`/`report`, один объект конфига проще держать согласованным.

### 7. Выключенный проект не трогается вовсе

`_run_locked` пропускает проекты с `enabled: false` до проверки локального клона и до запросов в GitLab, и не добавляет для них `Outcome` (выключение — штатное состояние, не сбой). В начале прохода в лог пишется одна строка со списком выключенных проектов, чтобы не гадать, почему проект «молчит».

## Risks / Trade-offs

- [Interactive-задача на каждом запуске мелькает консольным окном] → Документируем; `-RunWhetherLoggedOn` (S4U) запускает без окна и без входа пользователя, но S4U не даёт доступа к сетевым кредам Windows/DPAPI — если `glab` хранит токен в keyring (`--use-keyring`), а не в `config.yml`, или VPN поднимается только в пользовательской сессии, проход упадёт на preflight (код 2, причина в логе). Проверяется вживую (задача 6.x); по умолчанию — `Interactive`.
- [VPN выключен → каждый проход «GitLab недоступен», код 2] → Ожидаемо; причина в логе прохода, логи ограничены сроком хранения.
- [Проход дольше интервала (3–4 мин на MR)] → `IgnoreNew` в Task Scheduler + `poll.lock`; пропущенный интервал безвреден — MR без маркера возьмёт следующий проход. В лог пишется длительность прохода, чтобы было видно, когда интервал пора увеличить.
- [Удалён отчёт, который ещё нужен] → Отчёт уже опубликован в GitLab (если не dry-run); срок настраивается, `retention_days: null` отключает очистку.
- [`-RepetitionInterval` без `-RepetitionDuration` на некоторых версиях Windows повторяется не бесконечно] → Проверить вживую `Get-ScheduledTask ... | Select -Expand Triggers` (задача 6.x); при необходимости явно задать длительность.
- [Неверная рабочая папка задачи → «клон не найден»/«конфиг не найден»] → Скрипт задаёт её явно и проверяет конфиг относительно неё; первая строка лога — cwd.

## Migration Plan

Миграции данных нет: все новые поля необязательные, старый `config.yaml` работает как есть (лог пишется в `./logs`, очистка с 14 днями включается автоматически — это единственное заметное изменение по умолчанию, отражается в README). Развёртывание: обновить пакет → прогнать `review-agent poll --all --dry-run` вручную → `scripts/register-task.ps1 -DryRun` → проверить логи → перерегистрировать без `-DryRun`. Откат: `scripts/register-task.ps1 -Remove`.

## Open Questions

- Работает ли S4U-режим с текущим способом хранения токенов `glab`/OpenCode и VPN на рабочей машине — выясняется живой проверкой; влияет только на рекомендацию в README, не на код.
