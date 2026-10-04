# AGENTS.md — review-agent

Основной источник контекста для любого агента (и человека), который
**разрабатывает** этот проект: принципы, ограничения, карта репозитория,
дорожная карта. Прочитай его перед тем, как планировать или реализовывать
что-либо.

**Каждый change обязан актуализировать документацию — см. п.11.**

Где что читать:

- как пользоваться (команды, флаги, конфиг) — `README.md` и `docs/`;
- как устроено внутри — `docs/architecture.md`;
- почему технические решения именно такие — `docs/decisions.md`;
- история и протоколы проверок — архив OpenSpec
  (`openspec/changes/archive/*/validation-notes.md`).

`openspec/config.yaml` ссылается сюда же — не дублируй туда полный текст,
только короткую отсылку и openspec-специфичный статус.

**Важно не путать:** этот файл описывает разработку самого `review-agent`.
Он НЕ имеет отношения к инструкциям, которые движок передаёт агенту при
каждом прогоне ревью. Агенту передаётся путь к `AGENTS.md` / `CLAUDE.md`
**целевого** (ревьюируемого) репозитория, а ограничения прогона задаются
в сгенерированном `opencode.json` (`harness_config.py`).

## 1. Что это за проект

Автономный агент для код-ревью Merge Request'ов в приватном VPN-only
GitLab. По расписанию опрашивает заданные репозитории, находит MR, где
назначен сконфигурированный ревьюер, проводит код-ревью через
agentic-харнесс с подключаемым провайдером модели (Claude / Codex /
локальная модель) и публикует markdown-отчёт комментарием к MR.

## 2. Целевая среда

- Разработка и первый запуск — локально, **Windows** (нет нативного
  cron — учитывай при выборе инструментов планирования и окружения).
- Архитектура платформонезависимая: тот же код в будущем переедет в
  приватный GitLab CI (Linux-раннер).
- Сейчас — только frontend-репозитории. Архитектура не должна жёстко
  завязываться на фронтенд-специфику: в будущем добавятся backend и
  другие стеки.

## 3. Ключевой эмпирический вывод, определивший архитектуру

Из предыдущего ~3-недельного исследования автора на этом же классе
задачи (бенчмарк: репозиторий `b2c/m`, MR `!544`, base SHA
`d6a9078821e467208acad0791586e577456c6c96`, head SHA
`eb73b471e4292dc40a78203b3c9f68c91ae4dec1`):

- Recall находок у агента **растёт**, когда он свободно исследует
  репозиторий сам (пустой agent без skill и без подготовленного
  контекста нашёл 9 находок — эталон).
- Recall **падает**, когда поведение жёстко задано: подробный
  workflow-skill дал 3 находки, сильно урезанный skill — 2.
- Удаление skill + прямой user-prompt + минимальный runtime-конфиг
  вернули результат к 7 находкам — лучший воспроизводимый baseline.
- Размер контекста НЕ коррелирует с качеством (~91k токенов что в
  хорошем, что в плохом прогоне) — не нужно экономить на context budget
  из опасения навредить, дело не в этом.
- Полный diff и урезанный diff (разные значения `--unified=N`) давали
  одинаково слабый результат — сам по себе diff как промпт не работает
  хорошо, нужен tool-access к дереву файлов.
- Незакрытая на момент решения гипотеза (которую архитектура обходит
  «по построению», не проверяя отдельно): заранее подготовленный
  «brief» с картой MR может анкорить модель и вызывать ранний stopping.

Отсюда — прямые архитектурные требования ниже.

## 4. Движок ревью — архитектурные решения

- **Agentic-харнесс с полным tool-access** к изолированному
  `git worktree`, не diff-pipeline (см. п.3). Headless/non-interactive
  запуск. Харнесс — **OpenCode**; провайдер задаётся одним
  конфиг-значением, не зашит в код; вызов спрятан за тонким адаптером
  (`harness.py`), чтобы харнесс можно было заменить без переделки
  остального pipeline.
- **Reasoning effort — явный конфиг** на провайдера, не default (в
  исходном исследовании это осталось незакрытой переменной).
- **Никакого обязательного workflow-skill.** Методология ревью (что
  проверять: correctness/readability/testability/security/SRP/
  контракты/best practices; SEV Blocker/Major/Minor; допустимы находки
  с неполной уверенностью) идёт ОДНИМ прямым user-prompt'ом на каждый
  запуск (`prompt.py`), не системным конфигом харнесса.
- **Опциональный knowledge-skill слой** — только подсказка («вот
  паттерны, которые стоит иметь в виду»), НЕ ограничение исследования.
  Toggle per-run (0 или несколько skill-файлов, в т.ч. на проект) — для
  A/B-сравнения, как в исходном исследовании.
- **Подготовка контекста — минимальная.** Агент получает только: путь к
  worktree, base/head SHA, title+description MR, путь (не содержимое) к
  `AGENTS.md` → `CLAUDE.md` → `.github/copilot-instructions.md` целевого
  репозитория (первый найденный; не найдено — едем дальше без ошибки) и
  к `docs/`, если есть. НЕ готовим diff.patch, группировку файлов,
  risk-zones, dependency-impact — агент строит это сам. Любые данные,
  которые оркестратор считает о MR (размер изменения и т.п.), агенту не
  передаются. Постобработка отчёта (ссылки на файлы) — детерминированная,
  промпт ради неё не меняется.
- **Безопасность во время ревью — строго read-only.** Нельзя выполнять
  код/тесты/сборку/lint из MR; разрешены read-only `git`, `rg`, чтение
  файлов. Нет веб-доступа и прав на запись в GitLab — комментарий
  публикует только оркестратор. Цикл автономный, без присмотра человека.
  Механизм — denylist опасных bash-шаблонов в сгенерированном
  `opencode.json` (allowlist в OpenCode 2.0.21 не работает) плюс
  обязательный `opencode run --standalone`. Подробности и проверки —
  `docs/decisions.md`.

## 5. Интеграция и инфраструктура

- **GitLab API — `glab`** (официальный CLI GitLab), не `python-gitlab` и
  не свой REST-клиент. Весь доступ к GitLab — в `gitlab.py`. GitLab сам
  рекомендует этот паттерн для AI-агентов.
- **Идемпотентность и состояние — только в GitLab**, не в локальном
  файле: маркер `<!-- ai-review: sha=<head_sha> -->` в комментарии бота,
  ключ `(project, MR iid, head_sha)`; claim «ревью выполняется» — тоже
  комментарий. Это даёт будущую фичу «ревью на новый diff» без
  переделки состояния.
- **Фильтр опроса** — только MR, где заданный пользователь (или список)
  указан как reviewer.
- **Планировщик — не пишем свой cron/daemon.** Вся работа — один
  идемпотентный CLI-вызов без внутреннего state. Кто вызывает по
  расписанию (Task Scheduler сейчас, cron/systemd/GitLab CI `schedule`
  потом), entrypoint не знает и от этого не зависит.
- **Креды не в конфиге:** провайдер авторизован в OpenCode, GitLab — в
  `glab`; git кэш-клонов авторизуется через `glab auth git-credential`.

## 6. Рассмотренные и отклонённые готовые решения

Чтобы не переизобретать этот анализ заново:

- **PR-Agent / Qodo Merge** — архитектурно diff-pipeline с фиксированной
  методологией промпта. Отклонён: именно эта архитектура эмпирически
  снижает recall под эту задачу (п.3).
- **`gitlab-review-agent`** (github.com/antlss) — агентский подход
  (read_file/search_code/multi_diff tool-verification loop), близко к
  нашей архитектуре. НЕ взят за основу: ~15 звёзд, молодой,
  Linux/Docker/webhook, Windows не поддерживается. Паттерны на заметку
  (идеи, не код): инкрементальный «smart base SHA» re-review;
  verification loop перед записью finding (уже встроен как принцип в
  промпт); авто-resolve старых AI-тредов; self-generating best-practices
  ruleset из истории ревью и фидбека.
- **`pr-review-bot`** (github.com/antoniooreany) — тонкая обёртка над
  PR-Agent. Отклонён по той же причине, плюс не community-proven.

Вывод: проверенный сообществом слой — не нишевые review-боты, а
инструменты уровнем ниже: `glab` и agentic-харнесс. Основа решения —
тонкая собственная обвязка поверх них.

## 7. Дорожная карта

Реализуется отдельными OpenSpec change (не один монолит). Следующий
change пропозится только после `apply` и ручной проверки предыдущего на
реальном MR. Полное описание, итоги проверок и найденные баги — в
`proposal.md` / `validation-notes.md` архива каждого change.

| # | Change | Суть | Архив |
| --- | --- | --- | --- |
| 1 | `core-review-engine` | worktree + read-only агент + прямой промпт → отчёт в файл (ручной режим) | `archive/2026-10-02-core-review-engine` |
| 2 | `gitlab-polling-and-publishing` | `poll`: поиск MR по ревьюеру, интерактив/`--all`, публикация с маркером, `--dry-run`, lock | `archive/2026-10-02-gitlab-polling-and-publishing` |
| 3 | `scheduling-and-multi-repo-config` | Task Scheduler, рабочая папка с очисткой, лог прохода, claim, переопределения проекта | `archive/2026-10-02-scheduling-and-multi-repo-config` |
| 4 | `managed-repo-cache` | `local_repo` необязателен: кэш bare-клонов с авторизацией через `glab` | `archive/2026-10-04-managed-repo-cache` |
| 5 | `review-usage-accounting` | журнал расхода, замер квоты в интерактиве, `review-agent usage` | `archive/2026-10-04-review-usage-accounting` |
| 6 | `gitlab-file-links` | ссылки на файлы в отчёте ведут на GitLab в отревьюенном коммите | `archive/2026-10-04-gitlab-file-links` |
| 7 | `restructure-documentation` | README как справочник, `docs/`, карта и правило актуализации документации | в работе |

Пути архивов — относительно `openspec/changes/`.

Известные пробелы: количественное сравнение находок со списком A–M из
исходного исследования не сделано (исходный документ недоступен на
текущей машине) — см. `validation-notes.md` change 1.

**Бэклог (отдельные будущие change):**

- прекращать проход после фатальной ошибки провайдера (лимит/авторизация),
  не пробуя остальные MR (иначе claim создаётся и удаляется на каждом MR);
- рост профиля OpenCode (`opencode.db`, ~1,3 МБ на ревью) — не чистится;
- авто-resolve AI-тредов при фиксе кода разработчиком;
- self-generating best-practices ruleset из истории ревью+фидбека;
- ревью на новый diff после правок (повторный прогон той же MR);
- backend/другие стеки помимо frontend;
- `skills` с относительными путями: путь передаётся агенту как есть, а
  агент работает из worktree — относительный путь не находится
  (сейчас в документации требуются абсолютные пути); `provider.api_key_env`
  и `safety.output_language` движком фактически не используются —
  решить, убрать или задействовать;
- автоматический замер % лимитов подписки по каждому ревью (сейчас —
  ввод остатка в интерактивном прогоне): прямой `wham/usage` с
  OAuth-токеном OpenCode v2 не работает (401: свой OAuth-клиент, account
  id в JWT зашифрован; маскироваться под Codex не будем); в debug-логе
  OpenCode (`--print-logs --log-level debug`) заголовков `x-codex-*` нет —
  проверено. Остался кандидат: Codex CLI как харнесс (`codex exec --json`
  отдаёт rate limits по прогону);
- учёт расхода: передавать `opencode run --session ses_<run_id> --title
  "<project>!<iid>"` (проверено: OpenCode принимает свой ID и заголовок) и
  экспортировать сессию прогона напрямую, без поиска по пути worktree;
- учёт расхода, продолжение: отправка записей по OTLP в collector
  (Grafana/Langfuse) для этапа CI, строка расхода в комментарии к MR,
  LiteLLM-шлюз как корпоративный учёт расходов на API, выбор модели по
  размеру MR на данных журнала.

## 8. Процесс работы над самим проектом

- Используем OpenSpec (`.claude/skills/openspec-*`, команды `/opsx:*`).
  Один change = одна логическая порция работы, не смешиваем несколько
  change в одной пачке задач.
- Change-артефакты (`proposal.md` / `design.md` / `tasks.md` /
  specs-дельты) — файлы в репозитории, не зависят от памяти чата. Новую
  сессию для следующего change открывать нормально — она подхватит
  состояние через `openspec list` / `status` и этот файл.
- Каждый change актуализирует документацию — п.11.
- После значимых изменений — коммитить и пушить, иначе результат не
  переживёт завершение сессии.
- Вывод агента (ревью-отчёты, результаты OpenSpec-сессий в этом
  репозитории, документация) — по умолчанию на русском языке.

## 9. Команды разработки

```bash
pip install -e ".[dev]"     # пакет + pytest (Python 3.10+)
pytest                      # все тесты; харнесс и glab подменяются стабами
review-agent --help         # ручное ревью диапазона коммитов (без GitLab)
review-agent poll --help    # проход опроса GitLab
review-agent poll --dry-run # первый безопасный прогон на реальном GitLab
review-agent poll --all --debug   # проход без вопросов, артефакты прогонов — в <work_dir>/debug/
review-agent usage                # сводка расхода (--by model, --wide, --format csv)
```

```powershell
.\scripts\register-task.ps1 -DryRun -IntervalMinutes 15   # задача Task Scheduler, сначала dry-run
.\scripts\register-task.ps1                               # боевая задача (обновляет ту же)
.\scripts\register-task.ps1 -Remove                       # удалить задачу
Get-ScheduledTaskInfo -TaskName review-agent-poll          # LastTaskResult: 0/1/2 = код выхода poll
```

Всё, что пишет review-agent, — в `storage.work_dir` (по умолчанию
`./.review-agent`), раскладка — `docs/work-dir.md`. Внешние инструменты
(не Python-зависимости): `git`, `opencode`, `glab` (для `poll`, заранее
авторизованный). Линтера в проекте пока нет.

## 10. Карта репозитория

```
src/review_agent/        код (таблица модулей ниже)
tests/                   pytest: test_<модуль>.py, test_register_script.py, test_docs.py
  conftest.py            временные git-репозитории для тестов
  fixtures/opencode/     реальный экспорт сессии OpenCode (разбор токенов)
  fixtures/file_links/   отчёты с путями (переписывание ссылок)
scripts/register-task.ps1  регистрация задачи Task Scheduler
docs/                    документация для пользователя (см. ниже)
openspec/specs/          действующие спецификации по capability
openspec/changes/        текущие change; archive/ — завершённые, с validation-notes.md
openspec/config.yaml     контекст и правила OpenSpec (отсылает сюда)
config.example.yaml      образец конфига (config.yaml — личный, в .gitignore)
README.md                справочник пользователя: установка, команды, флаги, ключи конфига
```

**Модули `src/review_agent/`:**

| Модуль | Ответственность |
| --- | --- |
| `cli.py` | точки входа `review-agent`, `poll`, `usage`; ручной режим |
| `config.py` | загрузка и проверка конфига, значения по умолчанию, настройки проекта |
| `pipeline.py` | движок одного ревью: worktree → промпт → `opencode.json` → харнесс → учёт → уборка |
| `worktree.py` | изолированный `git worktree`, гарантированное удаление, сироты |
| `prompt.py` | промпт ревью (единственное место методологии), поиск инструкций целевого репо |
| `harness_config.py` | `opencode.json` с read-only агентом, safety-note |
| `harness.py` | вызов харнесса подпроцессом |
| `report.py` | отчёт ручного режима в файл |
| `polling.py` | проход `poll` целиком, коды выхода |
| `gitlab.py` | всё общение с GitLab через `glab` |
| `publishing.py` | маркеры ревью и claim'а, тело комментария, проверка отчёта (чистая логика) |
| `file_links.py` | пути в отчёте → ссылки GitLab (чистая логика) |
| `repo_source.py` | `local_repo` или кэш-клон, fetch веток MR, уборка кэша |
| `housekeeping.py` | раскладка `work_dir`, очистка |
| `lock.py` | `poll.lock` |
| `passlog.py` | лог прохода |
| `proc.py` | флаги дочерних процессов (без окна) |
| `quota_prompt.py` | интерактивный ввод остатка лимитов |
| `review_stats.py` | размер изменения и число находок для журнала |
| `usage_source.py` | токены из сессий OpenCode |
| `usage_ledger.py` | журнал `usage/ledger.jsonl` |
| `usage_prices.py` | цены LiteLLM и переопределения |
| `usage_summary.py` | `review-agent usage` |
| `__init__.py`, `__main__.py` | пакет, `python -m review_agent` |

**Capability → модули** (`openspec/specs/<capability>/spec.md`):

| Capability | Модули |
| --- | --- |
| `mr-review-engine` | `pipeline.py`, `worktree.py`, `prompt.py`, `harness.py`, `harness_config.py`, `report.py` |
| `mr-discovery` | `polling.py`, `gitlab.py` |
| `review-polling-run` | `polling.py`, `passlog.py`, `lock.py`, `cli.py` |
| `review-publishing` | `publishing.py`, `file_links.py`, `gitlab.py`, `polling.py` |
| `review-claim` | `publishing.py`, `gitlab.py`, `polling.py` |
| `project-review-settings` | `config.py` |
| `local-artifact-lifecycle` | `housekeeping.py`, `lock.py`, `passlog.py` |
| `managed-repo-cache` | `repo_source.py` |
| `review-usage-accounting` | `usage_source.py`, `usage_ledger.py`, `usage_prices.py`, `usage_summary.py`, `review_stats.py`, `quota_prompt.py` |
| `scheduled-task-registration` | `scripts/register-task.ps1` |

**`docs/`:**

| Файл | Что там |
| --- | --- |
| `docs/configuration.md` | каждый ключ конфига: тип, default, смысл; переопределения проекта |
| `docs/polling.md` | проход `poll`: шаги, режимы, маркер, claim, lock, кэш, ссылки, лог |
| `docs/work-dir.md` | раскладка рабочей папки, что и когда удаляется |
| `docs/scheduling.md` | Task Scheduler: регистрация, параметры, мониторинг, коды результата |
| `docs/usage-accounting.md` | журнал расхода, замер квоты, `review-agent usage` |
| `docs/architecture.md` | как устроено: поток прогона, внешние процессы, модули |
| `docs/decisions.md` | технические решения: что → почему → где проверено → где в коде |
| `docs/troubleshooting.md` | симптом → причина → что делать |

## 11. Актуализация документации

**Правило:** change считается завершённым, только когда `README.md`,
`docs/`, этот файл, `config.example.yaml` и `openspec/config.yaml`
описывают состояние **после** него. Если документация не затронута, это
записывается явно (в `tasks.md` или итоговом сообщении: «документация не
затронута: <почему>»), а не подразумевается молча. Задачи по документации
входят в ту группу `tasks.md`, чью работу они описывают, а не
откладываются в конец.

| Что изменилось | Что обновить |
| --- | --- |
| флаг или подкоманда CLI | `README.md` (таблица флагов), `docs/<тема>.md` |
| параметр `register-task.ps1` | `README.md`, `docs/scheduling.md` |
| ключ конфига или его default | `README.md` (пример конфига), `docs/configuration.md`, `config.example.yaml` |
| новый файл или папка в `work_dir`, срок хранения | `README.md` (дерево), `docs/work-dir.md` |
| поведение `poll` (шаги, маркер, claim, коды выхода) | `docs/polling.md`, коды выхода в `README.md` |
| новый модуль, перенос ответственности | п.10 (модули, capability → модули), `docs/architecture.md` |
| новая capability в `openspec/specs/` | п.10 (capability → модули) |
| обходной путь или решение, проверенное вживую | `docs/decisions.md` (со ссылкой на `validation-notes.md`) |
| новый симптом сбоя и способ его исправить | `docs/troubleshooting.md` |
| новая внешняя зависимость или проверенная версия | `README.md` (зависимости), п.5 при изменении интеграции |
| изменение принципов или ограничений | п.3–5 этого файла |
| пункт бэклога сделан или появился новый | п.7 (бэклог) |
| архивация change | п.7 (строка таблицы), статус в `openspec/config.yaml` |

**Стиль:** `README.md` и `docs/` (кроме `decisions.md`) описывают текущее
состояние — без «Change N», «раньше было», рассказов о ходе проверки.
История живёт в архиве OpenSpec. Подробности — только в `docs/`, в README
— выжимка со ссылкой. Документация пишется на русском.

**Страховка:** `tests/test_docs.py` проверяет, что каждый флаг CLI и
параметр `register-task.ps1` упомянут в `README.md`, пример конфига из
README загружается настоящим загрузчиком, а относительные ссылки в
`README.md`, `AGENTS.md` и `docs/*.md` ведут на существующие файлы. Полноту
ключей конфига тест не проверяет — новый ключ добавляй по таблице выше.
