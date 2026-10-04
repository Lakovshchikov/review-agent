# review-agent

Автономный агент для код-ревью merge request'ов в GitLab. По расписанию
находит MR, где ревьюером назначен заданный пользователь, проводит ревью
агентным харнессом (OpenCode) с подключаемой моделью (Claude, Codex,
локальная) и публикует markdown-отчёт комментарием к MR.

```
review-agent poll ──glab──► GitLab: MR на ревьюера → claim-комментарий
       │
       ├─ git worktree на head MR (одноразовая копия, read-only для агента)
       ├─ opencode run: агент сам исследует репозиторий (git diff/log/grep, чтение файлов)
       └─ отчёт ──glab──► комментарий в MR вместо claim'а  (+ строка в журнале расхода)
```

Агенту запрещено выполнять код, тесты, сборку и lint из MR, ходить в
интернет и писать в GitLab. Отчёт публикует сам review-agent.

Два режима:

- **`review-agent poll`** — опрос GitLab и публикация (основной режим);
- **`review-agent --repo … --base … --head …`** — ревью диапазона
  коммитов в локальном репозитории, отчёт в файл, без GitLab.

Расписание задаёт внешний планировщик (на Windows — задача Task Scheduler
из `scripts/register-task.ps1`). Сам review-agent — один проход на вызов.

**Документация:** [конфигурация](docs/configuration.md) ·
[опрос GitLab](docs/polling.md) · [рабочая папка](docs/work-dir.md) ·
[расписание](docs/scheduling.md) · [учёт расхода](docs/usage-accounting.md) ·
[как устроено](docs/architecture.md) · [технические решения](docs/decisions.md) ·
[если что-то пошло не так](docs/troubleshooting.md) ·
для разработчиков и агентов — [AGENTS.md](AGENTS.md)

## Зависимости

| Что | Зачем | Проверено на |
| --- | --- | --- |
| Python 3.10+ | сам review-agent (зависимость пакета — только PyYAML) | 3.10 |
| `git` | worktree, клоны | Git for Windows 2.49 |
| [OpenCode](https://opencode.ai) | агентный харнесс, вызывается как внешний процесс | 2.0.21 |
| [`glab`](https://gitlab.com/gitlab-org/cli) | GitLab API и авторизация git (только для `poll`); нужен `glab auth git-credential` | 1.115.0 |

Работает на Windows (основная платформа сейчас) и Linux.

## Установка

```bash
pip install -e ".[dev]"            # пакет + pytest; создаёт команду review-agent
opencode auth login                # авторизация облачного провайдера (или: ollama pull <model>)
glab auth login --hostname <host>  # только для poll; от этой учётки публикуются комментарии
cp config.example.yaml config.yaml # и отредактировать
```

Кредов в конфиге нет: провайдер авторизуется в OpenCode, GitLab — в `glab`.

## Быстрый старт

```bash
review-agent poll --dry-run        # найти MR, выбрать один, отревьюить; в GitLab ничего не пишется
review-agent poll                  # то же с публикацией
```

```powershell
.\scripts\register-task.ps1 -DryRun -IntervalMinutes 15   # расписание в режиме dry-run
.\scripts\register-task.ps1                               # боевое расписание (обновляет ту же задачу)
```

## Команды

### `review-agent poll` — проход опроса GitLab

```bash
review-agent poll [--all] [--dry-run] [--include-closed] [--debug] [--config config.yaml]
```

| Флаг | Что делает |
| --- | --- |
| (без флагов) | интерактивно: список найденных MR со ссылками, ревью одного выбранного; до и после ревью спрашивается остаток лимитов подписки (Enter — пропустить) |
| `--all` | все найденные MR по очереди, без вопросов (для планировщика) |
| `--dry-run` | ничего не писать в GitLab; несостоявшийся комментарий — в `<work_dir>/dry-run/` |
| `--include-closed` | искать также закрытые и смёрженные MR (для тестов) |
| `--debug` | сохранить файлы каждого прогона в `<work_dir>/debug/` |
| `--config PATH` | конфиг, по умолчанию `config.yaml` |

Отревьюенный MR помечается невидимым маркером в комментарии
(`<!-- ai-review: sha=<head_sha> -->`) и больше не ревьюится. Пока идёт
ревью, на MR висит claim-комментарий «⏳ … выполняется», который потом
заменяется отчётом. Пути к файлам в отчёте становятся ссылками на GitLab в
отревьюенном коммите. Подробно — [docs/polling.md](docs/polling.md).

### `review-agent` — ручное ревью диапазона коммитов

```bash
review-agent --repo PATH --base SHA --head SHA [--mr-title TEXT] [--mr-description TEXT] [--config config.yaml] [--debug]
```

(то же: `python -m review_agent …`)

| Флаг | Что делает |
| --- | --- |
| `--repo PATH` | локальный git-репозиторий (не меняется: ревью идёт в отдельном worktree) |
| `--base SHA` | базовый коммит |
| `--head SHA` | ревьюируемый коммит |
| `--mr-title TEXT` | заголовок, передаётся агенту (по умолчанию пусто) |
| `--mr-description TEXT` | описание, передаётся агенту (по умолчанию пусто) |
| `--config PATH` | конфиг, по умолчанию `config.yaml`; секция `gitlab` не нужна |
| `--debug` | сохранить файлы прогона в `<work_dir>/debug/<время>-manual-<sha12>/` |

Отчёт пишется в `report.output_path`, ссылки не переписываются. Из консоли
спрашивается остаток лимитов подписки.

### `review-agent usage` — сводка расхода

```bash
review-agent usage [--since 30d|YYYY-MM-DD] [--until YYYY-MM-DD] [--by review|mr|model|day] [--format table|csv|json] [--wide] [--config config.yaml]
```

| Флаг | Что делает |
| --- | --- |
| `--since` | начало периода: `<N>d` или `YYYY-MM-DD` (по умолчанию весь журнал) |
| `--until` | конец периода `YYYY-MM-DD`, включительно (по умолчанию сейчас) |
| `--by` | группировка: `review` (по умолчанию), `mr`, `model`, `day` |
| `--format` | `table` (по умолчанию), `csv`, `json` |
| `--wide` | таблица со всеми колонками (в CSV и JSON — всегда все) |
| `--config PATH` | конфиг, по умолчанию `config.yaml` |

Показывает токены, время, размер MR, находки, API-эквивалент в долларах по
актуальным ценам LiteLLM и долю лимитов подписки. Печатает в консоль, файл
— перенаправлением (`--format csv > usage.csv`). Подробно —
[docs/usage-accounting.md](docs/usage-accounting.md).

### `scripts/register-task.ps1` — задача Task Scheduler (Windows)

| Параметр | По умолчанию | Смысл |
| --- | --- | --- |
| `-TaskName` | `review-agent-poll` | имя задачи; повторный запуск обновляет её |
| `-IntervalMinutes` | `30` | интервал проходов |
| `-ConfigPath` | `config.yaml` | конфиг, относительно рабочей папки |
| `-WorkingDirectory` | папка проекта | рабочая папка задачи; от неё считаются относительные пути конфига |
| `-ReviewAgentPath` | из PATH | путь к `review-agent.exe`, если venv не активирован |
| `-LogonDelayMinutes` | `5` | доп. проход через N минут после входа в Windows; `0` — нет |
| `-ExecutionTimeLimitHours` | `4` | лимит прохода; `gitlab.claim_ttl_minutes` — не меньше |
| `-DryRun` | выкл. | `poll --all --dry-run` |
| `-DebugMode` | выкл. | `poll --all --debug` |
| `-ShowConsole` | выкл. | показывать окно (по умолчанию проход идёт без окна через `pythonw`) |
| `-RunWhetherLoggedOn` | выкл. | запускать без входа пользователя (S4U; не рекомендуется без проверки) |
| `-Remove` | выкл. | удалить задачу |

Задача всегда запускает `poll --all`. Как следить за задачей и что значат
коды результата — [docs/scheduling.md](docs/scheduling.md).

### Коды выхода

| Код | `poll` | ручной режим |
| --- | --- | --- |
| `0` | всё опубликовано / пропущено / нечего ревьюить / выбор отменён | отчёт записан |
| `1` | хотя бы один MR или проект упал (проход дошёл до конца) | ревью упало (ошибка с трассировкой) |
| `2` | ничего не запускалось: конфиг, `glab`, GitLab недоступен, другой прогон идёт, нет консоли без `--all` | ошибка конфига, другой прогон идёт |

## Конфигурация

`config.yaml` (образец — `config.example.yaml`). Полный справочник с
типами и примерами — [docs/configuration.md](docs/configuration.md).
Относительные пути считаются от текущей папки процесса.

| Ключ | По умолчанию | Смысл |
| --- | --- | --- |
| `provider.name` | обязателен | провайдер OpenCode: `openai`, `anthropic`, `ollama`… |
| `provider.model` | обязателен | модель без префикса провайдера |
| `provider.reasoning_effort` | обязателен (может быть `null`) | `low`/`medium`/`high`…; `null` — для моделей без вариантов |
| `provider.api_key_env` | — | только для справки, движком не читается |
| `harness.command` | обязателен | шаблон вызова OpenCode; берите из примера (`--standalone` и `--file "{prompt_file}"` обязательны) |
| `harness.agent_name` | `reviewer` | имя генерируемого read-only агента |
| `report.output_path` | обязателен | файл отчёта ручного режима, `{run_id}` подставляется |
| `skills` | `[]` | абсолютные пути к knowledge-skill файлам (подсказки агенту, не ограничения) |
| `safety.denied_bash_patterns` | список из примера | запрещённые агенту shell-команды (denylist) |
| `safety.output_language` | `ru` | пишется только в safety-note; язык отчёта — русский, задан в промпте |
| `storage.work_dir` | `./.review-agent` | куда review-agent пишет всё |
| `storage.retention_days` | `7` | срок `logs/`, `dry-run/`, `debug/`; `null` — бессрочно |
| `storage.repo_retention_days` | `30` | срок неиспользуемого кэш-клона; `null` — бессрочно |
| `usage.enabled` | `true` | журнал расхода и вопросы о квоте |
| `usage.source` | `opencode` | `opencode` / `none` |
| `usage.session_list_command` | `[opencode, session, list]` | список сессий харнесса |
| `usage.session_export_command` | `[opencode, session, export, "{session_id}"]` | экспорт сессии |
| `usage.quota_windows` | `[5h, week]` | окна лимита подписки |
| `usage.price_catalog` | справочник LiteLLM на GitHub | URL или путь к ценам |
| `usage.price_overrides` | `{}` | свои цены `"<provider>/<model>": {input_cost_per_token: …}` |
| `gitlab.hostname` | обязателен для `poll` | хост GitLab |
| `gitlab.reviewers` | — | username'ы; MR берётся, если любой из них — ревьюер |
| `gitlab.review_drafts` | `false` | ревьюить draft-MR |
| `gitlab.min_report_chars` | `200` | более короткий отчёт не публикуется |
| `gitlab.claim_ttl_minutes` | `240` | когда claim считается брошенным; ≥ лимита задачи |
| `gitlab.projects[].path` | обязателен | `group/project` |
| `gitlab.projects[].local_repo` | — | свой клон; без него — кэш-клон в `<work_dir>/repos/` |
| `gitlab.projects[].remote` | `origin` | remote в `local_repo` (только вместе с ним) |
| `gitlab.projects[].enabled` | `true` | `false` — проект не опрашивается |
| `gitlab.projects[].reviewers`, `review_drafts`, `provider`, `skills` | глобальные | переопределения проекта; заменяют глобальное целиком |

## Рабочая папка

Всё, что пишет review-agent, — в `storage.work_dir`. Исключение — отчёты
ручного режима в `report.output_path`. Подробно, что и когда удаляется, —
[docs/work-dir.md](docs/work-dir.md).

```
<work_dir>/
  poll.lock      один прогон за раз
  tmp/           временное, пусто после прохода
  logs/          лог каждого прохода poll + stderr упавших ревью (retention_days)
  dry-run/       комментарии --dry-run (retention_days)
  debug/         файлы прогонов с --debug (retention_days)
  repos/         кэш-клоны проектов без local_repo (repo_retention_days)
  usage/         ledger.jsonl — журнал расхода, не удаляется; price-catalog.json
```

## Тесты

```bash
pytest
```

Харнесс и `glab` подменяются стабами, `git` настоящий. Тесты проходят на
Windows и Linux. `tests/test_docs.py` проверяет, что все флаги CLI и
параметры `register-task.ps1` упомянуты в этом README.
