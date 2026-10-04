Проведу ревью только на чтение: сравню `HEAD` с указанной базой, изучу затронутые точки интеграции и связанные контракты. Код и проверки запускать не буду.
## Замечания

### Major — `BUTTON_BACK` перестаёт работать при включённом V2-рендерере
`WidgetRendererProvider` передаёт в V2 весь объект `widgetProps`, где callback лежит в `props.onClose`, а `LANDING_WIDGET_MANIFEST` ожидает `onClose` непосредственно в контексте.

- `src/app/provider/WidgetRenderer/WidgetRendererProvider.tsx:34`
- `src/app/provider/WidgetRenderer/v2/registries/landing.tsx:36`
- `src/pages/LandingPodeli/LandingPodeli.tsx:62`

В результате `onClose` равен `undefined`, `BUTTON_BACK` возвращает `null`, и пользователь не сможет закрыть landing «Подели» кнопкой из серверного ответа.

**Минимальное исправление:** унифицировать контракт контекста. Например, распаковать `props` при передаче:

```tsx
context={{
    ...widgetProps,
    ...widgetProps.props,
}}
```

Либо сохранить прежнюю вложенность и читать `context.props?.onClose` в манифесте. Первый вариант лучше соответствует объявленному `WidgetRenderContext`.

---

### Major — виджет избранного не рендерится при включённом V2
Та же ошибка контракта затрагивает `SECTION_WISHLISTS`.

- `src/pages/WishlistPage/WishlistPage.tsx:66`
- `src/app/provider/WidgetRenderer/WidgetRendererProvider.tsx:34`
- `src/app/provider/WidgetRenderer/v2/registries/wishlist.tsx:7`

Экран передаёт `props={{ isLoading: loading }}`, но V2-манифест получает `{ props: { isLoading } }`. Поэтому проверка `typeof isLoading === 'boolean'` всегда не проходит и виджет возвращает `null`. Экран избранного будет пустым после включения тогла.

**Минимальное исправление:** то же — распаковать `widgetProps.props` в V2-контекст либо обращаться к `props.isLoading` из манифеста.

---

### Major — V2 меняет контракт сохранения скрытого виджета и может перемонтировать его
В legacy-версии при `useWidgetVisibility(false)` контент остаётся смонтированным внутри `Surface hidden`, убирается только gap. Это сделано намеренно, чтобы не перезапускать загрузку/локальное состояние.

- legacy: `src/app/provider/WidgetRenderer/legacy/LegacyWidgetRenderer.tsx:565-573`
- V2: `src/app/provider/WidgetRenderer/v2/WidgetRendererV2.tsx:54-57`

V2 при `!isContentVisible` возвращает `null`, то есть размонтирует виджет. Это может сбрасывать состояние и повторно запускать запросы при последующем появлении. В частности, механизм используется у виджетов рекомендаций отзывов.
