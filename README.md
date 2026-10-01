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
  # ALLOWLIST паттернов для bash (не denylist!) — см. раздел ниже.
  allowed_bash_patterns:
    - "git diff*"
    - "git show*"
    - "git log*"
    - "git blame*"
    - "git grep*"
    - "git ls-files*"
    - "git status*"
    - "git branch*"
    - "rg *"
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
        "bash": { "git diff*": "allow", "...": "allow", "*": "deny" },
        "edit": "deny",
        "webfetch": "deny",
        "websearch": "deny"
      }
    }
  }
}
```

`bash` — это **allowlist паттернов, не denylist команд**: явно
перечисленные read-only паттерны разрешены, `"*"` запрещает всё
остальное по умолчанию. Это надёжнее перечисления запрещённых имён
команд, которое легко обойти чуть другим вызовом. Запись в worktree
безопасна, потому что весь worktree — одноразовая копия, удаляемая
после прогона (см. `worktree.py`), и `opencode.json` — не то же имя
файла, что `AGENTS.md`/`CLAUDE.md`, так что не перекрывает инструкции
самого ревьюируемого репозитория.

**Что подтверждено вживую, а что нет:**

- ✅ **Deny подтверждён**: на реальном локальном прогоне (Ollama,
  ограниченный агент) модель, которую прямо попросили выполнить
  запрещённую команду (`npm test`), сообщила об отсутствии bash-доступа
  и **не выполнила** её — побочного эффекта (файла-маркера) не возникло.
- ⚠️ **Allow-сторона НЕ подтверждена до конца**: доступные здесь локальные
  модели, похоже, получают от OpenCode не классический bash-инструмент,
  а sandboxed JS "Code Mode" (`execute`), который может вообще не иметь
  реального доступа к `git`/shell независимо от `permission.bash` — это
  не задокументировано в публичных доках OpenCode на момент проверки и
  не воспроизведено с облачным провайдером (Claude/Codex) в этом
  окружении. **Перед первым реальным прогоном обязательно вручную
  проверьте на вашем реальном провайдере**, что `git log`/`git diff` и
  другие разрешённые паттерны реально работают через bash у
  ограниченного агента — иначе движок физически не сможет исследовать
  репозиторий. Детали и инструкция — `openspec/changes/core-review-engine/tasks.md`,
  задача 3.2, намеренно не отмечена как полностью завершённая по этой причине.

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
`test_harness.py`). Харнесс в тестах подменяется стабом — единственная
проверка, которая требует реального харнесса, реального провайдера и
сетевого доступа к вашему GitLab, — ручной прогон на бенчмарк-MR,
описанный в `tasks.md` (раздел 8).
