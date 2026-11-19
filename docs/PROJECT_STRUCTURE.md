# Структура репозитория Tsukiyora

Разделение внутренних Python-модулей по областям. Не меняет SQLite,
документы, личность или интерфейс. Новый дизайн Python-пакетов не требует
сторонних зависимостей, Docker, установки системных служб или новых серверов.

```text
Tsukiyora/
├── Tsukiyora.bat             Windows: единый переносимый запуск
├── launcher.py               Python HTTP API, проверка запуска, main
├── restore_memory.py         Существующая CLI-утилита восстановления
├── README.md
├── soul/
│   └── personality.json     Характер (остаётся на прежнем месте)
├── tsukiyora/                Внутренние Python-пакеты
│   ├── academy/              Обучение, уроки, тесты, откат
│   ├── cognition/            Cognitive Soul 1–7, Skill Forge, стратегии
│   ├── memory/               SQLite, векторы, семантика, мозг, атлас
│   ├── cloud/                Cloud.ru, лимиты, потоковый чат
│   ├── runtime/              Планировщик, надёжность, бэкапы
│   ├── personality/          Опыт, биография, непрерывность и развитие V8
│   └── documents/            Документы, RAG, цитаты и договоры
├── ui/                       HTML, CSS, TypeScript, Canvas
├── docs/                     Документация
├── tests/                    Unit + интеграционные проверки
├── scripts/                  Нагрузочные тесты
└── .github/workflows/        CI: Windows/Linux/BAT/SQLite
```

## Импорты

Предпочтительны явные абсолютные импорты:
```python
from tsukiyora.memory.memory_store import MemoryStore
from tsukiyora.cognition.cognitive_core import CognitiveCore
from tsukiyora.cloud.cloudru import CloudSession
```

Раньше эти модули лежали непосредственно рядом с launcher.py.
Публичные точки входа и API сохранены. Для запуска по-прежнему достаточно
`Tsukiyora.bat` на Windows или `python launcher.py`, без установки пакета.
Для восстановления по-прежнему используется `python restore_memory.py`.

Точки запуска запускаются из корня проекта, а `scripts/test_million_atlas.py`
сам добавляет этот корень в sys.path для ручной нагрузки.

## Проверки и добавление нового модуля

Создавайте новый модуль в подходящей теме и используйте пакетный импорт,
а не новый верхнеуровневый .py. Тест `tests/test_repository_layout.py`
останавливает случайное возвращение внутреннего кода в корень.

```text
python -m unittest discover -s tests -v
python launcher.py --self-test
python scripts/test_million_atlas.py
```

Тестирование переноса на Windows особенно важно из-за кириллицы, длинных
путей, BAT и обычных папок OneDrive.

## Совместимость данных

Пути к локальной SQLite, настройкам Cloud.ru, резервным копиям,
`soul/personality.json`, `ui/` и `data/` остаются прежними.
Миграция не переносит файлы пользователя. Обновляется только структура
исходного кода; существующие таблицы и история не меняются.
