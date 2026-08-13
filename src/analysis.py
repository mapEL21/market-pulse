"""SQL-анализ накопленных прогонов. Этап 10.

Каждый раздел — один запрос, и запрос печатается вместе с результатом.
Это сознательно: смысл этапа в том, чтобы видно было, каким именно вопросом
получен ответ, а не только сам ответ.

Анализируются только прогоны бэктеста. Смешивать их с живыми нельзя:
в бэктесте оборот за 24 ч считается по свечам, а в живом режиме берётся
из тикера (раздел 9.2 spec.md).

Псевдонимы колонок русские, в двойных кавычках. SQLite это допускает,
а отчёт становится читаемым без словаря — и в консоли, и на странице.

Запуск: python -m src.analysis
"""

import sqlite3
from dataclasses import dataclass

from src.storage import connect


@dataclass(frozen=True)
class Query:
    """Раздел анализа: вопрос, пояснение, запрос и колонка с ответом.

    highlight — имя колонки, в которой лежит собственно ответ. Консоли она
    не нужна, а странице нужна: по ней рисуются столбики. Держать её рядом
    с запросом правильнее, чем заводить второй список на стороне страницы:
    так числа в консоли и на странице не смогут разойтись.

    Про псевдонимы важно помнить одно: **имя псевдонима не должно совпадать
    с именем колонки таблицы**. В GROUP BY SQLite разрешит такое имя как
    настоящую колонку, а не как псевдоним, и сгруппирует не по тому. Ошибка
    не падает — запрос просто возвращает тысячи строк вместо шести.
    Поэтому диапазоны названы «диапазон RVOL», а не «RVOL».
    """

    title: str
    note: str
    sql: str
    highlight: str


# Общая часть всех запросов: наблюдение, его прогон и его результат.
FROM_CLAUSE = """
    FROM observations ob
    JOIN runs r ON r.id = ob.run_id
    JOIN outcomes o ON o.observation_id = ob.id
    WHERE r.source = 'backtest'
"""

# Порог «заметного движения»: за два часа цена ушла хотя бы на 3 % в любую
# сторону. Величина оформительская — она нужна, чтобы выразить результат
# долей, а не только средним, которое чувствительно к выбросам.
NOTABLE_MOVE_PCT = 3.0

SHARE = f"""ROUND(100.0 * SUM(o.max_move_2h >= {NOTABLE_MOVE_PCT}) / COUNT(*), 1)
                     AS "ходов от 3 %" """

QUERIES = [
    Query(
        "1. Контрольная группа: кандидаты против остальных",
        "Главный вопрос проекта. Если отбор ничего не даёт, строки совпадут.",
        f"""
        SELECT CASE WHEN ob.rank IS NOT NULL THEN 'кандидаты'
                    ELSE 'остальные' END AS "группа",
               COUNT(*) AS "наблюдений",
               ROUND(AVG(o.max_move_2h), 2) AS "ход за 2 ч, %",
               ROUND(AVG(ABS(o.ret_2h)), 2) AS "|доходность| 2 ч",
               ROUND(AVG(ABS(o.ret_8h)), 2) AS "|доходность| 8 ч",
               {SHARE}
        {FROM_CLAUSE}
        GROUP BY "группа"
        """,
        "ходов от 3 %",
    ),
    Query(
        "2. Сила сигнала: движение по диапазонам RVOL",
        "Растёт ли результат вместе с метрикой и где на самом деле проходит "
        "граница интересного.",
        f"""
        SELECT CASE WHEN ob.rvol <  1 THEN '1) до 1x'
                    WHEN ob.rvol <  2 THEN '2) 1-2x'
                    WHEN ob.rvol <  3 THEN '3) 2-3x'
                    WHEN ob.rvol <  5 THEN '4) 3-5x'
                    WHEN ob.rvol < 10 THEN '5) 5-10x'
                    ELSE                   '6) 10x+' END AS "диапазон RVOL",
               COUNT(*) AS "наблюдений",
               ROUND(AVG(o.max_move_2h), 2) AS "ход за 2 ч, %",
               {SHARE}
        {FROM_CLAUSE}
        GROUP BY "диапазон RVOL"
        ORDER BY "диапазон RVOL"
        """,
        "ходов от 3 %",
    ),
    Query(
        "3. Правило «не меньше двух метрик»",
        "Оправдана ли защита от разовой аномалии в одном показателе.",
        f"""
        SELECT ob.triggered AS "сработало метрик",
               COUNT(*) AS "наблюдений",
               ROUND(AVG(o.max_move_2h), 2) AS "ход за 2 ч, %",
               {SHARE}
        {FROM_CLAUSE}
        GROUP BY "сработало метрик"
        ORDER BY "сработало метрик"
        """,
        "ходов от 3 %",
    ),
    Query(
        "4. Ложные срабатывания среди кандидатов",
        "Доля кандидатов, у которых за два часа не случилось почти ничего.",
        """
        SELECT ob.exchange AS "биржа",
               COUNT(*) AS "кандидатов",
               SUM(o.max_move_2h < 1) AS "ход меньше 1 %",
               ROUND(100.0 * SUM(o.max_move_2h < 1) / COUNT(*), 1)
                     AS "доля пустышек"
        """
        + FROM_CLAUSE
        + """
          AND ob.rank IS NOT NULL
        GROUP BY "биржа"
        """,
        "доля пустышек",
    ),
    Query(
        "5. Ликвидность: спор про 50 против 200 млн",
        "Отличается ли поведение кандидатов по группам оборота за 24 ч.",
        f"""
        SELECT CASE WHEN ob.quote_volume_24h >= 200e6 THEN '3) 200M+'
                    WHEN ob.quote_volume_24h >= 100e6 THEN '2) 100-200M'
                    ELSE '1) 50-100M' END AS "оборот за 24 ч",
               COUNT(*) AS "кандидатов",
               ROUND(AVG(o.max_move_2h), 2) AS "ход за 2 ч, %",
               {SHARE}
        {FROM_CLAUSE}
          AND ob.rank IS NOT NULL
        GROUP BY "оборот за 24 ч"
        ORDER BY "оборот за 24 ч"
        """,
        "ходов от 3 %",
    ),
    Query(
        "6. Биржи",
        "У OKX метрик две вместо трёх, поэтому сравнение с оговоркой.",
        f"""
        SELECT ob.exchange AS "биржа",
               COUNT(*) AS "кандидатов",
               ROUND(AVG(ob.score), 2) AS "средний score",
               ROUND(AVG(o.max_move_2h), 2) AS "ход за 2 ч, %",
               {SHARE}
        {FROM_CLAUSE}
          AND ob.rank IS NOT NULL
        GROUP BY "биржа"
        """,
        "ходов от 3 %",
    ),
    Query(
        "7. Час суток",
        "Отложенный вопрос с этапа 4: медиана RVOL по рынку зависела от "
        "времени суток. Влияет ли это на качество кандидатов.",
        """
        SELECT substr(r.candle_time, 12, 2) AS "час UTC",
               COUNT(*) AS "кандидатов",
               ROUND(AVG(o.max_move_2h), 2) AS "ход за 2 ч, %"
        """
        + FROM_CLAUSE
        + """
          AND ob.rank IS NOT NULL
        GROUP BY "час UTC"
        HAVING "кандидатов" >= 10
        ORDER BY "ход за 2 ч, %" DESC
        LIMIT 6
        """,
        "ход за 2 ч, %",
    ),
]


def render_table(cursor: sqlite3.Cursor) -> str:
    """Результат запроса таблицей с выровненными колонками."""
    names = [description[0] for description in cursor.description]
    rows = [["" if value is None else str(value) for value in row] for row in cursor]

    widths = [
        max(len(name), *(len(row[index]) for row in rows)) if rows else len(name)
        for index, name in enumerate(names)
    ]
    header = "  ".join(name.rjust(width) for name, width in zip(names, widths))
    lines = [header, "-" * len(header)]
    lines += [
        "  ".join(value.rjust(width) for value, width in zip(row, widths))
        for row in rows
    ]
    return "\n".join(lines)


def main() -> None:
    connection = connect()
    try:
        scope = connection.execute(
            """
            SELECT COUNT(DISTINCT r.id), COUNT(*), SUM(ob.rank IS NOT NULL)
            FROM observations ob
            JOIN runs r ON r.id = ob.run_id
            WHERE r.source = 'backtest'
            """
        ).fetchone()
        print(
            f"Данные: прогонов {scope[0]}, наблюдений {scope[1]}, "
            f"из них кандидатов {scope[2]}"
        )

        for query in QUERIES:
            print()
            print("=" * 78)
            print(query.title)
            print(query.note)
            print("=" * 78)
            print(query.sql.strip())
            print()
            print(render_table(connection.execute(query.sql)))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
