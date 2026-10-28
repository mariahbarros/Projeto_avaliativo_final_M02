-- Consultas analiticas de referencia (sintaxe PostgreSQL).
-- Reproduzem em SQL as metricas calculadas em Pandas no app.
-- Ajuste o periodo nos filtros BETWEEN conforme necessario.

-- Q1) Base 100 (rentabilidade comparativa normalizada)
WITH base AS (
    SELECT a.ticker, f.data, f.fech_ajustado,
           FIRST_VALUE(f.fech_ajustado) OVER (PARTITION BY f.id_ativo ORDER BY f.data) AS p0
    FROM fact_cotacao f JOIN dim_ativo a USING (id_ativo)
    WHERE f.data BETWEEN '2019-12-30' AND '2025-12-30'
)
SELECT ticker, data, 100 * fech_ajustado / p0 AS base100
FROM base ORDER BY ticker, data;

-- Q2) Risco x Retorno (retorno acumulado, volatilidade anualizada e razao retorno/risco)
WITH agg AS (
    SELECT id_ativo,
           (ARRAY_AGG(fech_ajustado ORDER BY data DESC))[1]
         / (ARRAY_AGG(fech_ajustado ORDER BY data ASC))[1] - 1 AS ret_acum,
           STDDEV_SAMP(retorno_diario) * SQRT(252)             AS vol_anual,
           AVG(volume_financeiro)                              AS vol_fin_medio
    FROM fact_cotacao
    WHERE data BETWEEN '2019-12-30' AND '2025-12-30'
    GROUP BY id_ativo
)
SELECT a.ticker, ret_acum, vol_anual, vol_fin_medio,
       ret_acum / NULLIF(vol_anual, 0) AS razao_ret_vol
FROM agg JOIN dim_ativo a USING (id_ativo)
ORDER BY razao_ret_vol DESC;

-- Q3) Distribuicao dos retornos diarios (dispersao e caudas)
SELECT a.ticker,
       AVG(retorno_diario)                                          AS media,
       STDDEV_SAMP(retorno_diario)                                  AS desvio,
       MIN(retorno_diario)                                          AS pior_dia,
       MAX(retorno_diario)                                          AS melhor_dia,
       PERCENTILE_CONT(0.05) WITHIN GROUP (ORDER BY retorno_diario) AS var_95
FROM fact_cotacao f JOIN dim_ativo a USING (id_ativo)
WHERE f.data BETWEEN '2019-12-30' AND '2025-12-30'
GROUP BY a.ticker;

-- Q4) Drawdown maximo por ativo
WITH dd AS (
    SELECT id_ativo, data,
           fech_ajustado / MAX(fech_ajustado) OVER (PARTITION BY id_ativo ORDER BY data) - 1 AS drawdown
    FROM fact_cotacao
    WHERE data BETWEEN '2019-12-30' AND '2025-12-30'
)
SELECT a.ticker, MIN(drawdown) AS max_drawdown
FROM dd JOIN dim_ativo a USING (id_ativo)
GROUP BY a.ticker ORDER BY max_drawdown DESC;   -- 1o = mais resiliente

-- Q5) Medias moveis simples (SMA 21 e SMA 50) de um ativo
SELECT f.data, f.fech_ajustado,
       AVG(f.fech_ajustado) OVER (ORDER BY f.data ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) AS sma_21,
       AVG(f.fech_ajustado) OVER (ORDER BY f.data ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma_50
FROM fact_cotacao f JOIN dim_ativo a USING (id_ativo)
WHERE a.ticker = 'WEGE3'
ORDER BY f.data;
