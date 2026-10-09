#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scanner conservador de dividendos B3 (ações + FIIs) com backtest mensal.
Dados de preços/dividendos: Yahoo Finance via yfinance. Não envia ordens.
Uso: python scanner_dividendos_b3.py --anos 8 --aporte 200 --top 5
"""

from __future__ import annotations

import argparse
import warnings
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)

# Lista inicial EDITÁVEL. Não representa recomendação de compra nem todo o mercado.
# Ações (exemplos de setores diversos) + FIIs (exemplos de segmentos diversos).
ACOES = [
    "ITUB4.SA", "BBSE3.SA", "EGIE3.SA", "TAEE11.SA", "CPFE3.SA",
    "ISAE4.SA", "BBDC4.SA", "PETR4.SA", "VALE3.SA", "VIVT3.SA",
    "ABEV3.SA", "WEGE3.SA", "CMIG4.SA", "SANB11.SA", "CXSE3.SA",
]
FIIS = [
    "HGLG11.SA", "XPLG11.SA", "KNRI11.SA", "VISC11.SA", "HSML11.SA",
    "MXRF11.SA", "KNCR11.SA", "CPTS11.SA", "RBRR11.SA", "HGCR11.SA",
    "TRXF11.SA", "BTLG11.SA", "XPML11.SA", "PVBI11.SA", "RECR11.SA",
]
UNIVERSO = ACOES + FIIS

APORTE_PADRAO = 200.0
TOP_PADRAO = 5
CUSTO_TRADE = 0.0015       # estimativa de 0,15% sobre o valor negociado; altere conforme sua corretora
YIELD_MINIMO = 0.03        # 3% trailing 12 meses; filtro, não meta de retorno
YIELD_MAXIMO = 0.18        # acima disso exige cautela: pode ser evento não recorrente ou queda forte
MESES_HISTORICO = 36
MIN_ANOS_COM_DIVIDENDO = 2
MAX_PESO_ATIVO = 0.25      # limite de peso por ativo
MAX_PESO_FII = 0.60        # limite agregado para FIIs
MAX_PESO_ACOES = 0.80      # limite agregado para ações


def baixar_dados(tickers: list[str], inicio: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Baixa preços brutos, preços ajustados e dividendos. Ativos sem dados são ignorados."""
    brutos, ajustados, dividendos = {}, {}, {}
    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(start=inicio, auto_adjust=False, actions=True, repair=True)
            if hist.empty or "Close" not in hist or "Adj Close" not in hist:
                print(f"[AVISO] Sem histórico suficiente: {ticker}")
                continue
            hist = hist[~hist.index.duplicated(keep="last")].copy()
            hist.index = pd.to_datetime(hist.index).tz_localize(None)
            brutos[ticker] = hist["Close"].resample("ME").last()
            ajustados[ticker] = hist["Adj Close"].resample("ME").last()
            if "Dividends" in hist:
                dividendos[ticker] = hist["Dividends"].resample("ME").sum()
            else:
                dividendos[ticker] = pd.Series(0.0, index=brutos[ticker].index)
            print(f"[OK] {ticker}: {len(hist)} pregões")
        except Exception as exc:
            print(f"[AVISO] Falha ao baixar {ticker}: {exc}")
    if not ajustados:
        raise RuntimeError("Nenhum ticker retornou dados. Verifique internet, tickers e Yahoo Finance.")
    raw = pd.concat(brutos, axis=1).sort_index()
    adj = pd.concat(ajustados, axis=1).sort_index()
    div = pd.concat(dividendos, axis=1).sort_index().fillna(0.0)
    # Calendário mensal comum. Não preencher preços ausentes para ativos que ainda não existiam.
    raw.index = pd.to_datetime(raw.index).to_period("M").to_timestamp("M")
    adj.index = pd.to_datetime(adj.index).to_period("M").to_timestamp("M")
    div.index = pd.to_datetime(div.index).to_period("M").to_timestamp("M")
    return raw, adj, div


def metricas_historicas(raw: pd.DataFrame, div: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Calcula sinais mensais; o backtest sempre usa o sinal do mês anterior."""
    div = div.reindex(raw.index).fillna(0.0)
    raw = raw.sort_index()
    trailing_div = div.rolling(12, min_periods=12).sum()
    dy = trailing_div / raw.replace(0, np.nan)
    anual = div.resample("YE").sum()
    # Para cada mês, contar quantos dos três anos anteriores tiveram proventos.
    anos_com_div = pd.DataFrame(index=raw.index, columns=raw.columns, dtype=float)
    for ticker in raw.columns:
        serie = div[ticker]
        for dt in raw.index:
            janela = serie.loc[(serie.index < dt) & (serie.index >= dt - pd.DateOffset(months=36))]
            anos = janela.groupby(janela.index.year).sum()
            anos_com_div.loc[dt, ticker] = int((anos > 0).sum())
    # Liquidez é aproximada por existência de preço mensal, não por volume negociado.
    retorno_12m = raw.pct_change(12)
    return {"dy": dy, "anos_div": anos_com_div, "retorno_12m": retorno_12m}


def selecionar(raw: pd.DataFrame, adj: pd.DataFrame, div: pd.DataFrame,
               sinais: dict[str, pd.DataFrame], data_sinal: pd.Timestamp,
               top_n: int) -> pd.DataFrame:
    """Ranking conservador com dados disponíveis até data_sinal."""
    rows = []
    for t in raw.columns:
        if data_sinal not in raw.index:
            continue
        preco = raw.at[data_sinal, t] if t in raw.columns else np.nan
        dy = sinais["dy"].at[data_sinal, t]
        anos = sinais["anos_div"].at[data_sinal, t]
        hist = adj[t].loc[:data_sinal].dropna()
        if pd.isna(preco) or preco <= 0 or pd.isna(dy) or pd.isna(anos):
            continue
        if len(hist) < MESES_HISTORICO:
            continue
        if not (YIELD_MINIMO <= dy <= YIELD_MAXIMO):
            continue
        if anos < MIN_ANOS_COM_DIVIDENDO:
            continue
        # Penaliza tendência negativa de longo prazo; não usa qualquer dado posterior à data do sinal.
        ret12 = sinais["retorno_12m"].at[data_sinal, t]
        if pd.isna(ret12):
            continue
        # Pontuação: dividend yield moderado, histórico de proventos e tendência positiva.
        # Yield tem teto e piso para evitar premiar yield extremo.
        score_dy = 1.0 - abs(float(dy) - 0.06) / 0.12
        score_consistencia = min(float(anos) / 3.0, 1.0)
        score_tendencia = float(np.clip((ret12 + 0.20) / 0.60, 0.0, 1.0))
        score = 0.45 * score_consistencia + 0.35 * score_dy + 0.20 * score_tendencia
        rows.append({
            "ticker": t, "preco_referencia": float(preco),
            "dy_12m_pct": float(dy * 100), "anos_com_div_ult_3": int(anos),
            "retorno_preco_12m_pct": float(ret12 * 100), "score": score,
        })
    if not rows:
        return pd.DataFrame(columns=["ticker", "preco_referencia", "dy_12m_pct",
                                     "anos_com_div_ult_3", "retorno_preco_12m_pct", "score"])
    return pd.DataFrame(rows).sort_values("score", ascending=False).head(top_n).reset_index(drop=True)


def backtest(raw: pd.DataFrame, adj: pd.DataFrame, div: pd.DataFrame,
             aporte: float, top_n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Backtest mensal sem look-ahead, com caixa e custo estimado nas negociações.

    O preço ajustado é usado como índice de retorno total (proventos reinvestidos
    matematicamente). As unidades são sintéticas; não equivalem à quantidade real
    de ações/cotas negociáveis, especialmente para FIIs.
    """
    sinais = metricas_historicas(raw, div)
    datas = adj.index.intersection(raw.index).sort_values()
    if len(datas) < MESES_HISTORICO + 2:
        raise RuntimeError("Histórico comum insuficiente: tente aumentar a lista ou reduzir o período.")
    unidades: dict[str, float] = {}
    caixa = 0.0
    total_aportado = 0.0
    registros, operacoes = [], []

    for i in range(MESES_HISTORICO + 1, len(datas)):
        dt = datas[i]
        sinal_dt = datas[i - 1]  # decisão baseada apenas no último mês fechado
        selecionados = selecionar(raw, adj, div, sinais, sinal_dt, top_n)
        candidatos = selecionados["ticker"].tolist()
        precos_hoje = adj.loc[dt]

        # Liquida posições que deixaram de passar no filtro; desconta custo estimado.
        for t in list(unidades):
            if t not in candidatos:
                p = precos_hoje.get(t, np.nan)
                if pd.notna(p) and p > 0:
                    bruto = unidades[t] * float(p)
                    liquido = bruto * (1 - CUSTO_TRADE)
                    caixa += liquido
                    operacoes.append({"data": dt.date().isoformat(), "ticker": t,
                                      "tipo": "VENDA_FILTRO", "valor_estimado": bruto,
                                      "custo_estimado": bruto - liquido})
                unidades.pop(t, None)

        # O aporte entra mensalmente; caixa de vendas também é reinvestido.
        caixa += aporte
        total_aportado += aporte
        validos = [t for t in candidatos if pd.notna(precos_hoje.get(t, np.nan))
                   and float(precos_hoje.get(t, 0) or 0) > 0]
        if validos and caixa > 0:
            verba_por_ativo = caixa / len(validos)
            for t in validos:
                p = float(precos_hoje[t])
                # O valor alocado inclui o custo de compra.
                q = verba_por_ativo / (p * (1 + CUSTO_TRADE))
                unidades[t] = unidades.get(t, 0.0) + q
                operacoes.append({"data": dt.date().isoformat(), "ticker": t,
                                  "tipo": "COMPRA_APORTE", "valor_estimado": verba_por_ativo,
                                  "custo_estimado": verba_por_ativo - q * p})
            caixa = 0.0

        valor_posicoes = sum(q * float(precos_hoje[t]) for t, q in unidades.items()
                             if t in precos_hoje and pd.notna(precos_hoje[t]))
        valor_fim = valor_posicoes + caixa
        retorno_total = (valor_fim / total_aportado - 1) * 100 if total_aportado else 0.0
        registros.append({
            "data": dt.date().isoformat(), "aporte_mes": aporte,
            "total_aportado": total_aportado, "valor_carteira": valor_fim,
            "valor_posicoes": valor_posicoes, "caixa": caixa,
            "ganho_perda_vs_aportes": valor_fim - total_aportado,
            "retorno_vs_aportes_pct": retorno_total,
            "ativos_na_carteira": len(unidades),
            "selecionados_com_sinal_anterior": ",".join(candidatos),
        })
    return pd.DataFrame(registros), pd.DataFrame(operacoes)


def max_drawdown(curva: pd.Series) -> float:
    if curva.empty:
        return float("nan")
    dd = curva / curva.cummax() - 1
    return float(dd.min() * 100)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scanner conservador de dividendos B3 + backtest.")
    parser.add_argument("--anos", type=int, default=8, help="Anos de histórico para baixar (padrão: 8).")
    parser.add_argument("--aporte", type=float, default=APORTE_PADRAO, help="Aporte mensal em reais (padrão: 200).")
    parser.add_argument("--top", type=int, default=TOP_PADRAO, help="Máximo de ativos por mês (padrão: 5).")
    parser.add_argument("--saida", default="resultado_dividendos", help="Prefixo dos arquivos de saída.")
    args = parser.parse_args()
    if args.anos < 4 or args.aporte <= 0 or args.top < 1:
        raise SystemExit("Use --anos >= 4, --aporte > 0 e --top >= 1.")

    inicio = (pd.Timestamp.today().normalize() - pd.DateOffset(years=args.anos)).strftime("%Y-%m-%d")
    print(f"Baixando histórico desde {inicio}. A lista de ativos está no início do arquivo.")
    raw, adj, div = baixar_dados(UNIVERSO, inicio)
    sinais = metricas_historicas(raw, div)
    datas = adj.index.intersection(raw.index).sort_values()
    data_atual = datas[-1]
    # Scanner atual: ranking com o último mês fechado, não com dado parcial do mês corrente.
    sinal_atual = datas[-2] if len(datas) > 1 else data_atual
    ranking = selecionar(raw, adj, div, sinais, sinal_atual, max(args.top * 3, 10))
    ranking.to_csv(f"{args.saida}_ranking.csv", index=False, encoding="utf-8-sig")

    curva, operacoes = backtest(raw, adj, div, args.aporte, args.top)
    curva.to_csv(f"{args.saida}_backtest.csv", index=False, encoding="utf-8-sig")
    operacoes.to_csv(f"{args.saida}_operacoes.csv", index=False, encoding="utf-8-sig")

    if not curva.empty:
        final = float(curva["valor_carteira"].iloc[-1])
        aportado = float(curva["total_aportado"].iloc[-1])
        print("\n=== RESULTADO DO BACKTEST (simulação, não previsão) ===")
        print(f"Período: {curva['data'].iloc[0]} a {curva['data'].iloc[-1]}")
        print(f"Aportes acumulados: R$ {aportado:,.2f}")
        print(f"Valor final estimado: R$ {final:,.2f}")
        print(f"Diferença vs. aportes: R$ {final-aportado:,.2f}")
        print(f"Retorno vs. soma dos aportes: {(final/aportado-1)*100:.2f}%")
        print(f"Maior drawdown da curva de patrimônio: {max_drawdown(curva['valor_carteira']):.2f}%")
        print("Observação: retorno vs. aportes não é TIR/XIRR e não é retorno anualizado.")
    print("\n=== RANKING ATUAL (sinal do último mês fechado) ===")
    if ranking.empty:
        print("Nenhum ativo passou nos filtros. Não relaxe os filtros sem revisar a qualidade dos dados.")
    else:
        print(ranking.to_string(index=False, formatters={
            "preco_referencia": lambda x: f"R$ {x:,.2f}",
            "dy_12m_pct": lambda x: f"{x:.2f}%",
            "retorno_preco_12m_pct": lambda x: f"{x:.2f}%",
            "score": lambda x: f"{x:.3f}",
        }))
    print(f"\nArquivos gerados: {args.saida}_ranking.csv, {args.saida}_backtest.csv, {args.saida}_operacoes.csv")
    print("O script não envia ordens. Revise os dados e os fundamentos antes de investir.")


if __name__ == "__main__":
    main()
