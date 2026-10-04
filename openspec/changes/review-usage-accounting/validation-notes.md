# Validation notes — review-usage-accounting

Машина автора: Windows 11, OpenCode v2.0.21, провайдер `openai` (подписка ChatGPT).

## 1.1 Образцы вывода OpenCode (2026-10-04)

Сняты на реальных сессиях, обезличены (весь текст → `<text …>` с сохранением
`ses_…`-ссылок, пути → `{worktree}`), лежат в `tests/fixtures/opencode/`:

- `review-session.json` — реальное ревью `b2c/front-shopping` (PLATCHCKT-762),
  `ses_f0344a877ffemdWd4lWd2BI520`;
- `subagent-parent.json` / `subagent-child.json` — короткий `opencode run`
  (gpt-5.6-luna#low) в пустом тестовом репозитории с просьбой запустить
  субагента через task;
- `session-list.txt` — формат `opencode session list` (`ses_<id>\t<заголовок>\t<дата>`);
- `version.txt` — `opencode v2.0.21`.

Ответы на вопросы design.md (решение 1):

1. **Связь родитель–ребёнок.** У дочерней сессии есть `info.parentID`; у
   родителя — часть `type: tool`, `name: "subagent"` с
   `state.metadata.sessionID = <id ребёнка>`. Дочерние сессии в
   `session list` не показываются. Поддерживаемая кодом форма
   «ссылка в сообщениях родителя + проверка `parentID` ребёнка» подтверждена.
   `info.tokens` родителя **не включает** токены ребёнка (12 484 против
   10 636 input) — суммирование по сессиям не даёт двойного счёта.
   `opencode stats` считает такую сессию субагентом (`subagents: 1`).
2. **Шаги** — сообщения `type: "assistant"`; у каждого свои `tokens` и
   `finish` (`tool-calls` / `stop`). В ревью-сессии их 9. Прочие типы
   сообщений: `user`, `agent-switched`, `idle`.
3. **Длительность сессии** — `time.idle − time.created`: в ревью-сессии
   155,1 с, а `time.updated − time.created` всего 2,8 с (`updated`
   выставляется в начале и дальше не меняется). Код берёт
   `max(updated, idle) − created`, что даёт то же.

Попутно: у сессии агента по умолчанию (`opencode run` без `--agent`) поля
`info.agent` нет — это не ошибка формата, агент просто `null`. Сумма
`tokens.input` по сообщениям ассистента родителя (11 943) немного меньше
`info.tokens.input` (12 484) — видимо, служебный вызов генерации заголовка;
источник правды — агрегат `info.tokens`.

Найдено тестом на реальном формате и исправлено: ошибка экспорта
*соседней* сессии из `session list` давала предупреждение о формате, даже
когда сессия прогона найдена. Теперь проблемы соседей учитываются только
если сессия прогона не найдена.
