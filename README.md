# review-agent

Автономный движок код-ревью merge request'ов: изолированный `git worktree`
+ agentic-харнесс с подключаемым провайдером модели + прямой review-промпт
→ markdown-отчёт. Полный контекст архитектурных решений — в `/AGENTS.md`.

Этот change (`core-review-engine`) реализует только сам движок — запуск
локально, dry-run, без GitLab и без шедулинга. Это появится в следующих
change (см. `AGENTS.md`, раздел 7).

## Установка

1. Python 3.10+
2. Установить сам пакет и зависимости (editable-режим для разработки):

   ```bash
   pip install -e ".[dev]"
   ```

3. Установить [OpenCode](https://opencode.ai) (`opencode --version`
   должен работать в PATH) и настроить провайдера: `opencode auth login`
   для облачного провайдера (Claude/Codex), или `ollama pull <model>`
   для локальной модели. `review-agent` вызывает харнесс как внешний
   процесс — он не входит в зависимости этого пакета.

## Конфигурация

Скопируйте `config.example.yaml` в `config.yaml` и отредактируйте.
Синтаксис ниже сверен с реальным OpenCode CLI v2.0.21 (`opencode --help`,
`opencode run --help`, `opencode debug agents`, схема
`opencode.ai/config.json`) — не предположение, а проверенные факты:

```yaml
provider:
  name: anthropic              # информационное имя — харнесс сам резолвит провайдера
  model: claude-sonnet-4-5
  # Явный reasoning effort — не оставляем харнессу default. У OpenCode
  # это "variant" в строке provider/model#variant (#high/#medium/#low).
  # ВАЖНО: variant есть не у всех моделей — для локальных (Ollama) без
  # вариантов любой суффикс ломает вызов ("Variant unavailable",
  # проверено вживую). Для них ставьте явно `reasoning_effort: null`.
  reasoning_effort: medium
  api_key_env: ANTHROPIC_API_KEY   # имя переменной окружения с ключом — сам ключ сюда не кладём

harness:
  command:                     # плейсхолдеры подставляются адаптером
    - opencode
    - run
    - --standalone              # ОБЯЗАТЕЛЕН — см. раздел "Безопасность" ниже
    - --agent
    - "{agent}"                # -> harness.agent_name (см. ниже про safety)
    - --model
    - "{model}"                # -> "<provider.name>/<provider.model>#<reasoning_effort>"
    - "{prompt}"                # -> отрендеренный промпт ЦЕЛИКОМ как текст
                                 #    (opencode run принимает сообщение позиционно,
                                 #     НЕ файлом — флага --prompt-file не существует)
  agent_name: reviewer          # имя read-only агента, генерируемого в opencode.json

report:
  output_path: "./reports/review-{run_id}.md"   # {run_id} подставляется автоматически

skills: []    # опциональные knowledge-skill файлы (справочник паттернов, не ограничение)

safety:
  output_language: ru
  # DENYLIST паттернов для bash (не allowlist!) — см. раздел ниже, почему.
  denied_bash_patterns:
    - "npm *"
    - "npx *"
    - "yarn *"
    - "pnpm *"
    - "bun *"
    - "*test*"
    - "*build*"
    - "*lint*"
    - "tsc*"
    - "node *"
    - "python *"
    - "rm *"
    - "curl *"
    - "wget *"
    - "sh *"
    - "bash *"
    - "powershell *"
    - "cmd *"
```

### Переключение провайдера

Смена провайдера — только правка `provider` в конфиге, без изменения
кода. Пример для локальной модели через Ollama:

```yaml
provider:
  name: ollama
  model: qwen2.5-coder:32b
  reasoning_effort: null   # у локальных моделей обычно нет variant'ов
```

## Безопасность: read-only permission setup

На каждый прогон в **корень worktree** (не в scratch — проверено, что
OpenCode сам подхватывает `opencode.json` из текущей директории, флага
`--agents-file` не существует) генерируется `opencode.json`,
определяющий ограниченного агента (`harness.agent_name`, по умолчанию
`reviewer`):

```json
{
  "agent": {
    "reviewer": {
      "permission": {
        "bash": { "npm *": "deny", "rm *": "deny", "...": "deny" },
        "edit": "deny",
        "webfetch": "deny",
        "websearch": "deny"
      }
    }
  }
}
```

`bash` — это **DENYLIST паттернов, не allowlist**. Изначально
задумывался allowlist (deny-by-default принципиально безопаснее
denylist'а, который можно обойти чуть другим вызовом команды) — но
**проверено вживую на реальном облачном провайдере** (`openai/gpt-5.6-terra`
через настоящий OpenCode v2.0.21, не стаб): если добавить `"*": "deny"`
в карту `permission.bash`, bash-инструмент становится **полностью
недоступен модели**, даже для паттернов, явно помеченных `"allow"`. Без
catch-all'а неперечисленная команда разрешена по умолчанию. То есть
рабочего способа сказать OpenCode "запрети всё, кроме этих паттернов" в
этой версии нет — пришлось перейти на denylist конкретных опасных
паттернов, принимая его известную слабость (другая формулировка той же
команды может его обойти).

Запись `opencode.json` в worktree безопасна, потому что весь worktree —
одноразовая копия, удаляемая после прогона (см. `worktree.py`), и
`opencode.json` — не то же имя файла, что `AGENTS.md`/`CLAUDE.md`, так
что не перекрывает инструкции самого ревьюируемого репозитория.

Отдельная находка: без флага `--standalone` `opencode run` может
использовать долгоживущий background-сервис, который не знает про
только что созданный worktree (каждый прогон — новая директория) и
падает с `Agent not found` на только что сгенерированном агенте —
`--standalone` в шаблоне команды выше обязателен именно поэтому.

**Что подтверждено вживую (на реальном облачном провайдере, не стабе):**

- ✅ **Allow-сторона**: `git log`, `git status` и подобные (не входящие в
  denylist) реально выполняются через bash у ограниченного агента и
  возвращают настоящий вывод
- ✅ **Deny-сторона**: явно запрещённый паттерн (`npm *`) реально
  блокируется (`Permission denied: shell`), побочного эффекта
  (файла-маркера) не возникает

Единственная оставшаяся оговорка: denylist в принципе слабее allowlist'а
(это известный компромисс, не что-то упущенное) — расширяйте
`denied_bash_patterns`, если заметите, что модель находит обходной путь
через ещё не перечисленный интерпретатор/команду.

## Запуск

```bash
review-agent \
  --repo /path/to/local/clone \
  --base <base-sha> \
  --head <head-sha> \
  --mr-title "Заголовок MR" \
  --mr-description "Описание MR" \
  --config config.yaml \
  --scratch-dir .review-agent-scratch
```

(эквивалентно: `python -m review_agent ...`)

Что происходит за один вызов:

1. Чистится возможный "осиротевший" worktree от предыдущего прерванного
   прогона
2. Создаётся изолированный `git worktree` на `--head` (репозиторий в
   `--repo` не меняется; путь резолвится в абсолютный до вызова git —
   см. комментарий в `worktree.py` про относительные `--scratch-dir`)
3. В worktree генерируется `opencode.json` (read-only агент) и
   отдельно, в scratch — safety-note (документация) и review-промпт
   (путь к `AGENTS.md`/`CLAUDE.md`/`docs/` целевого репозитория — если
   найдены — передаются харнессу как путь, не как содержимое)
4. Вызывается харнесс (команда резолвится через PATH — на Windows это
   важно: многие npm-инструменты ставятся как `.CMD`/`.ps1`-обёртки, и
   `subprocess` без `shell=True` не находит их по голому имени), его
   вывод сохраняется как markdown-отчёт по пути из `report.output_path`
5. Worktree удаляется — независимо от того, успешно ли прошёл прогон

GitLab, идемпотентность и шедулинг — вне скоупа этого change.

## Тесты

```bash
pytest
```

Прогнан и зелёный как на Linux, так и на Windows (целевая платформа
первого запуска) — кросс-платформенные различия в путях (`\` vs `/`) и
резолвинге команд через PATH пойманы и закрыты тестами (`test_worktree.py`,
`test_harness.py`). Харнесс в тестах подменяется стабом.

Отдельно от юнит-тестов движок целиком прогнан вживую (настоящий
`review-agent` CLI + настоящий OpenCode + реальный облачный провайдер,
без стаба) на одноразовом тестовом репозитории — это и выявило разницу
между allowlist/denylist и необходимость `--standalone`, описанные выше.
Единственное, что остаётся сделать на реальном бенчмарк-MR из вашего
GitLab (VPN-only, недоступен из автоматических прогонов) — задачи
8.1/8.2 в `tasks.md`.
