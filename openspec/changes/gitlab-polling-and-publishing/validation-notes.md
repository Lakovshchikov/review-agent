# Валидация на боевом GitLab (задачи 6.1–6.4)

Окружение: Windows 11, `glab 1.115.0 (c3612c8)`, GitLab `git.platops.ru`,
проект `b2c/front-shopping`, локальный клон `E:\Projects\work_alfa_2\m`
(`origin` = `https://git.platops.ru/b2c/front-shopping.git`).
`glab` авторизован как `i.lakovschikov` — это же и ревьюер в конфиге,
комментарии публикуются от его имени.

## 6.1. Синтаксис `glab` (2026-10-02) — OK, правок адаптера не потребовалось

Проверено ручными вызовами и затем самим адаптером
`review_agent.gitlab.GitLabClient` (только чтение, без публикации):

| Вызов | Результат |
|-------|-----------|
| `glab api --hostname <host> user` | `username: i.lakovschikov`; `preflight()` проходит |
| `glab api --hostname <host> --paginate "projects/b2c%2Ffront-shopping/merge_requests?state=opened&reviewer_username=i.lakovschikov&per_page=100"` | 2 MR: `!587`, `!595` (не draft) — совпадает с UI |
| `glab mr view 595 -R b2c/front-shopping --output json` (с `GITLAB_HOST`) | все нужные поля есть: `iid`, `title` (кириллица без искажений), `description`, `author.username`, `web_url`, `draft` (+ устаревший `work_in_progress`) |
| `glab api ... merge_requests/595` → `diff_refs` | `base_sha`, `head_sha`, `start_sha` заполнены |
| `glab api --paginate ... merge_requests/595/notes?per_page=100` | JSON-массив, 11 notes; у `!587` — 34 |
| `glab api --help` | есть `--input <file>` («The file to use as the body for the HTTP request»), `--method`, `-H`, `--hostname`, `--paginate`; `--output` по умолчанию `json` |

Через адаптер: кандидаты `[(587, False), (595, False)]`, по каждому —
метаданные, `diff_refs` и notes получены, `is_already_reviewed = False`
у обоих (наших маркеров ещё нет).

Дополнительно (часть 6.2, но безопасно): `git fetch origin
refs/merge-requests/595/head` в локальном клоне — код 0, base и head SHA
доступны локально, ветка клона (`main...origin/main`) не изменилась.

Не проверено на этом шаге: реальная публикация через
`glab api --method POST --input` — флаг подтверждён в `--help`,
фактический POST проверяется в 6.3.

## 6.2. `poll --include-closed --dry-run` на `!544` (2026-10-02) — OK

Флаг `--include-closed` добавлен по ходу проверки (design.md 6b), чтобы
тестировать на уже смёрженных MR. Выбран бенчмарк-MR `!544` (merged).

- Ревью прошло на `base d6a9078821e4` / `head eb73b471e429` — ровно SHA
  бенчмарка из `AGENTS.md` §3, полученные из `diff_refs`.
- Провайдер `openai/gpt-5.6-terra#medium`, прогон ~3,5 мин
  (12:23:42 → 12:27:08).
- `[dry-run]`, код выхода 0; в GitLab ничего не опубликовано.
- Файлы: отчёт `reports/b2c-front-shopping-544-eb73b471e429.md` (7 077 байт),
  `.comment.md` (шапка с коммитом/base/моделью, дисклеймер, отчёт, маркер
  `<!-- ai-review: sha=eb73b471e4292dc40a78203b3c9f68c91ae4dec1 -->` последней
  строкой), `.harness-stderr.log` (2 957 строк — агент реально исследовал
  репозиторий).
- После прохода: `poll.lock` удалён; worktree прогона удалён (в `git worktree
  list` клона — только собственные worktree пользователя); клон без
  изменений (`git status` чистый). Переключение ветки клона на
  `feature/B2CDEV-548` в 12:20:12 сделано до старта прогона и не им —
  `review-agent` не делает checkout в клоне.
- Остаются папки `<scratch>/<run_id>/` с `prompt.md` и safety-note — так
  задумано ещё в Change 1 (диагностика), не утечка worktree.

**Качество (по сравнению с прогоном Change 1 на том же MR):** 6 находок
(3 Major, 3 Minor) против 5 (3 Major, 2 Minor). Совпали: повторное
открытие/отправка формы через «Вперёд», гонка старого запроса пагинации
после `reset()`, недостаток тестов. Новые: глобальный host модалки вне
`RouteGate`, удаление зависит от `transitionend`, `radiogroup` без
клавиатурной навигации. Не повторились: silent-refresh уводит на страницу
ошибки, утечка старого ключа кэша пагинации. Разброс между прогонами
ожидаемый для свободного исследования; качество на уровне Change 1.

Дополнительно проверено пользователем (2026-10-02), всё корректно:
- второй `poll` из той же папки во время идущего прохода сразу отказывает
  («проход уже выполняется», путь к `poll.lock`, код 2), не опрашивая GitLab;
- после завершения/прерывания первого прохода `poll.lock` удалён;
- неверный ввод (`abc`) → «Неверный ввод» и повторный вопрос; `q` → выход
  без ревью, код 0.