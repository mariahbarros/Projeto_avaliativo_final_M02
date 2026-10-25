from __future__ import annotations

import sys
import time
import tomllib
from pathlib import Path
from typing import Mapping

import pandas as pd
import yfinance as yf
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine

# 1) CONFIGURAÇÕES GERAIS

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SQLITE_PATH = DATA_DIR / "b3_catarinenses.db"

START, END = "2019-12-30", "2025-12-30"

ATIVOS = {
    "WEGE3": ("WEG", "Bens de Capital"),
    "EGIE3": ("Engie Brasil Energia", "Energia Elétrica"),
    "CASN4": ("Casan", "Saneamento"),
    "INTB3": ("Intelbras", "Tecnologia"),
    "SHUL4": ("Schulz", "Bens de Capital"),
    "TUPY3": ("Tupy", "Autopeças / Metalurgia"),
    "RANI3": ("Irani", "Papel e Celulose"),
    "CLSC3": ("Celesc", "Energia Elétrica"),
    "FIQE3": ("Unifique", "Telecomunicações"),
}

COLUNAS = {  # nomes do Yahoo → nomes em português
    "Open": "abertura", "High": "maxima", "Low": "minima",
    "Close": "fechamento", "Adj Close": "fech_ajustado", "Volume": "volume",
}

# 2) CRIAÇÃO DAS TABELAS NO BANCO DE DADOS

DDL = """
DROP TABLE IF EXISTS fact_cotacao;
DROP TABLE IF EXISTS dim_calendario;
DROP TABLE IF EXISTS dim_ativo;
DROP TABLE IF EXISTS meta_qualidade;
CREATE TABLE dim_ativo (
    id_ativo INTEGER PRIMARY KEY, ticker VARCHAR(10) NOT NULL UNIQUE,
    ticker_yf VARCHAR(15) NOT NULL, empresa VARCHAR(80) NOT NULL, setor VARCHAR(60) NOT NULL);
CREATE TABLE dim_calendario (
    data DATE PRIMARY KEY, ano INTEGER NOT NULL, trimestre INTEGER NOT NULL,
    mes INTEGER NOT NULL, dia_semana INTEGER NOT NULL);
CREATE TABLE fact_cotacao (
    id_ativo INTEGER NOT NULL REFERENCES dim_ativo(id_ativo),
    data DATE NOT NULL REFERENCES dim_calendario(data),
    abertura DOUBLE PRECISION, maxima DOUBLE PRECISION, minima DOUBLE PRECISION,
    fechamento DOUBLE PRECISION, fech_ajustado DOUBLE PRECISION NOT NULL,
    volume BIGINT, volume_financeiro DOUBLE PRECISION, retorno_diario DOUBLE PRECISION,
    PRIMARY KEY (id_ativo, data));
CREATE INDEX idx_fact_cotacao_data ON fact_cotacao (data);
CREATE TABLE meta_qualidade (
    ticker VARCHAR(10), linhas_brutas INTEGER, linhas_finais INTEGER, removidas INTEGER,
    outliers_retorno INTEGER, data_inicio VARCHAR(10), data_fim VARCHAR(10));
"""

# 3) EXTRAÇÃO DOS DADOS NO SITE YAHOO FINANCE

def extrair(ticker: str, tentativas: int = 3) -> pd.DataFrame:
    fim_exclusivo = (pd.Timestamp(END) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")  # Yahoo: fim é exclusivo
    erro = None
    for n in range(1, tentativas + 1):
        try:
            bruto = yf.Ticker(f"{ticker}.SA").history(
                start=START, end=fim_exclusivo, auto_adjust=False, actions=False
            )
            if not bruto.empty:
                return bruto
            erro = "retorno vazio"
        except Exception as exc:
            erro = str(exc)
        time.sleep(2 * n)  
    raise RuntimeError(f"{ticker}: falha na extração ({erro})")

# 4) TRATAMENTO DOS DADOS

def tratar(bruto: pd.DataFrame, ticker: str) -> tuple[pd.DataFrame, dict]:
    df = bruto.copy()
    datas = pd.to_datetime(df.index)
    if datas.tz is not None:
        datas = datas.tz_localize(None)     # remove fuso horário
    df.index = datas.normalize()
    df.index.name = "data"
    df = df.reset_index()[["data", *COLUNAS]].rename(columns=COLUNAS)
    n_bruto = len(df)

    df = df.drop_duplicates("data", keep="last")                       # remove duplicatas
    for c in COLUNAS.values():
        df[c] = pd.to_numeric(df[c], errors="coerce")                  # transforma texto em número
    df = df[df["data"].between(pd.Timestamp(START), pd.Timestamp(END))]  # organiza período de interesse
    df = df.dropna(subset=["fech_ajustado", "fechamento"])             # remove linhas sem fechamento
    df = df[(df[["abertura", "maxima", "minima", "fechamento", "fech_ajustado"]] > 0).all(axis=1)]
    df = df[df["maxima"] >= df["minima"]]                              # informações inconsistentes
    df["volume"] = df["volume"].fillna(0).clip(lower=0).astype("int64")
    df = df.sort_values("data").reset_index(drop=True)

    df["volume_financeiro"] = df["volume"] * df["fechamento"]          # cria coluna de volume financeiro
    df["retorno_diario"] = df["fech_ajustado"].pct_change()            # cria coluna de retorno diário (fechamento ajustado)

    relatorio = {               
        "ticker": ticker, "linhas_brutas": n_bruto, "linhas_finais": len(df),
        "removidas": n_bruto - len(df),
        "outliers_retorno": int((df["retorno_diario"].abs() > 0.40).sum()),  # só sinaliza
        "data_inicio": df["data"].min().strftime("%Y-%m-%d") if len(df) else None,
        "data_fim": df["data"].max().strftime("%Y-%m-%d") if len(df) else None,
    }
    return df, relatorio

# 5) CARREGAMENTO DOS DADOS 

def carregar(engine: Engine, tabelas: dict[str, pd.DataFrame], meta: list[dict]) -> None:
    dim_ativo = pd.DataFrame(
        [{"id_ativo": i, "ticker": t, "ticker_yf": f"{t}.SA", "empresa": ATIVOS[t][0], "setor": ATIVOS[t][1]}
         for i, t in enumerate(tabelas, start=1)]
    )
    fato = pd.concat([d.assign(ticker=t) for t, d in tabelas.items()], ignore_index=True)
    fato = fato.merge(dim_ativo[["id_ativo", "ticker"]], on="ticker")
    datas = pd.DataFrame({"data": sorted(fato["data"].unique())})
    dim_cal = datas.assign(ano=datas["data"].dt.year, trimestre=datas["data"].dt.quarter,
                           mes=datas["data"].dt.month, dia_semana=datas["data"].dt.dayofweek)
    fato = fato[["id_ativo", "data", "abertura", "maxima", "minima", "fechamento",
                 "fech_ajustado", "volume", "volume_financeiro", "retorno_diario"]]

    with engine.begin() as conn:  # transação: ou grava tudo, ou nada
        for comando in [c.strip() for c in DDL.split(";") if c.strip()]:
            conn.exec_driver_sql(comando)
        dim_ativo.to_sql("dim_ativo", conn, if_exists="append", index=False)
        dim_cal.to_sql("dim_calendario", conn, if_exists="append", index=False)
        fato.to_sql("fact_cotacao", conn, if_exists="append", index=False, chunksize=2000)
        pd.DataFrame(meta).to_sql("meta_qualidade", conn, if_exists="append", index=False)

# execução do ETL
def executar_etl(engine: Engine, log=print) -> list[dict]:
    
    tabelas, meta = {}, []
    for ticker in ATIVOS:
        try:
            df, rel = tratar(extrair(ticker), ticker)
            if df.empty:
                raise RuntimeError("sem dados após a limpeza")
            tabelas[ticker] = df
            meta.append(rel)
            log(f"OK   {ticker}: {rel['linhas_finais']} pregões ({rel['data_inicio']} → {rel['data_fim']})")
        except Exception as exc:  
            log(f"ERRO {ticker}: {exc}")
    if not tabelas:
        raise RuntimeError("Nenhum ativo pôde ser carregado.")
    carregar(engine, tabelas, meta)
    log(f"Banco gravado com {len(tabelas)} ativo(s).")
    return meta
