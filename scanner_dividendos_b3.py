#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scanner conservador de dividendos B3 (ações + FIIs) com backtest mensal.
Dados de preços/dividendos: Yahoo Finance via yfinance. Não envia ordens.
Uso: python scanner_dividendos_b3.py --anos 8 --aporte 200 --top 5
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
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
CLASSE = {**{t: "ACAO" for t in ACOES}, **{t: "FII" for t in FIIS}}

APORTE_PADRAO = 200.0
TOP_PADRAO = 5
FRACAO_FIIS_PADRAO = 0.4   # parte das vagas (--top) reservada a FIIs: 2 de 5; o restante é de ações
CUSTO_TRADE = 0.0015       # estimativa de 0,15% sobre o valor negociado; altere conforme sua corretora
YIELD_MINIMO = 0.03        # 3% trailing 12 meses; filtro, não meta de retorno
YIELD_MAXIMO = 0.18        # acima disso exige cautela: pode ser evento não recorrente ou queda forte
MESES_HISTORICO = 36
MIN_ANOS_COM_DIVIDENDO = 2  # de 3 períodos consecutivos de 12 meses, quantos precisam ter pago proventos
MIN_RAZAO_PROVENTOS = 0.5   # proventos dos últimos 12m / 12m anteriores; abaixo disso = corte forte. 0 desativa
# Limites aplicados nos aportes (não há venda para rebalancear). O que não puder ser alocado fica em caixa.
MAX_PESO_ATIVO = 0.25      # limite de peso por ativo (relaxado para 1/n se houver poucos candidatos)
MAX_PESO_FII = 0.60        # limite agregado para FIIs
MAX_PESO_ACOES = 0.80      # limite agregado para ações
LIMITE_CLASSE = {"FII": MAX_PESO_FII, "ACAO": MAX_PESO_ACOES}

# Benchmarks: os mesmos aportes, nas mesmas datas, aplicados em cada referência.
# ETFs: o preço ajustado do Yahoo aproxima o retorno total do índice (já líquido da taxa do ETF).
# O DIVO11 é um ETF recente: se o histórico não cobrir o período do backtest, é ignorado com aviso.
# O XFIX11 replica o IFIX (índice de FIIs) e é o benchmark de FIIs. Se o Yahoo não tiver o ticker, é
# ignorado com aviso; para usar outra referência de FIIs, troque o ticker aqui.
BENCHMARKS_ETF = {"IBOV (BOVA11)": "BOVA11.SA", "IDIV (DIVO11)": "DIVO11.SA", "IFIX (XFIX11)": "XFIX11.SA"}
# Benchmarks mistos, para uma carteira de ações e FIIs: cada aporte é dividido entre os componentes
# (nomes de BENCHMARKS_ETF) nos pesos indicados, que somam 1. Sem venda para rebalancear, como na
# estratégia, então o peso de cada componente deriva com o tempo. Só entra se todos os componentes existirem.
BENCHMARKS_MISTOS = {"Misto 50/50 (IBOV+IFIX)": {"IBOV (BOVA11)": 0.5, "IFIX (XFIX11)": 0.5}}
# CDI diário (% ao dia): série 12 do SGS do Banco Central.
URL_CDI_BCB = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"


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
        except ImportError as exc:
            # Módulo ausente falharia em todos os tickers (ex.: scipy, usado pelo repair=True).
            raise RuntimeError(f"Falta uma dependência do yfinance ({exc}). "
                               "Instale com: pip install -r requirements.txt") from exc
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
    raw = raw.sort_index()
    div = div.reindex(raw.index).fillna(0.0)
    trailing_div = div.rolling(12, min_periods=12).sum()
    dy = trailing_div / raw.replace(0, np.nan)
    # Três períodos consecutivos de 12 meses terminando no mês do sinal (mesma janela do dy).
    # Usar anos-calendário daria até 4 anos em uma janela de 36 meses.
    pagou = (trailing_div > 0).astype(float).where(trailing_div.notna())
    anos_com_div = pagou + pagou.shift(12) + pagou.shift(24)   # 0 a 3; NaN sem 36 meses de dados
    # Proventos dos últimos 12m frente aos 12m anteriores; NaN quando o período anterior não pagou.
    anterior = trailing_div.shift(12)
    razao_proventos = trailing_div / anterior.where(anterior > 0)
    # Liquidez é aproximada por existência de preço mensal, não por volume negociado.
    retorno_12m = raw.pct_change(12)
    return {"dy": dy, "anos_div": anos_com_div, "retorno_12m": retorno_12m,
            "razao_proventos": razao_proventos}


COLUNAS_RANKING = ["ticker", "preco_referencia", "dy_12m_pct", "anos_com_div_ult_3",
                   "retorno_preco_12m_pct", "score"]


def motivos_exclusao(raw: pd.DataFrame, adj: pd.DataFrame, sinais: dict[str, pd.DataFrame],
                     data_sinal: pd.Timestamp, ticker: str) -> tuple[list[str], dict[str, float]]:
    """Por que o ativo não passa nos filtros em data_sinal, e as métricas calculadas.

    Lista vazia = aprovado. É a única definição dos filtros: o ranking (`selecionar`) e o
    diagnóstico usam esta função, então não podem divergir. Sem dados suficientes, devolve só
    esse motivo; com dados, lista todos os filtros reprovados, não apenas o primeiro.
    """
    preco = raw.at[data_sinal, ticker]
    dy = sinais["dy"].at[data_sinal, ticker]
    anos = sinais["anos_div"].at[data_sinal, ticker]
    razao = sinais["razao_proventos"].at[data_sinal, ticker]
    ret12 = sinais["retorno_12m"].at[data_sinal, ticker]
    meses = len(adj[ticker].loc[:data_sinal].dropna())
    metricas = {"preco": preco, "dy": dy, "anos": anos, "razao": razao, "ret12": ret12, "meses": meses}
    if pd.isna(preco) or preco <= 0:
        return ["sem cotação no mês do sinal"], metricas
    if pd.isna(dy) or pd.isna(anos) or meses < MESES_HISTORICO:
        return [f"histórico menor que {MESES_HISTORICO} meses ({meses} meses de dados)"], metricas
    motivos = []
    if dy < YIELD_MINIMO:
        motivos.append(f"yield de 12m {dy * 100:.2f}% abaixo do mínimo de {YIELD_MINIMO * 100:.1f}%")
    if dy > YIELD_MAXIMO:
        motivos.append(f"yield de 12m {dy * 100:.2f}% acima do máximo de {YIELD_MAXIMO * 100:.1f}%")
    if anos < MIN_ANOS_COM_DIVIDENDO:
        motivos.append(f"proventos em {int(anos)} de 3 períodos de 12m (mínimo {MIN_ANOS_COM_DIVIDENDO})")
    if MIN_RAZAO_PROVENTOS and pd.notna(razao) and razao < MIN_RAZAO_PROVENTOS:
        motivos.append(f"proventos caíram para {razao * 100:.0f}% do período anterior "
                       f"(mínimo {MIN_RAZAO_PROVENTOS * 100:.0f}%)")
    if pd.isna(ret12):
        motivos.append("sem retorno de preço de 12 meses (falta de cotação)")
    return motivos, metricas


def selecionar(raw: pd.DataFrame, adj: pd.DataFrame,
               sinais: dict[str, pd.DataFrame], data_sinal: pd.Timestamp,
               top_n: int) -> pd.DataFrame:
    """Ranking conservador com dados disponíveis até data_sinal."""
    rows = []
    for t in raw.columns:
        if data_sinal not in raw.index:
            continue
        motivos, m = motivos_exclusao(raw, adj, sinais, data_sinal, t)
        if motivos:
            continue
        dy, anos, ret12 = m["dy"], m["anos"], m["ret12"]
        # Pontuação: dividend yield moderado, histórico de proventos e tendência positiva.
        # Yield tem teto e piso para evitar premiar yield extremo. Penaliza tendência negativa de
        # longo prazo; não usa qualquer dado posterior à data do sinal.
        score_dy = 1.0 - abs(float(dy) - 0.06) / 0.12
        score_consistencia = min(float(anos) / 3.0, 1.0)
        score_tendencia = float(np.clip((ret12 + 0.20) / 0.60, 0.0, 1.0))
        score = 0.45 * score_consistencia + 0.35 * score_dy + 0.20 * score_tendencia
        rows.append({
            "ticker": t, "preco_referencia": float(m["preco"]),
            "dy_12m_pct": float(dy * 100), "anos_com_div_ult_3": int(anos),
            "retorno_preco_12m_pct": float(ret12 * 100), "score": score,
        })
    if not rows:
        return pd.DataFrame(columns=COLUNAS_RANKING)
    return pd.DataFrame(rows).sort_values("score", ascending=False).head(top_n).reset_index(drop=True)


def escolher_candidatos(ranking: list[str], top_n: int, n_fiis: int | None) -> list[str]:
    """Quem recebe aporte: os top_n do ranking geral, ou com cota por classe.

    Com n_fiis, reserva essa quantidade de vagas para FIIs e as demais (top_n - n_fiis) para ações,
    cada classe pelo seu melhor ranqueamento. Vaga que uma classe não consegue preencher (poucos
    aprovados) vai para o próximo melhor do ranking geral. Devolve na ordem do ranking.
    """
    if n_fiis is None:
        return ranking[:top_n]
    fiis = [t for t in ranking if CLASSE.get(t, "ACAO") == "FII"]
    acoes = [t for t in ranking if CLASSE.get(t, "ACAO") != "FII"]
    escolhidos = acoes[:top_n - n_fiis] + fiis[:n_fiis]
    sobra = [t for t in ranking if t not in escolhidos]
    escolhidos += sobra[:top_n - len(escolhidos)]
    return sorted(escolhidos, key=ranking.index)


def tabela_ranking(ranking_geral: pd.DataFrame, escolhidos: list[str], n_ranking: int) -> pd.DataFrame:
    """Ranking para exibir: posição, classe e marca dos escolhidos. Mostra os n_ranking primeiros e
    também os escolhidos que ficaram mais abaixo (FIIs, pela cota)."""
    tabela = ranking_geral.assign(
        posicao=range(1, len(ranking_geral) + 1),
        classe=ranking_geral["ticker"].map(lambda t: "FII" if CLASSE.get(t, "ACAO") == "FII" else "ação"),
        escolhido_hoje=ranking_geral["ticker"].map(lambda t: "sim" if t in escolhidos else ""))
    tabela = tabela[(tabela["posicao"] <= n_ranking) | tabela["ticker"].isin(escolhidos)]
    return tabela[["posicao", "ticker", "classe", "escolhido_hoje"] + COLUNAS_RANKING[1:]].reset_index(drop=True)


def misto_por_cota(top_n: int, n_fiis: int) -> dict[str, dict[str, float]]:
    """Benchmark misto com o mesmo peso das vagas: bolsa para as de ações, IFIX para as de FIIs.

    Vazio quando a carteira seria só de um tipo (o misto repetiria o IBOV ou o IFIX).
    """
    if not 0 < n_fiis < top_n:
        return {}
    pesos = {"IBOV (BOVA11)": (top_n - n_fiis) / top_n, "IFIX (XFIX11)": n_fiis / top_n}
    pct_bolsa = round(pesos["IBOV (BOVA11)"] * 100)
    return {f"Misto {pct_bolsa}/{100 - pct_bolsa} (IBOV+IFIX)": pesos}


def formatar_escolhidos(escolhidos: list[str], posicao: dict[str, int], aporte: float,
                        top_n: int, n_fiis: int | None) -> list[str]:
    """Linhas do terminal: quem o scanner escolhe hoje e como o aporte seria dividido."""
    n_acoes = sum(CLASSE.get(t, "ACAO") != "FII" for t in escolhidos)
    n_fii = len(escolhidos) - n_acoes
    regra = (f"{top_n - n_fiis} ações + {n_fiis} FIIs" if n_fiis is not None
             else f"os {top_n} melhores do ranking geral")
    linhas = [f"=== ESCOLHIDOS PARA O PRÓXIMO APORTE ({regra}; resultado dos filtros, não recomendação) ==="]
    divisao = alocar_aporte(aporte, escolhidos, {})
    for t in escolhidos:
        tipo = "FII" if CLASSE.get(t, "ACAO") == "FII" else "ação"
        linhas.append(f"  {t:<10} {tipo:<5} #{posicao[t]} no ranking geral   R$ {divisao.get(t, 0.0):,.2f}")
    sobra = aporte - sum(divisao.values())
    if sobra > 0.005:
        linhas.append(f"  Sobra em caixa pelos limites de peso: R$ {sobra:,.2f}")
    if n_fiis is not None and (n_fii, n_acoes) != (n_fiis, top_n - n_fiis):
        linhas.append(f"  Atenção: só passaram nos filtros {n_acoes} ações e {n_fii} FIIs; as vagas "
                      f"que faltavam foram para o melhor do ranking geral.")
    return linhas


COLUNAS_DIAGNOSTICO = ["ticker", "classe", "situacao", "posicao_ranking", "score", "motivo", "dy_12m_pct",
                       "anos_com_div_ult_3", "proventos_vs_12m_anteriores_pct", "retorno_preco_12m_pct",
                       "meses_de_historico"]


def diagnosticar(raw: pd.DataFrame, adj: pd.DataFrame, sinais: dict[str, pd.DataFrame],
                 data_sinal: pd.Timestamp, universo: list[str]) -> pd.DataFrame:
    """Situação de cada ativo do universo em data_sinal: aprovado (com a posição no ranking) ou
    excluído (com os motivos). Inclui os ativos que nem foram baixados."""
    aprovados = selecionar(raw, adj, sinais, data_sinal, len(raw.columns))
    posicao = {t: i + 1 for i, t in enumerate(aprovados["ticker"])}
    score = dict(zip(aprovados["ticker"], aprovados["score"]))
    linhas = []
    for t in universo:
        base = {"ticker": t, "classe": CLASSE.get(t, "ACAO")}
        if t not in raw.columns:
            linhas.append({**base, "situacao": "EXCLUIDO", "motivo": "sem dados do Yahoo (falha no download)"})
            continue
        motivos, m = motivos_exclusao(raw, adj, sinais, data_sinal, t)
        linhas.append({**base, "situacao": "EXCLUIDO" if motivos else "APROVADO",
                       "posicao_ranking": posicao.get(t), "score": score.get(t), "motivo": "; ".join(motivos),
                       "dy_12m_pct": m["dy"] * 100, "anos_com_div_ult_3": m["anos"],
                       "proventos_vs_12m_anteriores_pct": m["razao"] * 100,
                       "retorno_preco_12m_pct": m["ret12"] * 100, "meses_de_historico": m["meses"]})
    tabela = pd.DataFrame(linhas, columns=COLUNAS_DIAGNOSTICO)
    ordem = tabela.assign(_grupo=tabela["situacao"].map({"APROVADO": 0, "EXCLUIDO": 1}),
                          _pos=tabela["posicao_ranking"].fillna(1e9))
    return tabela.loc[ordem.sort_values(["_grupo", "_pos", "classe", "ticker"]).index].reset_index(drop=True)


def formatar_diagnostico(diagnostico: pd.DataFrame, data_sinal: pd.Timestamp, n_exibidos: int) -> list[str]:
    """Linhas de texto do diagnóstico para o terminal."""
    aprovados = diagnostico[diagnostico["situacao"] == "APROVADO"]
    excluidos = diagnostico[diagnostico["situacao"] == "EXCLUIDO"]
    por_classe = ", ".join(
        f"{nome}: {int((aprovados['classe'] == c).sum())}/{int((diagnostico['classe'] == c).sum())}"
        for c, nome in (("ACAO", "ações"), ("FII", "FIIs")))
    linhas = [f"=== DIAGNÓSTICO DE EXCLUSÃO (sinal de {data_sinal:%Y-%m}) ===",
              f"Aprovados: {len(aprovados)} de {len(diagnostico)} ({por_classe})"]
    if excluidos.empty:
        linhas.append("Nenhum ativo foi excluído.")
    else:
        linhas.append(f"Excluídos ({len(excluidos)}):")
        largura = int(excluidos["ticker"].str.len().max())
        for _, r in excluidos.iterrows():
            classe = "FII" if r["classe"] == "FII" else "ação"
            linhas.append(f"  {r['ticker']:<{largura}}  {classe:<4}  {r['motivo']}")
    fora = aprovados[aprovados["posicao_ranking"] > n_exibidos]
    if not fora.empty:
        lista = ", ".join(f"{r['ticker']} (#{int(r['posicao_ranking'])})" for _, r in fora.iterrows())
        linhas.append(f"Aprovados fora do ranking exibido (abaixo da posição {n_exibidos}): {lista}")
    return linhas


def alocar_aporte(caixa: float, candidatos: list[str],
                  valores_atuais: dict[str, float]) -> dict[str, float]:
    """Divide o caixa entre os candidatos respeitando os limites de peso.

    Parte de uma divisão igual e redistribui o que exceder os limites aos demais.
    Os pesos são medidos sobre o patrimônio após o aporte (posições atuais + caixa).
    Não há venda para rebalancear: um ativo que já passou do limite só deixa de receber
    aportes. O que não couber nos limites volta como sobra e fica em caixa.
    Relaxamentos para não deixar caixa parado sem necessidade: o limite por ativo vira
    1/n com menos de 1/MAX_PESO_ATIVO candidatos, e o limite de uma classe só vale se
    houver candidatos de outra classe para receber o excedente.
    """
    if not candidatos or caixa <= 0:
        return {}
    eps = 1e-9
    total = sum(valores_atuais.values()) + caixa
    teto_ativo = max(MAX_PESO_ATIVO, 1 / len(candidatos)) * total
    classes = {CLASSE.get(t, "ACAO") for t in candidatos}
    teto_classe = {c: LIMITE_CLASSE[c] * total for c in classes} if len(classes) > 1 else {}
    atual_classe = {c: sum(v for t, v in valores_atuais.items() if CLASSE.get(t, "ACAO") == c)
                    for c in teto_classe}

    alocado = {t: 0.0 for t in candidatos}
    abertos = list(candidatos)
    restante = caixa
    while restante > eps and abertos:
        parcela = restante / len(abertos)
        pedido = {t: min(parcela, teto_ativo - valores_atuais.get(t, 0.0) - alocado[t])
                  for t in abertos}
        pedido = {t: max(v, 0.0) for t, v in pedido.items()}
        for c, teto in teto_classe.items():
            membros = [t for t in abertos if CLASSE.get(t, "ACAO") == c]
            soma = sum(pedido[t] for t in membros)
            livre = max(teto - atual_classe[c] - sum(alocado[t] for t in candidatos
                                                      if CLASSE.get(t, "ACAO") == c), 0.0)
            if soma > livre:
                for t in membros:
                    pedido[t] *= livre / soma
        gasto = sum(pedido.values())
        if gasto <= eps:
            break
        for t, v in pedido.items():
            alocado[t] += v
        restante -= gasto
        abertos = [t for t in abertos if pedido[t] >= parcela - eps]
    return {t: v for t, v in alocado.items() if v > eps}


def backtest(raw: pd.DataFrame, adj: pd.DataFrame, div: pd.DataFrame,
             aporte: float, top_n: int, cota_fiis: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Backtest mensal sem look-ahead, com caixa e custo estimado nas negociações.

    O preço ajustado é usado como índice de retorno total (proventos reinvestidos
    matematicamente). As unidades são sintéticas; não equivalem à quantidade real
    de ações/cotas negociáveis, especialmente para FIIs.

    Regras: recebem aportes os top_n do ranking geral ou, com cota_fiis, as vagas por classe
    (top_n - cota_fiis ações e cota_fiis FIIs; ver escolher_candidatos), respeitando os limites de
    peso (ver alocar_aporte); só se vende um ativo que deixou de passar nos filtros; ativo sem
    cotação no mês é avaliado/liquidado pelo último preço conhecido.
    """
    sinais = metricas_historicas(raw, div)
    datas = adj.index.intersection(raw.index).sort_values()
    if len(datas) < MESES_HISTORICO + 2:
        raise RuntimeError("Histórico comum insuficiente: tente aumentar a lista ou reduzir o período.")
    unidades: dict[str, float] = {}
    caixa = 0.0
    total_aportado = 0.0
    registros, operacoes = [], []

    # Último preço conhecido: avalia e liquida ativos sem cotação em determinado mês.
    precos_ref = adj.ffill()

    for i in range(MESES_HISTORICO + 1, len(datas)):
        dt = datas[i]
        sinal_dt = datas[i - 1]  # decisão baseada apenas no último mês fechado
        # Ranking completo dos ativos que passam nos filtros; só os top_n recebem aporte.
        elegiveis = selecionar(raw, adj, sinais, sinal_dt, len(raw.columns))["ticker"].tolist()
        candidatos = escolher_candidatos(elegiveis, top_n, cota_fiis)
        precos_hoje = adj.loc[dt]
        ref_hoje = precos_ref.loc[dt]

        # Liquida só as posições que deixaram de passar nos filtros. Quem continua elegível
        # mas saiu do top_n é mantido (apenas deixa de receber aportes). Desconta custo estimado.
        for t in list(unidades):
            if t in elegiveis:
                continue
            p = ref_hoje.get(t, np.nan)
            if pd.isna(p) or p <= 0:
                continue  # sem nenhum preço de referência: mantém a posição em vez de descartá-la
            sem_cotacao = pd.isna(precos_hoje.get(t, np.nan))
            bruto = unidades.pop(t) * float(p)
            liquido = bruto * (1 - CUSTO_TRADE)
            caixa += liquido
            operacoes.append({"data": dt.date().isoformat(), "ticker": t,
                              "tipo": "VENDA_FILTRO", "valor_estimado": bruto,
                              "custo_estimado": bruto - liquido,
                              "observacao": "sem cotação no mês; usado o último preço disponível"
                              if sem_cotacao else ""})

        # O aporte entra mensalmente; caixa de vendas também é reinvestido, dentro dos limites de peso.
        caixa += aporte
        total_aportado += aporte
        validos = [t for t in candidatos if pd.notna(precos_hoje.get(t, np.nan))
                   and float(precos_hoje.get(t, 0) or 0) > 0]
        valores_atuais = {t: q * float(ref_hoje[t]) for t, q in unidades.items()
                          if pd.notna(ref_hoje.get(t, np.nan))}
        for t, verba in alocar_aporte(caixa, validos, valores_atuais).items():
            p = float(precos_hoje[t])
            # O valor alocado inclui o custo de compra.
            q = verba / (p * (1 + CUSTO_TRADE))
            unidades[t] = unidades.get(t, 0.0) + q
            caixa -= verba
            operacoes.append({"data": dt.date().isoformat(), "ticker": t,
                              "tipo": "COMPRA_APORTE", "valor_estimado": verba,
                              "custo_estimado": verba - q * p, "observacao": ""})
        caixa = caixa if caixa > 1e-9 else 0.0  # descarta resíduo de ponto flutuante

        valor_por_classe = {"ACAO": 0.0, "FII": 0.0}
        for t, q in unidades.items():
            if pd.notna(ref_hoje.get(t, np.nan)):
                valor_por_classe[CLASSE.get(t, "ACAO")] += q * float(ref_hoje[t])
        valor_posicoes = sum(valor_por_classe.values())
        valor_fim = valor_posicoes + caixa
        retorno_total = (valor_fim / total_aportado - 1) * 100 if total_aportado else 0.0
        registros.append({
            "data": dt.date().isoformat(), "aporte_mes": aporte,
            "total_aportado": total_aportado, "valor_carteira": valor_fim,
            "valor_posicoes": valor_posicoes, "caixa": caixa,
            "valor_posicoes_acoes": valor_por_classe["ACAO"], "valor_posicoes_fiis": valor_por_classe["FII"],
            "ganho_perda_vs_aportes": valor_fim - total_aportado,
            "retorno_vs_aportes_pct": retorno_total,
            "ativos_na_carteira": len(unidades),
            "qtd_elegiveis_com_sinal_anterior": len(elegiveis),
            "selecionados_com_sinal_anterior": ",".join(candidatos),
        })
    return pd.DataFrame(registros), pd.DataFrame(operacoes)


def max_drawdown(curva: pd.Series) -> float:
    if curva.empty:
        return float("nan")
    dd = curva / curva.cummax() - 1
    return float(dd.min() * 100)


def indice_retorno(valores: pd.Series, aportes: pd.Series) -> pd.Series:
    """Índice de retorno ponderado pelo tempo (base 1,0), sem o efeito dos aportes.

    O aporte do mês entra no fim do mês, então o retorno do mês é (valor - aporte) / valor do
    mês anterior - 1. Custos de negociação ficam no retorno. O drawdown do patrimônio não serve
    para medir risco: os aportes o empurram para cima e escondem parte da queda.
    """
    anterior = valores.shift(1)
    retorno = ((valores - aportes) / anterior - 1).where(anterior > 0, 0.0)
    return (1 + retorno).cumprod()


def composicao(curva: pd.DataFrame) -> dict[str, float] | None:
    """Proporção de ações e FIIs entre as posições (sem o caixa), em %, ao final e ao longo do período.

    Devolve None se a carteira nunca teve posições. A média é a média dos pesos mensais.
    """
    posicoes = curva["valor_posicoes_acoes"] + curva["valor_posicoes_fiis"]
    peso_fiis = (curva["valor_posicoes_fiis"] / posicoes).where(posicoes > 0).dropna() * 100
    if peso_fiis.empty:
        return None
    final = float(peso_fiis.iloc[-1])
    return {"fiis_final_pct": final, "acoes_final_pct": 100 - final,
            "fiis_media_pct": float(peso_fiis.mean()), "acoes_media_pct": 100 - float(peso_fiis.mean()),
            "fiis_min_pct": float(peso_fiis.min()), "fiis_max_pct": float(peso_fiis.max()),
            "caixa_final_pct": float(curva["caixa"].iloc[-1] / curva["valor_carteira"].iloc[-1] * 100)}


def xirr(fluxos: list[tuple[pd.Timestamp, float]]) -> float:
    """Taxa interna de retorno anual de fluxos datados (base de 365 dias, como no Excel).

    Aportes entram com sinal negativo e o valor final com sinal positivo. Devolve NaN se não
    houver fluxo positivo e negativo ou se a taxa estiver fora de -99,99% a 1.000% ao ano.
    """
    datas = pd.to_datetime([d for d, _ in fluxos])
    valores = np.array([v for _, v in fluxos], dtype=float)
    if not ((valores > 0).any() and (valores < 0).any()):
        return float("nan")
    anos = np.array((datas - datas.min()).days, dtype=float) / 365.0

    def vpl(taxa: float) -> float:
        return float(np.sum(valores / (1.0 + taxa) ** anos))

    # Com aportes antes do valor final, o VPL é decrescente na taxa: bisseção é suficiente.
    baixo, alto = -0.9999, 10.0
    v_baixo, v_alto = vpl(baixo), vpl(alto)
    if v_baixo * v_alto > 0:
        return float("nan")
    for _ in range(200):
        meio = (baixo + alto) / 2
        v_meio = vpl(meio)
        if v_baixo * v_meio <= 0:
            alto, v_alto = meio, v_meio
        else:
            baixo, v_baixo = meio, v_meio
        if alto - baixo < 1e-12:
            break
    return (baixo + alto) / 2


def baixar_cdi(inicio: pd.Timestamp, fim: pd.Timestamp,
               abrir=urllib.request.urlopen) -> pd.Series:
    """Índice acumulado do CDI (dia útil a dia útil) pela série 12 do SGS/Banco Central.

    A consulta é dividida em janelas de até 5 anos, porque a API limita o intervalo das
    séries diárias. `abrir` existe para os testes poderem simular a resposta.
    """
    registros: list[dict] = []
    ini = pd.Timestamp(inicio)
    while ini <= fim:
        fim_janela = min(ini + pd.DateOffset(years=5) - pd.Timedelta(days=1), fim)
        url = f"{URL_CDI_BCB}?formato=json&dataInicial={ini:%d/%m/%Y}&dataFinal={fim_janela:%d/%m/%Y}"
        pedido = urllib.request.Request(url, headers={"User-Agent": "scanner-dividendos-b3"})
        with abrir(pedido, timeout=30) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
        if not isinstance(dados, list):
            raise RuntimeError(f"Resposta inesperada do Banco Central: {str(dados)[:120]}")
        registros.extend(dados)
        ini = fim_janela + pd.Timedelta(days=1)
    if not registros:
        raise RuntimeError("O Banco Central não retornou dados do CDI para o período.")
    tabela = pd.DataFrame(registros)
    taxa = pd.Series(tabela["valor"].astype(float).to_numpy(),
                     index=pd.to_datetime(tabela["data"], format="%d/%m/%Y"))
    taxa = taxa[~taxa.index.duplicated()].sort_index()
    return (1 + taxa / 100).cumprod()


def simular_aportes(indice: pd.Series, aportes: pd.Series, custo: float = 0.0) -> pd.Series | None:
    """Patrimônio de quem aplicou os mesmos aportes, nas mesmas datas, em um índice de retorno total.

    Devolve None se o índice não cobrir todas as datas dos aportes.
    """
    nivel = indice.reindex(aportes.index)
    if nivel.isna().any() or (nivel <= 0).any():
        return None
    return (aportes / (1 + custo) / nivel).cumsum() * nivel


def resumir(curva: pd.DataFrame, series: dict[str, pd.Series],
            datas_fluxo: pd.DatetimeIndex) -> pd.DataFrame:
    """Valor final, ganho, XIRR e drawdown da estratégia e de cada benchmark, com os mesmos fluxos de caixa."""
    aportes = curva["aporte_mes"].to_numpy(dtype=float)

    def linha(nome: str, valores: pd.Series) -> dict:
        final = float(valores.iloc[-1])
        fluxos = list(zip(datas_fluxo, -aportes)) + [(datas_fluxo[-1], final)]
        patrimonio = pd.Series(valores.to_numpy(dtype=float), index=curva.index)
        queda = max_drawdown(indice_retorno(patrimonio, curva["aporte_mes"]))
        return {"nome": nome, "total_aportado": float(aportes.sum()), "valor_final": final,
                "ganho": final - float(aportes.sum()), "xirr_aa_pct": xirr(fluxos) * 100,
                "max_drawdown_pct": queda}

    resumo = pd.DataFrame([linha("Estratégia", curva["valor_carteira"])]
                          + [linha(nome, s) for nome, s in series.items()])
    # Positivo = a estratégia rendeu mais que a referência. Vazio na própria linha da estratégia.
    resumo["diferenca_xirr_pp"] = resumo.loc[0, "xirr_aa_pct"] - resumo["xirr_aa_pct"]
    resumo.loc[0, "diferenca_xirr_pp"] = np.nan
    return resumo


def avaliar_benchmarks(curva: pd.DataFrame, inicio: str, usar_benchmarks: bool = True,
                       mistos: dict[str, dict[str, float]] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Acrescenta à curva o patrimônio de cada benchmark e devolve também o resumo com XIRR.

    Falhas de download (CDI ou ETFs) geram aviso e o benchmark é ignorado; o XIRR da
    estratégia sai sempre.
    """
    datas = pd.DatetimeIndex(pd.to_datetime(curva["data"]))
    # O último ponto pode ser um mês ainda em andamento (rótulo = fim do mês): nos fluxos
    # datados, usa-se hoje no lugar de uma data futura.
    hoje = pd.Timestamp.today().normalize()
    datas_fluxo = datas if datas[-1] <= hoje else datas[:-1].append(pd.DatetimeIndex([hoje]))
    aportes = pd.Series(curva["aporte_mes"].to_numpy(dtype=float), index=datas)
    series: dict[str, pd.Series] = {}

    if usar_benchmarks:
        try:
            cdi = baixar_cdi(datas_fluxo[0] - pd.Timedelta(days=10), datas_fluxo[-1])
            nivel = pd.Series([cdi.asof(d) for d in datas_fluxo], index=datas)
            valores = simular_aportes(nivel, aportes)   # CDI: sem custo de negociação
            if valores is None:
                print("[AVISO] CDI: dados não cobrem todo o período do backtest; ignorado.")
            else:
                series["CDI"] = valores
        except Exception as exc:
            print(f"[AVISO] Não foi possível baixar o CDI do Banco Central ({exc}); ignorado.")
        try:
            _, adj_etf, _ = baixar_dados(list(BENCHMARKS_ETF.values()), inicio)
        except Exception as exc:
            print(f"[AVISO] Não foi possível baixar os ETFs de referência ({exc}); ignorados.")
        else:
            for nome, ticker in BENCHMARKS_ETF.items():
                if ticker not in adj_etf.columns:
                    continue
                nivel = adj_etf[ticker]
                valores = simular_aportes(nivel, aportes, CUSTO_TRADE)   # ETF: mesmo custo da estratégia
                if valores is None:
                    primeiro = nivel.first_valid_index()
                    desde = f" (dados desde {primeiro:%Y-%m})" if primeiro is not None else ""
                    print(f"[AVISO] {nome}: histórico não cobre todo o período do backtest{desde}; ignorado.")
                else:
                    series[nome] = valores
        for nome, pesos in (BENCHMARKS_MISTOS if mistos is None else mistos).items():
            if not np.isclose(sum(pesos.values()), 1.0):
                raise ValueError(f"Os pesos de '{nome}' em BENCHMARKS_MISTOS devem somar 1.")
            faltam = [c for c in pesos if c not in series]
            if faltam:
                print(f"[AVISO] {nome}: sem {', '.join(faltam)}; ignorado.")
            else:
                # O patrimônio é linear nos aportes: aplicar peso*aporte em cada componente
                # equivale a ponderar os patrimônios dos componentes.
                series[nome] = sum(peso * series[c] for c, peso in pesos.items())

    curva = curva.copy()
    for nome, valores in series.items():
        sufixo = re.sub(r"\W+", "_", nome.lower()).strip("_")   # "IBOV (BOVA11)" -> "ibov_bova11"
        curva[f"valor_{sufixo}"] = valores.to_numpy()
    return curva, resumir(curva, series, datas_fluxo)


def main() -> None:
    parser = argparse.ArgumentParser(description="Scanner conservador de dividendos B3 + backtest.")
    parser.add_argument("--anos", type=int, default=8, help="Anos de histórico para baixar (padrão: 8).")
    parser.add_argument("--aporte", type=float, default=APORTE_PADRAO, help="Aporte mensal em reais (padrão: 200).")
    parser.add_argument("--top", type=int, default=TOP_PADRAO, help="Máximo de ativos por mês (padrão: 5).")
    parser.add_argument("--fiis", type=int, default=None,
                        help="Vagas reservadas a FIIs entre os --top; as demais são de ações "
                             "(padrão: 40%% das vagas, 2 de 5).")
    parser.add_argument("--sem-cota", action="store_true",
                        help="Escolhe os --top melhores do ranking geral, sem reservar vagas por classe.")
    parser.add_argument("--saida", default="resultado_dividendos", help="Prefixo dos arquivos de saída.")
    parser.add_argument("--sem-benchmark", action="store_true",
                        help="Não baixa CDI nem ETFs de referência; calcula só o XIRR da estratégia.")
    args = parser.parse_args()
    if args.anos < 4 or args.aporte <= 0 or args.top < 1:
        raise SystemExit("Use --anos >= 4, --aporte > 0 e --top >= 1.")
    n_fiis = None if args.sem_cota else (
        args.fiis if args.fiis is not None else int(args.top * FRACAO_FIIS_PADRAO + 0.5))
    if n_fiis is not None and not 0 <= n_fiis <= args.top:
        raise SystemExit("Use --fiis entre 0 e o valor de --top.")

    inicio = (pd.Timestamp.today().normalize() - pd.DateOffset(years=args.anos)).strftime("%Y-%m-%d")
    print(f"Baixando histórico desde {inicio}. A lista de ativos está no início do arquivo.")
    raw, adj, div = baixar_dados(UNIVERSO, inicio)
    sinais = metricas_historicas(raw, div)
    datas = adj.index.intersection(raw.index).sort_values()
    data_atual = datas[-1]
    # Scanner atual: ranking com o último mês fechado, não com dado parcial do mês corrente.
    sinal_atual = datas[-2] if len(datas) > 1 else data_atual
    n_ranking = max(args.top * 3, 10)
    ranking_geral = selecionar(raw, adj, sinais, sinal_atual, len(raw.columns))
    ordem = ranking_geral["ticker"].tolist()
    escolhidos = escolher_candidatos(ordem, args.top, n_fiis)
    posicao = {t: i + 1 for i, t in enumerate(ordem)}
    ranking = tabela_ranking(ranking_geral, escolhidos, n_ranking)
    ranking.to_csv(f"{args.saida}_ranking.csv", index=False, encoding="utf-8-sig")
    diagnostico = diagnosticar(raw, adj, sinais, sinal_atual, UNIVERSO)
    diagnostico.to_csv(f"{args.saida}_diagnostico.csv", index=False, encoding="utf-8-sig")

    curva, operacoes = backtest(raw, adj, div, args.aporte, args.top, cota_fiis=n_fiis)
    # Com cota por classe, o benchmark misto tem o mesmo peso das vagas (ex.: 3 de 5 = 60% bolsa).
    mistos = None if n_fiis is None else misto_por_cota(args.top, n_fiis)
    resumo = pd.DataFrame()
    if not curva.empty:
        curva, resumo = avaliar_benchmarks(curva, inicio, usar_benchmarks=not args.sem_benchmark, mistos=mistos)
    curva.to_csv(f"{args.saida}_backtest.csv", index=False, encoding="utf-8-sig")
    operacoes.to_csv(f"{args.saida}_operacoes.csv", index=False, encoding="utf-8-sig")
    resumo.to_csv(f"{args.saida}_comparacao.csv", index=False, encoding="utf-8-sig")

    if not curva.empty:
        final = float(curva["valor_carteira"].iloc[-1])
        aportado = float(curva["total_aportado"].iloc[-1])
        print("\n=== RESULTADO DO BACKTEST (simulação, não previsão) ===")
        print(f"Período: {curva['data'].iloc[0]} a {curva['data'].iloc[-1]}")
        print(f"Aportes acumulados: R$ {aportado:,.2f}")
        print(f"Valor final estimado: R$ {final:,.2f}")
        print(f"Diferença vs. aportes: R$ {final-aportado:,.2f}")
        print(f"Retorno vs. soma dos aportes: {(final/aportado-1)*100:.2f}%")
        print(f"Maior drawdown (índice de retorno, sem o efeito dos aportes): "
              f"{resumo.loc[0, 'max_drawdown_pct']:.2f}%")
        print(f"XIRR da estratégia: {resumo.loc[0, 'xirr_aa_pct']:.2f}% a.a. (base de 365 dias, sem IR)")
        print("Observação: retorno vs. soma dos aportes não é anualizado; compare estratégias pelo XIRR.")
        print("\n=== COMPARAÇÃO COM BENCHMARKS (mesmos aportes, mesmas datas, sem IR) ===")
        tabela = resumo.copy()   # texto formatado; to_string não aplica formatadores a valores ausentes
        for coluna in ("total_aportado", "valor_final", "ganho"):
            tabela[coluna] = tabela[coluna].map(lambda x: f"R$ {x:,.2f}")
        tabela["xirr_aa_pct"] = tabela["xirr_aa_pct"].map(lambda x: "n/d" if pd.isna(x) else f"{x:.2f}%")
        tabela["max_drawdown_pct"] = tabela["max_drawdown_pct"].map(lambda x: f"{x:.2f}%")
        tabela["diferenca_xirr_pp"] = tabela["diferenca_xirr_pp"].map(
            lambda x: "" if pd.isna(x) else f"{x:+.2f} p.p.")
        print(tabela.to_string(index=False))
        print("diferenca_xirr_pp: XIRR da estratégia menos o do benchmark (positivo = estratégia rendeu mais).")
        mix = composicao(curva)
        if mix:
            print("\n=== COMPOSIÇÃO DA CARTEIRA (ações x FIIs, entre as posições) ===")
            print(f"Ao final:        ações {mix['acoes_final_pct']:.1f}% | FIIs {mix['fiis_final_pct']:.1f}%"
                  f"  (caixa: {mix['caixa_final_pct']:.1f}% do patrimônio)")
            print(f"Média mensal:    ações {mix['acoes_media_pct']:.1f}% | FIIs {mix['fiis_media_pct']:.1f}%")
            print(f"Peso de FIIs no período: de {mix['fiis_min_pct']:.1f}% a {mix['fiis_max_pct']:.1f}%")
            usados = list(BENCHMARKS_MISTOS if mistos is None else mistos)
            alvo = ", ".join(usados) if usados else "o IBOV e o IFIX separadamente"
            print(f"Compare com {alvo}; se a proporção for bem diferente, o misto é uma referência fraca.")
    if escolhidos:
        print()
        print("\n".join(formatar_escolhidos(escolhidos, posicao, args.aporte, args.top, n_fiis)))
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
    print()
    print("\n".join(formatar_diagnostico(diagnostico, sinal_atual, n_ranking)))
    print(f"\nArquivos gerados: {args.saida}_ranking.csv, {args.saida}_backtest.csv, "
          f"{args.saida}_operacoes.csv, {args.saida}_comparacao.csv, {args.saida}_diagnostico.csv")
    print("O script não envia ordens. Revise os dados e os fundamentos antes de investir.")


if __name__ == "__main__":
    main()
