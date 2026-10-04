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
[промпт ревью](docs/prompt.md) · [опрос GitLab](docs/polling.md) · [рабочая папка](docs/work-dir.md) ·
[расписание](docs/scheduling.md) · [учёт расхода](docs/usage-accounting.md) ·
[как устроено](docs/architecture.md) · [технические решения](docs/decisions.md) ·
[если что-то пошло не так](docs/troubleshooting.md) ·
для разработчиков и агентов — [AGENTS.md](AGENTS.md)

## Зависимости

| Что | Зачем | Проверено на |
| --- | --- | --- |
| Python 3.10+ | сам review-agent (зависимости пакета — PyYAML и Jinja2) | 3.10 |
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

### `review-agent prompt-check` — проверка шаблонов промпта

```bash
review-agent prompt-check [--project PATH | --template FILE] [--show] [--config config.yaml]
```

| Флаг | Что делает |
| --- | --- |
| (без флагов) | глобальные настройки и итоговые настройки каждого проекта, включая выключенные |
| `--project PATH` | только итоговые настройки проекта `group/project` |
| `--template FILE` | отдельный файл шаблона без проектов (конфиг не обязателен) |
| `--show` | напечатать промпт, отрендеренный с тестовыми значениями |
| `--config PATH` | конфиг, по умолчанию `config.yaml` |

Ошибки (файла нет, синтаксис Jinja2, неизвестная переменная) и
предупреждения (шаблон не использует переменную, нет файла skill) выводятся
все сразу. Коды выхода: `0` — чисто, `1` — только предупреждения, `2` —
ошибки. Та же проверка идёт в начале `poll` и ручного режима: при ошибке
работа не начинается, при предупреждениях в консоли задаётся один вопрос, а
в `poll --all` они только пишутся в лог. Подробно — [docs/prompt.md](docs/prompt.md).

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
| `2` | ничего не запускалось: конфиг, ошибка шаблона промпта или отказ продолжить с его предупреждениями, `glab`, GitLab недоступен, другой прогон идёт, нет консоли без `--all` | ошибка конфига или шаблона промпта, отказ продолжить с предупреждениями, другой прогон идёт |

Коды `prompt-check` — в его разделе выше.

## Конфигурация

`config.yaml` (образец — `config.example.yaml`). Полный справочник с
типами и проверками — [docs/configuration.md](docs/configuration.md).
Относительные пути считаются от текущей папки процесса. Ниже все ключи;
`# обяз.` — обязательный ключ, у остальных указано значение по умолчанию.

```yaml
provider:                         # модель ревью (обяз.)
  name: openai                    # обяз.; провайдер OpenCode: openai, anthropic, ollama…
  model: gpt-5                    # обяз.; без префикса провайдера
  reasoning_effort: medium        # обяз.; low/medium/high…; null — у модели нет вариантов (локальные)
  api_key_env: OPENAI_API_KEY     # только для справки, не читается; авторизация — opencode auth login

harness:                          # вызов OpenCode (обяз.)
  command: [opencode, run, --standalone, --agent, "{agent}", --model, "{model}",
            --file, "{prompt_file}", "Внимательно прочитай вложенный файл целиком и строго следуй его инструкциям."]
                                  # обяз.; берите из примера: --standalone и --file обязательны
  agent_name: reviewer            # = reviewer; имя генерируемого read-only агента

report:
  output_path: ./reports/review-{run_id}.md   # обяз.; отчёт ручного режима, {run_id} подставляется

skills: []                        # = []; пути к knowledge-skill файлам (подсказки, не ограничения); становятся абсолютными

prompt:                           # промпт ревью; в конфиге только пути, не текст — docs/prompt.md
  template: null                  # = null (встроенный builtin/default.md.j2); путь к своему Jinja2-шаблону
  instruction_files: [AGENTS.md, CLAUDE.md, .github/copilot-instructions.md]   # = это значение; в репо MR, берётся первый найденный
  docs_dirs: [docs, documentation]   # = это значение; папка документации в репо MR, первая найденная

safety:
  denied_bash_patterns: ["npm *", "rm *", "*test*"]   # = список из config.example.yaml; запрещённые агенту команды, заменяет список целиком
  output_language: ru             # = ru; пишется только в safety-note, отчёт всегда на русском

storage:
  work_dir: ./.review-agent       # = ./.review-agent; сюда review-agent пишет всё
  retention_days: 7               # = 7; срок logs/, dry-run/, debug/; null — бессрочно
  repo_retention_days: 30         # = 30; срок неиспользуемого кэш-клона; null — бессрочно

usage:                            # учёт расхода
  enabled: true                   # = true; false — ни журнала, ни вопросов о квоте
  source: opencode                # = opencode; opencode | none
  session_list_command: [opencode, session, list]                    # = это значение; + --standalone, если сессии не находятся
  session_export_command: [opencode, session, export, "{session_id}"]  # = это значение; {session_id} обязателен
  quota_windows: [5h, week]       # = [5h, week]; окна лимита подписки для ручного замера
  price_catalog: https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
                                  # = это значение; URL или путь к ценам LiteLLM
  price_overrides:                # = {}; свои цены, заменяют запись справочника целиком
    openai/my-model: {input_cost_per_token: 0.00000125, output_cost_per_token: 0.00001}

gitlab:                           # только для poll
  hostname: git.example.local     # обяз.
  reviewers: [ai-reviewer]        # MR берётся, если любой из них — ревьюер; можно не задавать, если есть у каждого проекта
  review_drafts: false            # = false; ревьюить draft-MR
  min_report_chars: 200           # = 200; более короткий отчёт не публикуется
  claim_ttl_minutes: 240          # = 240; claim старше считается брошенным; ≥ лимита задачи Task Scheduler
  projects:                       # обяз., не пустой
    - path: group/project         # обяз.; без local_repo — кэш-клон в <work_dir>/repos/
    - path: group/other
      local_repo: C:/repos/other  # свой клон; review-agent только делает в нём fetch
      remote: origin              # = origin; только вместе с local_repo
      enabled: true               # = true; false — проект не опрашивается
      reviewers: [ai-reviewer, lead]   # переопределения проекта: заменяют глобальное целиком
      review_drafts: true
      provider: {name: openai, model: gpt-5, reasoning_effort: high}   # весь блок, со своим reasoning_effort
      skills: []                  # [] — без skill'ов при непустом глобальном списке
      prompt:                     # а этот блок — ПО КЛЮЧАМ: незаданные ключи берутся из глобального prompt
        template: ./prompts/backend.md.j2
```

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
