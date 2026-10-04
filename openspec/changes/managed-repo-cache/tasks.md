# Tasks

## 1. Живая проверка авторизации через glab (до кода)

- [x] 1.1 На целевой Windows-машине проверить `glab auth git-credential`:
  - `glab --version` — записать версию;
  - `git -c credential.helper= -c "credential.helper=!'<abs path to glab>' auth git-credential" clone --bare --no-tags https://<host>/<path>.git <scratch>` с `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never` — клон успешен, окно GCM не появилось;
  - в получившемся клоне нет токена: поиск значения `glab auth token`/`glab config get token` по файлам клона пуст.

  Проверка: результат и версия glab записаны в `validation-notes.md` этого change. Если хелпер не работает — **остановиться** и вернуться к обсуждению авторизации (design.md, риски), задачи ниже не начинать.
- [x] 1.2 Повторить `git fetch` в том же клоне с `--config`, записанным при клонировании (без `-c` в команде), — креды берутся из конфига клона. Проверка: результат записан в `validation-notes.md`.

## 2. Конфиг

- [x] 2.1 Сделать `local_repo` и `remote` в `GitLabProjectConfig` необязательными (design.md, решение 1): `remote` без `local_repo` → `ConfigError` с `gitlab.projects[i]`; эффективный remote `local_repo` — `origin` по умолчанию. Проверка — тесты в `tests/test_config.py`: проект только с `path` грузится; `remote` без `local_repo` отвергается; старый конфиг с `local_repo`/`remote` грузится как раньше.
- [x] 2.2 Добавить `storage.repo_retention_days` (по умолчанию 30, положительное целое или `null`, `0`/отрицательное/`true` → `ConfigError`). Проверка — тесты в `tests/test_config.py`.
- [x] 2.3 Обновить `config.example.yaml` (проект только с `path`, комментарий о кэше и `repo_retention_days`, `remote` только с `local_repo`) и раздел README «Конфигурация». Проверка: тест загрузки примера зелёный.

## 3. GitLab: целевая ветка MR

- [x] 3.1 `get_diff_refs()` возвращает также `target_branch` из того же ответа API (без нового вызова); отсутствие поля → `GitLabError`. Проверка — тесты в `tests/test_gitlab.py` на стабе `glab`: ветка возвращается; число вызовов `glab` не изменилось.

## 4. Git-операции: fetch нескольких refspec и окружение без интерактива

- [x] 4.1 В `worktree.py`: функция fetch нескольких refspec одним вызовом (`--no-tags`) с запасным вариантом «по одному refspec'у, неудачи — списком предупреждений» (design.md, решение 5); для всех сетевых git-вызовов (`fetch`, `ensure_commits_available`) — окружение `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never`. Проверка — тесты в `tests/test_worktree.py` на реальных временных репозиториях (локальный bare-«сервер» как `origin`): оба refspec'а приходят одним fetch; при отсутствующем MR-ref целевая ветка всё равно обновлена и возвращено предупреждение.
- [x] 4.2 `create_worktree` выполняет `worktree add` с `GIT_LFS_SKIP_SMUDGE=1`; проверить, что `managed_worktree` работает с bare-репозиторием как источником. Проверка — тест в `tests/test_worktree.py`: worktree от bare-клона создаётся, содержит файлы head-коммита и удаляется с очисткой метаданных в bare-клоне.

## 5. Модуль источника репозитория

- [x] 5.1 Создать `src/review_agent/repo_source.py` (design.md, решения 2–4):
  - имя кэша: `_slug(path)` + 8 hex SHA-1 от `path` + `.git` (перенести `_slug` из `polling.py` в общее место);
  - URL `https://<hostname>/<path>.git`;
  - клонирование `--bare --no-tags` в `repos/.incoming-<id>` с `-c`/`--config` для хелпера `glab` (абсолютный путь через `shutil.which`, в кавычках, прямые слэши), запись метки, `rename`;
  - проверка валидности (bare + метка), невалидный — удалить и склонировать заново.

  Проверка — тесты `tests/test_repo_source.py` (клонирование из локального bare-«сервера» через подменяемый URL/хелпер): имена различают `a/b-c` и `a-b/c`; после клона нет рабочих файлов; прерванный клон (папка `.incoming-*`) не используется; повреждённый кэш переклонируется; хелпер записан в `config` кэша, токенов в файлах нет.
- [x] 5.2 `RepoSources.prepare(project, target_branch, iid)` (design.md, решения 5–6): для `local_repo` — fetch с refspec'ами `refs/remotes/<remote>/<T>` + MR-ref; для кэша — `ensure_cache` (мемоизация на проход), fetch `refs/heads/<T>` + MR-ref, `touch` метки; возвращает путь и предупреждения. Проверка — тесты в `tests/test_repo_source.py`: локальная ветка с именем целевой и рабочие файлы `local_repo` не меняются; base, добавленный в целевую ветку после клонирования, доступен после `prepare`; второй `prepare` того же проекта за проход не клонирует повторно; метка обновлена.

## 6. Встраивание в проход опроса

- [x] 6.1 В `polling.py` заменить `fetch_fn` на `RepoSources` в `_PassContext` (шаг 4.5 `_review_one`, design.md, решение 6): путь из `prepare()` идёт в `review_fn`; ошибка клонирования → MR `failed` с причиной, claim снимается, остальные кандидаты продолжаются; предупреждения fetch — в причину/лог как сейчас. Проверка — тесты в `tests/test_polling.py` со стабом `RepoSources`: успешный путь, ошибка клонирования, предупреждение fetch.
- [x] 6.2 Проверку `is_git_repo` в `_run_locked` выполнять только для проектов с `local_repo`; проект без `local_repo` и без кандидатов не вызывает `prepare`; в интерактивном режиме невыбранный проект не клонируется. Проверка — тесты в `tests/test_polling.py`.
- [x] 6.3 Обновить README (раздел о `poll`: откуда берётся репозиторий, ленивое клонирование, авторизация через `glab`, актуализация целевой ветки и MR-ref). Проверка: описанное совпадает с поведением тестов 6.1–6.2.

## 7. Housekeeping кэша

- [x] 7.1 Добавить `repos` в раскладку `WorkDir` и docstring `housekeeping.py`; реализовать уборку по design.md, решение 7: осиротевшие worktree во всех валидных кэшах, удаление `repos/.incoming-*`, удаление кэшей с меткой старше `repo_retention_days` или без метки, `null` — без удаления по возрасту; `repos/` не входит в `RETAINED_AREAS`. Проверка — тесты в `tests/test_housekeeping.py` и `tests/test_polling.py`: свежий кэш остаётся (в том числе старше `retention_days`, но моложе `repo_retention_days`); старый удаляется и это записано в лог; недокачанный удаляется; осиротевший worktree в кэше снят, сам кэш цел; при занятом lock ничего не удаляется; ошибка удаления — предупреждение, проход продолжается.
- [x] 7.2 Обновить описание рабочей папки в `config.example.yaml`, README и AGENTS.md (§9 команды/рабочая папка, §7 дорожная карта — change в работе). Проверка: раскладка в документации совпадает с `WorkDir`.

## 8. Интеграционная проверка

- [x] 8.1 `pytest` — все тесты зелёные.
- [ ] 8.2 Живая проверка на боевом GitLab (`poll --dry-run`, затем `poll --all`) с проектом без `local_repo`: первый проход клонирует кэш и ревьюит MR; повторный проход по новому MR того же проекта не переклонирует; MR из ветки, отставшей от целевой, ревьюится с корректным base; после прохода `tmp/` пуст, кэш на месте; запуск из задачи Task Scheduler (`pythonw`, без консоли) не зависает на кредах. Проверка: результаты записаны в `validation-notes.md` этого change.
