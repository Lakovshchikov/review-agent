# Validation notes: managed-repo-cache

## Живая проверка авторизации через glab (задачи 1.1–1.2), 2026-10-04

Выполнено до начала реализации, чтобы снять главный риск design.md (решение 4).

**Окружение:** Windows, PowerShell; glab 1.115.0 (токен в системном keyring); git 2.49.0.windows.1; в system-конфиге Git for Windows (`C:/Program Files/Git/etc/gitconfig`) прописан `credential.helper=manager` (GCM). Хост `git.platops.ru`, проект `web/mf-loader` (приватный).

| # | Проверка | Результат |
|---|----------|-----------|
| 1 | `glab auth git-credential` существует и по протоколу git-хелпера отдаёт `username`/`password` для хоста | ✅ (поля `capability[]`, `username`, `password`) |
| 2 | `git -c credential.helper= -c "credential.helper=!'<glab.exe>' auth git-credential" clone --bare --no-tags --config ... https://git.platops.ru/web/mf-loader.git` при `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never` | ✅ клон за ~2 с, окна GCM нет |
| 2a | Контроль: тот же клон без хелпера glab (`-c credential.helper=`) | ❌ как и ожидалось: `could not read Username ... terminal prompts disabled`, exit 128, сразу. Значит, проект требует авторизации, и в шаге 2 она прошла **через хелпер glab** |
| 3 | `config --local --get-all credential.helper` клона | ✅ пустое значение (сброс GCM) + `!'C:/Users/.../glab.exe' auth git-credential`; `--is-bare-repository` = `true`; рабочих файлов нет |
| 4 | `git fetch` в клоне **без** `-c` (креды из конфига клона): `+refs/heads/main:refs/heads/main` + `refs/merge-requests/9/head` | ✅ авторизация — см. ниже; ⚠ fetch упал: `couldn't find remote ref refs/merge-requests/9/head` |
| 5 | Токен из `glab auth git-credential get` не встречается в файлах клона (вне `objects/`) | ✅ пусто |
| 6 | Хелпер glab есть, но кредов для хоста у него нет (`ls-remote https://gitlab.com/<несуществующий>`) | ✅ отказ за 0,8 с, `terminal prompts disabled`, окна GCM нет |

### Выводы

- **Решение 4 подтверждено, обходной путь не нужен.** Хелпер glab, записанный в конфиг bare-клона, работает. Пустой `credential.helper=` действительно отключает GCM из system-конфига, токен на диск не попадает, без кредов git падает сразу.
- **Авторизация в шаге 4 — вывод, а не прямое наблюдение.** Ошибка `couldn't find remote ref` возвращается сервером после того, как он отдал список ref'ов приватного репозитория. Без авторизации git упал бы раньше, с `could not read Username` (как в 2a). Прямое подтверждение fetch'а целевой ветки из конфига клона даст живая проверка 8.2.
- **Подтверждено вживую решение 5.** `git fetch` с несколькими refspec'ами падает **целиком**, если хотя бы одного remote-ref'а нет: целевая ветка при этом тоже не обновляется. Поэтому запасной вариант «повторить по одному refspec'у, неудачи превратить в предупреждения» обязателен, а не перестраховка. Почему у `!9` (последний по дате MR `web/mf-loader`) нет `refs/merge-requests/9/head`, не выяснялось. Вероятная причина — очистка ref'ов закрытых/смёрженных MR на стороне GitLab. Сценарий «MR head reference no longer available» в спеке `review-polling-run` это покрывает.
- **Мелочь.** glab при каждом вызове пытается отправить телеметрию на gitlab.com (`401 Unauthorized` в stderr). Внутри credential-хелпера это лишний сетевой запрос на каждый fetch. Стоит проверить, отключается ли телеметрия в этой версии glab, и если да — упомянуть это в README при реализации.
