"""Страница отчёта в браузере.

Команда пишет data/report.html, который открывается двойным щелчком.
Ни сервера, ни внешних файлов: всё оформление внутри страницы, поэтому она
работает без интернета и её можно просто переслать.

Объяснения берутся из report.build_reasons — тех же самых структур, что
и в консоли. Ради этого они и сделаны данными, а не строками: формулировки
не могут разойтись между форматами, потому что источник один.

Запуск: python -m src.report_html
"""

import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from src.analysis import QUERIES
from src.exchanges import binance, okx
from src.metrics import Metrics
from src.report import (
    build_reasons,
    compact,
    format_amount,
    format_candle_close,
    format_symbol,
    summary,
)
from src.storage import connect, from_iso, latest_run, run_candidates

OUTPUT_PATH = Path("data") / "report.html"
EXCHANGES = (binance, okx)

# Копирование тикера по клику.
#
# navigator.clipboard требует защищённого контекста. Схема file:// по
# спецификации считается доверенной, но полагаться на это одно нельзя:
# если API недоступен, клик молча ничего не сделает. Поэтому есть запасной
# путь через устаревший execCommand — он работает везде.
SCRIPT = """
function fallbackCopy(text) {
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  try { document.execCommand('copy'); } finally { area.remove(); }
}

function markCopied(button) {
  button.dataset.copied = '1';
  setTimeout(() => delete button.dataset.copied, 1400);
}

document.addEventListener('click', (event) => {
  const button = event.target.closest('.ticker');
  if (!button) return;
  const symbol = button.dataset.symbol;

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(symbol)
      .then(() => markCopied(button))
      .catch(() => { fallbackCopy(symbol); markCopied(button); });
  } else {
    fallbackCopy(symbol);
    markCopied(button);
  }
});
"""

STYLE = """
:root {
  --bg: #f7f8f7;
  --panel: #ffffff;
  --ink: #1b1f1e;
  --muted: #5d6a66;
  --line: #dfe4e2;
  --accent: #0f6b5c;
  --up: #17724f;
  --down: #a33a2c;
  --dim: #8b9793;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #141917;
    --panel: #1c2321;
    --ink: #e8ecea;
    --muted: #9aa8a3;
    --line: #2c3633;
    --accent: #5fd3b6;
    --up: #5fd3b6;
    --down: #e58a76;
    --dim: #77857f;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 2rem 1.25rem 4rem;
  background: var(--bg);
  color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 1080px; margin: 0 auto; }
h1 { font-size: 1.6rem; margin: 0 0 .3rem; letter-spacing: -.01em; }
h2 {
  font-size: 1.05rem; margin: 2.5rem 0 .9rem; text-transform: uppercase;
  letter-spacing: .09em; color: var(--muted); font-weight: 600;
}
.sub { color: var(--muted); margin: 0 0 .15rem; }
.num { font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
       font-variant-numeric: tabular-nums; }
.funnel {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: .8rem 1rem; margin-bottom: 1rem;
  display: flex; flex-wrap: wrap; gap: 1.4rem; align-items: baseline;
}
.funnel b { font-size: 1.15rem; }
.funnel span { color: var(--muted); font-size: .85rem; }
.tag {
  font-size: .72rem; letter-spacing: .06em; text-transform: uppercase;
  border: 1px solid var(--line); border-radius: 999px; padding: .12rem .55rem;
  color: var(--muted);
}
.cards { display: grid; gap: 1rem; grid-template-columns: 1fr; }
@media (min-width: 820px) { .cards { grid-template-columns: 1fr 1fr; } }
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 1rem 1.1rem;
}
.card header {
  display: flex; justify-content: space-between; align-items: baseline;
  gap: 1rem; border-bottom: 1px solid var(--line);
  padding-bottom: .6rem; margin-bottom: .7rem;
}
.ticker {
  font: inherit; font-weight: 650; font-size: 1.05rem; color: inherit;
  background: none; border: 0; padding: 0; cursor: pointer;
  border-bottom: 1px dashed var(--line);
}
.ticker:hover { border-bottom-color: var(--accent); }
.ticker:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.ticker[data-copied] { border-bottom-color: transparent; }
.ticker[data-copied]::after {
  content: " скопировано";
  font-size: .7rem; font-weight: 500; letter-spacing: .05em;
  text-transform: uppercase; color: var(--accent);
}
.rank { color: var(--dim); font-weight: 400; }
.score { color: var(--accent); font-weight: 650; }
.facts { display: flex; gap: 1.6rem; margin-bottom: .85rem; }
.facts div { font-size: .8rem; color: var(--muted); }
.facts strong { display: block; font-size: 1rem; font-weight: 600; }
.up { color: var(--up); }
.down { color: var(--down); }
.why { font-size: .78rem; text-transform: uppercase; letter-spacing: .07em;
       color: var(--muted); margin: .9rem 0 .35rem; }
ul { list-style: none; margin: 0; padding: 0; }
li { padding: .3rem 0 .3rem .9rem; border-left: 2px solid var(--accent); }
li.miss { border-left-color: var(--line); color: var(--muted); }
li .usual { display: block; color: var(--muted); font-size: .85rem; }
.total { margin-top: .9rem; padding-top: .7rem; border-top: 1px solid var(--line);
         color: var(--muted); font-size: .9rem; }
.empty { color: var(--muted); font-style: italic; }
.study {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 1rem 1.1rem; margin-bottom: 1rem;
}
.study h3 { margin: 0 0 .2rem; font-size: 1rem; }
.study p { margin: 0 0 .9rem; color: var(--muted); font-size: .88rem; }
table { border-collapse: collapse; width: 100%; font-size: .88rem; }
th, td { padding: .3rem .5rem; text-align: right; white-space: nowrap; }
th { color: var(--muted); font-weight: 500; font-size: .76rem;
     text-transform: uppercase; letter-spacing: .05em;
     border-bottom: 1px solid var(--line); }
td:first-child, th:first-child { text-align: left; }
.bar { width: 100%; min-width: 90px; }
.bar div {
  height: 9px; border-radius: 3px; background: var(--accent); min-width: 2px;
}
.caveats { background: var(--panel); border: 1px solid var(--line);
           border-left: 3px solid var(--down); border-radius: 10px;
           padding: 1rem 1.1rem; }
.caveats li { border-left: 0; padding: .25rem 0 .25rem 1rem;
              text-indent: -1rem; color: var(--muted); font-size: .9rem; }
.caveats li::before { content: "— "; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--line);
         color: var(--dim); font-size: .82rem; }
"""


def metrics_from_row(row: tuple) -> Metrics:
    """Собрать Metrics из строки таблицы observations.

    Порядок полей задан запросом storage.run_candidates.
    """
    (
        _symbol, _rank, _score, rvol, rtc, ve, price, change_pct,
        volume, volume_median, trades, trades_median,
        range_pct, range_pct_median, _turnover,
    ) = row
    return Metrics(
        rvol=rvol,
        rtc=rtc,
        ve=ve,
        change_pct=change_pct,
        close=price,
        volume=volume,
        volume_median=volume_median,
        trades=trades,
        trades_median=trades_median,
        range_pct=range_pct,
        range_pct_median=range_pct_median,
    )


def render_card(row: tuple) -> str:
    """Карточка одного кандидата."""
    symbol, rank, score = row[0], row[1], row[2]
    turnover = row[14]
    metrics = metrics_from_row(row)

    reasons = build_reasons(metrics)
    triggered = [reason for reason in reasons if reason.triggered]
    missed = [reason for reason in reasons if not reason.triggered]

    direction = "up" if metrics.change_pct >= 0 else "down"
    parts = [
        '<article class="card">',
        "<header>",
        "<span>"
        f'<button type="button" class="ticker" data-symbol="{escape(symbol)}"'
        f' title="Скопировать тикер">{escape(format_symbol(symbol))}</button>'
        f' <span class="rank num">#{rank}</span></span>',
        f'<span class="score num">score {score:.2f}</span>',
        "</header>",
        '<div class="facts">',
        f'<div>изменение за свечу<strong class="num {direction}">'
        f"{metrics.change_pct:+.2f} %</strong></div>",
        f'<div>оборот за 24 ч<strong class="num">'
        f"{escape(compact(turnover or 0))} USDT</strong></div>",
        "</div>",
        '<p class="why">Почему в списке</p>',
        "<ul>",
    ]
    parts += [
        f"<li>{escape(reason.what)} — {escape(reason.ratio)}"
        f'<span class="usual">{escape(reason.usual)}</span></li>'
        for reason in triggered
    ]
    parts.append("</ul>")

    if missed:
        parts.append('<p class="why">Не сработало</p><ul>')
        parts += [
            f'<li class="miss">{escape(reason.what)} — {escape(reason.ratio)}'
            f'<span class="usual">{escape(reason.usual)}, '
            f"{escape(reason.threshold)}</span></li>"
            for reason in missed
        ]
        parts.append("</ul>")

    parts.append(f'<p class="total">{escape(summary(metrics))}</p>')
    parts.append("</article>")
    return "".join(parts)


def render_exchange(connection: sqlite3.Connection, client) -> str:
    """Раздел одной биржи: воронка и карточки последнего прогона."""
    run = latest_run(connection, client.NAME)
    if run is None:
        return (
            f"<h2>{escape(client.NAME)}</h2>"
            '<p class="empty">Прогонов пока нет.</p>'
        )

    run_id, source, candle_time, total, passed, analysed, candidates = run
    rows = run_candidates(connection, run_id)

    funnel = (
        f'<div class="funnel">'
        f'<span class="tag">{escape(source)}</span>'
        f"<span>свеча закрылась<br><b class=\"num\">"
        f"{escape(format_candle_close(from_iso(candle_time)))}</b></span>"
        f'<span>проверено<br><b class="num">{total}</b></span>'
        f'<span>после фильтров<br><b class="num">{passed}</b></span>'
        f'<span>с полной историей<br><b class="num">{analysed}</b></span>'
        f'<span>кандидатов<br><b class="num">{candidates}</b></span>'
        f"</div>"
    )

    if not rows:
        cards = '<p class="empty">В этом прогоне кандидатов не нашлось.</p>'
    else:
        cards = '<div class="cards">' + "".join(render_card(row) for row in rows) + "</div>"

    return (
        f"<h2>{escape(client.NAME)} {escape(client.MARKET)}</h2>{funnel}{cards}"
    )


CAVEATS = (
    "Одна неделя и один режим рынка — выводы могут не пережить смену фазы.",
    "Выживший отбор: список инструментов брался сегодняшний, делистнутые "
    "за период в выборку не попали, а это часто как раз те, кто сильно двигался.",
    "Порог «заметного движения» в 3 % выбран для наглядности, а не выведен "
    "из чего-либо.",
    "Движение — не прибыль. Ход на 5 % в любую сторону не означает, что "
    "на нём можно было заработать: направление система не предсказывает.",
    "Кандидаты ходят сильнее остальных прежде всего потому, что волатильность "
    "кластеризуется. Это известное свойство рынков, а не открытие проекта.",
)


def render_study(connection: sqlite3.Connection, query) -> str:
    """Один раздел исследования: таблица со столбиками в колонке-ответе."""
    cursor = connection.execute(query.sql)
    names = [description[0] for description in cursor.description]
    rows = cursor.fetchall()
    if not rows:
        return ""

    highlight = names.index(query.highlight)
    values = [row[highlight] or 0 for row in rows]
    largest = max(values) or 1

    head = "".join(f"<th>{escape(name)}</th>" for name in names) + "<th></th>"
    body = []
    for row in rows:
        cells = "".join(
            f'<td class="num">{escape("" if value is None else str(value))}</td>'
            for value in row
        )
        share = round(100 * (row[highlight] or 0) / largest)
        body.append(
            f'<tr>{cells}<td class="bar"><div style="width:{share}%"></div></td></tr>'
        )

    return (
        f'<section class="study"><h3>{escape(query.title)}</h3>'
        f"<p>{escape(query.note)}</p>"
        f"<table><thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></section>"
    )


def render_research(connection: sqlite3.Connection) -> str:
    """Раздел исследования целиком: все запросы плюс оговорки."""
    scope = connection.execute(
        """
        SELECT COUNT(DISTINCT r.id), COUNT(*), SUM(ob.rank IS NOT NULL)
        FROM observations ob
        JOIN runs r ON r.id = ob.run_id
        WHERE r.source = 'backtest'
        """
    ).fetchone()

    if not scope or not scope[1]:
        return (
            "<h2>Исследование</h2>"
            '<p class="empty">Данных бэктеста пока нет. '
            "Запустите <code>python -m src.backtest</code>.</p>"
        )

    sections = "".join(render_study(connection, query) for query in QUERIES)
    caveats = "".join(f"<li>{escape(text)}</li>" for text in CAVEATS)

    return (
        "<h2>Исследование</h2>"
        f'<p class="sub">Прогонов бэктеста {scope[0]}, наблюдений {scope[1]}, '
        f"из них кандидатов {scope[2]}. Столбик показывает колонку с ответом.</p>"
        f"{sections}"
        f'<div class="caveats"><p class="why">Чего эти числа не доказывают</p>'
        f"<ul>{caveats}</ul></div>"
    )


def build_page(connection: sqlite3.Connection) -> str:
    """Собрать страницу целиком."""
    generated = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sections = "".join(render_exchange(connection, client) for client in EXCHANGES)
    research = render_research(connection)

    return (
        "<!doctype html>"
        '<html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Market Pulse</title>"
        f"<style>{STYLE}</style></head><body><div class=\"wrap\">"
        "<h1>Market Pulse</h1>"
        '<p class="sub">Инструменты, которые ведут себя необычно относительно '
        "своего обычного состояния. Не сигналы на покупку — список для "
        "ручного просмотра.</p>"
        f'<p class="sub num">страница собрана {generated}</p>'
        f"{sections}"
        f"{research}"
        "<footer>Данные обновляются командой <code>python -m src.main</code>, "
        "страница пересобирается командой <code>python -m src.report_html</code>. "
        "Направление движения система не предсказывает.</footer>"
        f"</div><script>{SCRIPT}</script></body></html>"
    )


def main() -> None:
    connection = connect()
    try:
        page = build_page(connection)
    finally:
        connection.close()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(page, encoding="utf-8")
    print(f"Страница записана: {OUTPUT_PATH.resolve()}")


if __name__ == "__main__":
    main()
