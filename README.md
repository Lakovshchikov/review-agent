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

3. Установить agentic-харнесс, который вы собираетесь использовать
   (по умолчанию конфиг рассчитан на [OpenCode](https://opencode.ai)).
   `review-agent` вызывает харнесс как внешний процесс — он не входит
   в зависимости этого пакета.

## Конфигурация

Скопируйте `config.example.yaml` в `config.yaml` и отредактируйте:

```yaml
provider:
  name: anthropic              # информационное имя — харнесс сам резолвит провайдера
  model: claude-sonnet-4-5
  reasoning_effort: medium     # явный конфиг, не оставляем harness-у default
  api_key_env: ANTHROPIC_API_KEY   # имя переменной окружения с ключом — сам ключ сюда не кладём

harness:
  command:                     # шаблон команды харнесса; плейсхолдеры подставляются адаптером
    - opencode
    - run
    - --model
    - "{model}"                # -> "<provider.name>/<provider.model>"
    - --agents-file
    - "{agents_file}"          # -> путь к сгенерированному safety/runtime-конфигу
    - --prompt-file
    - "{prompt_file}"          # -> путь к отрендеренному review-промпту
    # Доступны также {reasoning_effort} и {worktree_path}, если харнессу
    # нужно передать их отдельным флагом. Reasoning effort дополнительно
    # всегда прокидывается в переменную окружения REVIEW_AGENT_REASONING_EFFORT
    # для харнессов, которые читают его оттуда, а не из флага.

report:
  output_path: "./reports/review-{run_id}.md"   # {run_id} подставляется автоматически

skills: []    # опциональные knowledge-skill файлы (справочник паттернов, не ограничение)

safety:
  output_language: ru
  forbidden_commands: [yarn, npm, pnpm, npx, test, lint, stylelint, build, typecheck, tsc]
```

### Переключение провайдера

Смена провайдера — только правка `provider`/`harness.command` в конфиге,
без изменения кода. Пример для локальной модели через Ollama:

```yaml
provider:
  name: ollama
  model: qwen2.5-coder:32b
  reasoning_effort: high
harness:
  command: [opencode, run, --model, "{model}", --agents-file, "{agents_file}", --prompt-file, "{prompt_file}"]
```

## Безопасность: read-only permission setup

На каждый прогон генерируется `harness-agents.md` (в scratch-директории
прогона, не внутри ревьюируемого worktree) со списком запрещённых команд
из `safety.forbidden_commands` и инструкцией не выполнять код/тесты/
сборку/lint, не выходить в общий интернет и не публиковать ничего в
GitLab самостоятельно.

**Важно:** этот файл — инструкция для модели, а не технический sandbox.
Фактическое принудительное ограничение зависит от **нативного механизма
permission/tool-restriction выбранного харнесса** (см. design.md, risk
"harness's tool-permission configuration may not cleanly separate read
from write/execute"). Для OpenCode это нужно настроить отдельно согласно
его актуальной документации (точный синтаксис permission-конфига не
фиксирован в этом репозитории, т.к. не был проверен вживую в этой
сессии). **Перед первым реальным прогоном на закрытом репозитории
обязательно вручную проверьте**, что харнесс в вашей конфигурации
действительно отказывается выполнять команду из списка
`forbidden_commands` — см. task 3.2/8.1 в
`openspec/changes/core-review-engine/tasks.md`, этот пункт сознательно
не отмечен как полностью завершённый по этой причине.

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
   `--repo` не меняется)
3. Генерируется safety/runtime-конфиг харнесса и review-промпт (путь к
   `AGENTS.md`/`CLAUDE.md`/`docs/` целевого репозитория — если найдены —
   передаются харнессу как путь, не как содержимое)
4. Вызывается харнесс, его вывод сохраняется как markdown-отчёт по пути
   из `report.output_path`
5. Worktree удаляется — независимо от того, успешно ли прошёл прогон

GitLab, идемпотентность и шедулинг — вне скоупа этого change.

## Тесты

```bash
pytest
```

Все модули, кроме реального вызова харнесса, покрыты тестами без
внешних зависимостей (харнесс в тестах подменяется стабом). Единственная
проверка, которая требует реального харнесса, реального провайдера
модели и сетевого доступа к вашему GitLab — ручной прогон на бенчмарк-MR,
описанный в tasks.md (раздел 8).
