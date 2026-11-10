"""Data analysis and rendering for the artifact layer.

The numbers come from pandas and the pictures from matplotlib, both already
dependencies. Nothing here calls a model: a chart the model asked for is drawn
from the user's actual data, which is the only way its labels can be trusted.
"""

from __future__ import annotations

import html
import io
from typing import Any

MAX_ROWS = 200_000
CHART_TYPES = ("bar", "line", "scatter", "hist", "pie")


class AnalysisError(ValueError):
    """The request was understood but cannot be satisfied with this data."""


def _frame(raw: bytes, *, name: str = "data.csv"):
    import pandas as pd

    if not raw:
        raise AnalysisError("The file is empty.")
    buffer = io.BytesIO(raw)
    try:
        if name.lower().endswith((".xlsx", ".xls")):
            frame = pd.read_excel(buffer)
        elif name.lower().endswith(".json"):
            frame = pd.read_json(buffer)
        else:
            frame = pd.read_csv(buffer)
    except Exception as exc:
        raise AnalysisError(f"This file could not be read as a table: {exc}") from exc
    if frame.empty:
        raise AnalysisError("The table has no rows.")
    if len(frame) > MAX_ROWS:
        raise AnalysisError(f"The table has {len(frame)} rows; the limit is {MAX_ROWS}.")
    return frame


def summarise(raw: bytes, *, name: str = "data.csv", sample_rows: int = 5) -> dict[str, Any]:
    """Profile a table: shape, column types, missing values and a sample."""
    frame = _frame(raw, name=name)
    columns = []
    for column in frame.columns:
        series = frame[column]
        entry = {
            "name": str(column),
            "dtype": str(series.dtype),
            "missing": int(series.isna().sum()),
            "unique": int(series.nunique(dropna=True)),
        }
        if str(series.dtype).startswith(("int", "float")):
            numeric = series.dropna()
            if not numeric.empty:
                entry.update({
                    "min": float(numeric.min()), "max": float(numeric.max()),
                    "mean": round(float(numeric.mean()), 4),
                    "median": round(float(numeric.median()), 4),
                })
        else:
            top = series.dropna().astype(str).value_counts().head(3)
            entry["top_values"] = [{"value": k, "count": int(v)} for k, v in top.items()]
        columns.append(entry)

    sample = frame.head(sample_rows).copy()
    for column in sample.columns:
        sample[column] = sample[column].astype(str).str.slice(0, 80)
    return {
        "rows": int(len(frame)),
        "columns": columns,
        "missing_total": int(frame.isna().sum().sum()),
        "sample": sample.to_dict(orient="records"),
        "sample_columns": [str(c) for c in frame.columns],
    }


def summarise_text(profile: dict[str, Any]) -> str:
    """A compact, model-readable rendering of :func:`summarise`."""
    lines = [f"{profile['rows']} rows, {len(profile['columns'])} columns, "
             f"{profile['missing_total']} missing values."]
    for column in profile["columns"]:
        detail = f"- {column['name']} ({column['dtype']})"
        if "mean" in column:
            detail += (f": min {column['min']}, max {column['max']}, "
                       f"mean {column['mean']}, median {column['median']}")
        detail += f", missing {column['missing']}"
        if column.get("top_values"):
            top = ", ".join(f"{t['value']} ({t['count']})" for t in column["top_values"])
            detail += f", most common: {top}"
        lines.append(detail)
    if profile.get("sample"):
        lines.append("First rows:")
        lines.append(" | ".join(profile["sample_columns"]))
        for row in profile["sample"]:
            lines.append(" | ".join(str(row.get(c, "")) for c in profile["sample_columns"]))
    return "\n".join(lines)


def chart_png(raw: bytes, *, chart_type: str = "bar", x: str = "", y: str = "",
              title: str = "", name: str = "data.csv") -> tuple[bytes, dict[str, Any]]:
    """Render one chart from a real table and return PNG bytes plus metadata."""
    if chart_type not in CHART_TYPES:
        raise AnalysisError("Chart type must be one of: " + ", ".join(CHART_TYPES))
    frame = _frame(raw, name=name)
    columns = [str(c) for c in frame.columns]
    if not x:
        x = columns[0]
    if x not in columns:
        raise AnalysisError(f"There is no column called {x!r}. Columns: " + ", ".join(columns))

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(figsize=(7.2, 4.2), dpi=144)
    try:
        if chart_type == "hist":
            series = frame[x]
            if not str(series.dtype).startswith(("int", "float")):
                raise AnalysisError(f"{x!r} is not numeric, so it cannot be a histogram.")
            axes.hist(series.dropna(), bins=min(30, max(5, series.nunique())))
            axes.set_ylabel("count")
        elif chart_type == "pie":
            counts = frame[x].astype(str).value_counts().head(8)
            axes.pie(counts.values, labels=counts.index, autopct="%1.0f%%")
        else:
            if not y:
                numeric = [c for c in columns
                           if str(frame[c].dtype).startswith(("int", "float"))]
                if not numeric:
                    raise AnalysisError("This table has no numeric column to plot.")
                y = numeric[0]
            if y not in columns:
                raise AnalysisError(f"There is no column called {y!r}. Columns: "
                                    + ", ".join(columns))
            if not str(frame[y].dtype).startswith(("int", "float")):
                raise AnalysisError(f"{y!r} is not numeric.")
            if chart_type == "bar":
                grouped = frame.groupby(frame[x].astype(str))[y].mean().head(20)
                axes.bar(grouped.index, grouped.values)
                axes.tick_params(axis="x", labelrotation=45)
                axes.set_ylabel(f"mean {y}")
            elif chart_type == "line":
                axes.plot(frame[x].astype(str), frame[y])
                axes.tick_params(axis="x", labelrotation=45)
                axes.set_ylabel(y)
            else:
                axes.scatter(frame[x], frame[y])
                axes.set_xlabel(x)
                axes.set_ylabel(y)
        axes.set_title(title or f"{chart_type}: {x}" + (f" vs {y}" if y else ""))
        figure.tight_layout()
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", bbox_inches="tight")
    finally:
        plt.close(figure)
    return buffer.getvalue(), {"chart_type": chart_type, "x": x, "y": y,
                               "title": title, "rows": int(len(frame))}


def report_html(title: str, sections: list[dict[str, str]]) -> str:
    """A standalone HTML report with the user's text escaped.

    Sections are ``{"heading": ..., "body": ...}``; bodies are plain text and
    are escaped, because a report is a document, not a template engine.
    """
    safe_title = html.escape(title or "Report")
    parts = []
    for section in sections or []:
        heading = html.escape(str(section.get("heading") or ""))
        body = html.escape(str(section.get("body") or ""))
        if heading:
            parts.append(f"<h2>{heading}</h2>")
        if body:
            paragraphs = "".join(f"<p>{line}</p>" for line in body.split("\n\n") if line.strip())
            parts.append(paragraphs or "")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
:root{{--ink:#22201d;--muted:#5b544c;--line:#e3ddd3;--paper:#fbf8f3;--accent:#8a5a2b}}
body{{margin:0;background:var(--paper);color:var(--ink);
font:16px/1.6 ui-serif,Georgia,'Times New Roman',serif}}
main{{max-width:46rem;margin:0 auto;padding:3rem 1.5rem 5rem}}
h1{{font-size:2rem;line-height:1.2;margin:0 0 .35rem;letter-spacing:-.01em}}
.meta{{color:var(--muted);font:13px/1.4 ui-sans-serif,system-ui;margin-bottom:2.5rem}}
h2{{font-size:1.15rem;margin:2.2rem 0 .6rem;padding-bottom:.35rem;
border-bottom:1px solid var(--line);font-family:ui-sans-serif,system-ui}}
p{{margin:.6rem 0;white-space:pre-wrap}}
footer{{margin-top:3rem;color:var(--muted);font:12px/1.4 ui-sans-serif,system-ui}}
</style></head>
<body><main>
<h1>{safe_title}</h1>
<div class="meta">Produced by Tolesh</div>
{''.join(parts)}
<footer>Generated locally. Figures come from the supplied data, not from a model.</footer>
</main></body></html>"""
