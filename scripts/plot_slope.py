#!/usr/bin/env python3
"""
Slope charts showing how each backend's metrics change across phases.

One panel per metric (QPS, p50, p99), stacked vertically.
Faded lines show individual log rates; bold line shows the mean across rates.

Usage:
    python plot_slope.py
    python plot_slope.py --input steady_state.csv --output slope.svg
"""

import argparse
from pathlib import Path

import numpy as np
import polars as pl
import plotly.graph_objects as go
from plotly.subplots import make_subplots

DISPLAY_NAMES = {
    "qdrant": "Qdrant",
    "elasticsearch": "Elasticsearch",
    "opensearch": "OpenSearch",
    "pgvector": "pgvector",
}

BACKEND_ORDER = ["Qdrant", "Elasticsearch", "OpenSearch", "pgvector"]

COLORS = {
    "Qdrant": (220, 36, 76),
    "Elasticsearch": (77, 187, 213),
    "OpenSearch": (0, 94, 184),
    "pgvector": (0, 166, 126),
}

PHASE_ORDER = ["pre_write", "during_write", "post_write"]
PHASE_LABELS = ["Pre-write", "During write", "Post-write"]

METRICS = [
    ("qps", "QPS", "queries s⁻¹"),
    ("p50_ms", "p50 latency", "ms"),
    ("p99_ms", "p99 latency", "ms"),
]


def rgb(color_tuple, alpha=1.0):
    r, g, b = color_tuple
    if alpha < 1.0:
        return f"rgba({r},{g},{b},{alpha})"
    return f"rgb({r},{g},{b})"


def main():
    parser = argparse.ArgumentParser(description="Slope charts across phases")
    parser.add_argument("--input", default="steady_state.csv", help="Input CSV")
    parser.add_argument("--output", default="slope.svg", help="Output file")
    args = parser.parse_args()

    df = pl.read_csv(args.input)
    df = df.with_columns(
        pl.col("backend").replace_strict(DISPLAY_NAMES, default=pl.col("backend")).alias("display_name")
    )

    backends = [b for b in BACKEND_ORDER if b in df["display_name"].unique().to_list()]
    log_rates = sorted(df["log_rate"].unique().to_list())

    n_rows = len(METRICS)

    fig = make_subplots(
        rows=n_rows,
        cols=1,
        subplot_titles=[f"<b>{label}</b>" for _, label, _ in METRICS],
        vertical_spacing=0.12,
    )

    for row_idx, (metric, label, unit) in enumerate(METRICS, start=1):
        metric_df = df.filter(pl.col("metric") == metric)

        for backend in backends:
            backend_df = metric_df.filter(pl.col("display_name") == backend)
            color_t = COLORS.get(backend, (136, 136, 136))

            # individual log-rate lines (faded)
            for rate in log_rates:
                rate_df = backend_df.filter(pl.col("log_rate") == rate)

                phase_vals = []
                for phase in PHASE_ORDER:
                    row = rate_df.filter(pl.col("phase") == phase)
                    phase_vals.append(row["mean"][0] if not row.is_empty() else None)

                fig.add_trace(
                    go.Scatter(
                        x=PHASE_LABELS,
                        y=phase_vals,
                        mode="lines+markers",
                        legendgroup=backend,
                        showlegend=False,
                        line=dict(color=rgb(color_t, 0.2), width=1.5),
                        marker=dict(size=5, color=rgb(color_t, 0.2)),
                        hovertext=f"{backend} @ {rate:.0f} logs/s",
                        hoverinfo="text+y",
                    ),
                    row=row_idx,
                    col=1,
                )

            # mean across log rates (bold)
            mean_vals = []
            for phase in PHASE_ORDER:
                phase_df = backend_df.filter(pl.col("phase") == phase)
                if phase_df.is_empty():
                    mean_vals.append(None)
                else:
                    mean_vals.append(round(float(np.mean(phase_df["mean"].to_list())), 2))

            show_legend = row_idx == 1

            fig.add_trace(
                go.Scatter(
                    x=PHASE_LABELS,
                    y=mean_vals,
                    mode="lines+markers",
                    name=backend,
                    legendgroup=backend,
                    showlegend=show_legend,
                    line=dict(color=rgb(color_t), width=3.5),
                    marker=dict(size=10, color=rgb(color_t)),
                ),
                row=row_idx,
                col=1,
            )

        fig.update_yaxes(title_text=unit, row=row_idx, col=1)

    fig.update_layout(
        width=500,
        height=300 * n_rows,
        template="plotly_white",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
        ),
        margin=dict(l=70, r=30, t=80, b=50),
    )

    output = Path(args.output)
    if output.suffix == ".html":
        fig.write_html(str(output))
    elif output.suffix == ".png":
        fig.write_image(str(output), format="png", scale=2)
    else:
        fig.write_image(str(output))

    print(f"Saved → {output}")


if __name__ == "__main__":
    main()