# Tasks

## 1. Пути коммита и путь worktree

- [x] 1.1 Добавить в `src/review_agent/worktree.py` функцию
  `list_tree_paths(repo_path, sha) -> set[str]`. Она выполняет
  `git -c core.quotepath=off ls-tree -r -t -z --name-only <sha>` с
  `encoding="utf-8"` и без окна консоли (design.md, решение 6), а при
  ненулевом коде возврата выбрасывает `WorktreeError` со stderr git.
  Проверка: тест в `tests/test_worktree.py` на временном репозитории
  получает файлы, вложенные директории и путь с кириллицей и пробелом,
  а несуществующий SHA приводит к `WorktreeError`.
- [x] 1.2 Добавить в `ReviewResult` (`src/review_agent/pipeline.py`)
  поле `worktree_path: Path | None = None` и заполнять его в
  `run_review` значением `handle.path` (решение 7). Проверка: тест в
  `tests/test_pipeline.py` проверяет, что `result.worktree_path` равен
  пути worktree прогона; существующие тесты `test_polling.py` проходят
  без изменений.

## 2. Переписывание ссылок (чистая логика)

- [x] 2.1 Создать `src/review_agent/file_links.py` с функциями
  `project_web_url(mr_web_url, hostname, project_path)` (решение 3) и
  `link_file_references(report, *, worktree_path, project_url, head_sha,
  base_sha, head_paths, base_paths)`, которые реализуют шаги 1–4 и
  нормализацию префикса worktree (решения 4–5). Проверка: тесты в
  `tests/test_file_links.py` покрывают все сценарии требования «File
  references link to the reviewed commit in GitLab»:
  - ссылка на worktree с `#L13`, `#L13-L20` и `#L13-20`;
  - inline-код `path`, `path:66`, `path:565-573`;
  - inline-код, который не является путём (`isFallback`, `useGetGroup()`,
    несуществующий путь);
  - файл, удалённый в MR (ссылка на base);
  - путь директории (`/-/tree/`);
  - путь с пробелом или кириллицей (URL-кодирование);
  - http-ссылки остаются как были.
- [x] 2.2 Покрыть тестами требование «Published comments carry no local
  paths»: путь worktree внутри fenced-блока, в обычном тексте и в тексте
  ссылки; обратные слэши; другой регистр буквы диска; `file:///` и `%20`;
  ссылка на путь, которого нет ни в одном коммите. Проверка: во всех
  случаях в результате нет подстроки пути worktree (сравнение без учёта
  регистра и разделителей), остаётся относительный путь.
- [x] 2.3 Регрессионный тест на реальных отчётах. В
  `tests/fixtures/` положить обезличенные фрагменты двух отчётов
  (формат !13 — ссылки на worktree; формат !587 — inline-код
  `path:line`) и прогнать через `link_file_references`. Проверка:
  ожидаемые ссылки GitLab появились, остальной текст (находки, SEV,
  блоки кода) совпадает с исходным побайтно.

## 3. Встраивание в публикацию

- [x] 3.1 В `src/review_agent/polling.py` (`_review_one`) перед
  `format_comment` получить пути head и base через `list_tree_paths`
  из `prepared.path`, вычислить URL проекта и пропустить отчёт через
  `link_file_references` (решения 4 и 8). `WorktreeError` и любое
  исключение переписывания перехватываются, пишется
  `ctx.log.warning(...)` с `<project> !<iid>`, а публикация продолжается
  с тем, что удалось переписать. Проверка: тесты в
  `tests/test_polling.py`:
  - опубликованный комментарий и dry-run-файл содержат ссылку
    `https://<host>/<path>/-/blob/<head_sha>/…#L…` и не содержат пути
    worktree;
  - при падающем `list_tree_paths` отчёт опубликован, ссылки на worktree
    переписаны, inline-код не тронут, в логе есть предупреждение;
  - при исключении внутри переписывания публикуется исходный отчёт.
- [x] 3.2 Описать поведение в README (раздел о публикации комментария):
  ссылки ведут на файл в отревьюенном коммите; распознаются ссылки на
  worktree и inline-код `path:line`; ошибка переписывания не блокирует
  публикацию. Проверка: описание в README не противоречит
  `specs/review-publishing/spec.md` этого change.

## 4. Живая проверка и документация проекта

- [x] 4.1 `pytest` целиком. Проверка: все тесты зелёные.
- [x] 4.2 `review-agent poll --dry-run` на реальном MR (например, !13
  `web/front-checkout-admin-panel` с `--include-closed`, если он уже
  смёржен). Открыть ссылки из dry-run-комментария в браузере. Проверка:
  ссылки открывают нужный файл на нужной строке в отревьюенном коммите,
  в файле нет локальных путей. Отдельно посчитать упоминания файлов, которые
  не стали ссылками (путь в обычном тексте, «строка N» прозой и т.п.),
  и сделать вывод, нужен ли отдельный change с инструкцией формата в
  промпте и A/B на бенчмарке (design.md, Risks). Результат и вывод записаны в
  `openspec/changes/gitlab-file-links/validation-notes.md`.
- [x] 4.3 Обновить AGENTS.md (п.7: change 6 `gitlab-file-links`) и
  статус в `openspec/config.yaml`. Проверка: `openspec validate
  gitlab-file-links --strict` зелёный.
