import argparse
import json
from pathlib import Path

import polars as pl
import plotly.graph_objects as go
from plotly.subplots import make_subplots

parser = argparse.ArgumentParser(description='Analyze benchmark results')
parser.add_argument('results_dir', help='Path to results directory (e.g. results/2026-02-11T08-17-00)')
parser.add_argument('--skip-metrics', action='store_true', help='Skip infrastructure metrics plots')
RESULTS_DIR = parser.parse_args().results_dir
SKIP_METRICS = parser.parse_args().skip_metrics

# brand colors
COLORS = {
    'Qdrant': '#DC244C',
    'Elasticsearch': '#4DBBD5',
    'pgvector': '#00A67E',
}


def load_jsonl(filepath):
    with open(filepath, 'r') as f:
        return [json.loads(line) for line in f]


def load_backend(results_dir, name):
    data = load_jsonl(f'{results_dir}/{name}.jsonl')
    df = pl.DataFrame(data)
    df = df.with_columns(pl.col('timestamp').str.to_datetime(time_zone='UTC'))
    df = df.unnest('latency')
    # μs → ms
    us_cols = [c for c in df.columns if c.endswith('_us')]
    df = df.with_columns([
        (pl.col(c) / 1000).alias(c.replace('_us', '_ms'))
        for c in us_cols
    ])
    return df


# load metadata for phase boundaries
with open(f'{RESULTS_DIR}/metadata.json') as f:
    metadata = json.load(f)

# auto-detect backends from JSONL files in the results directory
BACKEND_DISPLAY_NAMES = {
    'elasticsearch': 'Elasticsearch',
    'qdrant': 'Qdrant',
    'pgvector': 'pgvector',
}

backends = {}
for jsonl_file in sorted(Path(RESULTS_DIR).glob('*.jsonl')):
    key = jsonl_file.stem  # e.g. "qdrant", "elasticsearch", "pgvector"
    display_name = BACKEND_DISPLAY_NAMES.get(key, key)
    backends[display_name] = load_backend(RESULTS_DIR, key)
# use qstorm start as the common t=0 so phase lines align with data
t0 = pl.Series([metadata['t_qstorm_start']]).str.to_datetime(time_zone='UTC')[0]
for name in backends:
    backends[name] = backends[name].with_columns(
        ((pl.col('timestamp') - t0).dt.total_milliseconds() / 1000).alias('elapsed_s')
    )

# panels: QPS, Mean Latency, p95, p99
fig = make_subplots(
    rows=2, cols=2,
    subplot_titles=(
        '<b>a</b>  QPS',
        '<b>b</b>  Mean latency',
        '<b>c</b>  p95 latency',
        '<b>d</b>  p99 latency',
    ),
    horizontal_spacing=0.10,
    vertical_spacing=0.15,
)

panels = [
    (1, 1, 'qps'),
    (1, 2, 'mean_ms'),
    (2, 1, 'p95_ms'),
    (2, 2, 'p99_ms'),
]

for name, df in backends.items():
    color = COLORS[name]
    for i, (row, col, y_col) in enumerate(panels):
        fig.add_trace(go.Scatter(
            x=df['elapsed_s'], y=df[y_col],
            mode='lines', name=name,
            line=dict(color=color, width=1.5),
            legendgroup=name,
            showlegend=(i == 0),
        ), row=row, col=col)

# phase boundaries as vertical lines
phase_markers = {
    'Heavy write ON':  metadata.get('t_heavy_start') or metadata['t_steady_end'],
    'Heavy write OFF': metadata['t_heavy_end'],
}
for label, ts_str in phase_markers.items():
    ts = pl.Series([ts_str]).str.to_datetime(time_zone='UTC')[0]
    x_sec = (ts - t0).total_seconds()
    for row, col, _ in panels:
        fig.add_vline(
            x=x_sec, row=row, col=col,
            line_width=1, line_dash='dash', line_color='#888888',
            annotation=dict(
                text=label, font_size=9, font_color='#888888',
                textangle=-90, yanchor='top', yref='y domain', y=0.95,
            ) if row == 1 and col == 1 else None,
        )

# axis labels
fig.update_yaxes(title_text='queries s<sup>-1</sup>', type='log', row=1, col=1)
fig.update_yaxes(title_text='ms', row=1, col=2)
fig.update_yaxes(title_text='ms', row=2, col=1)
fig.update_yaxes(title_text='ms', row=2, col=2)
for col in (1, 2):
    fig.update_xaxes(title_text='Time (s)', row=2, col=col)

# Nature style
fig.update_layout(
    font=dict(family='Arial', size=12),
    plot_bgcolor='white',
    paper_bgcolor='white',
    width=900,
    height=650,
    margin=dict(t=40, b=50, l=60, r=20),
    legend=dict(
        orientation='h',
        yanchor='bottom', y=1.04,
        xanchor='center', x=0.5,
        font=dict(size=11),
    ),
    hovermode='x unified',
)

fig.update_xaxes(
    showgrid=False,
    showline=True, linewidth=1, linecolor='black',
    ticks='outside', ticklen=4, tickwidth=1, tickcolor='black',
)
fig.update_yaxes(
    showgrid=False,
    showline=True, linewidth=1, linecolor='black',
    ticks='outside', ticklen=4, tickwidth=1, tickcolor='black',
)

# bold the panel labels (plotly stores subplot_titles as annotations)
for ann in fig.layout.annotations:
    ann.font = dict(family='Arial', size=13)
    ann.xanchor = 'left'
    ann.x = ann.x - 0.04

fig.show()
# save figure as svg
fig.write_image(f'{RESULTS_DIR}/benchmark_results.svg')


# infrastructure metrics (CPU, memory) from CloudWatch CSVs

def load_metrics_csv(results_dir, backend, metric):
    """
    Load a metrics CSV (e.g. qdrant_cpu.csv) and return a polars DataFrame.
    """
    path = Path(results_dir) / f'{backend}_{metric}.csv'
    if not path.exists():
        return None
    df = pl.read_csv(str(path))
    col = 'cpu_percent' if metric == 'cpu' else 'memory_percent'
    df = df.with_columns(pl.col('timestamp').str.to_datetime(time_zone='UTC'))
    df = df.with_columns(
        ((pl.col('timestamp') - t0).dt.total_milliseconds() / 1000).alias('elapsed_s')
    )
    df = df.filter(pl.col('elapsed_s') >= 0)
    return df.select(['elapsed_s', col])


if not SKIP_METRICS:
    # check which backends have metrics files
    metrics_backends = {}
    for backend_name, display_name in BACKEND_DISPLAY_NAMES.items():
        cpu_df = load_metrics_csv(RESULTS_DIR, backend_name, 'cpu')
        mem_df = load_metrics_csv(RESULTS_DIR, backend_name, 'memory')
        if cpu_df is not None or mem_df is not None:
            metrics_backends[display_name] = {
                'cpu': cpu_df,
                'memory': mem_df,
                'backend_key': backend_name,
            }

    if metrics_backends:
        metrics_fig = make_subplots(
            rows=1, cols=2,
            subplot_titles=(
                '<b>a</b>  CPU utilization',
                '<b>b</b>  Memory utilization',
            ),
            horizontal_spacing=0.12,
        )

        for i, (name, data) in enumerate(metrics_backends.items()):
            color = COLORS[name]

            if data['cpu'] is not None:
                metrics_fig.add_trace(go.Scatter(
                    x=data['cpu']['elapsed_s'],
                    y=data['cpu']['cpu_percent'],
                    mode='lines', name=name,
                    line=dict(color=color, width=1.5),
                    legendgroup=name,
                    showlegend=(i == 0 or name not in [list(metrics_backends.keys())[0]]),
                ), row=1, col=1)

            if data['memory'] is not None:
                metrics_fig.add_trace(go.Scatter(
                    x=data['memory']['elapsed_s'],
                    y=data['memory']['memory_percent'],
                    mode='lines', name=name,
                    line=dict(color=color, width=1.5),
                    legendgroup=name,
                    showlegend=data['cpu'] is None,
                ), row=1, col=2)

        # phase boundaries
        for label, ts_str in phase_markers.items():
            ts = pl.Series([ts_str]).str.to_datetime(time_zone='UTC')[0]
            x_sec = (ts - t0).total_seconds()
            for col_idx in (1, 2):
                metrics_fig.add_vline(
                    x=x_sec, row=1, col=col_idx,
                    line_width=1, line_dash='dash', line_color='#888888',
                    annotation=dict(
                        text=label, font_size=9, font_color='#888888',
                        textangle=-90, yanchor='top', yref='y domain', y=0.95,
                    ) if col_idx == 1 else None,
                )

        # axis labels + 0-100 range
        metrics_fig.update_yaxes(title_text='CPU %', range=[0, 100], row=1, col=1)
        metrics_fig.update_yaxes(title_text='Memory %', range=[0, 100], row=1, col=2)
        for col_idx in (1, 2):
            metrics_fig.update_xaxes(title_text='Time (s)', row=1, col=col_idx)

        # Nature style
        metrics_fig.update_layout(
            font=dict(family='Arial', size=12),
            plot_bgcolor='white',
            paper_bgcolor='white',
            width=900,
            height=350,
            margin=dict(t=40, b=50, l=60, r=20),
            legend=dict(
                orientation='h',
                yanchor='bottom', y=1.04,
                xanchor='center', x=0.5,
                font=dict(size=11),
            ),
            hovermode='x unified',
        )
        metrics_fig.update_xaxes(
            showgrid=False,
            showline=True, linewidth=1, linecolor='black',
            ticks='outside', ticklen=4, tickwidth=1, tickcolor='black',
        )
        metrics_fig.update_yaxes(
            showgrid=False,
            showline=True, linewidth=1, linecolor='black',
            ticks='outside', ticklen=4, tickwidth=1, tickcolor='black',
        )
        for ann in metrics_fig.layout.annotations:
            ann.font = dict(family='Arial', size=13)
            ann.xanchor = 'left'
            ann.x = ann.x - 0.04

        metrics_fig.show()
        metrics_fig.write_image(f'{RESULTS_DIR}/infra_metrics.svg')
        print(f'Infrastructure metrics saved to {RESULTS_DIR}/infra_metrics.svg')
    else:
        print('No infrastructure metrics CSVs found — run collect_metrics.py first to generate them.')