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
        time.sleep(2 * n)  # espera e tenta de novo
    raise RuntimeError(f"{ticker}: falha na extração ({erro})")

