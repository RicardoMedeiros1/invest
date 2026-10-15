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


def selecionar(raw: pd.DataFrame, adj: pd.DataFrame,
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
        razao = sinais["razao_proventos"].at[data_sinal, t]
        if MIN_RAZAO_PROVENTOS and pd.notna(razao) and razao < MIN_RAZAO_PROVENTOS:
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
             aporte: float, top_n: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Backtest mensal sem look-ahead, com caixa e custo estimado nas negociações.

    O preço ajustado é usado como índice de retorno total (proventos reinvestidos
    matematicamente). As unidades são sintéticas; não equivalem à quantidade real
    de ações/cotas negociáveis, especialmente para FIIs.

    Regras: só os top_n do ranking recebem aportes (respeitando os limites de peso, ver
    alocar_aporte); só se vende um ativo que deixou de passar nos filtros; ativo sem cotação
    no mês é avaliado/liquidado pelo último preço conhecido.
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
        candidatos = elegiveis[:top_n]
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

        valor_posicoes = sum(q * float(ref_hoje[t]) for t, q in unidades.items()
                             if pd.notna(ref_hoje.get(t, np.nan)))
        valor_fim = valor_posicoes + caixa
        retorno_total = (valor_fim / total_aportado - 1) * 100 if total_aportado else 0.0
        registros.append({
            "data": dt.date().isoformat(), "aporte_mes": aporte,
            "total_aportado": total_aportado, "valor_carteira": valor_fim,
            "valor_posicoes": valor_posicoes, "caixa": caixa,
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


def avaliar_benchmarks(curva: pd.DataFrame, inicio: str,
                       usar_benchmarks: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
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
        for nome, pesos in BENCHMARKS_MISTOS.items():
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
    parser.add_argument("--saida", default="resultado_dividendos", help="Prefixo dos arquivos de saída.")
    parser.add_argument("--sem-benchmark", action="store_true",
                        help="Não baixa CDI nem ETFs de referência; calcula só o XIRR da estratégia.")
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
    ranking = selecionar(raw, adj, sinais, sinal_atual, max(args.top * 3, 10))
    ranking.to_csv(f"{args.saida}_ranking.csv", index=False, encoding="utf-8-sig")

    curva, operacoes = backtest(raw, adj, div, args.aporte, args.top)
    resumo = pd.DataFrame()
    if not curva.empty:
        curva, resumo = avaliar_benchmarks(curva, inicio, usar_benchmarks=not args.sem_benchmark)
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
    print(f"\nArquivos gerados: {args.saida}_ranking.csv, {args.saida}_backtest.csv, "
          f"{args.saida}_operacoes.csv, {args.saida}_comparacao.csv")
    print("O script não envia ordens. Revise os dados e os fundamentos antes de investir.")


if __name__ == "__main__":
    main()
