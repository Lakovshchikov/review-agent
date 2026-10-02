# Валидация на Windows (задачи 7.1–7.7)

Окружение: Windows 11 Pro 10.0.26200, Python 3.10.11, Windows PowerShell
5.1.26100, `glab 1.115.0 (c3612c8)`, GitLab `git.platops.ru`, проект
`b2c/front-shopping`, клон `E:\Projects\work_alfa_2\m`. `review-agent`
установлен editable из рабочей копии (глобальный Python, без venv):
`C:\Users\lakov\AppData\Local\Programs\Python\Python310\Scripts\review-agent.exe`.

## Предварительные проверки без записи в GitLab (2026-10-02, агент)

- `pytest` на Windows: **184 passed**.
- `scripts/register-task.ps1` в Windows PowerShell 5.1: парсер — 0 ошибок;
  кириллица в сообщениях без искажений (UTF-8 с BOM работает); ветки
  «exe не найден», «конфиг не найден», `-Remove` несуществующей задачи —
  понятные сообщения, задача не регистрируется.
- Объекты задачи собираются теми же вызовами, что в скрипте, без
  регистрации: `New-ScheduledTaskTrigger -Once -RepetitionInterval 15m` →
  `Interval=PT15M`, `Duration` пустой (= повтор бесконечно);
  `MultipleInstances=IgnoreNew`, `ExecutionTimeLimit=PT4H`; принципал
  `Interactive` и `S4U` для `IVANPC\lakov` создаются.
- Текущий `config.yaml` грузится без правок: `storage` по умолчанию
  (`./.review-agent`, 7 дней), `claim_ttl_minutes` 240.
- 7.1, только чтение: `preflight()` → `i.lakovschikov`; notes `!544`
  через адаптер содержат `id` и `created_at`
  (`2026-10-02T10:04:54.456Z`, разбирается `_parse_time` на 3.10);
  `id` растут во времени (`314214` от 2026-09-02 < `336792` от
  2026-10-02) — на это опирается разрешение гонки claim'ов.
  `is_already_reviewed(!544) = True`, claim'ов нет.
  **Не проверено:** POST/PUT/DELETE — пишут в GitLab, делаются вместе с
  пользователем.
- След вне `work_dir` до начала живых проверок (точка отсчёта для 7.2):
  - профиль OpenCode `%USERPROFILE%\.local\share\opencode` — 33,8 МБ
    (`opencode.db` + wal, `log/`, `snapshot/`, `shell/`, `repos/`);
  - клон `m`: `size-pack` 375 МБ, **`garbage: 51`, 619 МБ** — pack-файлы
    без `.idx` (остатки прерванных fetch). По датам (2026-07-10 …
    2026-10-01) почти все старше review-agent, это не его след.
- Остались старые папки Change 1–2: `.review-agent-scratch/` и `reports/`
  (удалить вручную, review-agent их не трогает).

Подготовлены (не в git, `*.local.yaml`):
- `config.change3-a.local.yaml` — копия `config.yaml`, у
  `b2c/front-shopping` свой `provider` (`reasoning_effort: low`) и
  `skills: []`, плюс выключенный проект `b2c/disabled-check` с
  несуществующим клоном (7.2);
- `config.change3-b.local.yaml` — копия с `work_dir: ./.review-agent-b`
  (второй проход из другой рабочей папки, 7.3).
