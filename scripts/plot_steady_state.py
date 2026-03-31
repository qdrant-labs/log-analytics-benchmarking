#!/usr/bin/env python3
"""
Plot steady-state statistics across log rates from the aggregated CSV.

Produces a vertical stack of three panels (QPS, p50 latency, p99 latency)
with log rate on the x-axis, one line per backend, faceted by phase.

Usage:
    python plot_steady_state.py
    python plot_steady_state.py --input steady_state.csv --output steady_state.svg
"""

import argparse
from pathlib import Path

import polars as pl
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# match analysis.py colors
COLORS = {
    "Qdrant": "#DC244C",
    "Elasticsearch": "#4DBBD5",
    "OpenSearch": "#005EB8",
    "pgvector": "#00A67E",
}

DISPLAY_NAMES = {
    "qdrant": "Qdrant",
    "elasticsearch": "Elasticsearch",
    "opensearch": "OpenSearch",
    "pgvector": "pgvector",
}

PHASE_ORDER = ["pre_write", "during_write", "post_write"]
PHASE_LABELS = {
    "pre_write": "Pre-write",
    "during_write": "During write",
    "post_write": "Post-write",
}

PANELS = [
    ("qps", "QPS", "queries s⁻¹"),
    ("p50_ms", "p50 latency", "ms"),
    ("p99_ms", "p99 latency", "ms"),
]


def main():
    parser = argparse.ArgumentParser(description="Plot steady-state stats across log rates")
    parser.add_argument("--input", default="steady_state.csv", help="Input CSV (default: steady_state.csv)")
    parser.add_argument("--output", default="steady_state.svg", help="Output file (default: steady_state.svg)")
    args = parser.parse_args()

    df = pl.read_csv(args.input)

    # map backend names to display names
    df = df.with_columns(
        pl.col("backend").replace_strict(DISPLAY_NAMES, default=pl.col("backend")).alias("display_name")
    )

    backends = sorted(df["display_name"].unique().to_list(), key=lambda b: list(COLORS.keys()).index(b) if b in COLORS else 99)
    n_rows = len(PANELS)
    n_cols = len(PHASE_ORDER)

    fig = make_subplots(
        rows=n_rows,
        cols=n_cols,
        subplot_titles=[
            f"<b>{PHASE_LABELS[phase]}</b>"
            for phase in PHASE_ORDER
        ] + [""] * (n_rows * n_cols - n_cols),
        horizontal_spacing=0.08,
        vertical_spacing=0.10,
        shared_xaxes=True,
    )

    for row_idx, (metric, label, unit) in enumerate(PANELS, start=1):
        metric_df = df.filter(pl.col("metric") == metric)

        for col_idx, phase in enumerate(PHASE_ORDER, start=1):
            phase_df = metric_df.filter(pl.col("phase") == phase)

            for backend in backends:
                bdf = phase_df.filter(pl.col("display_name") == backend).sort("log_rate")
                if bdf.is_empty():
                    continue

                color = COLORS.get(backend, "#888888")
                show_legend = row_idx == 1 and col_idx == 1

                # line with mean value
                fig.add_trace(
                    go.Scatter(
                        x=bdf["log_rate"].to_list(),
                        y=bdf["mean"].to_list(),
                        mode="lines+markers",
                        name=backend,
                        legendgroup=backend,
                        showlegend=show_legend,
                        line=dict(color=color, width=2),
                        marker=dict(size=6, color=color),
                    ),
                    row=row_idx,
                    col=col_idx,
                )

                # error band: p25–p75
                if bdf["p25"].null_count() == 0 and bdf["p75"].null_count() == 0:
                    x = bdf["log_rate"].to_list()
                    y_upper = bdf["p75"].to_list()
                    y_lower = bdf["p25"].to_list()
                    # convert hex color to rgba for fill
                    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
                    fill_color = f"rgba({r},{g},{b},0.15)"

                    fig.add_trace(
                        go.Scatter(
                            x=x + x[::-1],
                            y=y_upper + y_lower[::-1],
                            fill="toself",
                            fillcolor=fill_color,
                            line=dict(width=0),
                            showlegend=False,
                            legendgroup=backend,
                            hoverinfo="skip",
                        ),
                        row=row_idx,
                        col=col_idx,
                    )

        # y-axis labels (left column only)
        fig.update_yaxes(title_text=unit, row=row_idx, col=1)

    # x-axis labels (bottom row only)
    for col_idx in range(1, n_cols + 1):
        fig.update_xaxes(title_text="Log rate (logs/sec)", row=n_rows, col=col_idx)

    # row labels on the right margin
    annotations = list(fig.layout.annotations)
    for row_idx, (_, label, _) in enumerate(PANELS):
        annotations.append(dict(
            text=f"<b>{label}</b>",
            xref="paper", yref="paper",
            x=1.02,
            y=1 - (row_idx + 0.5) / n_rows,
            showarrow=False,
            textangle=90,
            font=dict(size=13),
        ))
    fig.update_layout(annotations=annotations)

    fig.update_layout(
        width=900,
        height=900,
        template="plotly_white",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
        ),
        margin=dict(l=60, r=60, t=80, b=50),
    )

    output = Path(args.output)
    if output.suffix == ".svg":
        fig.write_image(str(output), format="svg")
    elif output.suffix == ".png":
        fig.write_image(str(output), format="png", scale=2)
    elif output.suffix == ".html":
        fig.write_html(str(output))
    else:
        fig.write_image(str(output))

    print(f"Saved → {output}")


if __name__ == "__main__":
    main()