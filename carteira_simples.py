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
import html
import webbrowser
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

import scanner_dividendos_b3 as sc
from corretoras import AVISO as AVISO_CORRETORAS, CORRETORAS, PESQUISADO_EM
from planilha import atualizar_planilha, caminho_da_planilha

DIAS_DIARIOS = 430   # histórico diário baixado: 12 meses com folga
N_ACOES = 5          # quantas ações aparecem em "as melhores agora"
N_FIIS = 3           # quantos FIIs aparecem em "as melhores agora"
N_PERIODO = 5        # quantas aparecem em "quanto cada um subiu"
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


def baixar_diario(tickers: list[str], dias: int = DIAS_DIARIOS) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """Fechamentos e dividendos diários dos últimos `dias` dias corridos. Ativo sem dados é ignorado."""
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
        cotas = int(valor // preco) if preco > 0 else 0
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


def abas_do_painel(p: dict, tabela: pd.DataFrame) -> dict[str, pd.DataFrame]:
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


_CSS = """
:root{color-scheme:light;--bg:#f9f9f7;--surf:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--line:#e1e0d9;--acc:#2a78d6;--up:#1a7f4b;--down:#c0392b;--code:#f0efec}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#0d0d0d;--surf:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#2c2c2a;--acc:#3987e5;--up:#3aa57f;--down:#e66767;--code:#262624}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}
main{max-width:860px;margin:0 auto}h1{font-size:28px;margin:8px 0}h2{font-size:21px;margin:40px 0 6px}
.sub{color:var(--ink2);margin:0 0 14px;max-width:62ch}.panel{background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:16px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:15px}th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:middle}
th:first-child,td:first-child{padding-left:0}th:last-child,td:last-child{padding-right:0}
th{color:var(--ink2);font-weight:600;font-size:13px}td.n,th.n{text-align:right;white-space:nowrap}
.bar{display:block;height:10px;background:var(--line);border-radius:3px;min-width:60px;margin-bottom:3px}.bar i{display:block;height:100%;background:var(--acc);border-radius:3px}
.vb{display:flex;height:10px;min-width:120px}.vb span{flex:1;display:flex}.vb .l{justify-content:flex-end}.vb i{display:block;height:100%}
.vb .l i{background:var(--down);border-radius:3px 0 0 3px}.vb .r i{background:var(--up);border-radius:0 3px 3px 0}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}.cols h3{font-size:15px;margin:0 0 8px}
.chip{display:inline-block;background:var(--code);border-radius:6px;padding:1px 8px;margin:2px 4px 2px 0;font-size:14px}
.nota{font-size:14px;color:var(--ink2);max-width:70ch}a{color:var(--acc)}small{color:var(--ink2)}
td.w{white-space:normal}
@media (max-width:520px){.panel{padding:12px}th,td{padding:7px 5px}table{font-size:14px}}
"""


def _tabela_melhores(df: pd.DataFrame, valor: float) -> str:
    if df.empty:
        return "<p class='nota'>Nenhum passou nos filtros.</p>"
    linhas = []
    for r in df.itertuples():
        largura = 0 if pd.isna(r.dy_12m_pct) else min(r.dy_12m_pct / 12 * 100, 100)
        linhas.append(
            f"<tr><td><b>{html.escape(nome(r.ticker))}</b><br><small>{int(r.posicao)}º no ranking</small></td>"
            f"<td class='n'>{brl(r.preco)}</td>"
            f"<td><span class='bar'><i style='width:{largura:.0f}%'></i></span>"
            f"<small>{pct(r.dy_12m_pct)}<br>{brl(r.dividendo_12m)} por unidade</small></td>"
            f"<td class='n w'>{html.escape(_unidades_texto(r, valor))}</td></tr>")
    return ("<table><tr><th>Ativo</th><th class='n'>Preço</th><th>Dividendos pagos em 12 meses</th>"
            f"<th class='n'>{brl(valor)} compram</th></tr>" + "".join(linhas) + "</table>")


def _bloco_periodo(titulo: str, df: pd.DataFrame, coluna: str) -> str:
    if df.empty:
        return f"<div><h3>{html.escape(titulo)}</h3><p class='nota'>Sem dados.</p></div>"
    maximo = max(float(df[coluna].abs().max()), 0.01)
    linhas = []
    for r in df.itertuples():
        v = float(getattr(r, coluna))
        neg = f"<i style='width:{min(-v / maximo * 100, 100):.0f}%'></i>" if v < 0 else ""
        pos = f"<i style='width:{min(v / maximo * 100, 100):.0f}%'></i>" if v > 0 else ""
        linhas.append(f"<tr><td><b>{html.escape(nome(r.ticker))}</b></td><td><span class='vb'><span class='l'>{neg}</span>"
                      f"<span class='r'>{pos}</span></span></td><td class='n'>{pct(v, True)}</td></tr>")
    return f"<div><h3>{html.escape(titulo)}</h3><table>" + "".join(linhas) + "</table></div>"


def html_painel(p: dict) -> str:
    """O painel como página HTML autônoma (sem internet, sem scripts)."""
    esc = html.escape
    e = p["estimativa"]
    partes = [f"<h1>Sua carteira em poucos passos</h1><p class='sub'>Preços de {p['data']:%d/%m/%Y}. "
              f"{p['n_aprovados']} de {p['n_total']} ativos passaram nos filtros do scanner.</p>"]
    partes.append("<h2>As melhores agora</h2><p class='sub'>Na ordem da nota do scanner (dividendos, regularidade e preço no último ano). "
                  "A barra mostra quanto cada um pagou de dividendos em 12 meses, em % do preço.</p>"
                  "<div class='panel'><h3>Ações</h3>" + _tabela_melhores(p["melhores_acoes"], p["valor"])
                  + "<h3 style='margin-top:18px'>Fundos imobiliários (FIIs)</h3>" + _tabela_melhores(p["melhores_fiis"], p["valor"]) + "</div>")
    partes.append("<h2>Quanto cada um subiu</h2><p class='sub'>Variação do preço entre os aprovados. "
                  "Verde sobe, vermelho cai; o sinal e a barra mostram o mesmo. O dia muda muito de um pregão para o outro: "
                  "o mês e o ano dizem mais para quem investe a longo prazo.</p><div class='panel cols'>"
                  + "".join(_bloco_periodo(rotulo, p["por_periodo"][chave], coluna) for chave, coluna, rotulo in PERIODOS) + "</div>")
    if p["plano"]:
        linhas = "".join(
            f"<tr><td>Mês {m['mes']}</td><td>{esc(_compras_texto(m['compras'])) if m['compras'] else 'nenhuma compra (guarda o caixa)'}</td>"
            f"<td class='n'>{brl(m['gasto'])}</td><td class='n'>{brl(m['sobra'])}</td></tr>" for m in p["plano"])
        partes.append(f"<h2>Plano dos primeiros {p['meses']} meses</h2><p class='sub'>{brl(p['valor'])} por mês, só unidades inteiras. "
                      "O plano reforça sempre o ativo com menos dinheiro aplicado; se ele ainda não cabe, guarda o caixa para o mês seguinte.</p>"
                      "<div class='panel'><p>Ativos: " + "".join(f"<span class='chip'>{esc(nome(t))}</span>" for t in p["ordem"]) + "</p>"
                      "<table><tr><th>Quando</th><th>Comprar</th><th class='n'>Gasto</th><th class='n'>Sobra</th></tr>" + linhas + "</table>"
                      f"<p class='nota'><b>Quanto pode render:</b> se você investir {brl(e['total'])} em 12 meses nesses ativos e eles pagarem o "
                      f"mesmo dividendo do último ano, são cerca de {brl(e['dividendos_ano'])} por ano ({brl(e['dividendos_mes'])} por mês). "
                      "É o que aconteceu no último ano, não uma promessa: o preço também sobe e desce.</p></div>")
    anos = "".join(f"<tr><td>{ano}</td><td>{d['meses']}</td><td>" + "".join(f"<span class='chip'>{esc(nome(t))} ({n})</span>" for t, n in d["top"])
                   + "</td></tr>" for ano, d in p["passado_anos"].items())
    meses = "".join(f"<tr><td>{d:%Y-%m}</td><td>" + "".join(f"<span class='chip'>{esc(nome(t))}</span>" for t in picks) + "</td></tr>"
                    for d, picks in p["passado_meses"])
    partes.append("<h2>As melhores no passado</h2><p class='sub'>As escolhidas do scanner em cada momento, com o que se sabia na época. "
                  "O número entre parênteses é em quantos meses do ano o ativo foi escolhido.</p><div class='panel'><h3>Por ano</h3>"
                  "<table><tr><th>Ano</th><th>Meses</th><th>As que mais apareceram</th></tr>" + anos + "</table>"
                  "<h3 style='margin-top:18px'>Últimos 12 meses (no fim de cada mês)</h3><table><tr><th>Mês</th><th>Escolhidas</th></tr>"
                  + meses + "</table></div>")
    cor = "".join(f"<tr><td><b>{esc(c['nome'])}</b></td><td>{esc(c['corretagem'])}</td><td>{esc(c['custodia'])}</td><td>{esc(c['atencao'])} "
                  f"<a href='{esc(c['fonte'])}' rel='noopener' target='_blank'>fonte</a></td></tr>" for c in CORRETORAS)
    partes.append(f"<h2>Custos das corretoras</h2><p class='sub'>Pesquisado em {esc(PESQUISADO_EM)} em fontes públicas. Tarifas mudam: "
                  "confirme na tabela oficial antes de abrir conta.</p><div class='panel'><table><tr><th>Corretora</th><th>Corretagem</th>"
                  "<th>Custódia</th><th>Atenção</th></tr>" + cor + f"</table><p class='nota'>{esc(AVISO_CORRETORAS)}</p></div>")
    partes.append(f"<p class='nota' style='margin-top:32px'>{esc(AVISO_FINAL)}</p>")
    return ("<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Sua carteira em poucos passos</title><style>{_CSS}</style></head><body><main>" + "".join(partes) + "</main></body></html>")


def main() -> None:
    parser = argparse.ArgumentParser(description="Painel simples: as melhores agora, quanto subiram, plano de compra e custos.")
    parser.add_argument("--valor", type=float, default=sc.APORTE_PADRAO, help="Quanto você investe por mês, em reais (padrão: 200).")
    parser.add_argument("--ativos", type=int, default=sc.TOP_PADRAO, help="Quantos ativos no plano de compra (padrão: 5).")
    parser.add_argument("--fiis", type=int, default=0, help="Quantos desses ativos são FIIs (padrão: 0, só ações).")
    parser.add_argument("--meses", type=int, default=3, help="Quantos meses mostrar no plano de compra (padrão: 3).")
    parser.add_argument("--anos", type=int, default=8, help="Anos de histórico para o filtro e o passado (padrão: 8).")
    parser.add_argument("--saida", default="carteira",
                        help="Nome da planilha (.xlsx) e do relatório (.html), sem extensão (padrão: carteira). Sempre os mesmos arquivos.")
    parser.add_argument("--sem-abrir", action="store_true", help="Não abre o relatório no navegador.")
    args = parser.parse_args()
    if args.valor <= 0 or args.ativos < 1 or args.meses < 1 or args.anos < 4:
        raise SystemExit("Use --valor > 0, --ativos >= 1, --meses >= 1 e --anos >= 4.")
    if not 0 <= args.fiis <= args.ativos:
        raise SystemExit("Use --fiis entre 0 e o valor de --ativos.")

    inicio = (pd.Timestamp.today().normalize() - pd.DateOffset(years=args.anos)).strftime("%Y-%m-%d")
    print(f"Baixando o histórico mensal desde {inicio} e os preços diários. Isso pode levar alguns minutos.")
    raw, adj, div = sc.baixar_dados(sc.UNIVERSO, inicio)
    sinais = sc.metricas_historicas(raw, div)
    datas = adj.index.intersection(raw.index).sort_values()
    sinal_atual = datas[-2] if len(datas) > 1 else datas[-1]   # último mês fechado, como no scanner
    ranking_geral = sc.selecionar(raw, adj, sinais, sinal_atual, len(raw.columns))
    fechamentos, proventos = baixar_diario(list(raw.columns))
    tabela = montar_tabela(fechamentos, proventos, ranking_geral, args.valor)
    escolhidos = sc.escolher_candidatos(ranking_geral["ticker"].tolist(), args.ativos, args.fiis)
    historico = escolhidas_por_mes(raw, adj, sinais, args.ativos, args.fiis)
    data = max(f.index[-1] for f in fechamentos.values())
    painel = montar_painel(tabela, escolhidos, historico, args.valor, args.meses, data)

    print("\n" + texto_painel(painel))
    planilha = caminho_da_planilha(args.saida)
    arquivo_html = planilha.with_suffix(".html")
    arquivo_html.write_text(html_painel(painel), encoding="utf-8")
    print(f"\nRelatório atualizado: {arquivo_html}")
    if not args.sem_abrir:
        try:
            webbrowser.open(arquivo_html.resolve().as_uri())
        except Exception:
            print("Não consegui abrir o navegador; abra o arquivo .html com um duplo clique.")
    # A planilha vem por último: se ela estiver aberta no Excel, o relatório acima já foi gerado.
    atualizar_planilha(planilha, abas_do_painel(painel, tabela))
    print(f"Planilha atualizada: {planilha} (é sempre o mesmo arquivo; feche-o no Excel antes de rodar de novo).")


if __name__ == "__main__":
    main()
