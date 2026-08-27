from __future__ import annotations

import base64
import datetime as dt
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import seaborn as sns
from jinja2 import Template


ROOT = Path(__file__).resolve().parents[1]
RAW_PATTERN = "detalle_ARimportDetalladas_*.xlsx"
RAW_DIRS = [ROOT / "data", ROOT]
DATA_DIR = ROOT / "data" / "processed"
REPORT_DIR = ROOT / "reports"
FIG_DIR = REPORT_DIR / "figures"

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}

MILESIGHT_TRAFFICX_SOURCES = [
    {
        "titulo": "Milesight - DualVision TrafficX Enforcement Camera",
        "url": "https://www.milesight.com/security/product/trafficx-enforcement-camera",
        "nota": "Página oficial de Milesight que lista el modelo TS5511-GH para detección de cruce en rojo.",
    },
    {
        "titulo": "Milesight - DualVision TrafficX Camera",
        "url": "https://www.milesight.com/security/product/trafficx-camera",
        "nota": "Página oficial de Milesight que lista el modelo TS5510-GH dentro de TrafficX.",
    },
    {
        "titulo": "Milesight Support - radar speed monitoring parameters",
        "url": "https://support.milesight.com/support/solutions/articles/69000880499-how-to-configure-milesight-radar-speed-monitoring-device-parameters",
        "nota": "Artículo de soporte Milesight que menciona TS5510-GVH para configuración de parámetros de radar.",
    },
]


@dataclass(frozen=True)
class SourceWorkbook:
    path: Path
    parametros: list[str]


def normalize_header(value: object) -> str:
    text = str(value).strip()
    text = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )
    text = text.lower().replace("$", "usd").replace("%", "pct")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "columna"


def make_unique(headers: list[object]) -> list[str]:
    counts: dict[str, int] = {}
    output: list[str] = []
    for header in headers:
        base = normalize_header(header)
        counts[base] = counts.get(base, 0) + 1
        output.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return output


def excel_col_to_index(cell_ref: str) -> int:
    match = re.match(r"[A-Z]+", cell_ref)
    if not match:
        return 0
    index = 0
    for char in match.group(0):
        index = index * 26 + ord(char) - 64
    return index - 1


def load_shared_strings(zf: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall("m:si", NS):
        strings.append("".join(t.text or "" for t in item.findall(".//m:t", NS)))
    return strings


def workbook_sheet_paths(zf: ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    relmap = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels.findall("pr:Relationship", NS)}

    sheets: dict[str, str] = {}
    for sheet in workbook.findall("m:sheets/m:sheet", NS):
        rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        target = relmap[rel_id]
        if target.startswith("xl/"):
            path = target
        else:
            path = ("xl/" + target.lstrip("/")).replace("xl/../", "")
        sheets[sheet.attrib["name"]] = path
    return sheets


def parse_xlsx_sheet(zf: ZipFile, sheet_path: str, shared: list[str]) -> list[list[object]]:
    root = ET.fromstring(zf.read(sheet_path))
    raw_rows: list[dict[int, object]] = []
    max_col = 0

    for row in root.findall(".//m:sheetData/m:row", NS):
        values: dict[int, object] = {}
        for cell in row.findall("m:c", NS):
            ref = cell.attrib.get("r")
            if not ref:
                continue
            col_idx = excel_col_to_index(ref)
            max_col = max(max_col, col_idx)
            cell_type = cell.attrib.get("t")
            value_node = cell.find("m:v", NS)

            if cell_type == "s" and value_node is not None:
                value: object = shared[int(value_node.text or 0)]
            elif cell_type == "inlineStr":
                value = "".join(t.text or "" for t in cell.findall(".//m:t", NS))
            elif value_node is not None:
                value = value_node.text or ""
            else:
                value = ""
            values[col_idx] = value
        if values:
            raw_rows.append(values)

    return [[row.get(i, "") for i in range(max_col + 1)] for row in raw_rows]


def read_source_workbook(path: Path) -> tuple[pd.DataFrame, SourceWorkbook]:
    with ZipFile(path) as zf:
        shared = load_shared_strings(zf)
        sheets = workbook_sheet_paths(zf)
        detalle_rows = parse_xlsx_sheet(zf, sheets["Detalle"], shared)
        parametros_rows = parse_xlsx_sheet(zf, sheets.get("Parámetros", sheets.get("Parametros", "")), shared)

    headers = make_unique(detalle_rows[0])
    df = pd.DataFrame(detalle_rows[1:], columns=headers)
    df["archivo_origen"] = path.name
    parametros = [" ".join(str(cell) for cell in row if str(cell).strip()) for row in parametros_rows]
    return df, SourceWorkbook(path=path, parametros=parametros)


def parse_date_from_excel_serial(value: object) -> pd.Timestamp:
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number) or float(number) == 0:
        return pd.NaT
    return pd.Timestamp(dt.datetime(1899, 12, 30) + dt.timedelta(days=float(number)))


def clean_brand(value: object) -> str:
    text = str(value or "").upper().strip()
    match = re.search(r"MARCA:\s*([A-Z0-9/.-]+)", text)
    if match:
        brand = match.group(1).replace("/", "")
    else:
        brand = text.replace("MARCA:", "").strip()
    return brand or "SIN MARCA"


def clean_model(value: object) -> str:
    text = str(value or "").upper().strip()
    text = text.replace("NO DISPONIBLE", "").strip()
    text = re.sub(r"[`\"´]+", "", text)
    text = re.sub(r"\s+", " ", text)
    replacements = {
        "NT 5510": "NT5510",
        "TS 5510": "TS5510",
        "TS 5511": "TS5511",
        "MS FL01": "MS FL01",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text or "SIN MODELO"


def prepare_data() -> tuple[pd.DataFrame, list[SourceWorkbook]]:
    files = sorted({path for directory in RAW_DIRS if directory.exists() for path in directory.glob(RAW_PATTERN)})
    if not files:
        raise FileNotFoundError(f"No se encontraron archivos con patron {RAW_PATTERN}")

    frames: list[pd.DataFrame] = []
    sources: list[SourceWorkbook] = []
    for path in files:
        df, source = read_source_workbook(path)
        frames.append(df)
        sources.append(source)

    df = pd.concat(frames, ignore_index=True)

    numeric_cols = [
        "fecha",
        "uusds_unitario",
        "uusds_fob",
        "flete_uusds",
        "seguro_uusds",
        "uusds_cif",
        "cant_estad",
        "cantidad",
        "kgs_netos",
        "kgs_brutos",
        "derecho",
        "pct_dere",
        "cantidad_2",
        "unitario_divisa",
        "fob_divisa",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    df["fecha_dt"] = df["fecha"].map(parse_date_from_excel_serial)
    df["mes"] = df["fecha_dt"].dt.to_period("M").astype(str)
    df["marca"] = df["marca_sufijos"].map(clean_brand)
    df["modelo_limpio"] = df["modelo"].map(clean_model)
    df["texto_busqueda"] = (
        df[["marca_sufijos", "marca_o_descripcion", "descripcion_arancelaria", "modelo"]]
        .astype(str)
        .agg(" ".join, axis=1)
        .str.upper()
    )

    df["es_milesight"] = df["texto_busqueda"].str.contains("MILESIGHT", regex=False)
    df["trafficx_texto_en_archivo"] = df["texto_busqueda"].str.contains("TRAFFICX", regex=False)
    df["trafficx_confirmado_modelo"] = df["modelo_limpio"].str.contains(
        r"\bTS5511\s*GH\b", regex=True
    )
    df["trafficx_ts_relacionado"] = df["modelo_limpio"].str.contains(
        r"\bTS(?:5510|5511|4466)", regex=True
    )

    conditions = [
        df["es_milesight"] & df["ncm_sim"].astype(str).str.startswith("8525"),
        df["es_milesight"] & df["ncm_sim"].astype(str).str.startswith("9006"),
        df["es_milesight"] & df["ncm_sim"].astype(str).str.startswith("8544"),
        df["marca"].eq("JENOPTIK"),
        df["texto_busqueda"].str.contains("SPEED BUMP", regex=False),
        df["texto_busqueda"].str.contains(r"ROAD MARKING|RMT", regex=True),
    ]
    choices = [
        "Milesight camaras / ANPR",
        "Milesight flash / iluminación",
        "Milesight cables",
        "Jenoptik radar",
        "Seguridad vial - speed bump",
        "Cintas demarcacion / RMT",
    ]
    df["categoria_producto"] = np.select(conditions, choices, default="Otros")

    keys = ["identificador", "item", "ncm_sim"]
    groups = (
        df.groupby(keys, dropna=False)
        .agg(
            grupo_fob_usd=("uusds_fob", "sum"),
            grupo_cif_usd=("uusds_cif", "sum"),
            grupo_flete_usd=("flete_uusds", "sum"),
            grupo_seguro_usd=("seguro_uusds", "sum"),
            grupo_cant_estad=("cant_estad", "sum"),
            grupo_cantidad_modelo=("cantidad_2", "sum"),
            grupo_fecha=("fecha_dt", "min"),
        )
        .reset_index()
    )
    df = df.merge(groups, on=keys, how="left")

    df["base_cantidad_grupo"] = np.where(
        df["grupo_cantidad_modelo"] > 0, df["grupo_cantidad_modelo"], df["grupo_cant_estad"]
    )
    df["cantidad_modelo"] = np.where(df["cantidad_2"] > 0, df["cantidad_2"], df["cantidad"])
    df["usd_fob_unit_grupo"] = np.where(
        df["base_cantidad_grupo"] > 0,
        df["grupo_fob_usd"] / df["base_cantidad_grupo"],
        np.nan,
    )
    df["usd_cif_unit_grupo"] = np.where(
        df["base_cantidad_grupo"] > 0,
        df["grupo_cif_usd"] / df["base_cantidad_grupo"],
        np.nan,
    )
    df["fob_asignado_usd"] = df["usd_fob_unit_grupo"] * df["cantidad_modelo"]
    df["cif_asignado_usd"] = df["usd_cif_unit_grupo"] * df["cantidad_modelo"]

    return df, sources


def weighted_metrics(frame: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    def summarize(group: pd.DataFrame) -> pd.Series:
        qty = group["cantidad_modelo"].sum()
        fob = group["fob_asignado_usd"].sum()
        cif = group["cif_asignado_usd"].sum()
        return pd.Series(
            {
                "unidades": qty,
                "fob_usd": fob,
                "cif_usd": cif,
                "fob_unit_prom_usd": fob / qty if qty else np.nan,
                "cif_unit_prom_usd": cif / qty if qty else np.nan,
                "fob_unit_min_usd": group["usd_fob_unit_grupo"].min(),
                "fob_unit_max_usd": group["usd_fob_unit_grupo"].max(),
                "despachos": group["identificador"].nunique(),
            }
        )

    return frame.groupby(group_cols, dropna=False).apply(summarize, include_groups=False).reset_index()


def official_monthly(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.groupby("mes", dropna=False)
        .agg(
            fob_usd=("uusds_fob", "sum"),
            cif_usd=("uusds_cif", "sum"),
            unidades_estadisticas=("cant_estad", "sum"),
            despachos=("identificador", "nunique"),
        )
        .reset_index()
        .sort_values("mes")
    )


def monthly_allocated(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.groupby("mes", dropna=False)
        .agg(
            fob_usd=("fob_asignado_usd", "sum"),
            cif_usd=("cif_asignado_usd", "sum"),
            unidades=("cantidad_modelo", "sum"),
            despachos=("identificador", "nunique"),
        )
        .reset_index()
        .sort_values("mes")
    )


def fmt_usd(value: float, decimals: int = 0) -> str:
    if pd.isna(value):
        return "-"
    return f"USD {value:,.{decimals}f}"


def fmt_num(value: float, decimals: int = 0) -> str:
    if pd.isna(value):
        return "-"
    return f"{value:,.{decimals}f}"


def fmt_pct(value: float) -> str:
    if pd.isna(value):
        return "-"
    return f"{value:.1%}"


def fmt_usd_k(value: float) -> str:
    if pd.isna(value):
        return "-"
    return f"USD {value / 1000:,.0f}k" if abs(value) >= 1000 else fmt_usd(value, 0)


def savefig(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(path, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close()


def annotate_bars(ax, values, decimals: int = 0, prefix: str = "") -> None:
    max_value = max(values) if len(values) else 0
    offset = max_value * 0.012 if max_value else 1
    for patch, value in zip(ax.patches, values):
        if pd.isna(value):
            continue
        ax.text(
            patch.get_x() + patch.get_width() / 2,
            patch.get_height() + offset,
            f"{prefix}{value:,.{decimals}f}",
            ha="center",
            va="bottom",
            fontsize=8,
            color="#17202A",
            rotation=0,
        )


def declutter_axis(ax, numeric_axis: str = "y") -> None:
    ax.grid(False)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.spines[["top", "right"]].set_visible(False)
    if numeric_axis == "x":
        ax.tick_params(axis="x", bottom=False, labelbottom=False)
        ax.spines["bottom"].set_visible(False)
    else:
        ax.tick_params(axis="y", left=False, right=False, labelleft=False, labelright=False)
        ax.spines["left"].set_visible(False)


def build_charts(
    df: pd.DataFrame,
    monthly_all: pd.DataFrame,
    monthly_milesight: pd.DataFrame,
    model_summary: pd.DataFrame,
    trafficx_detail: pd.DataFrame,
) -> dict[str, str]:
    sns.set_theme(style="white", font="DejaVu Sans")
    plt.rcParams.update(
        {
            "axes.titlesize": 15,
            "axes.labelsize": 10,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "figure.titlesize": 16,
        }
    )

    paths: dict[str, str] = {}
    palette = {
        "navy": "#102A43",
        "blue": "#247BA0",
        "teal": "#00A896",
        "gold": "#F0B429",
        "red": "#D64545",
        "gray": "#829AB1",
        "light": "#D9E2EC",
    }

    merged_months = monthly_all.merge(
        monthly_milesight[["mes", "fob_usd", "unidades"]].rename(
            columns={"fob_usd": "fob_milesight_usd", "unidades": "unidades_milesight"}
        ),
        on="mes",
        how="left",
    ).fillna({"fob_milesight_usd": 0, "unidades_milesight": 0})

    plt.figure(figsize=(12, 6.4))
    ax = plt.gca()
    x = np.arange(len(merged_months))
    width = 0.38
    ax.bar(x - width / 2, merged_months["fob_usd"], width=width, color=palette["light"], label="FOB total")
    ax.bar(
        x + width / 2,
        merged_months["fob_milesight_usd"],
        width=width,
        color=palette["blue"],
        label="FOB Milesight",
    )
    ax.set_title("Gasto FOB declarado por mes")
    ax.set_xticks(x)
    ax.set_xticklabels(merged_months["mes"], rotation=45, ha="right")
    ax.legend(frameon=False, loc="upper left")
    offset = merged_months["fob_usd"].max() * 0.025
    for i, row in enumerate(merged_months.itertuples()):
        if row.fob_milesight_usd > 0:
            share = row.fob_milesight_usd / row.fob_usd if row.fob_usd else 0
            ax.text(
                i + width / 2,
                row.fob_milesight_usd + offset,
                f"{fmt_usd_k(row.fob_milesight_usd)}\n{share:.1%}",
                ha="center",
                va="bottom",
                fontsize=8,
                color=palette["navy"],
            )
    ax.set_ylim(0, merged_months["fob_usd"].max() * 1.22)
    declutter_axis(ax, numeric_axis="y")
    path = FIG_DIR / "01_gasto_fob_mensual.png"
    savefig(path)
    paths["gasto_fob_mensual"] = relative(path)

    fig, ax1 = plt.subplots(figsize=(12, 6.4))
    x = np.arange(len(monthly_milesight))
    bars = ax1.bar(x, monthly_milesight["unidades"], color=palette["teal"], label="Unidades Milesight")
    ax1.set_xticks(x)
    ax1.set_xticklabels(monthly_milesight["mes"], rotation=45, ha="right")
    total_monthly_units = monthly_milesight["unidades"].sum()
    max_units = monthly_milesight["unidades"].max()
    for bar, value in zip(bars, monthly_milesight["unidades"]):
        share = value / total_monthly_units if total_monthly_units else 0
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            value + max_units * 0.02,
            f"{value:,.0f} u.\n{share:.1%}",
            ha="center",
            va="bottom",
            fontsize=8,
            color=palette["navy"],
        )
    ax2 = ax1.twinx()
    avg_unit = monthly_milesight["fob_usd"] / monthly_milesight["unidades"]
    ax2.plot(x, avg_unit, color=palette["red"], marker="o", linewidth=2.5, label="FOB unitario promedio")
    for i, value in enumerate(avg_unit):
        ax2.text(i, value + avg_unit.max() * 0.025, f"USD {value:,.0f}/u", ha="center", fontsize=8)
    ax1.set_title("Milesight: unidades importadas y FOB unitario promedio")
    lines, labels = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    legend = ax2.legend(
        lines + lines2,
        labels + labels2,
        frameon=True,
        loc="upper left",
        handlelength=1.1,
        handletextpad=0.8,
    )
    legend.get_frame().set_facecolor("white")
    legend.get_frame().set_alpha(1.0)
    legend.get_frame().set_edgecolor("white")
    legend.set_zorder(20)
    ax1.set_ylim(0, max_units * 1.22)
    ax2.set_ylim(0, avg_unit.max() * 1.18)
    declutter_axis(ax1, numeric_axis="y")
    declutter_axis(ax2, numeric_axis="y")
    path = FIG_DIR / "02_milesight_unidades_unitario.png"
    savefig(path)
    paths["milesight_unidades_unitario"] = relative(path)

    top_models = model_summary.sort_values("fob_usd", ascending=True).tail(10)
    plt.figure(figsize=(11, 6.6))
    ax = plt.gca()
    colors = [palette["red"] if "TS5511" in model else palette["blue"] for model in top_models["modelo_limpio"]]
    ax.barh(top_models["modelo_limpio"], top_models["fob_usd"], color=colors)
    ax.set_title("Mix Milesight por modelo - FOB asignado")
    total_top_fob = top_models["fob_usd"].sum()
    for y, (_, row) in enumerate(top_models.iterrows()):
        ax.text(
            row["fob_usd"] + top_models["fob_usd"].max() * 0.012,
            y,
            f"{fmt_usd_k(row['fob_usd'])} | {row['fob_usd'] / total_top_fob:.1%}\n"
            f"{row['unidades']:.0f} u. | USD {row['fob_unit_prom_usd']:,.0f}/u",
            va="center",
            fontsize=8.5,
        )
    ax.set_xlim(0, top_models["fob_usd"].max() * 1.25)
    declutter_axis(ax, numeric_axis="x")
    path = FIG_DIR / "03_mix_modelos_milesight.png"
    savefig(path)
    paths["mix_modelos_milesight"] = relative(path)

    milesight = df[df["es_milesight"]].copy()
    core = milesight[milesight["categoria_producto"].eq("Milesight camaras / ANPR")].copy()
    plt.figure(figsize=(12, 6.4))
    ax = plt.gca()
    sns.scatterplot(
        data=core,
        x="fecha_dt",
        y="usd_fob_unit_grupo",
        size="cantidad_modelo",
        hue="modelo_limpio",
        sizes=(80, 650),
        alpha=0.85,
        ax=ax,
    )
    ax.set_title("Evolución del costo FOB unitario - equipos Milesight ANPR")
    ax.set_xlabel("")
    ax.set_ylabel("USD FOB / unidad")
    ax.yaxis.set_major_formatter(lambda val, pos: f"{val:,.0f}")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, bbox_to_anchor=(1.02, 1), loc="upper left", borderaxespad=0)
    path = FIG_DIR / "04_costo_unitario_evolucion.png"
    savefig(path)
    paths["costo_unitario_evolucion"] = relative(path)

    ship = (
        milesight.drop_duplicates("identificador")
        .sort_values("fecha_dt")
        [["fecha_dt", "identificador"]]
        .copy()
    )
    ship["dias_desde_anterior"] = ship["fecha_dt"].diff().dt.days
    interval_df = ship.dropna(subset=["dias_desde_anterior"]).copy()
    interval_df["fecha_label"] = interval_df["fecha_dt"].dt.strftime("%d/%m/%y")
    plt.figure(figsize=(12, 5.1))
    ax = plt.gca()
    colors = [palette["red"] if val <= 7 else palette["blue"] for val in interval_df["dias_desde_anterior"]]
    bars = ax.bar(interval_df["fecha_label"], interval_df["dias_desde_anterior"], color=colors)
    mean_days = interval_df["dias_desde_anterior"].mean()
    ax.axhline(mean_days, color=palette["gold"], linestyle="--", linewidth=2, label=f"Promedio {mean_days:.1f} días")
    for bar, value in zip(bars, interval_df["dias_desde_anterior"]):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + max(interval_df["dias_desde_anterior"].max() * 0.02, 1),
            f"{value:.0f} d",
            ha="center",
            va="bottom",
            fontsize=8.5,
        )
    ax.set_title("Cadencia Milesight: días entre importaciones")
    ax.set_xlabel("Fecha de la importación")
    ax.set_ylabel("Días desde la operación anterior")
    ax.legend(frameon=False, loc="upper right")
    ax.tick_params(axis="x", rotation=45)
    ax.set_ylim(0, interval_df["dias_desde_anterior"].max() * 1.18 + 1)
    ax.spines[["top", "right", "left"]].set_visible(False)
    path = FIG_DIR / "05_frecuencia_importaciones.png"
    savefig(path)
    paths["frecuencia_importaciones"] = relative(path)

    category_month = (
        df.groupby(["categoria_producto", "mes"], dropna=False)["fob_asignado_usd"]
        .sum()
        .reset_index()
        .pivot(index="categoria_producto", columns="mes", values="fob_asignado_usd")
        .fillna(0)
    )
    plt.figure(figsize=(12, 5.8))
    ax = plt.gca()
    sns.heatmap(
        category_month / 1000,
        cmap="YlGnBu",
        linewidths=0.5,
        linecolor="white",
        cbar_kws={"label": "USD FOB asignado (miles)"},
        ax=ax,
    )
    ax.set_title("Mapa de calor: categoría de producto vs mes")
    ax.set_xlabel("")
    ax.set_ylabel("")
    path = FIG_DIR / "06_heatmap_categoria_mes.png"
    savefig(path)
    paths["heatmap_categoria_mes"] = relative(path)

    if not trafficx_detail.empty:
        plt.figure(figsize=(11, 5.6))
        ax = plt.gca()
        plot_df = trafficx_detail.sort_values("fecha_dt").copy()
        if "dias_desde_compra_anterior" not in plot_df.columns:
            plot_df["dias_desde_compra_anterior"] = plot_df["fecha_dt"].diff().dt.days
        plot_df["fecha_label"] = plot_df["fecha_dt"].dt.strftime("%d/%m/%y")
        trafficx_fob_total = plot_df["fob_asignado_usd"].sum()
        trafficx_avg_unit = plot_df["fob_asignado_usd"].sum() / plot_df["cantidad_modelo"].sum()
        plot_df["share_fob"] = plot_df["fob_asignado_usd"] / trafficx_fob_total if trafficx_fob_total else 0
        bars = ax.bar(plot_df["fecha_label"], plot_df["usd_fob_unit_grupo"], color=palette["red"], alpha=0.9)
        for bar, (_, row) in zip(bars, plot_df.iterrows()):
            days = (
                "1ra compra"
                if pd.isna(row["dias_desde_compra_anterior"])
                else f"+{row['dias_desde_compra_anterior']:.0f} d"
            )
            unit_delta = row["usd_fob_unit_grupo"] / trafficx_avg_unit - 1 if trafficx_avg_unit else 0
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                row["usd_fob_unit_grupo"] + 35,
                f"USD {row['usd_fob_unit_grupo']:,.0f}/u\n"
                f"{unit_delta:+.1%} vs prom.\n"
                f"{row['share_fob']:.1%} FOB | {row['cantidad_modelo']:.0f} u. | {days}",
                ha="center",
                va="bottom",
                fontsize=8,
            )
        ax.set_title("TS5511 GH / TrafficX: FOB unitario por compra")
        ax.set_xlabel("Fecha de compra/importación")
        ax.set_ylabel("USD FOB / unidad")
        ax.set_ylim(0, plot_df["usd_fob_unit_grupo"].max() * 1.28)
        ax.tick_params(axis="x", rotation=0)
        declutter_axis(ax, numeric_axis="y")
        path = FIG_DIR / "07_trafficx_ts5511_unitario.png"
        savefig(path)
        paths["trafficx_ts5511_unitario"] = relative(path)

    return paths


def plotly_div(fig: go.Figure, include_plotlyjs: bool) -> str:
    fig.update_layout(
        template="plotly_white",
        font={"family": "Inter, Segoe UI, Arial, sans-serif", "size": 13, "color": "#102A43"},
        title={"font": {"size": 22}, "x": 0.02, "xanchor": "left"},
        margin={"l": 60, "r": 30, "t": 70, "b": 70},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "left", "x": 0},
        hoverlabel={"bgcolor": "white", "font_size": 13},
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=False, zeroline=False)
    return pio.to_html(
        fig,
        include_plotlyjs="inline" if include_plotlyjs else False,
        full_html=False,
        config={"displayModeBar": False, "responsive": True},
    )


def build_interactive_charts(
    monthly_all: pd.DataFrame,
    monthly_milesight: pd.DataFrame,
    model_summary: pd.DataFrame,
    trafficx_detail: pd.DataFrame,
) -> dict[str, str]:
    colors = {
        "blue": "#247BA0",
        "teal": "#00A896",
        "red": "#D64545",
        "gold": "#F0B429",
        "light": "#D9E2EC",
        "ink": "#102A43",
    }
    output: dict[str, str] = {}

    merged_months = monthly_all.merge(
        monthly_milesight[["mes", "fob_usd", "unidades"]].rename(
            columns={"fob_usd": "fob_milesight_usd", "unidades": "unidades_milesight"}
        ),
        on="mes",
        how="left",
    ).fillna({"fob_milesight_usd": 0, "unidades_milesight": 0})
    merged_months["milesight_share"] = np.where(
        merged_months["fob_usd"] > 0,
        merged_months["fob_milesight_usd"] / merged_months["fob_usd"],
        0,
    )
    fig = go.Figure()
    fig.add_bar(
        x=merged_months["mes"],
        y=merged_months["fob_usd"],
        name="FOB total",
        marker_color=colors["light"],
        text=[fmt_usd_k(v) for v in merged_months["fob_usd"]],
        textposition="outside",
        hovertemplate="Mes %{x}<br>FOB total USD %{y:,.0f}<extra></extra>",
    )
    fig.add_bar(
        x=merged_months["mes"],
        y=merged_months["fob_milesight_usd"],
        name="FOB Milesight",
        marker_color=colors["blue"],
        text=[
            f"{fmt_usd_k(row.fob_milesight_usd)}<br>{row.milesight_share:.1%}"
            if row.fob_milesight_usd
            else ""
            for row in merged_months.itertuples()
        ],
        textposition="outside",
        customdata=merged_months[["milesight_share", "unidades_milesight"]],
        hovertemplate=(
            "Mes %{x}<br>FOB Milesight USD %{y:,.0f}<br>"
            "Participación %{customdata[0]:.1%}<br>Unidades %{customdata[1]:,.0f}<extra></extra>"
        ),
    )
    fig.update_layout(title="Gasto FOB mensual: total vs Milesight", barmode="group", yaxis_title="")
    fig.update_yaxes(showticklabels=False, range=[0, merged_months["fob_usd"].max() * 1.22])
    output["gasto_mensual"] = plotly_div(fig, include_plotlyjs=True)

    model_plot = model_summary.sort_values("fob_usd", ascending=True).copy()
    total_model_fob = model_plot["fob_usd"].sum()
    model_plot["share_fob"] = model_plot["fob_usd"] / total_model_fob if total_model_fob else 0
    model_colors = [
        colors["red"] if model == "TS5511 GH" else colors["gold"] if "TS5510" in model else colors["blue"]
        for model in model_plot["modelo_limpio"]
    ]
    fig = go.Figure()
    fig.add_bar(
        x=model_plot["fob_usd"],
        y=model_plot["modelo_limpio"],
        orientation="h",
        marker_color=model_colors,
        text=[
            f"{fmt_usd_k(row.fob_usd)} | {row.share_fob:.1%}<br>"
            f"{row.unidades:,.0f} u. | USD {row.fob_unit_prom_usd:,.0f}/u"
            for row in model_plot.itertuples()
        ],
        textposition="outside",
        hovertemplate=(
            "Modelo %{y}<br>FOB asignado USD %{x:,.0f}<br>"
            "Participación %{customdata[2]:.1%}<br>"
            "Unidades %{customdata[0]:,.0f}<br>FOB/unit USD %{customdata[1]:,.2f}<extra></extra>"
        ),
        customdata=model_plot[["unidades", "fob_unit_prom_usd", "share_fob"]],
    )
    fig.update_layout(title="Milesight por modelo: volumen, FOB total y unitario", xaxis_title="")
    fig.update_xaxes(showticklabels=False, range=[0, model_plot["fob_usd"].max() * 1.38])
    output["modelos_milesight"] = plotly_div(fig, include_plotlyjs=False)

    if not trafficx_detail.empty:
        tx = trafficx_detail.sort_values("fecha_dt").copy()
        if "dias_desde_compra_anterior" not in tx.columns:
            tx["dias_desde_compra_anterior"] = tx["fecha_dt"].diff().dt.days
        tx["fecha_label"] = tx["fecha_dt"].dt.strftime("%d/%m/%y")
        tx["dias_label"] = tx["dias_desde_compra_anterior"].map(
            lambda x: "1ra compra" if pd.isna(x) else f"+{int(x)} días"
        )
        tx_fob_total = tx["fob_asignado_usd"].sum()
        tx_avg_unit = tx["fob_asignado_usd"].sum() / tx["cantidad_modelo"].sum()
        tx["share_fob"] = tx["fob_asignado_usd"] / tx_fob_total if tx_fob_total else 0
        fig = go.Figure()
        fig.add_bar(
            x=tx["fecha_label"],
            y=tx["usd_fob_unit_grupo"],
            marker_color=colors["red"],
            text=[
                f"USD {row.usd_fob_unit_grupo:,.0f}/u<br>"
                f"{(row.usd_fob_unit_grupo / tx_avg_unit - 1):+.1%} vs prom.<br>"
                f"{row.share_fob:.1%} del FOB<br>{row.cantidad_modelo:,.0f} u. | {row.dias_label}"
                for row in tx.itertuples()
            ],
            textposition="outside",
            hovertemplate=(
                "Fecha %{customdata[0]}<br>Despacho %{customdata[1]}<br>"
                "Unidades %{customdata[2]:,.0f}<br>FOB/unit USD %{y:,.2f}<br>"
                "Participación %{customdata[4]:.1%}<br>Días vs anterior %{customdata[3]}<extra></extra>"
            ),
            customdata=tx[["fecha_dt", "identificador", "cantidad_modelo", "dias_label", "share_fob"]].assign(
                fecha_dt=tx["fecha_dt"].dt.strftime("%d/%m/%Y")
            ),
        )
        fig.update_layout(
            title="TS5511-GH / TrafficX: FOB unitario por compra",
            xaxis_title="",
            yaxis_title="",
            showlegend=False,
        )
        fig.update_yaxes(showticklabels=False, range=[0, tx["usd_fob_unit_grupo"].max() * 1.36])
        output["ts5511_compra"] = plotly_div(fig, include_plotlyjs=False)

    return output


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def to_records_for_table(df: pd.DataFrame, columns: list[str]) -> list[dict[str, object]]:
    return df[columns].to_dict(orient="records")


def img_as_base64(path: str) -> str:
    raw = (ROOT / path).read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def markdown_chart_path(path: str) -> str:
    return Path(path).relative_to("reports").as_posix()


def build_report(
    df: pd.DataFrame,
    sources: list[SourceWorkbook],
    charts: dict[str, str],
    monthly_all: pd.DataFrame,
    monthly_milesight: pd.DataFrame,
    model_summary: pd.DataFrame,
    trafficx_detail: pd.DataFrame,
    ts_related: pd.DataFrame,
) -> None:
    total_fob = df["uusds_fob"].sum()
    total_cif = df["uusds_cif"].sum()
    total_despachos = df["identificador"].nunique()
    milesight = df[df["es_milesight"]].copy()
    milesight_fob = milesight["fob_asignado_usd"].sum()
    milesight_cif = milesight["cif_asignado_usd"].sum()
    milesight_units = milesight["cantidad_modelo"].sum()
    milesight_despachos = milesight["identificador"].nunique()
    milesight_share = milesight_fob / total_fob if total_fob else np.nan

    trafficx_units = trafficx_detail["cantidad_modelo"].sum()
    trafficx_fob = trafficx_detail["fob_asignado_usd"].sum()
    trafficx_cif = trafficx_detail["cif_asignado_usd"].sum()
    trafficx_avg_fob = trafficx_fob / trafficx_units if trafficx_units else np.nan
    trafficx_avg_cif = trafficx_cif / trafficx_units if trafficx_units else np.nan
    ts5510_detail = milesight[milesight["modelo_limpio"].str.contains(r"\bTS5510\s*GVH\b", regex=True)].copy()
    ts5510_units = ts5510_detail["cantidad_modelo"].sum()
    ts5510_fob = ts5510_detail["fob_asignado_usd"].sum()
    ts5510_cif = ts5510_detail["cif_asignado_usd"].sum()
    ts5510_avg_fob = ts5510_fob / ts5510_units if ts5510_units else np.nan
    ts5510_avg_cif = ts5510_cif / ts5510_units if ts5510_units else np.nan
    ts5510_exact_gh_rows = milesight[milesight["modelo_limpio"].str.contains(r"\bTS5510\s*GH\b", regex=True)]

    ship_dates = (
        milesight.drop_duplicates("identificador")
        .sort_values("fecha_dt")["fecha_dt"]
        .dropna()
        .reset_index(drop=True)
    )
    intervals = ship_dates.diff().dt.days.dropna()
    avg_interval = intervals.mean() if len(intervals) else np.nan
    median_interval = intervals.median() if len(intervals) else np.nan

    source_periods = []
    for source in sources:
        period_line = next((line for line in source.parametros if "Periodo:" in line), "")
        importer_line = next((line for line in source.parametros if "Importador:" in line), "")
        source_periods.append(
            {
                "archivo": source.path.name,
                "periodo": period_line.replace("Periodo:", "").strip(),
                "importador": importer_line.replace("Importador:", "").strip(),
            }
        )

    model_table = model_summary.sort_values("fob_usd", ascending=False).copy()
    model_table["unidades_fmt"] = model_table["unidades"].map(lambda x: fmt_num(x, 0))
    model_table["fob_fmt"] = model_table["fob_usd"].map(lambda x: fmt_usd(x, 0))
    model_table["cif_fmt"] = model_table["cif_usd"].map(lambda x: fmt_usd(x, 0))
    model_table["fob_unit_fmt"] = model_table["fob_unit_prom_usd"].map(lambda x: fmt_usd(x, 2))
    model_table["rango_fmt"] = model_table.apply(
        lambda r: f"{fmt_usd(r['fob_unit_min_usd'], 2)} - {fmt_usd(r['fob_unit_max_usd'], 2)}",
        axis=1,
    )
    focus_model_table = model_table[
        model_table["modelo_limpio"].isin(["TS5511 GH", "TS5510 GVH"])
    ].copy()
    focus_model_table["prioridad"] = focus_model_table["modelo_limpio"].map(
        {"TS5511 GH": "Principal", "TS5510 GVH": "Secundario"}
    )

    tx_table = trafficx_detail.sort_values("fecha_dt").copy()
    if "dias_desde_compra_anterior" not in tx_table.columns:
        tx_table["dias_desde_compra_anterior"] = tx_table["fecha_dt"].diff().dt.days
    tx_table["fecha_fmt"] = tx_table["fecha_dt"].dt.strftime("%d/%m/%Y")
    tx_table["unidades_fmt"] = tx_table["cantidad_modelo"].map(lambda x: fmt_num(x, 0))
    tx_table["fob_unit_fmt"] = tx_table["usd_fob_unit_grupo"].map(lambda x: fmt_usd(x, 2))
    tx_table["fob_asig_fmt"] = tx_table["fob_asignado_usd"].map(lambda x: fmt_usd(x, 0))
    tx_table["cif_unit_fmt"] = tx_table["usd_cif_unit_grupo"].map(lambda x: fmt_usd(x, 2))
    tx_table["dias_fmt"] = tx_table["dias_desde_compra_anterior"].map(
        lambda x: "Primera compra" if pd.isna(x) else f"{int(x)} días"
    )

    monthly_table = monthly_milesight.copy()
    monthly_table["fob_unit_prom_usd"] = monthly_table["fob_usd"] / monthly_table["unidades"]
    monthly_table["unidades_fmt"] = monthly_table["unidades"].map(lambda x: fmt_num(x, 0))
    monthly_table["fob_fmt"] = monthly_table["fob_usd"].map(lambda x: fmt_usd(x, 0))
    monthly_table["cif_fmt"] = monthly_table["cif_usd"].map(lambda x: fmt_usd(x, 0))
    monthly_table["unit_fmt"] = monthly_table["fob_unit_prom_usd"].map(lambda x: fmt_usd(x, 2))
    interactive_charts = build_interactive_charts(
        monthly_all=monthly_all,
        monthly_milesight=monthly_milesight,
        model_summary=model_summary,
        trafficx_detail=trafficx_detail,
    )

    context = {
        "generated_at": dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
        "source_periods": source_periods,
        "sources": MILESIGHT_TRAFFICX_SOURCES,
        "kpis": {
            "total_fob": fmt_usd(total_fob, 0),
            "total_cif": fmt_usd(total_cif, 0),
            "total_despachos": fmt_num(total_despachos, 0),
            "milesight_fob": fmt_usd(milesight_fob, 0),
            "milesight_cif": fmt_usd(milesight_cif, 0),
            "milesight_units": fmt_num(milesight_units, 0),
            "milesight_despachos": fmt_num(milesight_despachos, 0),
            "milesight_share": fmt_pct(milesight_share),
            "trafficx_units": fmt_num(trafficx_units, 0),
            "trafficx_fob": fmt_usd(trafficx_fob, 0),
            "trafficx_avg_fob": fmt_usd(trafficx_avg_fob, 2),
            "trafficx_avg_cif": fmt_usd(trafficx_avg_cif, 2),
            "trafficx_range": (
                f"{fmt_usd(trafficx_detail['usd_fob_unit_grupo'].min(), 2)} - "
                f"{fmt_usd(trafficx_detail['usd_fob_unit_grupo'].max(), 2)}"
                if not trafficx_detail.empty
                else "-"
            ),
            "ts5510_units": fmt_num(ts5510_units, 0),
            "ts5510_fob": fmt_usd(ts5510_fob, 0),
            "ts5510_avg_fob": fmt_usd(ts5510_avg_fob, 2),
            "ts5510_avg_cif": fmt_usd(ts5510_avg_cif, 2),
            "ts5510_range": (
                f"{fmt_usd(ts5510_detail['usd_fob_unit_grupo'].min(), 2)} - "
                f"{fmt_usd(ts5510_detail['usd_fob_unit_grupo'].max(), 2)}"
                if not ts5510_detail.empty
                else "-"
            ),
            "ts5510_exact_gh_units": fmt_num(ts5510_exact_gh_rows["cantidad_modelo"].sum(), 0),
            "avg_interval": fmt_num(avg_interval, 1),
            "median_interval": fmt_num(median_interval, 0),
            "first_date": df["fecha_dt"].min().strftime("%d/%m/%Y"),
            "last_date": df["fecha_dt"].max().strftime("%d/%m/%Y"),
            "trafficx_text_matches": int(df["trafficx_texto_en_archivo"].sum()),
            "ts_related_units": fmt_num(ts_related["cantidad_modelo"].sum(), 0),
            "ts_related_fob": fmt_usd(ts_related["fob_asignado_usd"].sum(), 0),
        },
        "charts": {
            key: {"path": value, "md_path": markdown_chart_path(value), "b64": img_as_base64(value)}
            for key, value in charts.items()
        },
        "interactive_charts": interactive_charts,
        "model_rows": to_records_for_table(
            model_table,
            [
                "modelo_limpio",
                "unidades_fmt",
                "fob_fmt",
                "cif_fmt",
                "fob_unit_fmt",
                "rango_fmt",
                "despachos",
            ],
        ),
        "focus_model_rows": to_records_for_table(
            focus_model_table,
            [
                "prioridad",
                "modelo_limpio",
                "unidades_fmt",
                "fob_fmt",
                "cif_fmt",
                "fob_unit_fmt",
                "rango_fmt",
                "despachos",
            ],
        ),
        "trafficx_rows": to_records_for_table(
            tx_table,
            [
                "fecha_fmt",
                "identificador",
                "modelo_limpio",
                "unidades_fmt",
                "fob_unit_fmt",
                "cif_unit_fmt",
                "fob_asig_fmt",
                "dias_fmt",
                "condicion_de_venta",
            ],
        ),
        "monthly_rows": to_records_for_table(
            monthly_table,
            ["mes", "unidades_fmt", "fob_fmt", "cif_fmt", "unit_fmt", "despachos"],
        ),
    }

    html = Template(HTML_TEMPLATE).render(**context)
    md = Template(MD_TEMPLATE).render(**context)
    html = html.replace("[-\u2013\u2014]", "[-\u2013\\u2014]")
    (REPORT_DIR / "analisis_importaciones_transito_seguro.html").write_text(html, encoding="utf-8")
    (REPORT_DIR / "analisis_importaciones_transito_seguro.md").write_text(md, encoding="utf-8")


def export_data(
    df: pd.DataFrame,
    monthly_all: pd.DataFrame,
    monthly_milesight: pd.DataFrame,
    model_summary: pd.DataFrame,
    trafficx_detail: pd.DataFrame,
) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    export_df = df.drop(columns=["texto_busqueda"], errors="ignore").copy()
    export_df.to_csv(DATA_DIR / "importaciones_limpias.csv", index=False, encoding="utf-8-sig")
    df[df["es_milesight"]].drop(columns=["texto_busqueda"], errors="ignore").to_csv(
        DATA_DIR / "milesight_detalle_asignado.csv", index=False, encoding="utf-8-sig"
    )
    monthly_all.to_csv(DATA_DIR / "mensual_total_oficial.csv", index=False, encoding="utf-8-sig")
    monthly_milesight.to_csv(DATA_DIR / "mensual_milesight_asignado.csv", index=False, encoding="utf-8-sig")
    model_summary.to_csv(DATA_DIR / "resumen_milesight_por_modelo.csv", index=False, encoding="utf-8-sig")
    trafficx_detail.to_csv(DATA_DIR / "trafficx_ts5511_detalle.csv", index=False, encoding="utf-8-sig")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    df, sources = prepare_data()
    monthly_all = official_monthly(df)
    milesight = df[df["es_milesight"]].copy()
    monthly_milesight = monthly_allocated(milesight)
    model_summary = weighted_metrics(milesight, ["modelo_limpio"]).sort_values("fob_usd", ascending=False)
    trafficx_detail = milesight[milesight["trafficx_confirmado_modelo"]].copy().sort_values("fecha_dt")
    trafficx_detail["dias_desde_compra_anterior"] = trafficx_detail["fecha_dt"].diff().dt.days
    ts_related = milesight[milesight["trafficx_ts_relacionado"]].copy()

    charts = build_charts(df, monthly_all, monthly_milesight, model_summary, trafficx_detail)
    export_data(df, monthly_all, monthly_milesight, model_summary, trafficx_detail)
    build_report(
        df=df,
        sources=sources,
        charts=charts,
        monthly_all=monthly_all,
        monthly_milesight=monthly_milesight,
        model_summary=model_summary,
        trafficx_detail=trafficx_detail,
        ts_related=ts_related,
    )

    metrics = {
        "total_fob_usd": float(df["uusds_fob"].sum()),
        "total_cif_usd": float(df["uusds_cif"].sum()),
        "despachos_total": int(df["identificador"].nunique()),
        "milesight_fob_asignado_usd": float(milesight["fob_asignado_usd"].sum()),
        "milesight_unidades": float(milesight["cantidad_modelo"].sum()),
        "trafficx_ts5511_fob_asignado_usd": float(trafficx_detail["fob_asignado_usd"].sum()),
        "trafficx_ts5511_unidades": float(trafficx_detail["cantidad_modelo"].sum()),
        "trafficx_ts5511_fob_unit_prom_usd": float(
            trafficx_detail["fob_asignado_usd"].sum() / trafficx_detail["cantidad_modelo"].sum()
        ),
        "ts5510_gvh_unidades": float(
            milesight.loc[
                milesight["modelo_limpio"].str.contains(r"\bTS5510\s*GVH\b", regex=True),
                "cantidad_modelo",
            ].sum()
        ),
        "ts5510_gvh_fob_asignado_usd": float(
            milesight.loc[
                milesight["modelo_limpio"].str.contains(r"\bTS5510\s*GVH\b", regex=True),
                "fob_asignado_usd",
            ].sum()
        ),
        "trafficx_texto_matches": int(df["trafficx_texto_en_archivo"].sum()),
        "generated_files": [
            relative(REPORT_DIR / "analisis_importaciones_transito_seguro.html"),
            relative(REPORT_DIR / "analisis_importaciones_transito_seguro.md"),
            *charts.values(),
        ],
    }
    (DATA_DIR / "metricas_resumen.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(metrics, indent=2, ensure_ascii=False))


HTML_TEMPLATE = r"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Análisis de importaciones - Tránsito Seguro / Milesight</title>
  <style>
    :root {
      --ink: #102A43;
      --muted: #52616B;
      --line: #D9E2EC;
      --soft: #F5F7FA;
      --blue: #247BA0;
      --teal: #00A896;
      --red: #D64545;
      --gold: #F0B429;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background: #ffffff;
      font-family: Inter, Segoe UI, Arial, sans-serif;
      line-height: 1.48;
    }
    .hero {
      padding: 44px 56px 34px;
      background:
        linear-gradient(135deg, rgba(16,42,67,0.96), rgba(36,123,160,0.88)),
        radial-gradient(circle at 80% 20%, rgba(240,180,41,0.28), transparent 30%);
      color: #fff;
    }
    .hero h1 {
      margin: 0 0 10px;
      font-size: 34px;
      letter-spacing: 0;
      line-height: 1.12;
      max-width: 1120px;
    }
    .subtitle {
      margin: 0;
      max-width: 980px;
      color: #E6F6FF;
      font-size: 16px;
    }
    .meta {
      margin-top: 18px;
      color: #DDEAF4;
      font-size: 12px;
    }
    main { max-width: 1180px; margin: 0 auto; padding: 28px 28px 60px; }
    section { margin: 34px 0; }
    h2 { margin: 0 0 14px; font-size: 22px; }
    h3 { margin: 24px 0 10px; font-size: 16px; }
    .kpis {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-top: -52px;
    }
    .kpi {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      box-shadow: 0 10px 24px rgba(16,42,67,0.10);
    }
    .kpi .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
    .kpi .value { margin-top: 7px; font-size: 24px; font-weight: 750; color: var(--ink); }
    .kpi .note { margin-top: 4px; color: var(--muted); font-size: 12px; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; align-items: start; }
    .callout {
      border-left: 5px solid var(--red);
      background: #FFF7F7;
      padding: 16px 18px;
      border-radius: 6px;
      margin: 18px 0;
    }
    .method {
      border-left: 5px solid var(--blue);
      background: var(--soft);
      padding: 16px 18px;
      border-radius: 6px;
    }
    .chart {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      background: #fff;
      box-shadow: 0 8px 20px rgba(16,42,67,0.06);
    }
    .chart img { width: 100%; display: block; }
    .interactive-chart {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      margin: 16px 0 24px;
      background: #fff;
      box-shadow: 0 8px 20px rgba(16,42,67,0.06);
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      margin: 12px 0 24px;
    }
    th {
      text-align: left;
      background: var(--ink);
      color: #fff;
      padding: 9px 10px;
      font-weight: 650;
    }
    td {
      border-bottom: 1px solid var(--line);
      padding: 8px 10px;
      vertical-align: top;
    }
    td.num, th.num { text-align: right; }
    tr:nth-child(even) td { background: #FAFBFC; }
    ul { padding-left: 21px; }
    li { margin: 7px 0; }
    .small { color: var(--muted); font-size: 12px; }
    .pill {
      display: inline-block;
      padding: 3px 8px;
      border-radius: 999px;
      background: #E6F6FF;
      color: #0B5F7A;
      font-size: 12px;
      font-weight: 650;
    }
    .footer {
      border-top: 1px solid var(--line);
      padding-top: 18px;
      color: var(--muted);
      font-size: 12px;
    }
    @media (max-width: 900px) {
      .hero { padding: 34px 24px 74px; }
      main { padding: 22px 16px 44px; }
      .kpis, .grid-2 { grid-template-columns: 1fr; }
      .kpis { margin-top: -66px; }
    }
  </style>
</head>
<body>
  <header class="hero">
    <h1>Análisis exploratorio de importaciones: Tránsito Seguro S.A. / Milesight</h1>
    <p class="subtitle">Foco ejecutivo: costo unitario declarado, gasto mensual, unidades importadas, frecuencia operativa y lectura específica de modelos asociados a TrafficX.</p>
    <div class="meta">Generado: {{ generated_at }} | Periodo observado: {{ kpis.first_date }} a {{ kpis.last_date }}</div>
  </header>

  <main>
    <div class="kpis">
      <div class="kpi"><div class="label">FOB total declarado</div><div class="value">{{ kpis.total_fob }}</div><div class="note">{{ kpis.total_despachos }} destinaciones</div></div>
      <div class="kpi"><div class="label">FOB Milesight</div><div class="value">{{ kpis.milesight_fob }}</div><div class="note">{{ kpis.milesight_share }} del FOB total</div></div>
      <div class="kpi"><div class="label">Unidades Milesight</div><div class="value">{{ kpis.milesight_units }}</div><div class="note">{{ kpis.milesight_despachos }} importaciones</div></div>
      <div class="kpi"><div class="label">TrafficX TS5511 GH</div><div class="value">{{ kpis.trafficx_avg_fob }}</div><div class="note">FOB unitario promedio</div></div>
    </div>

    <section>
      <h2>Resumen ejecutivo</h2>
      <ul>
        <li>El universo analizado suma <strong>{{ kpis.total_fob }}</strong> FOB y <strong>{{ kpis.total_cif }}</strong> CIF en {{ kpis.total_despachos }} destinaciones.</li>
        <li>Milesight concentra <strong>{{ kpis.milesight_fob }}</strong> FOB asignado, equivalente a <strong>{{ kpis.milesight_share }}</strong> del gasto FOB observado.</li>
        <li>El costo unitario promedio Milesight, mezclando cámaras, accesorios y modelos, fue <strong>{{ kpis.milesight_fob }} / {{ kpis.milesight_units }} u.</strong>. Para negociación no conviene usar ese promedio general: hay que mirar modelo por modelo.</li>
        <li>No aparece la palabra <strong>TrafficX</strong> en los Excel (<strong>{{ kpis.trafficx_text_matches }}</strong> coincidencias textuales). El hallazgo viene por modelo: <strong>TS5511 GH</strong> coincide con la familia oficial Milesight TrafficX Enforcement Camera.</li>
        <li>TrafficX confirmado por modelo TS5511 GH: <strong>{{ kpis.trafficx_units }}</strong> unidades, <strong>{{ kpis.trafficx_fob }}</strong> FOB asignado, promedio <strong>{{ kpis.trafficx_avg_fob }}</strong> FOB/unit y rango observado <strong>{{ kpis.trafficx_range }}</strong>.</li>
        <li>Frecuencia Milesight: {{ kpis.milesight_despachos }} importaciones, intervalo promedio de <strong>{{ kpis.avg_interval }}</strong> días y mediana de <strong>{{ kpis.median_interval }}</strong> días entre operaciones.</li>
      </ul>
      <div class="callout">
        <strong>Lectura para compras:</strong> si nos cotizan TS5511-GH/TrafficX arriba de USD 1.600 FOB/EXW unitario, la cotización queda cerca o por encima del promedio declarado por este importador. El piso histórico observado para TS5511 GH está cerca de USD 1.519 por unidad declarada.
      </div>
    </section>

    <section>
      <h2>Foco ejecutivo de modelos</h2>
      <p>Lectura prioritaria para compras: TS5511-GH es el modelo principal. En segundo lugar aparece TS5510-GVH en la base; no se detectó TS5510-GH exacto como texto declarado.</p>
      <table>
        <thead>
          <tr>
            <th>Prioridad</th><th>Modelo declarado</th><th class="num">Unidades</th><th class="num">FOB asignado</th><th class="num">CIF asignado</th><th class="num">FOB/unit prom.</th><th class="num">Rango FOB/unit</th><th class="num">Despachos</th>
          </tr>
        </thead>
        <tbody>
          {% for row in focus_model_rows %}
          <tr>
            <td>{{ row.prioridad }}</td><td>{{ row.modelo_limpio }}</td><td class="num">{{ row.unidades_fmt }}</td><td class="num">{{ row.fob_fmt }}</td><td class="num">{{ row.cif_fmt }}</td><td class="num">{{ row.fob_unit_fmt }}</td><td class="num">{{ row.rango_fmt }}</td><td class="num">{{ row.despachos|int }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      <div class="method">
        <strong>TS5510:</strong> la base no muestra TS5510-GH exacto; muestra TS5510-GVH con {{ kpis.ts5510_units }} unidades, {{ kpis.ts5510_fob }} FOB asignado y {{ kpis.ts5510_avg_fob }} FOB/unit promedio. Si el proveedor cotiza TS5510-GH, validar si GH y GVH son configuraciones equivalentes o variantes distintas.
      </div>
    </section>

    <section>
      <h2>Visuales interactivos</h2>
      <p class="small">Versión recomendada para portafolio: permite pasar el mouse por cada barra y revisar valores sin abrir la base.</p>
      <div class="interactive-chart">{{ interactive_charts.gasto_mensual }}</div>
      <div class="interactive-chart">{{ interactive_charts.modelos_milesight }}</div>
      {% if interactive_charts.ts5511_compra is defined %}
      <div class="interactive-chart">{{ interactive_charts.ts5511_compra }}</div>
      {% endif %}
      <h3>Visuales complementarios</h3>
      <div class="grid-2" style="margin-top:22px;">
        <div class="chart"><img src="{{ charts.milesight_unidades_unitario.b64 }}" alt="Unidades Milesight y costo unitario"></div>
        <div class="chart"><img src="{{ charts.costo_unitario_evolucion.b64 }}" alt="Evolución costo unitario"></div>
      </div>
      <div class="grid-2" style="margin-top:22px;">
        <div class="chart"><img src="{{ charts.frecuencia_importaciones.b64 }}" alt="Frecuencia de importaciones"></div>
        <div class="chart"><img src="{{ charts.heatmap_categoria_mes.b64 }}" alt="Heatmap categoría mes"></div>
      </div>
    </section>

    <section>
      <h2>Costo Milesight por modelo</h2>
      <p>Valores asignados por cantidad cuando una destinacion declara varios modelos bajo un mismo item arancelario con FOB total concentrado en el primer renglon.</p>
      <table>
        <thead>
          <tr>
            <th>Modelo</th><th class="num">Unidades</th><th class="num">FOB asignado</th><th class="num">CIF asignado</th><th class="num">FOB/unit prom.</th><th class="num">Rango FOB/unit</th><th class="num">Despachos</th>
          </tr>
        </thead>
        <tbody>
          {% for row in model_rows %}
          <tr>
            <td>{{ row.modelo_limpio }}</td><td class="num">{{ row.unidades_fmt }}</td><td class="num">{{ row.fob_fmt }}</td><td class="num">{{ row.cif_fmt }}</td><td class="num">{{ row.fob_unit_fmt }}</td><td class="num">{{ row.rango_fmt }}</td><td class="num">{{ row.despachos|int }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </section>

    <section>
      <h2>TS5511-GH / TrafficX: compra por compra</h2>
      <p><span class="pill">Hallazgo clave</span> El texto TrafficX no está escrito en los Excel, pero los modelos TS5511 GH se vinculan con la línea TrafficX Enforcement de Milesight. Para TS5510 GVH y TS4466 hay relación técnica por familia TS/traffic/radar, aunque conviene validarlo contra factura o ficha técnica del proveedor antes de venderlo internamente como TrafficX exacto.</p>
      <table>
        <thead>
          <tr>
            <th>Fecha</th><th>Despacho</th><th>Modelo</th><th class="num">Unidades</th><th class="num">FOB/unit</th><th class="num">CIF/unit</th><th class="num">FOB asignado</th><th>Días vs compra anterior</th><th>Incoterm declarado</th>
          </tr>
        </thead>
        <tbody>
          {% for row in trafficx_rows %}
          <tr>
            <td>{{ row.fecha_fmt }}</td><td>{{ row.identificador }}</td><td>{{ row.modelo_limpio }}</td><td class="num">{{ row.unidades_fmt }}</td><td class="num">{{ row.fob_unit_fmt }}</td><td class="num">{{ row.cif_unit_fmt }}</td><td class="num">{{ row.fob_asig_fmt }}</td><td>{{ row.dias_fmt }}</td><td>{{ row.condicion_de_venta }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      <p class="small">Modelos TS relacionados en la base: {{ kpis.ts_related_units }} unidades y {{ kpis.ts_related_fob }} FOB asignado, incluyendo TS5511 GH, TS5510 GVH y TS4466 X4RIPG1.</p>
    </section>

    <section>
      <h2>Mensual Milesight</h2>
      <table>
        <thead>
          <tr>
            <th>Mes</th><th class="num">Unidades</th><th class="num">FOB</th><th class="num">CIF</th><th class="num">FOB/unit prom.</th><th class="num">Despachos</th>
          </tr>
        </thead>
        <tbody>
          {% for row in monthly_rows %}
          <tr>
            <td>{{ row.mes }}</td><td class="num">{{ row.unidades_fmt }}</td><td class="num">{{ row.fob_fmt }}</td><td class="num">{{ row.cif_fmt }}</td><td class="num">{{ row.unit_fmt }}</td><td class="num">{{ row.despachos }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </section>

    <section>
      <h2>Metodologia y limitaciones</h2>
      <div class="method">
        <p><strong>Base:</strong> archivos de importaciones detalladas de Argentina provistos en la carpeta del proyecto.</p>
        <ul>
          {% for row in source_periods %}
          <li>{{ row.archivo }} | periodo declarado {{ row.periodo }} | importador {{ row.importador }}</li>
          {% endfor %}
        </ul>
        <p><strong>Asignación de valor:</strong> cuando varios submodelos aparecen dentro del mismo despacho/item/NCM y solo el primer renglón trae FOB/CIF positivo, se calcula un unitario promedio del grupo y se multiplica por la cantidad de cada submodelo. Esto permite estimar mix por modelo, pero no reemplaza la factura comercial.</p>
        <p><strong>Alcance de costo:</strong> FOB/CIF declarados en USD. No incluye derechos, tasa estadística, IVA, percepciones, depósito fiscal, despachante, transporte local ni margen comercial.</p>
      </div>
    </section>

    <section>
      <h2>Stack Python usado</h2>
      <ul>
        <li><strong>pandas:</strong> limpieza, agregaciones, tablas y exportación CSV.</li>
        <li><strong>numpy:</strong> cálculos vectorizados y asignación condicional.</li>
        <li><strong>matplotlib + seaborn:</strong> gráficos estáticos de alta resolución aptos para portafolio, GitHub o presentaciones.</li>
        <li><strong>jinja2:</strong> render del reporte HTML/Markdown.</li>
        <li><strong>zipfile + xml.etree:</strong> lectura directa de Excel OOXML para que el analisis corra incluso si no esta instalado openpyxl.</li>
      </ul>
    </section>

    <section>
      <h2>Fuentes externas usadas para identificar TrafficX</h2>
      <ul>
        {% for source in sources %}
        <li><a href="{{ source.url }}">{{ source.titulo }}</a> - {{ source.nota }}</li>
        {% endfor %}
      </ul>
    </section>

    <div class="footer">
      Reporte exploratorio ad hoc. Usar como soporte de compras y negociación; validar contra factura comercial, packing list y ficha técnica antes de tomar una decisión comercial definitiva.
    </div>
  </main>
</body>
</html>
"""


MD_TEMPLATE = r"""
# Análisis exploratorio de importaciones: Tránsito Seguro S.A. / Milesight

Generado: {{ generated_at }}  
Periodo observado: {{ kpis.first_date }} a {{ kpis.last_date }}

## KPIs ejecutivos

| Indicador | Valor |
|---|---:|
| FOB total declarado | {{ kpis.total_fob }} |
| CIF total declarado | {{ kpis.total_cif }} |
| Destinaciones totales | {{ kpis.total_despachos }} |
| FOB Milesight asignado | {{ kpis.milesight_fob }} |
| Participación Milesight sobre FOB | {{ kpis.milesight_share }} |
| Unidades Milesight | {{ kpis.milesight_units }} |
| Importaciones Milesight | {{ kpis.milesight_despachos }} |
| TrafficX confirmado TS5511 GH - unidades | {{ kpis.trafficx_units }} |
| TrafficX confirmado TS5511 GH - FOB unitario promedio | {{ kpis.trafficx_avg_fob }} |
| TrafficX confirmado TS5511 GH - rango FOB unitario | {{ kpis.trafficx_range }} |

## Resumen ejecutivo

- El universo analizado suma **{{ kpis.total_fob }} FOB** y **{{ kpis.total_cif }} CIF** en **{{ kpis.total_despachos }} destinaciones**.
- Milesight concentra **{{ kpis.milesight_fob }} FOB asignado**, equivalente a **{{ kpis.milesight_share }}** del gasto FOB observado.
- No aparece la palabra **TrafficX** en los Excel (**{{ kpis.trafficx_text_matches }}** coincidencias textuales). El hallazgo viene por modelo: **TS5511 GH** coincide con la familia oficial Milesight TrafficX Enforcement Camera.
- TrafficX confirmado por modelo TS5511 GH: **{{ kpis.trafficx_units }} unidades**, **{{ kpis.trafficx_fob }} FOB asignado**, promedio **{{ kpis.trafficx_avg_fob }} FOB/unit**.
- Frecuencia Milesight: **{{ kpis.milesight_despachos }} importaciones**, intervalo promedio de **{{ kpis.avg_interval }} días** y mediana de **{{ kpis.median_interval }} días**.

**Lectura para compras:** si nos cotizan TS5511-GH/TrafficX arriba de USD 1.600 FOB/EXW unitario, la cotización queda cerca o por encima del promedio declarado por este importador. El piso histórico observado para TS5511 GH está cerca de USD 1.519 por unidad declarada.

## Foco ejecutivo de modelos

Lectura prioritaria para compras: **TS5511-GH** es el modelo principal. En segundo lugar aparece **TS5510-GVH** en la base; no se detectó **TS5510-GH** exacto como texto declarado.

| Prioridad | Modelo declarado | Unidades | FOB asignado | CIF asignado | FOB/unit prom. | Rango FOB/unit | Despachos |
|---|---|---:|---:|---:|---:|---:|---:|
{% for row in focus_model_rows -%}
| {{ row.prioridad }} | {{ row.modelo_limpio }} | {{ row.unidades_fmt }} | {{ row.fob_fmt }} | {{ row.cif_fmt }} | {{ row.fob_unit_fmt }} | {{ row.rango_fmt }} | {{ row.despachos|int }} |
{% endfor %}

**TS5510:** la base no muestra TS5510-GH exacto; muestra TS5510-GVH con **{{ kpis.ts5510_units }} unidades**, **{{ kpis.ts5510_fob }} FOB asignado** y **{{ kpis.ts5510_avg_fob }} FOB/unit promedio**. Si el proveedor cotiza TS5510-GH, validar si GH y GVH son configuraciones equivalentes o variantes distintas.

## Visuales

![Gasto FOB mensual]({{ charts.gasto_fob_mensual.md_path }})

![Milesight unidades y unitario]({{ charts.milesight_unidades_unitario.md_path }})

![Mix Milesight por modelo]({{ charts.mix_modelos_milesight.md_path }})

![Evolución costo unitario]({{ charts.costo_unitario_evolucion.md_path }})

![Frecuencia importaciones]({{ charts.frecuencia_importaciones.md_path }})

![Heatmap categoría mes]({{ charts.heatmap_categoria_mes.md_path }})

{% if charts.trafficx_ts5511_unitario is defined %}
![TrafficX TS5511 GH unitario]({{ charts.trafficx_ts5511_unitario.md_path }})
{% endif %}

## Costo Milesight por modelo

| Modelo | Unidades | FOB asignado | CIF asignado | FOB/unit prom. | Rango FOB/unit | Despachos |
|---|---:|---:|---:|---:|---:|---:|
{% for row in model_rows -%}
| {{ row.modelo_limpio }} | {{ row.unidades_fmt }} | {{ row.fob_fmt }} | {{ row.cif_fmt }} | {{ row.fob_unit_fmt }} | {{ row.rango_fmt }} | {{ row.despachos|int }} |
{% endfor %}

## TS5511-GH / TrafficX: compra por compra

| Fecha | Despacho | Modelo | Unidades | FOB/unit | CIF/unit | FOB asignado | Días vs compra anterior | Incoterm declarado |
|---|---|---|---:|---:|---:|---:|---|---|
{% for row in trafficx_rows -%}
| {{ row.fecha_fmt }} | {{ row.identificador }} | {{ row.modelo_limpio }} | {{ row.unidades_fmt }} | {{ row.fob_unit_fmt }} | {{ row.cif_unit_fmt }} | {{ row.fob_asig_fmt }} | {{ row.dias_fmt }} | {{ row.condicion_de_venta }} |
{% endfor %}

Modelos TS relacionados en la base: **{{ kpis.ts_related_units }} unidades** y **{{ kpis.ts_related_fob }} FOB asignado**, incluyendo TS5511 GH, TS5510 GVH y TS4466 X4RIPG1.

## Mensual Milesight

| Mes | Unidades | FOB | CIF | FOB/unit prom. | Despachos |
|---|---:|---:|---:|---:|---:|
{% for row in monthly_rows -%}
| {{ row.mes }} | {{ row.unidades_fmt }} | {{ row.fob_fmt }} | {{ row.cif_fmt }} | {{ row.unit_fmt }} | {{ row.despachos }} |
{% endfor %}

## Metodología y limitaciones

Base: archivos de importaciones detalladas de Argentina provistos en la carpeta del proyecto.

{% for row in source_periods -%}
- {{ row.archivo }} | periodo declarado {{ row.periodo }} | importador {{ row.importador }}
{% endfor %}

Asignación de valor: cuando varios submodelos aparecen dentro del mismo despacho/item/NCM y solo el primer renglón trae FOB/CIF positivo, se calcula un unitario promedio del grupo y se multiplica por la cantidad de cada submodelo. Esto permite estimar mix por modelo, pero no reemplaza la factura comercial.

Alcance de costo: FOB/CIF declarados en USD. No incluye derechos, tasa estadística, IVA, percepciones, depósito fiscal, despachante, transporte local ni margen comercial.

## Stack Python usado

- `pandas`: limpieza, agregaciones, tablas y exportación CSV.
- `numpy`: cálculos vectorizados y asignación condicional.
- `matplotlib` + `seaborn`: gráficos estáticos de alta resolución.
- `jinja2`: render del reporte HTML/Markdown.
- `zipfile` + `xml.etree`: lectura directa de Excel OOXML sin depender de `openpyxl`.

## Fuentes externas para identificar TrafficX

{% for source in sources -%}
- [{{ source.titulo }}]({{ source.url }}): {{ source.nota }}
{% endfor %}
"""


if __name__ == "__main__":
    main()
