import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

matplotlib.use("Agg")

SUPPORTED_CHART_TYPES = ["line", "bar", "scatter", "hist", "box", "area", "pie", "violin", "heatmap"]
logger = logging.getLogger("python_service.charts")


def to_plot_label(value: Any) -> str:
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        value = value.tolist()
    if isinstance(value, (list, tuple, set, dict)):
        return json.dumps(value, default=str, sort_keys=True)
    try:
        if pd.isna(value):
            return "null"
    except Exception:
        pass
    return str(value)


def to_1d_series(df: pd.DataFrame, field: str) -> pd.Series:
    raw = df[field]
    if isinstance(raw, pd.DataFrame):
        if raw.shape[1] == 0:
            return pd.Series([], dtype="object")
        raw = raw.iloc[:, 0]
    if isinstance(raw, pd.Series):
        return raw
    return pd.Series(raw)


def choose_chart_specs(result_df: pd.DataFrame, chart_type: str) -> list[dict[str, Any]]:
    if result_df.empty or len(result_df.columns) == 0:
        return [{"type": "none", "xField": None, "yField": None}]

    cols = list(result_df.columns)
    numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(result_df[c])]
    text_cols = [c for c in cols if c not in numeric_cols]

    if chart_type == "all":
        selected_types = SUPPORTED_CHART_TYPES
    elif chart_type == "auto":
        selected_types = ["bar" if text_cols and numeric_cols else "line"]
    elif chart_type in SUPPORTED_CHART_TYPES:
        selected_types = [chart_type]
    else:
        selected_types = ["line"]

    x_field = text_cols[0] if text_cols else cols[0]
    y_field = numeric_cols[0] if numeric_cols else (cols[1] if len(cols) > 1 else cols[0])

    return [{"type": selected_type, "xField": x_field, "yField": y_field} for selected_type in selected_types]


def render_chart_png(result_df: pd.DataFrame, chart_spec: dict[str, Any], chart_dir: Path) -> Optional[str]:
    if chart_spec.get("type") == "none":
        return None

    x_field = chart_spec.get("xField")
    y_field = chart_spec.get("yField")
    if not x_field or not y_field:
        return None
    if x_field not in result_df.columns or y_field not in result_df.columns:
        return None

    chart_type = chart_spec.get("type")
    plot_df = result_df[[x_field, y_field]].head(200)
    if plot_df.empty:
        return None

    x_series = to_1d_series(plot_df, x_field)
    y_series = to_1d_series(plot_df, y_field)

    x_values = x_series.map(to_plot_label)
    y_numeric = pd.to_numeric(y_series, errors="coerce")

    chart_id = f"chart_{uuid.uuid4().hex}.png"
    chart_path = chart_dir / chart_id

    try:
        plt.figure(figsize=(10, 5))
        if chart_type == "line":
            if y_numeric.dropna().empty:
                return None
            plt.plot(x_values, y_numeric.fillna(0), marker="o")
        elif chart_type == "bar":
            if y_numeric.dropna().empty:
                return None
            plt.bar(x_values, y_numeric.fillna(0))
        elif chart_type == "scatter":
            if y_numeric.dropna().empty:
                return None
            plt.scatter(x_values, y_numeric.fillna(0), alpha=0.8)
        elif chart_type == "hist":
            hist_series = y_numeric.dropna()
            if hist_series.empty:
                return None
            plt.hist(hist_series, bins=20)
            plt.xlabel(str(y_field))
            plt.ylabel("Frequency")
        elif chart_type == "box":
            box_series = y_numeric.dropna()
            if box_series.empty:
                return None
            plt.boxplot(box_series)
            plt.xticks([1], [str(y_field)])
        elif chart_type == "area":
            area_series = y_numeric.fillna(0)
            if area_series.empty:
                return None
            plt.fill_between(range(len(area_series)), area_series)
            plt.xticks(range(len(x_values)), x_values, rotation=45, ha="right")
        elif chart_type == "pie":
            pie_source = pd.DataFrame({"x": x_values, "y": y_numeric.fillna(0)})
            pie_df = pie_source.groupby("x", dropna=False)["y"].sum().head(20)
            pie_df = pie_df[pie_df != 0]
            if pie_df.empty:
                return None
            plt.pie(pie_df.values, labels=pie_df.index.astype(str), autopct="%1.1f%%")
        elif chart_type == "violin":
            violin_series = y_numeric.dropna()
            if violin_series.empty:
                return None
            plt.violinplot(violin_series)
            plt.xticks([1], [str(y_field)])
        elif chart_type == "heatmap":
            numeric = result_df.select_dtypes(include="number")
            if numeric.shape[1] < 2:
                return None
            corr = numeric.corr().fillna(0)
            plt.imshow(corr, cmap="viridis", aspect="auto")
            plt.colorbar()
            plt.xticks(range(len(corr.columns)), corr.columns, rotation=45, ha="right")
            plt.yticks(range(len(corr.index)), corr.index)
        else:
            if y_numeric.dropna().empty:
                return None
            plt.bar(x_values, y_numeric.fillna(0))

        plt.title(f"{chart_type}: {y_field} by {x_field}")
        if chart_type not in {"hist", "pie", "heatmap", "violin"}:
            plt.xlabel(str(x_field))
            plt.ylabel(str(y_field))
            plt.xticks(rotation=45, ha="right")
        try:
            plt.tight_layout()
        except Exception as layout_error:
            logger.warning("Chart tight_layout failed for %s (%s): %s", chart_type, chart_id, layout_error)
        plt.savefig(chart_path)
        return chart_id
    except Exception as chart_error:
        logger.warning("Chart render failed for type=%s spec=%s: %s", chart_type, chart_spec, chart_error)
        return None
    finally:
        plt.close()


def render_charts(result_df: pd.DataFrame, chart_type: str, chart_dir: Path) -> list[dict[str, Any]]:
    specs = choose_chart_specs(result_df, chart_type)
    rendered: list[dict[str, Any]] = []
    for spec in specs:
        chart_file_name = render_chart_png(result_df, spec, chart_dir)
        if chart_file_name:
            item = dict(spec)
            item["downloadUrl"] = f"/charts/{chart_file_name}"
            rendered.append(item)

    if not rendered:
        return [{"type": "none", "xField": None, "yField": None}]
    return rendered
