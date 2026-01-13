"""
Конфигурация поисковых запросов для hh.ru

Структура запроса:
(ROLE) AND (STACK) AND (DOMAIN) NOT (BLACKLIST)

Где:
- ROLE: названия роли/должности
- STACK: технологии и инструменты
- DOMAIN: область применения (опционально)
- BLACKLIST: исключения (стажеры, джуны и т.д.)
"""

# Примеры поисковых запросов для Frontend разработчика React/Next.js
REACT_NEXTJS_QUERIES = [
    # Базовый запрос
    '(React OR "React.js" OR Next.js OR NextJS) AND (TypeScript OR JavaScript) NOT (стажер OR intern OR junior OR "без опыта")',
    
    # С акцентом на Next.js
    '("Next.js" OR NextJS OR "Next JS") AND (TypeScript OR JavaScript) AND (SSR OR "Server Side Rendering" OR "Server Components") NOT (стажер OR intern)',
    
    # С полным стеком
    '(React OR "React.js" OR Next.js) AND (TypeScript OR JavaScript) AND (Redux OR Zustand OR MobX) AND (Tailwind OR "styled-components" OR CSS) NOT (стажер OR intern OR junior)',
    
    # С бэкендом
    '(React OR Next.js) AND (Node.js OR Express OR NestJS) AND (TypeScript OR JavaScript) NOT (стажер OR intern)',
    
    # С мобильной разработкой
    '(React OR Next.js) AND (React Native OR "React Native") AND (TypeScript OR JavaScript) NOT (стажер OR intern)',
    
    # С GraphQL
    '(React OR Next.js) AND (GraphQL OR Apollo OR Relay) AND (TypeScript OR JavaScript) NOT (стажер OR intern)',
    
    # С тестированием
    '(React OR Next.js) AND (Jest OR Vitest OR "React Testing Library" OR Cypress) AND (TypeScript OR JavaScript) NOT (стажер OR intern)',
]

# Примеры для других ролей
QA_LEAD_QUERIES = [
    '("QA Lead" OR "Lead QA" OR "Test Lead" OR "QA Team Lead" OR "Head of QA" OR "руководитель тестирования" OR "лид тестирования") AND (python OR pytest OR playwright OR selenium) NOT (стажер OR intern OR junior)',
    
    '("QA Lead" OR "Lead QA") AND (python OR pytest) AND (API OR REST OR swagger) NOT (стажер OR intern OR junior)',
]

BACKEND_DEVELOPER_QUERIES = [
    '(Python OR "Python разработчик") AND (Django OR Flask OR FastAPI) AND (PostgreSQL OR MySQL OR MongoDB) NOT (стажер OR intern OR junior)',
    
    '(Java OR "Java разработчик") AND (Spring OR Spring Boot) AND (PostgreSQL OR MySQL) NOT (стажер OR intern OR junior)',
]

# Функция для получения запроса по умолчанию
def get_default_query(role: str = "react_nextjs") -> str:
    """
    Возвращает поисковый запрос по умолчанию для указанной роли.
    
    Args:
        role: Роль ('react_nextjs', 'qa_lead', 'backend')
    
    Returns:
        Поисковый запрос
    """
    queries_map = {
        "react_nextjs": REACT_NEXTJS_QUERIES[0],
        "qa_lead": QA_LEAD_QUERIES[0],
        "backend": BACKEND_DEVELOPER_QUERIES[0],
    }
    return queries_map.get(role, REACT_NEXTJS_QUERIES[0])


# Функция для получения всех запросов для роли
def get_all_queries(role: str = "react_nextjs") -> list[str]:
    """
    Возвращает все поисковые запросы для указанной роли.
    
    Args:
        role: Роль ('react_nextjs', 'qa_lead', 'backend')
    
    Returns:
        Список поисковых запросов
    """
    queries_map = {
        "react_nextjs": REACT_NEXTJS_QUERIES,
        "qa_lead": QA_LEAD_QUERIES,
        "backend": BACKEND_DEVELOPER_QUERIES,
    }
    return queries_map.get(role, REACT_NEXTJS_QUERIES)

