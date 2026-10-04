# Design: managed-repo-cache

## Context

Мотивация — `proposal.md` (Why), требования — `specs/`. Что есть сейчас и на что опирается дизайн:

- **Конфиг проекта.** `GitLabProjectConfig` (`config.py`) требует `local_repo`; `remote` по умолчанию `origin`. `storage` — `work_dir` и `retention_days` (7).
- **Worktree уже отделены от клона.** `managed_worktree()` (`worktree.py`) создаёт checkout в `<work_dir>/tmp/<run_id>/worktree` и удаляет его в `finally`; в самом клоне остаются только метаданные worktree (`.git/worktrees/`), которые снимаются `worktree remove`/`prune`. Перед созданием вызывается `cleanup_orphaned_worktrees(repo, tmp)` и `ensure_commits_available(repo, base, head)` (fetch по SHA из `origin`).
- **Fetch перед ревью.** `_review_one` (`polling.py`) после claim'а делает `ctx.fetch_fn(local_repo, remote, "refs/merge-requests/<iid>/head")`; ошибка не фатальна (предупреждение в отчёт прохода). Целевая ветка не обновляется.
- **Проверка клона.** `_run_locked` до листинга MR отбрасывает проект, если `is_git_repo(local_repo)` ложно. `_housekeeping` (под `poll.lock`) чистит осиротевшие worktree во всех клонах включённых проектов, затем `tmp/`, затем истёкшие `logs/`/`dry-run/`/`debug/`.
- **GitLab.** `get_diff_refs()` уже получает полный объект MR через `glab api .../merge_requests/<iid>`, но возвращает только `base_sha`/`head_sha`; `target_branch` в ответе есть.
- **Авторизация.** Сейчас git-креды review-agent не нужны вообще: клон и его авторизацию готовит человек. `glab` авторизован заранее (`glab auth login --hostname <host>`), хост один на весь конфиг.
- **Окружение.** Windows, проход из-под Task Scheduler через `pythonw` без консоли — любой интерактивный запрос кредов (терминал или GUI Git Credential Manager) = зависший проход.

## Goals / Non-Goals

**Goals**
- Минимальная запись проекта — только `path`; тот же конфиг работает в CI без подготовленных клонов.
- Ни одного секрета в конфиге, URL или файлах review-agent; ни одного интерактивного запроса кредов.
- Перед каждым ревью base и head MR гарантированно доступны без опоры на fetch по SHA.
- Убитый на любом шаге проход не оставляет состояния, которое следующий проход примет за рабочее.

**Non-Goals**
- Ручной режим `review-agent --repo` с URL — остаётся только с локальным путём.
- SSH, несколько хостов GitLab, явный `clone_url` у проекта.
- Partial/shallow clone, sparse checkout, общий кэш между несколькими `work_dir`.
- Обновление всех веток кэша «впрок» — только ветки конкретного MR.
- Ограничение сетевых git-команд самого агента внутри worktree (см. риски).

## Decisions

### 1. Конфиг: `local_repo` необязателен, `remote` — только вместе с ним

```yaml
storage:
  work_dir: ./.review-agent
  retention_days: 7          # logs/, dry-run/, debug/
  repo_retention_days: 30    # NEW: кэш-клоны; null — не удалять
gitlab:
  projects:
    - path: b2c/front-shopping              # кэш-клон
    - path: b2c/front-checkout
      local_repo: C:/repos/front-checkout   # как раньше
      remote: upstream                      # допустим только с local_repo
```

- `GitLabProjectConfig.local_repo: str | None = None`, `remote: str | None = None`; эффективный remote для `local_repo` — `remote or "origin"`, для кэша всегда `origin`.
- `remote` без `local_repo` — `ConfigError` с именем проекта: значение молча игнорировалось бы, а это почти наверняка ошибка в конфиге.
- `repo_retention_days` валидируется как `retention_days` (положительное целое или `null`), по умолчанию 30.
- Отдельный `clone_url` не вводим: хост один, URL однозначно выводится как `https://<gitlab.hostname>/<path>.git`.

### 2. Раскладка: `<work_dir>/repos/`, плоские имена с хэшем

```
<work_dir>/
  poll.lock
  tmp/                                     как сейчас: worktree и файлы прогонов
  repos/                                   NEW: кэш-клоны, по сроку repo_retention_days
    b2c-front-shopping-1a2b3c4d.git/       bare-клон
      review-agent-last-used               метка последнего использования
    .incoming-<run_id>/                    клон в процессе (только во время прохода)
  logs/ dry-run/ debug/
```

- Имя — существующий `_slug(path)` + первые 8 hex SHA-1 от `path`: читаемо и однозначно (`a/b-c` и `a-b/c` дают один slug, но разный хэш). Хост в имени не нужен — он один.
- Папка внутри `work_dir`, а не отдельная настройка: её защищает тот же `poll.lock`, поэтому TTL-удаление, клонирование и fetch никогда не идут параллельно (решение принято в обсуждении; в CI кэшируется `work_dir` целиком).
- Альтернатива — вложенные папки по пути проекта (`repos/b2c/front-shopping.git`): красивее, но TTL-сканирование становится рекурсивным поиском с неочевидной глубиной групп GitLab. Отклонено.

### 3. Кэш — полный bare-клон без тегов

`git clone --bare --no-tags <url> repos/.incoming-<id>` → затем запись метки → `rename` в `repos/<name>.git`.

- **Bare:** рабочие файлы в кэше не нужны — ревью читает код из отдельного worktree; `git worktree add` от bare-репозитория поддерживается штатно.
- **`--no-tags`:** ревью теги не нужны, экономия трафика и места.
- **Не `--mirror`:** на GitLab он тянет все `refs/merge-requests/*` и `refs/keep-around/*` — на живом проекте это тысячи ref'ов.
- **Не partial clone (`--filter=blob:none`):** `git blame`/`git log -p` агента во время ревью начали бы лениво тянуть блобы по сети — медленно, требует кредов внутри ревью, а без консоли может зависнуть. Полный клон — предсказуемее; вернуться к фильтру можно позже, если размер станет проблемой.
- **Атомарность:** клон считается существующим, только если папка с итоговым именем есть, это bare-репозиторий (`git rev-parse --is-bare-repository` = `true`) и в ней есть метка. Метка пишется до `rename`, так что недокачанный клон никогда не получает итоговое имя. Невалидная папка с итоговым именем удаляется и клонируется заново (спека: «Damaged copy»).

### 4. Авторизация: `glab auth git-credential` как credential helper, записанный в конфиг кэш-клона

Клонирование выполняется с

```
git -c credential.helper= -c "credential.helper=!'<abs path to glab>' auth git-credential" \
    clone --bare --no-tags \
    --config credential.helper= --config "credential.helper=!'<abs path to glab>' auth git-credential" \
    https://<host>/<path>.git repos/.incoming-<id>
```

(`-c` действует на сам `clone`, `--config` записывает то же в `config` нового репозитория.)

- Все последующие сетевые git-команды по кэшу — fetch веток MR и запасной `ensure_commits_available` — автоматически берут креды через `glab`, без протаскивания `-c` через каждый вызов.
- Пустой `credential.helper=` первым значением сбрасывает глобальные хелперы — в частности GCM, который на Windows может открыть GUI-окно логина.
- В файл пишется только ссылка на команду, не токен: требование «no credential on disk» выполнено. Путь к `glab` абсолютный (`shutil.which`, как уже делает `gitlab.py`), в одинарных кавычках и с прямыми слэшами — переживает пробелы в `C:/Program Files/...` и запуск из-под Task Scheduler с урезанным PATH.
- Для всех сетевых git-вызовов (кэш и `local_repo`) — окружение `GIT_TERMINAL_PROMPT=0` и `GCM_INTERACTIVE=never`: при отсутствии кредов команда падает сразу, а не ждёт ввода.
- В `local_repo` хелпер **не** подставляется: это клон пользователя со своей работающей авторизацией; переопределять её мы не вправе.

Отклонённые альтернативы:
- `glab repo clone` — делает обычный (не bare) клон по протоколу из настроек glab, а последующие `fetch` всё равно требуют настроенной авторизации git.
- Токен через `http.extraHeader` из `glab auth token`/переменной окружения — review-agent начал бы сам держать секрет в памяти и аргументах процесса (видны в списке процессов).
- «Авторизация git — забота машины» — отвергнуто пользователем: `glab` уже авторизован, второй механизм авторизации — лишняя настройка, особенно в CI.

### 5. Актуализация веток MR: один fetch, refspec зависит от источника

`get_diff_refs()` дополнительно возвращает `target_branch` (тот же ответ API, без нового вызова). Перед ревью — один `git fetch --no-tags origin|<remote> <refspec...>`:

| | кэш (bare) | `local_repo` |
|---|---|---|
| целевая ветка | `+refs/heads/<T>:refs/heads/<T>` | `+refs/heads/<T>:refs/remotes/<remote>/<T>` |
| голова MR | `refs/merge-requests/<iid>/head` (только `FETCH_HEAD`) | то же |

- Целевая ветка — источник `base_sha` (merge-base); в кэше её ref обновляется, поэтому следующие fetch'и инкрементальны. В `local_repo` пишем только в remote-tracking ref — так же, как обычный `git fetch`; локальные ветки, индекс и рабочие файлы не трогаются.
- Исходная сторона — через `refs/merge-requests/<iid>/head`, не через `source_branch`: у MR из форка исходная ветка живёт в другом проекте, MR-ref — всегда в целевом.
- `+` на целевой ветке — переживает force-push в целевую ветку.
- Если общий fetch упал (типично: GitLab удалил MR-ref старого закрытого MR, или удалена целевая ветка) — повторяем по одному refspec'у, каждую неудачу превращаем в предупреждение (как сейчас `fetch_warning`). Фатальной ошибкой для MR это не является: `ensure_commits_available` остаётся последней попыткой (fetch по SHA), и только её неудача валит MR.

### 6. Где это в коде: модуль `repo_source.py`, шаг 4.5 в `_review_one`

```
_review_one(MR)
  1-4  свежие заметки / протухшие claim'ы / свой claim / гонка claim'ов   (без изменений)
  4.5  prepared = ctx.repo_sources.prepare(project, target_branch, iid)   NEW вместо fetch_fn
         local_repo задан -> fetch (реш. 5) в нём
         иначе           -> ensure_cache (реш. 3-4, мемоизация на проход) -> fetch -> touch метки
  5    review_fn(repo_path=prepared.path, base, head, ...)                 (без изменений)
```

- `RepoSources` создаётся на проход (держит `work_dir`, `hostname`, путь к `glab`, мемо «кэш уже проверен/склонирован в этом проходе») и инжектируется в `_PassContext` вместо `fetch_fn` — тесты подменяют его стабом, как сейчас `fetch_fn`.
- Почему после claim, а не на уровне проекта до листинга: клонирование ленивое (проект без кандидатов или невыбранный интерактивно — не качается), первое клонирование большого репозитория уже прикрыто claim'ом, а ошибка клонирования — обычная ошибка MR: claim снимается в `finally`, MR — `failed`, остальные кандидаты продолжаются.
- Проверка `is_git_repo` на уровне проекта в `_run_locked` остаётся только для проектов с `local_repo`.
- `pipeline.run_review` и `managed_worktree` не меняются: им по-прежнему передаётся путь к репозиторию, bare он или нет — им всё равно (`git -C <bare> worktree add` работает).
- `worktree add` выполняется с `GIT_LFS_SKIP_SMUDGE=1`: LFS-ассеты фронтенда агенту не нужны, а smudge — сетевой вызов с кредами посреди подготовки ревью. Касается обоих источников.

### 7. Housekeeping под `poll.lock`: порядок и TTL кэша

В начале прохода, в `_housekeeping`, порядок:

1. Осиротевшие worktree — в клонах `local_repo` включённых проектов (как сейчас) **и** во всех валидных кэш-клонах в `repos/` (включая кэши выключенных и удалённых из конфига проектов — их worktree тоже могут остаться).
2. `clear_tmp` (как сейчас).
3. `repos/.incoming-*` — удаляются безусловно: под lock'ом ни один клон не идёт.
4. Кэш-клоны, у которых `mtime` метки `review-agent-last-used` старше `repo_retention_days`, или метки нет — удаляются (`remove_path`, уже умеет read-only объекты git на Windows); число удалённых — в лог.
5. Истёкшие `logs/`/`dry-run/`/`debug/` (как сейчас; `repos/` в `RETAINED_AREAS` не входит).

- Метка вместо `mtime` папки: на Windows `mtime` каталога меняется только при изменении его прямых детей, а `git fetch` пишет в `objects/pack/` и `refs/` — по папке нельзя понять, использовался ли кэш. Метку `touch`'ает каждый успешный `prepare()`.
- Удаление кэша проекта, который всё ещё в конфиге, — нормально (спека: «Expired copy needed again»): при следующей нужде он склонируется заново.
- Любая ошибка удаления — предупреждение в лог, проход продолжается (существующее требование «Cleanup failure does not fail the pass»).

## Risks / Trade-offs

- **`glab auth git-credential` может отсутствовать в установленной версии glab или вести себя иначе под Git for Windows** (хелпер с `!` исполняется через `sh` из Git for Windows) → первая задача — живая проверка на целевой машине до написания кода (tasks 1.x). Если не работает — остановиться и вернуться к обсуждению авторизации, а не обходить молча.
- **GCM всё равно может всплыть** (например, хелпер настроен в system-level конфиге и пустое значение его не сбрасывает в какой-то версии git) → `GCM_INTERACTIVE=never` + `GIT_TERMINAL_PROMPT=0` + живая проверка из-под задачи Task Scheduler без консоли.
- **Агент внутри worktree кэша может выполнить сетевую git-команду** (`git fetch` не в denylist) и получит креды через `glab` → не регрессия: в `local_repo` у него уже есть креды пользователя; результат — только чтение. Отдельное ограничение сети агента — вне объёма.
- **Размер кэша** — полные bare-клоны фронтенд-репозиториев (сотни МБ на проект) → TTL 30 дней, клон только по требованию; partial clone — возможное улучшение позже.
- **Первое ревью проекта дольше** (клонирование) → прикрыто claim'ом; `claim_ttl_minutes` (240) с запасом больше времени клонирования.
- **Хэш в имени папки** делает её менее очевидной для человека → slug в начале имени сохраняет читаемость.
- **Целевая ветка удалена/переименована** → fetch по одному refspec'у + запасной fetch по SHA (реш. 5); если и он не помог — MR `failed` с понятной причиной.

## Migration Plan

- Существующие конфиги с `local_repo` работают без изменений; `repos/` появляется только при первом проекте без `local_repo`.
- Переход проекта на кэш — удалить `local_repo` (и `remote`, если был) из записи проекта.
- Откат — вернуть `local_repo` в конфиг и при желании удалить `<work_dir>/repos/` вручную; других следов кэш не оставляет.
