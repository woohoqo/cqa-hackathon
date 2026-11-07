# Локальные визуальные ресурсы

Фотографии и шрифты включены в репозиторий и обслуживаются самим Vite-приложением. Внешних запросов для их показа не требуется. Это стоковые фотографии рабочих сцен, а не изображения сотрудников Freedom Broker и не результаты генерации ИИ.

## Фотографии

| Файл | Автор и источник | Лицензия | Размер |
| --- | --- | --- | --- |
| `public/media/team-workspace.jpg` | Yan Krukau, [People Working at the Office, Pexels №7794042](https://www.pexels.com/photo/people-working-at-the-office-7794042/) | [Pexels License](https://www.pexels.com/license/) | JPEG, 1400 × 933, 147 845 байт |
| `public/media/office-detail.jpg` | Ben Spray, [Two men sitting at a table working on laptops, Unsplash gEvMA8O6Et4](https://unsplash.com/photos/two-men-sitting-at-a-table-working-on-laptops-gEvMA8O6Et4) | [Unsplash License](https://unsplash.com/license) | JPEG, 1400 × 933, 216 903 байта |

Обе фотографии разрешены для бесплатного использования на сайте, включая коммерческое. Атрибуция сохранена здесь добровольно. Не представляйте изображённых людей как сотрудников, клиентов или сторонников продукта. Не перепродавайте исходные фотографии и не используйте их для создания конкурирующей стоковой библиотеки. Полные условия доступны по ссылкам лицензий выше.

Загруженные оптимизированные версии:

- [Pexels JPEG, ширина 1400](https://images.pexels.com/photos/7794042/pexels-photo-7794042.jpeg?auto=compress&cs=tinysrgb&w=1400&q=85).
- [Unsplash JPEG, ширина 1400](https://images.unsplash.com/photo-1636647677481-f134fda3f408?auto=format&fit=crop&fm=jpg&q=85&w=1400).

Рекомендуемые нейтральные подписи: «Совместная работа команды в офисе» и «Рабочее обсуждение за ноутбуками». Для полностью декоративного использования задавайте `alt=""`.

## Шрифты

Неизменённые variable WOFF2 из официальных пакетов Fontsource версии **5.3.0**. Включены Latin, Latin Extended, Cyrillic и Cyrillic Extended; соответствующие `unicode-range` перенесены из CSS пакетов. Файлы загружаются по необходимости, `font-display: swap` сохраняет доступность текста.

| Семейство CSS | Диапазон веса | Авторы | Источник | Лицензия |
| --- | --- | --- | --- | --- |
| `Oswald Variable` | 200–700 | Vernon Adams, Kalapi Gajjar, Cyreal / The Oswald Project Authors | [Fontsource](https://fontsource.org/fonts/oswald/about), [пакет 5.3.0](https://registry.npmjs.org/@fontsource-variable/oswald/-/oswald-5.3.0.tgz) | SIL OFL 1.1, полный текст `public/fonts/OSWALD-OFL.txt` |
| `Manrope Variable` | 200–800 | Mikhail Sharanda / The Manrope Project Authors | [Fontsource](https://fontsource.org/fonts/manrope/about), [пакет 5.3.0](https://registry.npmjs.org/@fontsource-variable/manrope/-/manrope-5.3.0.tgz) | SIL OFL 1.1, полный текст `public/fonts/MANROPE-OFL.txt` |

Точные имена включённых файлов:

| Файл в `public/fonts/` | Байт |
| --- | ---: |
| `oswald-cyrillic-ext-wght-normal.woff2` | 19 404 |
| `oswald-cyrillic-wght-normal.woff2` | 15 688 |
| `oswald-latin-ext-wght-normal.woff2` | 23 952 |
| `oswald-latin-wght-normal.woff2` | 28 488 |
| `manrope-cyrillic-ext-wght-normal.woff2` | 2 552 |
| `manrope-cyrillic-wght-normal.woff2` | 14 500 |
| `manrope-latin-ext-wght-normal.woff2` | 15 120 |
| `manrope-latin-wght-normal.woff2` | 24 836 |

Всего: Oswald — 87 532 байта, Manrope — 57 008 байт. Сохраняйте системный `sans-serif` в стеке для символов, которых нет в выбранном семействе, и для недоступного файла.

## Подключение

Импортируйте `src/fonts.css` один раз перед основными стилями, например в `src/main.tsx`:

```ts
import './fonts.css';
```

Или добавьте `@import './fonts.css';` в начало `src/styles.css`. Не используйте оба способа одновременно.

```css
:root {
  --display: 'Oswald Variable', 'Arial Narrow', sans-serif;
  --body: 'Manrope Variable', Arial, sans-serif;
}
```

Ресурсы фотографий доступны как `/media/team-workspace.jpg` и `/media/office-detail.jpg`. CSS шрифтов объявляет только `@font-face`; он не меняет стили компонентов или поведение приложения. Для обоих семейств включено только прямое начертание. Если нужны курсив или веса вне указанных диапазонов, не рассчитывайте на отдельные настоящие файлы этих начертаний.

## Проверка

Обе финальные фотографии открыты для визуальной проверки; размеры JPEG проверены локально. Файлы шрифтов скопированы без изменения из указанных пакетов, ссылки `@font-face` и наличие WOFF2 проверены. Браузерная проверка подключения выполняется в интеграционной ветке после импорта `fonts.css`.
