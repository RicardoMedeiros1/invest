# -*- coding: utf-8 -*-
"""Apostas: ações e fundos em que o preço balança muito, mostrados só com o que JÁ aconteceu.

Nada aqui prevê o que vai acontecer. Estes ativos não passam pelo filtro de dividendos do scanner, não entram no
plano de compra e não são recomendação: a lista só existe para quem quer ver, com números, o tamanho do risco
(quanto o preço oscila, a pior queda do último ano e quanto o ativo é negociado por dia).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

PREGOES_ANO = 252
MIN_PREGOES_OSCILACAO = 120      # histórico mínimo para calcular a oscilação
PREGOES_LIQUIDEZ = 60            # a negociação diária é a mediana dos últimos 60 pregões
LIQUIDEZ_BAIXA = 1_000_000.0     # abaixo de R$ 1 milhão por dia, comprar e vender pode custar caro (referência, não regra)

# (ticker no Yahoo, tipo, setor). Ativos que o Yahoo não devolver são ignorados na hora (ver carteira_simples.baixar_apostas).
APOSTAS = [
    ("MGLU3.SA", "ação", "varejo"), ("BHIA3.SA", "ação", "varejo"), ("AZZA3.SA", "ação", "moda"),
    ("CVCB3.SA", "ação", "turismo"), ("PRIO3.SA", "ação", "petróleo"), ("CSAN3.SA", "ação", "combustíveis e logística"),
    ("RAIZ4.SA", "ação", "açúcar e etanol"), ("SUZB3.SA", "ação", "celulose"), ("BRKM5.SA", "ação", "petroquímica"),
    ("USIM5.SA", "ação", "siderurgia"), ("CSNA3.SA", "ação", "siderurgia"), ("CYRE3.SA", "ação", "construção"),
    ("MRVE3.SA", "ação", "construção"), ("COGN3.SA", "ação", "educação"), ("YDUQ3.SA", "ação", "educação"),
    ("HAPV3.SA", "ação", "saúde"), ("IRBR3.SA", "ação", "resseguros"), ("CASH3.SA", "ação", "tecnologia"),
    ("LWSA3.SA", "ação", "tecnologia"), ("EMBR3.SA", "ação", "aviação"), ("RENT3.SA", "ação", "aluguel de carros"),
    ("SLCE3.SA", "ação", "agronegócio"),
    ("HASH11.SA", "ETF", "criptomoedas"), ("BITH11.SA", "ETF", "bitcoin"), ("IVVB11.SA", "ETF", "bolsa dos EUA (S&P 500)"),
    ("NASD11.SA", "ETF", "bolsa dos EUA (tecnologia)"), ("GOLD11.SA", "ETF", "ouro"), ("XINA11.SA", "ETF", "China"),
    ("SMAL11.SA", "ETF", "empresas pequenas"),
    ("BRCR11.SA", "FII", "escritórios"),
]


def _numero(x, casas: int):
    return None if x is None or pd.isna(x) else round(float(x), casas)


def metricas(fechamento: pd.Series, volume: pd.Series | None = None) -> dict:
    """Os números de risco de um ativo, do último ano de pregões.

    oscilacao: desvio-padrão dos retornos diários, anualizado, em % (quanto maior, mais o preço balança);
    queda12: a pior queda do último ano, do topo ao fundo, em % (negativa);
    topo: onde o preço está agora em relação ao maior preço do último ano, em % (0 = no topo);
    liquidez: quanto se negocia por dia, em reais (mediana dos últimos 60 pregões).
    Cada valor é None quando não há histórico suficiente.
    """
    f = fechamento.dropna().sort_index()
    janela = f.tail(PREGOES_ANO)
    vazio = {"oscilacao": None, "queda12": None, "topo": None, "liquidez": None}
    if len(janela) < 2:
        return vazio
    saida = dict(vazio)
    saida["topo"] = _numero((janela.iloc[-1] / janela.max() - 1) * 100, 2)
    saida["queda12"] = _numero((janela / janela.cummax() - 1).min() * 100, 2)
    if len(janela) >= MIN_PREGOES_OSCILACAO and (janela > 0).all():
        saida["oscilacao"] = _numero(np.log(janela).diff().dropna().std() * np.sqrt(PREGOES_ANO) * 100, 1)
    if volume is not None and not volume.dropna().empty:
        financeiro = (f * volume.reindex(f.index)).dropna().tail(PREGOES_LIQUIDEZ)
        if not financeiro.empty:
            saida["liquidez"] = _numero(financeiro.median(), 0)
    return saida


def serie_do_ano(fechamento: pd.Series, passo: int = 4) -> list:
    """O último ano para o gráfico: um ponto a cada `passo` pregões, sempre terminando no preço de agora."""
    f = fechamento.dropna().sort_index().tail(PREGOES_ANO)
    return [_numero(x, 4) for x in f.iloc[::-1].iloc[::passo].iloc[::-1]]
