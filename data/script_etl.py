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

