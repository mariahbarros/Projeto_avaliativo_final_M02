"""Data App – Análise Técnica e Performance de Ações Catarinenses (B3)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import etl

st.set_page_config(
    page_title="B3 Catarinenses | Análise Técnica",
    page_icon="📈",
    layout="wide",
)

st.markdown("""
<style>
@media print {
  @page { size: A4 landscape; margin: 10mm; }
  [data-testid="stSidebar"], [data-testid="stHeader"], [data-testid="stToolbar"],
  [data-testid="stDownloadButton"] { display: none !important; }
  .block-container { padding-top: 0 !important; max-width: 100% !important; }
  * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
</style>
""", unsafe_allow_html=True)

DIAS_UTEIS = 252
TEMPLATE = "plotly_white"
RF_PADRAO = 10.0  # % a.a. – aproximação do CDI médio do período; ajustável na barra lateral


# ──────────────────────────────────────────────────────────────────────────────
# 1) ACESSO A DADOS (resiliente + cache)
# ──────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner="Conectando à base de dados…")
def carregar_base(pg_cfg: dict | None):
    """PostgreSQL (nuvem) → SQLite local → reconstrução via yfinance."""
    avisos: list[str] = []

    if pg_cfg:
        try:
            eng = etl.engine_postgres(pg_cfg)
            df = etl.ler_cotacoes(eng)
            if not df.empty:
                return df, etl.ler_meta(eng), "PostgreSQL (nuvem)", avisos
            avisos.append("PostgreSQL conectado, porém sem dados (rode scripts/load_data.py).")
        except Exception as exc:  # nunca expõe credenciais na mensagem
            avisos.append(f"PostgreSQL indisponível ({type(exc).__name__}). Usando contingência local.")

    eng = etl.engine_sqlite()
    try:
        df = etl.ler_cotacoes(eng)
    except Exception:
        df = pd.DataFrame()

    if df.empty:
        avisos.append("Base local ausente: executando ETL via yfinance (pode levar alguns minutos).")
        etl.executar_etl(eng, log=lambda *_: None)
        df = etl.ler_cotacoes(eng)

    return df, etl.ler_meta(eng), "SQLite local (contingência)", avisos


def ler_pg_cfg() -> dict | None:
    try:
        return dict(st.secrets["postgres"]) if "postgres" in st.secrets else None
    except Exception:  # sem secrets.toml
        return None


# ──────────────────────────────────────────────────────────────────────────────
# 2) TRANSFORMAÇÕES E MÉTRICAS (cacheadas)
# ──────────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def filtrar(df: pd.DataFrame, tickers: tuple, d0: pd.Timestamp, d1: pd.Timestamp) -> pd.DataFrame:
    out = df[df["ticker"].isin(tickers) & df["data"].between(d0, d1)].sort_values(["ticker", "data"]).copy()
    g = out.groupby("ticker")["adj_close"]
    out["retorno_diario"] = g.pct_change()
    out["base100"] = g.transform(lambda s: s / s.iloc[0] * 100)
    out["drawdown"] = g.transform(lambda s: s / s.cummax() - 1)
    return out.reset_index(drop=True)


@st.cache_data(show_spinner=False)
def calcular_metricas(df: pd.DataFrame, rf: float) -> pd.DataFrame:
    """rf em decimal a.a. Sharpe = (retorno anualizado − rf) / volatilidade anualizada."""
    linhas = []
    for t, g in df.groupby("ticker"):
        g = g.sort_values("data")
        p, r = g["adj_close"], g["retorno_diario"].dropna()
        if len(p) < 3:
            continue
        ret_acum = p.iloc[-1] / p.iloc[0] - 1
        anos = (g["data"].iloc[-1] - g["data"].iloc[0]).days / 365.25
        cagr = (1 + ret_acum) ** (1 / anos) - 1 if anos > 0 else np.nan
        vol = r.std() * np.sqrt(DIAS_UTEIS)
        dd = g["drawdown"]
        linhas.append(
            {
                "ticker": t,
                "empresa": g["empresa"].iloc[0],
                "inicio": g["data"].iloc[0],
                "fim": g["data"].iloc[-1],
                "ret_acum": ret_acum,
                "ret_anual": cagr,
                "vol_anual": vol,
                "sharpe": (cagr - rf) / vol if vol > 0 else np.nan,
                "max_dd": dd.min(),
                "data_max_dd": g.loc[dd.idxmin(), "data"],
                "vol_fin_medio": g["volume_financeiro"].mean(),
            }
        )
    return pd.DataFrame(linhas)


@st.cache_data(show_spinner=False)
def estatisticas_retornos(df: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    for t, g in df.groupby("ticker"):
        r = g["retorno_diario"].dropna()
        if len(r) < 10:
            continue
        var95 = r.quantile(0.05)
        linhas.append(
            {
                "Ativo": t,
                "Média diária (%)": r.mean() * 100,
                "Desvio-padrão (%)": r.std() * 100,
                "Assimetria": r.skew(),
                "Curtose (excesso)": r.kurt(),
                "Pior dia (%)": r.min() * 100,
                "Melhor dia (%)": r.max() * 100,
                "VaR 95% (%)": var95 * 100,
                "CVaR 95% (%)": r[r <= var95].mean() * 100,
                "Dias negativos (%)": (r < 0).mean() * 100,
            }
        )
    return pd.DataFrame(linhas)


def _br(txt: str) -> str:
    return txt.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(x: float, casas: int = 1) -> str:
    return _br(f"{x * 100:,.{casas}f}%")


def brl(x: float) -> str:
    return _br(f"R$ {x:,.0f}")


# ──────────────────────────────────────────────────────────────────────────────
# 3) ESTADO DE SESSÃO E CARGA
# ──────────────────────────────────────────────────────────────────────────────
try:
    base, meta, fonte, avisos = carregar_base(ler_pg_cfg())
except Exception as exc:
    st.error(
        "Não foi possível carregar os dados. Execute `python scripts/load_data.py` "
        f"para gerar a base local. Detalhe: {type(exc).__name__}"
    )
    st.stop()

if base.empty:
    st.error("A base está vazia. Execute `python scripts/load_data.py`.")
    st.stop()

TODOS = sorted(base["ticker"].unique())
DATA_MIN, DATA_MAX = base["data"].min().date(), base["data"].max().date()
CORES = {t: px.colors.qualitative.Bold[i % 11] for i, t in enumerate(TODOS)}


def resetar_filtros() -> None:
    st.session_state.sel_tickers = TODOS
    st.session_state.sel_datas = (DATA_MIN, DATA_MAX)
    st.session_state.rf_pct = RF_PADRAO


if "sel_tickers" not in st.session_state:
    resetar_filtros()

# ──────────────────────────────────────────────────────────────────────────────
# 4) BARRA LATERAL – FILTROS REATIVOS
# ──────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🎛️ Filtros")
    st.multiselect("Ativos", TODOS, key="sel_tickers", help="Empresas catarinenses listadas na B3.")
    st.date_input(
        "Período", key="sel_datas", min_value=DATA_MIN, max_value=DATA_MAX, format="DD/MM/YYYY"
    )
    st.number_input(
        "Taxa livre de risco (% a.a.)", min_value=0.0, max_value=30.0, step=0.5, key="rf_pct",
        help="Usada no índice de Sharpe. Padrão ≈ CDI médio 2020-2025.",
    )
    st.button("↺ Restaurar padrão", on_click=resetar_filtros, width="stretch")
    st.divider()
    st.caption(f"🗄️ Fonte: **{fonte}**")
    for a in avisos:
        st.warning(a, icon="⚠️")

if not st.session_state.sel_tickers:
    st.warning("Selecione ao menos um ativo na barra lateral.")
    st.stop()
if len(st.session_state.sel_datas) != 2:
    st.info("Selecione a data final do período na barra lateral.")
    st.stop()

d0, d1 = (pd.Timestamp(d) for d in st.session_state.sel_datas)
tickers = tuple(sorted(st.session_state.sel_tickers))
rf = st.session_state.rf_pct / 100

df = filtrar(base, tickers, d0, d1)
met = calcular_metricas(df, rf)
if met.empty:
    st.warning("Sem dados suficientes para o filtro escolhido.")
    st.stop()

# ──────────────────────────────────────────────────────────────────────────────
# 5) CABEÇALHO + KPIs
# ──────────────────────────────────────────────────────────────────────────────
st.title("📈 Ações Catarinenses na B3")
st.caption(
    f"Análise técnica e de performance · {d0:%d/%m/%Y} a {d1:%d/%m/%Y} · "
    f"{len(tickers)} ativo(s) · Fechamento Ajustado (proventos e desdobramentos)"
)

melhor = met.loc[met["ret_acum"].idxmax()]
pior = met.loc[met["ret_acum"].idxmin()]
k1, k2, k3, k4 = st.columns(4)
k1.metric("Retorno Médio da Carteira", pct(met["ret_acum"].mean()), help="Média simples (pesos iguais) do retorno acumulado dos ativos selecionados.")
k2.metric("Ativo Destaque", melhor["ticker"], f"{melhor['ret_acum'] * 100:+.1f}%".replace(".", ","))
k3.metric("Ativo de Menor Performance", pior["ticker"], f"{pior['ret_acum'] * 100:+.1f}%".replace(".", ","))
k4.metric("Volatilidade Anualizada Média", pct(met["vol_anual"].mean()) + " a.a.")

tardios = met[met["inicio"] > d0 + pd.Timedelta(days=10)]
if not tardios.empty:
    txt = ", ".join(f"{r.ticker} ({r.inicio:%d/%m/%Y})" for r in tardios.itertuples())
    st.caption(f"ℹ️ Ativos com histórico iniciado após o começo do período (Base 100 parte da 1ª cotação disponível): {txt}.")

# ──────────────────────────────────────────────────────────────────────────────
# 6) ABAS
# ──────────────────────────────────────────────────────────────────────────────
aba1, aba2, aba3, aba4, aba5, aba6 = st.tabs(
    ["📊 Rentabilidade", "⚖️ Risco x Retorno", "📉 Distribuição", "🕳️ Drawdown", "📈 Tendência (SMA)", "🗄️ Dados"]
)

# ── Aba 1: Base 100 ───────────────────────────────────────────────────────────
with aba1:
    st.subheader("Rentabilidade comparativa normalizada (Base 100)")
    top = met.sort_values("ret_acum", ascending=False).iloc[0]
    st.info(
        f"R$ 100 investidos no início do período em **{top['ticker']}** teriam virado "
        f"**{brl(100 * (1 + top['ret_acum']))}**; o pior resultado foi de **{pior['ticker']}** "
        f"({brl(100 * (1 + pior['ret_acum']))})."
    )
    fig = px.line(
        df, x="data", y="base100", color="ticker", color_discrete_map=CORES,
        labels={"data": "Data", "base100": "Base 100", "ticker": "Ativo"}, template=TEMPLATE,
    )
    fig.add_hline(y=100, line_dash="dash", line_color="gray", annotation_text="Nível 100 (aporte inicial)")
    fig.update_layout(hovermode="x unified", height=520, legend_title_text="")
    st.plotly_chart(fig, width="stretch")

    tab = met.sort_values("ret_acum", ascending=False)[["ticker", "empresa", "ret_acum", "ret_anual", "vol_anual", "sharpe", "max_dd"]].copy()
    for c in ["ret_acum", "ret_anual", "vol_anual", "max_dd"]:
        tab[c] *= 100
    st.dataframe(
        tab, hide_index=True, width="stretch",
        column_config={
            "ticker": "Ativo", "empresa": "Empresa",
            "ret_acum": st.column_config.NumberColumn("Retorno acumulado", format="%.1f%%"),
            "ret_anual": st.column_config.NumberColumn("Retorno anualizado", format="%.1f%%"),
            "vol_anual": st.column_config.NumberColumn("Volatilidade a.a.", format="%.1f%%"),
            "sharpe": st.column_config.NumberColumn("Sharpe", format="%.2f"),
            "max_dd": st.column_config.NumberColumn("Drawdown máx.", format="%.1f%%"),
        },
    )

# ── Aba 2: Risco x Retorno ────────────────────────────────────────────────────
with aba2:
    st.subheader("Risco x Retorno e Índice de Sharpe")
    rank = met.dropna(subset=["sharpe"]).sort_values("sharpe", ascending=False)
    if not rank.empty:
        st.info(
            f"Melhor relação retorno/risco: **{rank.iloc[0]['ticker']}** (Sharpe {rank.iloc[0]['sharpe']:.2f}). "
            f"Sharpe = (retorno anualizado − {st.session_state.rf_pct:.1f}% a.a.) ÷ volatilidade anualizada."
        )
    plot = met.assign(vol_pct=met["vol_anual"] * 100, ret_pct=met["ret_acum"] * 100)
    c1, c2 = st.columns([3, 2])
    with c1:
        fig = px.scatter(
            plot, x="vol_pct", y="ret_pct", size="vol_fin_medio", color="ticker", text="ticker",
            color_discrete_map=CORES, size_max=55, template=TEMPLATE,
            labels={"vol_pct": "Volatilidade anualizada (% a.a.)", "ret_pct": "Retorno acumulado (%)",
                    "vol_fin_medio": "Volume financeiro médio (R$)", "ticker": "Ativo"},
            hover_data={"vol_fin_medio": ":,.0f", "sharpe": ":.2f", "vol_pct": ":.1f", "ret_pct": ":.1f"},
        )
        fig.update_traces(textposition="top center")
        fig.add_hline(y=0, line_dash="dot", line_color="gray")
        fig.update_layout(height=500, showlegend=False)
        st.plotly_chart(fig, width="stretch")
        st.caption("Tamanho da bolha = volume financeiro médio diário (volume × fechamento).")
    with c2:
        fig = px.bar(
            rank.sort_values("sharpe"), x="sharpe", y="ticker", orientation="h", color="ticker",
            color_discrete_map=CORES, template=TEMPLATE, text_auto=".2f",
            labels={"sharpe": "Índice de Sharpe", "ticker": ""},
        )
        fig.update_layout(height=500, showlegend=False, title="Ranking de Sharpe")
        st.plotly_chart(fig, width="stretch")

# ── Aba 3: Distribuição ───────────────────────────────────────────────────────
with aba3:
    st.subheader("Distribuição dos retornos diários")
    opcoes = ["Todos (sobrepostos)", *tickers]
    if st.session_state.get("sel_hist") not in opcoes:
        st.session_state.sel_hist = opcoes[0]
    st.selectbox("Ativo", opcoes, key="sel_hist")
    d = df.dropna(subset=["retorno_diario"]).assign(ret_pct=lambda x: x["retorno_diario"] * 100)
    if st.session_state.sel_hist != opcoes[0]:
        d = d[d["ticker"] == st.session_state.sel_hist]
    fig = px.histogram(
        d, x="ret_pct", color="ticker", nbins=120, marginal="box", barmode="overlay", opacity=0.65,
        color_discrete_map=CORES, template=TEMPLATE,
        labels={"ret_pct": "Retorno diário (%)", "count": "Frequência", "ticker": "Ativo"},
    )
    fig.update_layout(height=520, legend_title_text="")
    st.plotly_chart(fig, width="stretch")

    est = estatisticas_retornos(df)
    if not est.empty:
        mais_risco = est.loc[est["CVaR 95% (%)"].idxmin()]
        st.info(
            f"Maior risco de cauda: **{mais_risco['Ativo']}** — nos 5% piores dias a perda média foi de "
            f"{mais_risco['CVaR 95% (%)']:.2f}% (CVaR 95%). Curtose positiva indica caudas mais pesadas que a normal; "
            "assimetria negativa indica perdas extremas mais frequentes que ganhos extremos."
        )
        st.dataframe(est.set_index("Ativo").round(2), width="stretch")

# ── Aba 4: Drawdown ───────────────────────────────────────────────────────────
with aba4:
    st.subheader("Drawdown a partir do pico histórico")
    resiliente = met.loc[met["max_dd"].idxmax()]
    vulneravel = met.loc[met["max_dd"].idxmin()]
    st.info(
        f"Mais resiliente: **{resiliente['ticker']}** (drawdown máx. {resiliente['max_dd'] * 100:.1f}%). "
        f"Maior queda: **{vulneravel['ticker']}** ({vulneravel['max_dd'] * 100:.1f}% em {vulneravel['data_max_dd']:%d/%m/%Y})."
    )
    dd_plot = df.assign(dd_pct=df["drawdown"] * 100)
    fig = px.area(
        dd_plot, x="data", y="dd_pct", color="ticker", facet_col="ticker", facet_col_wrap=3,
        color_discrete_map=CORES, template=TEMPLATE,
        labels={"data": "", "dd_pct": "Drawdown (%)"},
    )
    fig.update_layout(showlegend=False, height=max(320, 260 * int(np.ceil(len(tickers) / 3))))
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
    st.plotly_chart(fig, width="stretch")

    bar = met.sort_values("max_dd", ascending=False).assign(dd_pct=lambda x: x["max_dd"] * 100)
    fig = px.bar(
        bar, x="ticker", y="dd_pct", color="ticker", color_discrete_map=CORES, template=TEMPLATE,
        text_auto=".1f", labels={"ticker": "Ativo", "dd_pct": "Drawdown máximo (%)"},
        title="Drawdown máximo por ativo (da esquerda p/ direita: mais → menos resiliente)",
    )
    fig.update_layout(showlegend=False, height=380)
    st.plotly_chart(fig, width="stretch")

# ── Aba 5: SMA ────────────────────────────────────────────────────────────────
with aba5:
    st.subheader("Preço de fechamento × Médias Móveis Simples (SMA 21 e SMA 50)")
    if st.session_state.get("sel_sma") not in tickers:
        st.session_state.sel_sma = tickers[0]
    st.selectbox("Ativo", tickers, key="sel_sma")
    sel = st.session_state.sel_sma
    # SMAs calculadas sobre o histórico completo para não "zerar" no início da janela
    h = base[base["ticker"] == sel].sort_values("data").copy()
    h["SMA 21"] = h["adj_close"].rolling(21).mean()
    h["SMA 50"] = h["adj_close"].rolling(50).mean()
    h = h[h["data"].between(d0, d1)].rename(columns={"adj_close": "Fechamento Ajustado"})
    long = h.melt(id_vars="data", value_vars=["Fechamento Ajustado", "SMA 21", "SMA 50"], var_name="Série", value_name="R$")
    fig = px.line(
        long, x="data", y="R$", color="Série", template=TEMPLATE,
        color_discrete_map={"Fechamento Ajustado": "#1B2430", "SMA 21": "#F58220", "SMA 50": "#0F62FE"},
        labels={"data": "Data"},
    )
    fig.update_layout(hovermode="x unified", height=520, legend_title_text="")
    st.plotly_chart(fig, width="stretch")

    ult = h.dropna(subset=["SMA 50"]).tail(1)
    if not ult.empty:
        u = ult.iloc[0]
        s1, s2, s3 = st.columns(3)
        s1.metric("Último fechamento", f"R$ {u['Fechamento Ajustado']:.2f}")
        s2.metric("vs SMA 21", f"{(u['Fechamento Ajustado'] / u['SMA 21'] - 1) * 100:+.1f}%")
        s3.metric("vs SMA 50", f"{(u['Fechamento Ajustado'] / u['SMA 50'] - 1) * 100:+.1f}%")
        acima = (h["Fechamento Ajustado"] > h["SMA 50"])[h["SMA 50"].notna()].mean()
        tendencia = "alta (SMA 21 > SMA 50)" if u["SMA 21"] > u["SMA 50"] else "baixa (SMA 21 < SMA 50)"
        st.info(f"Cruzamento atual indica tendência de **{tendencia}**. O preço ficou acima da SMA 50 em **{acima * 100:.0f}%** dos pregões do período.")

# ── Aba 6: Dados ──────────────────────────────────────────────────────────────
with aba6:
    st.subheader("Dados filtrados e exportação")
    exp = df[["data", "ticker", "empresa", "abertura", "maxima", "minima", "adj_close", "volume_financeiro",
              "retorno_diario", "base100", "drawdown"]].rename(
        columns={"adj_close": "fechamento_ajustado"}
    )
    st.dataframe(exp, hide_index=True, width="stretch", height=380)
    b1, b2 = st.columns(2)
    b1.download_button(
        "⬇️ Baixar cotações filtradas (CSV)", exp.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"cotacoes_b3_{d0:%Y%m%d}_{d1:%Y%m%d}.csv", mime="text/csv", width="stretch",
    )
    b2.download_button(
        "⬇️ Baixar métricas por ativo (CSV)", met.to_csv(index=False).encode("utf-8-sig"),
        file_name="metricas_ativos.csv", mime="text/csv", width="stretch",
    )
    with st.expander("🔎 Qualidade dos dados (relatório do ETL)"):
        if meta.empty:
            st.write("Relatório indisponível.")
        else:
            st.dataframe(meta, hide_index=True, width="stretch")
    with st.expander("🧾 Consultas SQL de referência"):
        arq = etl.ROOT / "queries.sql"
        st.code(arq.read_text(encoding="utf-8") if arq.exists() else "-- queries.sql não encontrado", language="sql")

st.divider()
st.caption("Fonte: Yahoo Finance (yfinance, sufixo .SA) · Material educacional, não constitui recomendação de investimento.")

st.caption("🖨️ Para gerar PDF da aba atual: Ctrl+P (Cmd+P no Mac) → Destino: 'Salvar como PDF'.")
