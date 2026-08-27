from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "processed"


st.set_page_config(
    page_title="Tránsito Seguro | Importaciones",
    layout="wide",
    initial_sidebar_state="auto",
)

st.markdown(
    """
<style>
    .stApp { background: #F8FAFC; color: #102A43; }
    .block-container {
        padding: 1.1rem 1.25rem 2rem;
        max-width: 100%;
    }
    .app-hero {
        background: linear-gradient(135deg, rgba(16,42,67,0.98), rgba(36,123,160,0.92));
        border-radius: 8px;
        color: #FFFFFF;
        padding: 1.35rem 1.6rem 1.25rem;
        margin-bottom: 1rem;
        border: 1px solid rgba(255,255,255,.18);
        box-shadow: 0 18px 42px rgba(16,42,67,.22);
    }
    .app-hero h1 {
        color: #FFFFFF !important;
        font-size: 2rem;
        line-height: 1.12;
        margin: .18rem 0 .5rem;
        letter-spacing: 0;
    }
    .app-hero p {
        color: #E6F6FF !important;
        font-size: .98rem;
        margin: 0;
        max-width: 980px;
    }
    .eyebrow {
        color: #DDEAF4;
        font-size: .74rem;
        font-weight: 700;
        letter-spacing: .08em;
        text-transform: uppercase;
    }
    .stMetric,
    [data-testid="stMetric"] {
        border: 1px solid #AEBBC8;
        padding: .7rem .82rem;
        border-radius: 10px;
        background: linear-gradient(180deg, #FFFFFF 0%, #FBFCFE 100%);
        color: #102A43 !important;
        box-shadow: 0 14px 28px rgba(16,42,67,.13), 0 2px 5px rgba(16,42,67,.08);
    }
    .stMetric *,
    [data-testid="stMetric"] *,
    [data-testid="stMetricLabel"] p,
    [data-testid="stMetricValue"],
    [data-testid="stMetricValue"] div {
        color: #102A43 !important;
    }
    [data-testid="stMetricLabel"] p {
        color: #52616B !important;
        font-weight: 650;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.65rem !important;
        line-height: 1.12 !important;
        white-space: nowrap !important;
        font-variant-numeric: tabular-nums;
    }
    [data-testid="stMetricDelta"] *,
    [data-testid="stMetricDelta"] svg {
        color: #0B7285 !important;
        fill: #0B7285 !important;
    }
    [data-testid="stSidebar"] {
        background: #EDE7DC;
        border-right: 1px solid #B8AFA1;
        box-shadow: 8px 0 22px rgba(16,42,67,.07);
    }
    [data-testid="stSidebar"] * {
        color: #102A43;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: .35rem;
        border-bottom: 1px solid #B8C4D0;
    }
    .stTabs [data-baseweb="tab"] {
        color: #52616B;
        font-weight: 650;
    }
    .stTabs [aria-selected="true"] {
        color: #102A43;
    }
    [data-testid="stPlotlyChart"] {
        background: #FFFFFF;
        border: 1px solid #AEBBC8;
        border-radius: 8px;
        padding: .45rem .55rem;
        box-shadow: 0 12px 26px rgba(16,42,67,.10), 0 2px 5px rgba(16,42,67,.07);
    }
    [data-testid="stDataFrame"] {
        border: 1px solid #AEBBC8;
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 10px 22px rgba(16,42,67,.08);
    }
    .stDownloadButton button {
        border: 1px solid #8FA3B8;
        border-radius: 8px;
        background: #FFFFFF;
        color: #102A43;
        box-shadow: 0 8px 18px rgba(16,42,67,.09);
    }
    button:focus-visible,
    [role="button"]:focus-visible,
    input:focus,
    textarea:focus,
    select:focus {
        outline: 3px solid #0369A1 !important;
        outline-offset: 2px !important;
        box-shadow: 0 0 0 4px rgba(3,105,161,.18) !important;
    }
    .small-label { color: #5b6b7a; font-size: .9rem; }
    [data-testid="stMetricDelta"],
    [data-testid="stMetricDelta"] > div {
        background: transparent !important;
        color: #5b6b7a !important;
        padding: 0 !important;
        margin-top: .2rem !important;
    }
    [data-testid="stMetricDelta"] svg,
    [data-testid="stMetricDeltaIcon"] {
        display: none !important;
    }
    [data-tag],
    [data-tag] * {
        color: #FFFFFF !important;
    }
    @media (max-width: 768px) {
        .block-container { padding-left: .9rem; padding-right: .9rem; }
    }
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data
def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    df = pd.read_csv(DATA / "importaciones_limpias.csv", parse_dates=["fecha_dt"])
    model_summary = pd.read_csv(DATA / "resumen_milesight_por_modelo.csv")
    tx = pd.read_csv(DATA / "trafficx_ts5511_detalle.csv", parse_dates=["fecha_dt"])
    metrics = pd.read_json(DATA / "metricas_resumen.json", typ="series").to_dict()
    return df, model_summary, tx, metrics


def money(value: float) -> str:
    return f"USD {value:,.0f}"


def money2(value: float) -> str:
    return f"USD {value:,.2f}"


def number(value: float) -> str:
    return f"{value:,.0f}"


def pct(value: float, total: float) -> str:
    if total is None or pd.isna(value) or pd.isna(total) or total == 0:
        return "-"
    return f"{value / total:.1%}"


def money_k(value: float) -> str:
    if pd.isna(value):
        return "-"
    return f"USD {value / 1000:,.0f}k" if abs(value) >= 1000 else money(value)


def vs_average(value: float, baseline: float | None) -> str:
    if baseline is None or pd.isna(value) or pd.isna(baseline) or baseline == 0:
        return ""
    return f"{(value / baseline) - 1:+.1%} vs prom."


def style_bar_chart(
    fig,
    *,
    numeric_axis: str = "y",
    hide_numeric_ticklabels: bool = True,
    left_margin: int = 24,
    right_margin: int = 28,
) -> None:
    fig.update_layout(
        template="plotly_white",
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font={"family": "Inter, Segoe UI, Arial, sans-serif", "size": 13, "color": "#102A43"},
        title={"font": {"size": 18, "color": "#102A43"}, "x": 0.01, "xanchor": "left"},
        margin={"l": left_margin, "r": right_margin, "t": 58, "b": 38},
        bargap=0.28,
        uniformtext_minsize=10,
        uniformtext_mode="show",
    )
    fig.update_traces(
        texttemplate="%{text}",
        textfont={"color": "#102A43", "size": 12},
        cliponaxis=False,
        hoverlabel={"bgcolor": "white", "font_color": "#102A43"},
    )
    fig.update_xaxes(showgrid=False, zeroline=False, title=None, showline=False)
    fig.update_yaxes(showgrid=False, zeroline=False, title=None, showline=False)
    if hide_numeric_ticklabels:
        if numeric_axis == "x":
            fig.update_xaxes(showticklabels=False)
        else:
            fig.update_yaxes(showticklabels=False)


def render_purchase_table(data: pd.DataFrame, *, include_days: bool = False) -> None:
    columns = [
        "fecha_dt",
        "identificador",
        "cantidad_modelo",
        "usd_fob_unit_grupo",
        "fob_asignado_usd",
        "condicion_de_venta",
    ]
    if include_days:
        columns.insert(4, "dias_desde_anterior")

    labels = {
        "fecha_dt": "Fecha",
        "identificador": "Despacho",
        "cantidad_modelo": "Unidades",
        "usd_fob_unit_grupo": "FOB/unit",
        "fob_asignado_usd": "FOB asignado",
        "dias_desde_anterior": "Días vs anterior",
        "condicion_de_venta": "Incoterm",
    }
    config = {
        "Fecha": st.column_config.DateColumn(format="DD/MM/YYYY"),
        "Unidades": st.column_config.NumberColumn(format="%d"),
        "FOB/unit": st.column_config.NumberColumn(format="USD %.2f"),
        "FOB asignado": st.column_config.NumberColumn(format="USD %.0f"),
    }
    if include_days:
        config["Días vs anterior"] = st.column_config.NumberColumn(format="%d")

    st.dataframe(
        data[columns].rename(columns=labels),
        use_container_width=True,
        hide_index=True,
        height=260,
        column_config=config,
    )


df, model_summary, tx, metrics = load_data()

st.markdown(
    """
<div class="app-hero">
  <div class="eyebrow">Comercio Exterior</div>
  <h1>Tránsito Seguro | Importaciones Milesight</h1>
  <p>Panel ejecutivo para analizar gasto FOB/CIF, mix por modelo, frecuencia de compra y costo unitario declarado de equipos asociados a TrafficX.</p>
</div>
""",
    unsafe_allow_html=True,
)

all_models = sorted(m for m in df["modelo_limpio"].dropna().unique() if m != "SIN MODELO")
st.sidebar.markdown("### Filtros")
selected_models = st.sidebar.multiselect(
    "Modelos",
    all_models,
    default=["TS5511 GH", "TS5510 GVH"],
)

show_only_milesight = st.sidebar.checkbox("Sólo Milesight", value=True)
show_only_trafficx = st.sidebar.checkbox("Sólo TrafficX / TS", value=False)

date_min = df["fecha_dt"].min().date()
date_max = df["fecha_dt"].max().date()
date_range = st.sidebar.date_input("Rango de fechas", value=(date_min, date_max), min_value=date_min, max_value=date_max)
if isinstance(date_range, tuple):
    start_date, end_date = date_range
else:
    start_date, end_date = date_min, date_max

filtered = df[(df["fecha_dt"].dt.date >= start_date) & (df["fecha_dt"].dt.date <= end_date)].copy()
if show_only_milesight:
    filtered = filtered[filtered["es_milesight"]]
if show_only_trafficx:
    filtered = filtered[filtered["trafficx_ts_relacionado"] | filtered["trafficx_confirmado_modelo"]]
if selected_models:
    filtered = filtered[filtered["modelo_limpio"].isin(selected_models)]

if filtered.empty:
    st.warning("No hay datos para ese filtro.")
    st.stop()

selected_tx = tx[
    (tx["fecha_dt"].dt.date >= start_date)
    & (tx["fecha_dt"].dt.date <= end_date)
].copy()
if selected_models:
    selected_tx = selected_tx[selected_tx["modelo_limpio"].isin(selected_models)]

total_fob = filtered["fob_asignado_usd"].sum()
total_cif = filtered["cif_asignado_usd"].sum()
total_units = filtered["cantidad_modelo"].sum()
despachos = filtered["identificador"].nunique()
base_fob = float(metrics.get("total_fob_usd", df["fob_asignado_usd"].sum()))
base_cif = float(metrics.get("total_cif_usd", df["cif_asignado_usd"].sum()))
base_units = df["cantidad_modelo"].sum()
base_despachos = df["identificador"].nunique()
filter_avg_unit = total_fob / total_units if total_units else None

col1, col2, col3, col4 = st.columns(4)
col1.metric("FOB asignado", money_k(total_fob), f"{pct(total_fob, base_fob)} del FOB base", delta_color="off")
col2.metric("CIF asignado", money_k(total_cif), f"{pct(total_cif, base_cif)} del CIF base", delta_color="off")
col3.metric("Unidades", number(total_units), f"{pct(total_units, base_units)} de unidades", delta_color="off")
col4.metric("Despachos", number(despachos), f"{pct(despachos, base_despachos)} de despachos", delta_color="off")

tab1, tab2, tab3, tab4 = st.tabs(["Resumen", "TS5511-GH", "TS5510-GVH", "Datos"])

with tab1:
    monthly = (
        filtered.assign(mes=filtered["fecha_dt"].dt.to_period("M").astype(str))
        .groupby("mes", as_index=False)
        .agg(fob_usd=("fob_asignado_usd", "sum"), unidades=("cantidad_modelo", "sum"))
        .sort_values("mes")
    )
    monthly["share_fob"] = monthly["fob_usd"] / total_fob if total_fob else 0
    monthly["label"] = monthly.apply(lambda r: f"{money_k(r['fob_usd'])}<br>{r['share_fob']:.1%}", axis=1)
    model_rank = (
        filtered.groupby("modelo_limpio", as_index=False)
        .agg(unidades=("cantidad_modelo", "sum"), fob_usd=("fob_asignado_usd", "sum"))
        .assign(fob_unit_usd=lambda x: x["fob_usd"] / x["unidades"])
        .sort_values("fob_usd", ascending=True)
    )
    model_rank["share_fob"] = model_rank["fob_usd"] / total_fob if total_fob else 0
    model_rank["label"] = model_rank.apply(
        lambda r: (
            f"{money_k(r['fob_usd'])} | {r['share_fob']:.1%}<br>"
            f"{r['unidades']:,.0f} u. | USD {r['fob_unit_usd']:,.0f}/u"
        ),
        axis=1,
    )
    c1, c2 = st.columns(2)
    with c1:
        fig = px.bar(
            monthly,
            x="mes",
            y="fob_usd",
            text="label",
            custom_data=["share_fob", "unidades"],
            title="FOB asignado por mes",
        )
        fig.update_traces(
            textposition="outside",
            marker_color="#247BA0",
            hovertemplate=(
                "Mes %{x}<br>FOB USD %{y:,.0f}<br>"
                "Participación %{customdata[0]:.1%}<br>Unidades %{customdata[1]:,.0f}<extra></extra>"
            ),
        )
        style_bar_chart(fig)
        fig.update_yaxes(range=[0, monthly["fob_usd"].max() * 1.18])
        st.plotly_chart(fig, use_container_width=True)
    with c2:
        model_plot = model_rank.tail(10).copy()
        fig = px.bar(
            model_plot,
            x="fob_usd",
            y="modelo_limpio",
            orientation="h",
            text="label",
            title="Mix por modelo",
            custom_data=["unidades", "fob_unit_usd", "share_fob"],
        )
        fig.update_traces(
            marker_color="#00A896",
            textposition="outside",
            hovertemplate=(
                "Modelo %{y}<br>FOB USD %{x:,.0f}<br>"
                "Participación %{customdata[2]:.1%}<br>"
                "Unidades %{customdata[0]:,.0f}<br>FOB/unit USD %{customdata[1]:,.2f}<extra></extra>"
            ),
        )
        style_bar_chart(fig, numeric_axis="x", left_margin=118, right_margin=178)
        fig.update_xaxes(range=[0, model_plot["fob_usd"].max() * 1.38])
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Top modelos")
    st.dataframe(
        model_rank.sort_values("fob_usd", ascending=False)
        .assign(share_pct=lambda x: x["share_fob"] * 100)[
            ["modelo_limpio", "unidades", "fob_usd", "share_pct", "fob_unit_usd"]
        ]
        .rename(
            columns={
                "modelo_limpio": "Modelo",
                "unidades": "Unidades",
                "fob_usd": "FOB asignado",
                "share_pct": "Participación FOB",
                "fob_unit_usd": "FOB/unit promedio",
            }
        ),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Unidades": st.column_config.NumberColumn(format="%d"),
            "FOB asignado": st.column_config.NumberColumn(format="USD %.0f"),
            "Participación FOB": st.column_config.NumberColumn(format="%.1f%%"),
            "FOB/unit promedio": st.column_config.NumberColumn(format="USD %.2f"),
        },
    )

with tab2:
    tx_ts = selected_tx[selected_tx["modelo_limpio"].eq("TS5511 GH")].sort_values("fecha_dt").copy()
    tx_ts["dias_desde_anterior"] = tx_ts["fecha_dt"].diff().dt.days
    tx_ts["dias_label"] = tx_ts["dias_desde_anterior"].map(lambda x: "1ra compra" if pd.isna(x) else f"+{int(x)} días")
    ts_units = tx_ts["cantidad_modelo"].sum()
    ts_fob = tx_ts["fob_asignado_usd"].sum()
    ts_avg = ts_fob / ts_units if ts_units else None
    ts_days = tx_ts["dias_desde_anterior"].mean() if len(tx_ts) > 1 else None
    tx_ts["share_fob"] = tx_ts["fob_asignado_usd"] / ts_fob if ts_fob else 0
    tx_ts["label"] = tx_ts.apply(
        lambda r: (
            f"USD {r['usd_fob_unit_grupo']:,.0f}/u<br>"
            f"{vs_average(r['usd_fob_unit_grupo'], ts_avg)}<br>"
            f"{r['share_fob']:.1%} del FOB<br>{r['cantidad_modelo']:,.0f} u. | {r['dias_label']}"
        ),
        axis=1,
    )

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Unidades TS5511-GH", number(ts_units), f"{pct(ts_units, total_units)} de unidades", delta_color="off")
    k2.metric("FOB asignado TS5511-GH", money_k(ts_fob), f"{pct(ts_fob, total_fob)} del FOB filtrado", delta_color="off")
    k3.metric(
        "FOB/unit promedio",
        money2(ts_avg) if ts_avg is not None else "-",
        vs_average(ts_avg, filter_avg_unit),
        delta_color="inverse",
    )
    k4.metric("Promedio días entre compras", f"{ts_days:.1f}" if ts_days is not None else "-", delta_color="off")

    if tx_ts.empty:
        st.info("No hay TS5511-GH para el filtro seleccionado.")
    else:
        fig = px.bar(
            tx_ts,
            x="fecha_dt",
            y="usd_fob_unit_grupo",
            text="label",
            custom_data=["identificador", "cantidad_modelo", "share_fob", "dias_label"],
            title="TS5511-GH / TrafficX: FOB unitario por compra",
        )
        fig.update_traces(
            marker_color="#D64545",
            textposition="outside",
            hovertemplate=(
                "Fecha %{x|%d/%m/%Y}<br>Despacho %{customdata[0]}<br>"
                "FOB/unit USD %{y:,.2f}<br>Participación %{customdata[2]:.1%}<br>"
                "Unidades %{customdata[1]:,.0f}<br>Días vs anterior %{customdata[3]}<extra></extra>"
            ),
        )
        style_bar_chart(fig)
        fig.update_layout(height=540)
        fig.update_yaxes(range=[0, tx_ts["usd_fob_unit_grupo"].max() * 1.36])
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Detalle de compras TS5511-GH")
        render_purchase_table(tx_ts, include_days=True)

with tab3:
    ts5510 = filtered[filtered["modelo_limpio"].eq("TS5510 GVH")].copy()
    ts5510_units = ts5510["cantidad_modelo"].sum()
    ts5510_fob = ts5510["fob_asignado_usd"].sum()
    ts5510_avg = ts5510_fob / ts5510_units if ts5510_units else None
    ts5510["share_fob"] = ts5510["fob_asignado_usd"] / ts5510_fob if ts5510_fob else 0
    ts5510["label"] = ts5510.apply(
        lambda r: (
            f"USD {r['usd_fob_unit_grupo']:,.0f}/u<br>"
            f"{vs_average(r['usd_fob_unit_grupo'], ts5510_avg)}<br>"
            f"{r['share_fob']:.1%} del FOB<br>{r['cantidad_modelo']:,.0f} u."
        ),
        axis=1,
    )
    ts5510_shipments = ts5510["identificador"].nunique()

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Unidades TS5510-GVH", number(ts5510_units), f"{pct(ts5510_units, total_units)} de unidades", delta_color="off")
    k2.metric("FOB asignado TS5510-GVH", money_k(ts5510_fob), f"{pct(ts5510_fob, total_fob)} del FOB filtrado", delta_color="off")
    k3.metric(
        "FOB/unit promedio",
        money2(ts5510_avg) if ts5510_avg is not None else "-",
        vs_average(ts5510_avg, filter_avg_unit),
        delta_color="inverse",
    )
    k4.metric("Despachos TS5510-GVH", number(ts5510_shipments), f"{pct(ts5510_shipments, despachos)} de despachos", delta_color="off")

    if ts5510.empty:
        st.info("No hay TS5510-GVH para el filtro seleccionado.")
    else:
        ts5510_plot = ts5510.sort_values("fecha_dt")
        fig = px.bar(
            ts5510_plot,
            x="fecha_dt",
            y="usd_fob_unit_grupo",
            text="label",
            custom_data=["identificador", "cantidad_modelo", "share_fob"],
            title="TS5510-GVH: FOB unitario por compra",
        )
        fig.update_traces(
            marker_color="#F0B429",
            textposition="outside",
            hovertemplate=(
                "Fecha %{x|%d/%m/%Y}<br>Despacho %{customdata[0]}<br>"
                "FOB/unit USD %{y:,.2f}<br>Participación %{customdata[2]:.1%}<br>"
                "Unidades %{customdata[1]:,.0f}<extra></extra>"
            ),
        )
        style_bar_chart(fig)
        fig.update_layout(height=540)
        fig.update_yaxes(range=[0, ts5510["usd_fob_unit_grupo"].max() * 1.36])
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Detalle de compras TS5510-GVH")
        render_purchase_table(ts5510_plot)

with tab4:
    st.dataframe(
        filtered[
            [
                "fecha_dt",
                "identificador",
                "modelo_limpio",
                "cantidad_modelo",
                "usd_fob_unit_grupo",
                "fob_asignado_usd",
                "cif_asignado_usd",
                "condicion_de_venta",
            ]
        ].rename(
            columns={
                "fecha_dt": "Fecha",
                "identificador": "Despacho",
                "modelo_limpio": "Modelo",
                "cantidad_modelo": "Unidades",
                "usd_fob_unit_grupo": "FOB/unit",
                "fob_asignado_usd": "FOB asignado",
                "cif_asignado_usd": "CIF asignado",
                "condicion_de_venta": "Incoterm",
            }
        ),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Fecha": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Unidades": st.column_config.NumberColumn(format="%d"),
            "FOB/unit": st.column_config.NumberColumn(format="USD %.2f"),
            "FOB asignado": st.column_config.NumberColumn(format="USD %.0f"),
            "CIF asignado": st.column_config.NumberColumn(format="USD %.0f"),
        },
    )

    st.download_button(
        "Descargar CSV filtrado",
        data=filtered.to_csv(index=False).encode("utf-8"),
        file_name="importaciones_filtradas.csv",
        mime="text/csv",
    )
