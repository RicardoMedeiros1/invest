#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Painel simples da carteira, em linguagem direta:
  - as melhores agora (ações e FIIs), com preço, dividendos e quantas unidades cabem no seu aporte;
  - quanto cada uma subiu no dia, no mês e no ano;
  - um plano de compra mês a mês, só com unidades inteiras;
  - quais eram as melhores no passado, por mês e por ano;
  - os custos das corretoras (pesquisados em fontes públicas, ver corretoras.py).
Usa o mesmo filtro do scanner (scanner_dividendos_b3.py). Atualiza a planilha carteira.xlsx (sempre o mesmo
arquivo) e o relatório carteira.html. Não envia ordens.
Uso: python carteira_simples.py --valor 200
"""

from __future__ import annotations

import argparse
import webbrowser
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

import scanner_dividendos_b3 as sc
import apostas as ap
import painel_pagina
from corretoras import AVISO as AVISO_CORRETORAS, CORRETORAS, PESQUISADO_EM
from planilha import atualizar_planilha, caminho_da_planilha

DIAS_DIARIOS = 430   # histórico diário baixado: 12 meses com folga
N_ACOES = 5          # quantas ações aparecem em "as melhores agora"
N_FIIS = 3           # quantos FIIs aparecem em "as melhores agora"
N_PERIODO = 5        # quantas aparecem em "quanto cada um subiu"
SERIE_PREGOES = 66   # pregões (cerca de 3 meses) no gráfico de cada ativo
INTERVALO_PADRAO_MIN = 15   # de quanto em quanto tempo o servidor atualiza os preços durante o pregão
BRASILIA = timezone(timedelta(hours=-3))
INDICES = [("^BVSP", "Ibovespa", "pts"), ("BRL=X", "Dólar", "R$")]   # (ticker no Yahoo, nome, unidade)
COLUNAS_TABELA = ["ticker", "classe", "preco", "dia_pct", "mes_pct", "ano_pct", "dividendo_12m", "dy_12m_pct",
                  "aprovado", "posicao", "nota", "cotas", "custo"]
PERIODOS = [("dia", "dia_pct", "No último pregão"), ("mes", "mes_pct", "No último mês"), ("ano", "ano_pct", "Em 12 meses")]
AVISO_FINAL = ("O painel usa o mesmo filtro do scanner (dividendos e histórico). Não é recomendação de compra ou de venda: "
               "confira o preço no aplicativo e os fundamentos de cada empresa ou fundo antes de investir. "
               "Os dividendos e as altas mostrados são o que já aconteceu, não uma promessa do que vai acontecer.")


def nome(ticker: str) -> str:
    """PETR4.SA -> PETR4 (o sufixo .SA é do Yahoo, não do dia a dia)."""
    return ticker[:-3] if ticker.endswith(".SA") else ticker


def brl(x: float) -> str:
    """R$ 1.234,56"""
    return "R$ " + f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(x: float, sinal: bool = False) -> str:
    """7,5% (ou +7,5% com sinal); n/d quando não há valor."""
    if x is None or pd.isna(x):
        return "n/d"
    return (f"{x:+.1f}" if sinal else f"{x:.1f}").replace(".", ",") + "%"


def baixar_diario(tickers: list[str], dias: int = DIAS_DIARIOS,
                  volumes: dict[str, pd.Series] | None = None) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """Fechamentos e dividendos diários dos últimos `dias` dias corridos. Ativo sem dados é ignorado.

    Se `volumes` for um dicionário, ele é preenchido com o volume negociado (em unidades) de cada ativo."""
    inicio = (pd.Timestamp.today().normalize() - pd.Timedelta(days=dias)).strftime("%Y-%m-%d")
    fechamentos, proventos = {}, {}
    for t in tickers:
        try:
            hist = yf.Ticker(t).history(start=inicio, auto_adjust=False, actions=True, repair=False)
            if hist.empty or "Close" not in hist:
                print(f"[AVISO] Sem preços diários: {t}")
                continue
            hist = hist[~hist.index.duplicated(keep="last")].copy()
            hist.index = pd.to_datetime(hist.index).tz_localize(None)
            fechamentos[t] = hist["Close"].dropna().sort_index()
            proventos[t] = hist["Dividends"] if "Dividends" in hist else pd.Series(0.0, index=hist.index)
            if volumes is not None and "Volume" in hist:
                volumes[t] = hist["Volume"]
        except ImportError as exc:
            raise RuntimeError(f"Falta uma dependência do yfinance ({exc}). "
                               "Instale com: pip install -r requirements.txt") from exc
        except Exception as exc:
            print(f"[AVISO] Falha ao baixar preços diários de {t}: {exc}")
    if not fechamentos:
        raise RuntimeError("Nenhum preço diário foi baixado. Verifique a internet e o Yahoo Finance.")
    return fechamentos, proventos


def variacoes(fechamento: pd.Series) -> dict[str, float]:
    """Variação do preço, em %: no último pregão, em 1 mês e em 12 meses. NaN sem histórico suficiente."""
    f = fechamento.dropna().sort_index()
    vazio = {"dia": np.nan, "mes": np.nan, "ano": np.nan}
    if len(f) < 2:
        return vazio
    ultimo = float(f.iloc[-1])

    def contra(data_ref: pd.Timestamp) -> float:
        anterior = f.loc[:data_ref]
        return (ultimo / float(anterior.iloc[-1]) - 1) * 100 if not anterior.empty else np.nan

    return {"dia": (ultimo / float(f.iloc[-2]) - 1) * 100,
            "mes": contra(f.index[-1] - pd.DateOffset(months=1)),
            "ano": contra(f.index[-1] - pd.DateOffset(months=12))}


def dividendos_12m(proventos: pd.Series, ate: pd.Timestamp) -> float:
    """Soma dos dividendos por unidade pagos nos 12 meses até `ate`."""
    if proventos.empty:
        return 0.0
    janela = proventos.loc[(proventos.index > ate - pd.DateOffset(months=12)) & (proventos.index <= ate)]
    return float(janela.sum())


def montar_tabela(fechamentos: dict[str, pd.Series], proventos: dict[str, pd.Series],
                  ranking_geral: pd.DataFrame, valor: float) -> pd.DataFrame:
    """Uma linha por ativo: preço, variações, dividendos, se passou nos filtros e quantas unidades cabem em `valor`."""
    posicao = {t: i + 1 for i, t in enumerate(ranking_geral["ticker"])}
    nota = dict(zip(ranking_geral["ticker"], ranking_geral["score"]))
    linhas = []
    for t, f in fechamentos.items():
        preco = float(f.iloc[-1])
        v = variacoes(f)
        div = dividendos_12m(proventos.get(t, pd.Series(dtype=float)), f.index[-1])
        cotas = int(valor / preco + 1e-9) if preco > 0 else 0
        linhas.append({"ticker": t, "classe": "FII" if sc.CLASSE.get(t, "ACAO") == "FII" else "ação", "preco": preco,
                       "dia_pct": v["dia"], "mes_pct": v["mes"], "ano_pct": v["ano"],
                       "dividendo_12m": div, "dy_12m_pct": div / preco * 100 if preco > 0 else np.nan,
                       "aprovado": t in posicao, "posicao": posicao.get(t), "nota": nota.get(t),
                       "cotas": cotas, "custo": cotas * preco})
    return pd.DataFrame(linhas, columns=COLUNAS_TABELA)


def melhores_por_classe(tabela: pd.DataFrame, classe: str, n: int) -> pd.DataFrame:
    """Os n aprovados de uma classe com a melhor posição no ranking do scanner."""
    sel = tabela[tabela["aprovado"] & (tabela["classe"] == classe)].sort_values("posicao")
    return sel.head(n).reset_index(drop=True)


def melhores_por_periodo(tabela: pd.DataFrame, coluna: str, n: int = N_PERIODO) -> pd.DataFrame:
    """Entre os aprovados, os n que mais subiram no período (coluna dia_pct, mes_pct ou ano_pct)."""
    sel = tabela[tabela["aprovado"]].dropna(subset=[coluna]).sort_values(coluna, ascending=False)
    return sel.head(n).reset_index(drop=True)


def plano_de_compras(precos: dict[str, float], ordem: list[str], valor: float, meses: int = 3) -> list[dict]:
    """Compras mês a mês, só com unidades inteiras.

    Equilibra o dinheiro entre os ativos: compra sempre o que tem menos dinheiro aplicado. Se esse ativo
    ainda não cabe no caixa (um FII de R$ 150 com aporte de R$ 200, por exemplo), guarda o caixa para o mês
    seguinte em vez de gastar com os outros. Empates seguem a ordem do ranking (`ordem`).
    """
    if not ordem:
        return []
    investido = {t: 0.0 for t in ordem}
    caixa, plano = 0.0, []
    for mes in range(1, meses + 1):
        caixa += valor
        compras: dict[str, int] = {}
        gasto = 0.0
        while True:
            alvo = min(ordem, key=lambda t: (investido[t], ordem.index(t)))
            if precos[alvo] > caixa + 1e-9:
                break
            caixa -= precos[alvo]
            investido[alvo] += precos[alvo]
            gasto += precos[alvo]
            compras[alvo] = compras.get(alvo, 0) + 1
        plano.append({"mes": mes, "compras": compras, "gasto": gasto, "sobra": caixa})
    return plano


def estimativa_dividendos(dys: list[float], total: float) -> float:
    """Dividendos de um ano sobre `total` investido, se repetirem o rendimento médio (em %) do último ano."""
    dys = [d for d in dys if not pd.isna(d)]
    return float(np.mean(dys)) / 100 * total if dys else 0.0


def escolhidas_por_mes(raw: pd.DataFrame, adj: pd.DataFrame, sinais: dict[str, pd.DataFrame],
                       top_n: int, n_fiis: int | None) -> dict[pd.Timestamp, list[str]]:
    """As escolhidas do scanner em cada mês fechado do passado (o mês atual, parcial, fica de fora)."""
    datas = adj.index.intersection(raw.index).sort_values()
    historico = {}
    for d in datas[:-1]:
        ordem = sc.selecionar(raw, adj, sinais, d, len(raw.columns))["ticker"].tolist()
        if ordem:
            historico[d] = sc.escolher_candidatos(ordem, top_n, n_fiis)
    return historico


def resumo_por_ano(historico: dict[pd.Timestamp, list[str]], n: int = 5) -> dict[int, dict]:
    """Por ano: quantos meses foram analisados e as n escolhidas que mais apareceram."""
    contagem: dict[int, Counter] = {}
    meses: dict[int, int] = {}
    for d, picks in historico.items():
        contagem.setdefault(d.year, Counter()).update(picks)
        meses[d.year] = meses.get(d.year, 0) + 1
    return {ano: {"meses": meses[ano], "top": sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:n]}
            for ano, c in sorted(contagem.items())}


def montar_painel(tabela: pd.DataFrame, escolhidos: list[str], historico: dict[pd.Timestamp, list[str]],
                  valor: float, meses: int, data: pd.Timestamp) -> dict:
    """Reúne tudo o que o terminal e o HTML mostram."""
    precos = {r.ticker: r.preco for r in tabela.itertuples() if r.ticker in escolhidos and r.preco > 0}
    ordem = [t for t in escolhidos if t in precos]
    dys = [float(tabela.loc[tabela["ticker"] == t, "dy_12m_pct"].iloc[0]) for t in ordem]
    total = valor * 12
    dividendos_ano = estimativa_dividendos(dys, total)
    return {
        "data": data, "valor": valor, "meses": meses,
        "melhores_acoes": melhores_por_classe(tabela, "ação", N_ACOES),
        "melhores_fiis": melhores_por_classe(tabela, "FII", N_FIIS),
        "por_periodo": {chave: melhores_por_periodo(tabela, coluna) for chave, coluna, _ in PERIODOS},
        "ordem": ordem, "precos": precos, "plano": plano_de_compras(precos, ordem, valor, meses),
        "estimativa": {"total": total, "dividendos_ano": dividendos_ano, "dividendos_mes": dividendos_ano / 12},
        "passado_meses": list(historico.items())[-12:], "passado_anos": resumo_por_ano(historico),
        "n_aprovados": int(tabela["aprovado"].sum()), "n_total": len(tabela),
    }


def abas_do_painel(p: dict, tabela: pd.DataFrame, lista_apostas: list[dict] | None = None) -> dict[str, pd.DataFrame]:
    """As abas que o painel grava na planilha (nomes de coluna em português, ticker sem o sufixo .SA)."""
    melhores = pd.concat([p["melhores_acoes"], p["melhores_fiis"]], ignore_index=True)
    abas = {"Melhores agora": pd.DataFrame({
        "Classe": melhores["classe"], "Posição no ranking": melhores["posicao"],
        "Ativo": melhores["ticker"].map(nome), "Preço (R$)": melhores["preco"],
        "Dividendos em 12 meses (%)": melhores["dy_12m_pct"], "Dividendo por unidade (R$)": melhores["dividendo_12m"],
        "Unidades que cabem no aporte": melhores["cotas"], "Custo dessas unidades (R$)": melhores["custo"]})}
    linhas = [{"Período": rotulo, "Posição": i, "Ativo": nome(r.ticker), "Variação do preço (%)": getattr(r, coluna)}
              for chave, coluna, rotulo in PERIODOS for i, r in enumerate(p["por_periodo"][chave].itertuples(), start=1)]
    abas["Quanto subiu"] = pd.DataFrame(linhas, columns=["Período", "Posição", "Ativo", "Variação do preço (%)"])
    abas["Plano de compra"] = pd.DataFrame(
        [{"Mês": m["mes"], "Comprar": _compras_texto(m["compras"]) or "nenhuma compra (guarda o caixa)",
          "Gasto (R$)": m["gasto"], "Sobra (R$)": m["sobra"]} for m in p["plano"]],
        columns=["Mês", "Comprar", "Gasto (R$)", "Sobra (R$)"])
    passado = [{"Tipo": "Ano", "Período": str(ano), "Escolhidas": ", ".join(f"{nome(t)} ({n})" for t, n in d["top"])}
               for ano, d in p["passado_anos"].items()]
    passado += [{"Tipo": "Mês", "Período": f"{d:%Y-%m}", "Escolhidas": ", ".join(nome(t) for t in picks)}
                for d, picks in p["passado_meses"]]
    abas["Passado"] = pd.DataFrame(passado, columns=["Tipo", "Período", "Escolhidas"])
    if lista_apostas:   # sem dados (Yahoo fora do ar), a aba da última vez fica como estava
        abas["Apostas"] = pd.DataFrame({
            "Ativo": [a["t"] for a in lista_apostas], "Tipo": [a["classe"] for a in lista_apostas],
            "Setor": [a["setor"] for a in lista_apostas], "Preço (R$)": [a["preco"] for a in lista_apostas],
            "Último pregão (%)": [a["dia"] for a in lista_apostas], "Último mês (%)": [a["mes"] for a in lista_apostas],
            "12 meses (%)": [a["ano"] for a in lista_apostas], "Oscilação ao ano (%)": [a["oscilacao"] for a in lista_apostas],
            "Pior queda em 12 meses (%)": [a["queda12"] for a in lista_apostas],
            "Distância do topo de 12 meses (%)": [a["topo"] for a in lista_apostas],
            "Negociado por dia (R$)": [a["liquidez"] for a in lista_apostas]})
    abas["Corretoras"] = pd.DataFrame(
        [{"Corretora": c["nome"], "Corretagem": c["corretagem"], "Custódia": c["custodia"], "Atenção": c["atencao"],
          "Fonte": c["fonte"], "Pesquisado em": PESQUISADO_EM} for c in CORRETORAS])
    todos = tabela.copy()
    todos["ticker"] = todos["ticker"].map(nome)
    todos["aprovado"] = todos["aprovado"].map({True: "sim", False: "não"})
    abas["Todos os ativos"] = todos.rename(columns={
        "ticker": "Ativo", "classe": "Classe", "preco": "Preço (R$)", "dia_pct": "Último pregão (%)", "mes_pct": "Último mês (%)",
        "ano_pct": "12 meses (%)", "dividendo_12m": "Dividendo em 12 meses (R$)", "dy_12m_pct": "Dividendos em 12 meses (%)",
        "aprovado": "Passou nos filtros", "posicao": "Posição no ranking", "nota": "Nota do scanner",
        "cotas": "Unidades que cabem no aporte", "custo": "Custo dessas unidades (R$)"})
    return abas


def _compras_texto(compras: dict[str, int]) -> str:
    return " + ".join(f"{n} {nome(t)}" for t, n in compras.items())


def _unidades_texto(r, valor: float) -> str:
    if r.cotas == 0:
        return f"não cabe (1 custa {brl(r.preco)})"
    return f"{r.cotas} ({brl(r.custo)})"


def texto_painel(p: dict) -> str:
    """O painel em texto, para o terminal."""
    L = [f"=== AS MELHORES AGORA (preços de {p['data']:%d/%m/%Y}; ordem da nota do scanner) ==="]
    for titulo, df in (("Ações", p["melhores_acoes"]), ("FIIs", p["melhores_fiis"])):
        L.append(f"{titulo}:")
        if df.empty:
            L.append("  nenhum passou nos filtros")
        for r in df.itertuples():
            L.append(f"  {int(r.posicao):>2}. {nome(r.ticker):<8} {brl(r.preco):>11}   dividendos em 12 meses: {pct(r.dy_12m_pct):>6} "
                     f"({brl(r.dividendo_12m)} por unidade)   {brl(p['valor'])} compram: {_unidades_texto(r, p['valor'])}")
    L.append(f"(Posição no ranking geral à esquerda. {p['n_aprovados']} de {p['n_total']} ativos passaram nos filtros.)")

    L.append("\n=== QUANTO CADA UM SUBIU (preço, entre os aprovados) ===")
    for chave, coluna, rotulo in PERIODOS:
        df = p["por_periodo"][chave]
        L.append(f"{rotulo}: " + (" | ".join(f"{nome(r.ticker)} {pct(getattr(r, coluna), True)}" for r in df.itertuples())
                               if not df.empty else "sem dados"))

    L.append(f"\n=== PLANO DOS PRIMEIROS {p['meses']} MESES ({brl(p['valor'])} por mês, só unidades inteiras) ===")
    if not p["plano"]:
        L.append("Nenhum ativo aprovado para montar o plano.")
    else:
        L.append("Ativos do plano: " + ", ".join(nome(t) for t in p["ordem"]))
        for m in p["plano"]:
            compra = _compras_texto(m["compras"]) if m["compras"] else "nenhuma compra (guarda o caixa)"
            L.append(f"  Mês {m['mes']}: {compra}  =  {brl(m['gasto'])}  (sobra {brl(m['sobra'])})")
        L.append(f"Usa os preços de hoje nos {p['meses']} meses. O gasto de um mês pode passar de {brl(p['valor'])} "
                 "porque inclui a sobra dos meses anteriores.")
        e = p["estimativa"]
        L.append(f"Quanto pode render: se você investir {brl(e['total'])} em 12 meses nesses ativos e eles pagarem o mesmo "
                 f"dividendo do último ano, são cerca de {brl(e['dividendos_ano'])} por ano ({brl(e['dividendos_mes'])} por mês).")
        L.append("Isso é o que aconteceu no último ano, não uma promessa: o preço também sobe e desce.")

    L.append("\n=== AS MELHORES NO PASSADO (as escolhidas do scanner em cada momento) ===")
    L.append("Por ano (as que mais apareceram):")
    for ano, d in p["passado_anos"].items():
        L.append(f"  {ano} ({d['meses']} meses): " + ", ".join(f"{nome(t)} ({n})" for t, n in d["top"]))
    L.append("Últimos 12 meses (no fim de cada mês):")
    for d, picks in p["passado_meses"]:
        L.append(f"  {d:%Y-%m}: " + ", ".join(nome(t) for t in picks))

    L.append(f"\n=== CUSTOS DAS CORRETORAS (pesquisado em {PESQUISADO_EM}; confirme na tabela oficial) ===")
    for c in CORRETORAS:
        L.append(f"  {c['nome']}\n    Corretagem: {c['corretagem']}\n    Custódia: {c['custodia']}\n    {c['atencao']}")
    L.append(AVISO_CORRETORAS)
    L.append("\n" + AVISO_FINAL)
    return "\n".join(L)


def baixar_indices() -> list[dict]:
    """Ibovespa e dólar para o topo do painel. Se o Yahoo não responder, o painel só fica sem esses cartões."""
    try:
        fechamentos, _ = baixar_diario([t for t, _, _ in INDICES], dias=120)
    except RuntimeError as exc:
        print(f"[AVISO] Sem Ibovespa e dólar: {exc}")
        return []
    saida = []
    for ticker, rotulo, unidade in INDICES:
        if ticker not in fechamentos or len(fechamentos[ticker]) < 2:
            continue
        f = fechamentos[ticker]
        saida.append({"id": ticker, "nome": rotulo, "unidade": unidade, "valor": _numero(f.iloc[-1], 4),
                      "dia_pct": _numero(variacoes(f)["dia"], 2), "serie": [_numero(x, 4) for x in f.tail(SERIE_PREGOES)]})
    return saida


def eventos_de_dividendos(proventos: pd.Series | None, ate: pd.Timestamp, dias: int = 365) -> list[list]:
    """Dividendos por unidade dos últimos `dias` dias até `ate`: [[AAAA-MM-DD, valor], ...]. A carteira virtual os credita."""
    if proventos is None or proventos.empty:
        return []
    p = proventos.dropna()
    p = p[(p > 0) & (p.index > ate - pd.Timedelta(days=dias)) & (p.index <= ate)]
    return [[f"{d:%Y-%m-%d}", _numero(v, 4)] for d, v in p.sort_index().items()]


def montar_apostas(fechamentos: dict[str, pd.Series], volumes: dict[str, pd.Series],
                   proventos: dict[str, pd.Series] | None = None) -> list[dict]:
    """Uma linha por aposta com preço, variações e os números de risco (ver apostas.py). Sem histórico, fica de fora."""
    saida = []
    for ticker, tipo, setor in ap.APOSTAS:
        f = fechamentos.get(ticker)
        if f is None or len(f.dropna()) < 2:
            continue
        v, m = variacoes(f), ap.metricas(f, volumes.get(ticker))
        saida.append({"t": nome(ticker), "classe": tipo, "setor": setor, "preco": _numero(f.iloc[-1], 4),
                      "dia": _numero(v["dia"], 2), "mes": _numero(v["mes"], 2), "ano": _numero(v["ano"], 2),
                      "oscilacao": m["oscilacao"], "queda12": m["queda12"], "topo": m["topo"], "liquidez": m["liquidez"],
                      "serie": ap.serie_do_ano(f), "div": eventos_de_dividendos((proventos or {}).get(ticker), f.index[-1])})
    return saida


def baixar_apostas() -> list[dict]:
    """As apostas com dados de hoje. Se o Yahoo não responder, a página só fica sem essa seção."""
    tickers = [t for t, _, _ in ap.APOSTAS if t not in sc.UNIVERSO]   # quem já está no scanner não é aposta
    volumes: dict[str, pd.Series] = {}
    try:
        fechamentos, proventos = baixar_diario(tickers, volumes=volumes)
    except RuntimeError as exc:
        print(f"[AVISO] Sem a seção de apostas: {exc}")
        return []
    return montar_apostas(fechamentos, volumes, proventos)


def _numero(x, casas: int):
    """Número arredondado para o JSON; vazio (NaN) vira None."""
    return None if x is None or pd.isna(x) else round(float(x), casas)


def dados_do_painel(p: dict, tabela: pd.DataFrame, fechamentos: dict[str, pd.Series], indices: list[dict], agora: datetime,
                    config: dict, link_planilha: str | None = None, dados_url: str | None = None,
                    intervalo_min: int = INTERVALO_PADRAO_MIN, lista_apostas: list[dict] | None = None,
                    proventos: dict[str, pd.Series] | None = None) -> dict:
    """Tudo o que a página mostra, em tipos simples (vai para o JSON). `config`: valor, ativos, fiis e meses iniciais."""
    ativos = []
    for r in tabela.itertuples():
        ativos.append({
            "t": nome(r.ticker), "classe": r.classe, "preco": _numero(r.preco, 4), "dia": _numero(r.dia_pct, 2),
            "mes": _numero(r.mes_pct, 2), "ano": _numero(r.ano_pct, 2), "div12": _numero(r.dividendo_12m, 4),
            "dy": _numero(r.dy_12m_pct, 2), "ok": bool(r.aprovado), "pos": None if pd.isna(r.posicao) else int(r.posicao),
            "nota": _numero(r.nota, 3), "serie": [_numero(x, 4) for x in fechamentos[r.ticker].tail(SERIE_PREGOES)],
            "div": eventos_de_dividendos((proventos or {}).get(r.ticker), p["data"])})
    return {
        "versao": painel_pagina.VERSAO_DADOS, "gerado_em": agora.isoformat(timespec="seconds"),
        "data_precos": f"{p['data']:%Y-%m-%d}", "intervalo_min": intervalo_min, "dados_url": dados_url, "planilha_url": link_planilha,
        "config": {"valor": config["valor"], "ativos": config["ativos"], "fiis": config["fiis"], "meses": config["meses"]},
        "n_aprovados": p["n_aprovados"], "n_total": p["n_total"], "indices": indices, "ativos": ativos,
        "apostas": lista_apostas or [], "liquidez_baixa": ap.LIQUIDEZ_BAIXA,
        "passado": {"anos": [{"ano": ano, "meses": d["meses"], "top": [[nome(t), n] for t, n in d["top"]]} for ano, d in p["passado_anos"].items()],
                    "meses": [{"mes": f"{d:%Y-%m}", "escolhidas": [nome(t) for t in picks]} for d, picks in p["passado_meses"]]},
        "corretoras": {"pesquisado_em": PESQUISADO_EM, "aviso": AVISO_CORRETORAS,
                       "lista": [{k: c[k] for k in ("nome", "corretagem", "custodia", "atencao", "fonte")} for c in CORRETORAS]},
        "aviso_final": AVISO_FINAL,
    }


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Painel simples: as melhores agora, quanto subiram, plano de compra e custos.")
    parser.add_argument("--valor", type=float, default=sc.APORTE_PADRAO, help="Quanto você investe por mês, em reais (padrão: 200).")
    parser.add_argument("--ativos", type=int, default=sc.TOP_PADRAO, help="Quantos ativos no plano de compra (padrão: 5).")
    parser.add_argument("--fiis", type=int, default=0, help="Quantos desses ativos são FIIs (padrão: 0, só ações).")
    parser.add_argument("--meses", type=int, default=3, help="Quantos meses mostrar no plano de compra (padrão: 3).")
    parser.add_argument("--anos", type=int, default=8, help="Anos de histórico para o filtro e o passado (padrão: 8).")
    parser.add_argument("--saida", default="carteira",
                        help="Nome da planilha (.xlsx) e do relatório (.html), sem extensão (padrão: carteira). Sempre os mesmos arquivos.")
    parser.add_argument("--sem-abrir", action="store_true", help="Não abre o relatório no navegador.")
    parser.add_argument("--link-planilha", default=None,
                        help="Endereço da planilha para o link 'Baixar a planilha' no relatório (usado ao publicar na internet).")
    parser.add_argument("--dados-json", default=None, metavar="ARQUIVO",
                        help="Grava também os dados da página neste arquivo (ao lado do .html), e a página passa a relê-lo sozinha "
                             "para mostrar preços novos sem recarregar (usado ao publicar na internet).")
    parser.add_argument("--intervalo-min", type=int, default=INTERVALO_PADRAO_MIN,
                        help="De quantos em quantos minutos o servidor atualiza os preços (só para avisar quando a página está desatualizada).")
    return parser


def main() -> None:
    args = criar_parser().parse_args()
    if args.valor <= 0 or args.ativos < 1 or args.meses < 1 or args.anos < 4:
        raise SystemExit("Use --valor > 0, --ativos >= 1, --meses >= 1 e --anos >= 4.")
    if not 0 <= args.fiis <= args.ativos:
        raise SystemExit("Use --fiis entre 0 e o valor de --ativos.")
    if args.dados_json and (Path(args.dados_json).name != args.dados_json or not args.dados_json.endswith(".json")):
        raise SystemExit("Use --dados-json com só o nome de um arquivo .json, por exemplo dados.json.")
    if args.intervalo_min < 1:
        raise SystemExit("Use --intervalo-min >= 1.")

    inicio = (pd.Timestamp.today().normalize() - pd.DateOffset(years=args.anos)).strftime("%Y-%m-%d")
    print(f"Baixando o histórico mensal desde {inicio} e os preços diários. Isso pode levar alguns minutos.")
    raw, adj, div = sc.baixar_dados(sc.UNIVERSO, inicio)
    sinais = sc.metricas_historicas(raw, div)
    datas = adj.index.intersection(raw.index).sort_values()
    sinal_atual = datas[-2] if len(datas) > 1 else datas[-1]   # último mês fechado, como no scanner
    ranking_geral = sc.selecionar(raw, adj, sinais, sinal_atual, len(raw.columns))
    agora = datetime.now(BRASILIA)
    fechamentos, proventos = baixar_diario(list(raw.columns))
    indices = baixar_indices()
    lista_apostas = baixar_apostas()
    tabela = montar_tabela(fechamentos, proventos, ranking_geral, args.valor)
    escolhidos = sc.escolher_candidatos(ranking_geral["ticker"].tolist(), args.ativos, args.fiis)
    historico = escolhidas_por_mes(raw, adj, sinais, args.ativos, args.fiis)
    data = max(f.index[-1] for f in fechamentos.values())
    painel = montar_painel(tabela, escolhidos, historico, args.valor, args.meses, data)

    print("\n" + texto_painel(painel))
    planilha = caminho_da_planilha(args.saida)
    arquivo_html = planilha.with_suffix(".html")
    arquivo_html.parent.mkdir(parents=True, exist_ok=True)
    dados = dados_do_painel(painel, tabela, fechamentos, indices, agora,
                            {"valor": args.valor, "ativos": args.ativos, "fiis": args.fiis, "meses": args.meses},
                            args.link_planilha, args.dados_json, args.intervalo_min, lista_apostas, proventos)
    arquivo_html.write_text(painel_pagina.pagina_html(dados), encoding="utf-8")
    print(f"\nRelatório atualizado: {arquivo_html}")
    if args.dados_json:
        (arquivo_html.parent / args.dados_json).write_text(painel_pagina.dados_json(dados), encoding="utf-8")
        print(f"Dados da página atualizados: {arquivo_html.parent / args.dados_json}")
    if not args.sem_abrir:
        try:
            webbrowser.open(arquivo_html.resolve().as_uri())
        except Exception:
            print("Não consegui abrir o navegador; abra o arquivo .html com um duplo clique.")
    # A planilha vem por último: se ela estiver aberta no Excel, o relatório acima já foi gerado.
    atualizar_planilha(planilha, abas_do_painel(painel, tabela, lista_apostas))
    print(f"Planilha atualizada: {planilha} (é sempre o mesmo arquivo; feche-o no Excel antes de rodar de novo).")


if __name__ == "__main__":
    main()
