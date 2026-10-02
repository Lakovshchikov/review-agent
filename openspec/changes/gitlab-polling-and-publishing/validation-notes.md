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
