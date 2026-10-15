"""Testes offline do scanner: dados sintéticos, sem acesso ao Yahoo Finance."""
import json
import zlib
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
import pytest

import scanner_dividendos_b3 as sc

FIIS_TESTE = ["HGLG11.SA", "XPLG11.SA", "KNRI11.SA"]
ACAO_TESTE = "ITUB4.SA"


def mercado(tickers, n=60, preco=10.0, div_mensal=0.0):
    """Preços planos (adj == raw) e dividendos mensais constantes."""
    idx = pd.date_range("2020-01-31", periods=n, freq="ME")
    raw = pd.DataFrame(preco, index=idx, columns=tickers)
    div = pd.DataFrame(div_mensal, index=idx, columns=tickers)
    return raw, raw.copy(), div


def ranking_fixo(esquema):
    """Substitui `selecionar`: devolve a ordem dada por esquema(posição_do_mês_do_sinal)."""
    def stub(raw, adj, sinais, data_sinal, top_n):
        return pd.DataFrame({"ticker": esquema(raw.index.get_loc(data_sinal))})
    return stub


# ---------- item 3: contagem de períodos com proventos ----------

def test_anos_com_dividendo_nunca_passa_de_3():
    raw, _, div = mercado(["X"], n=84, div_mensal=0.05)
    anos = sc.metricas_historicas(raw, div)["anos_div"]["X"]
    assert anos.max() == 3
    assert anos.iloc[:35].isna().all()          # sem 36 meses de dados não há contagem
    assert (anos.iloc[35:] == 3).all()


def test_anos_com_dividendo_conta_periodos_de_12_meses():
    raw, _, div = mercado(["X"], n=60)
    div.loc["2023-06-30", "X"] = 0.5            # só um pagamento no último ano
    div.loc["2021-06-30", "X"] = 0.5            # e um dois anos antes; nada entre os dois
    anos = sc.metricas_historicas(raw, div)["anos_div"]["X"]
    # Em 31/03/2024: períodos abr/23-mar/24 (sim), abr/22-mar/23 (não), abr/21-mar/22 (sim).
    assert anos.loc["2024-03-31"] == 2
    div.loc["2021-06-30", "X"] = 0.0
    assert sc.metricas_historicas(raw, div)["anos_div"]["X"].loc["2024-03-31"] == 1


def test_corte_forte_de_proventos_exclui_do_ranking(monkeypatch):
    raw, adj, div = mercado(["OK", "CORTE"])
    div["OK"] = 0.05                            # 6% a.a. estável
    div["CORTE"] = 0.125                        # 15% a.a. ...
    div.loc[div.index[48]:, "CORTE"] = 0.04167  # ... cortado para ~5% nos últimos 12 meses
    sinais = sc.metricas_historicas(raw, div)
    data = raw.index[-1]
    assert sinais["razao_proventos"].at[data, "CORTE"] == pytest.approx(0.333, abs=0.01)
    assert sc.selecionar(raw, adj, sinais, data, 10)["ticker"].tolist() == ["OK"]
    monkeypatch.setattr(sc, "MIN_RAZAO_PROVENTOS", 0)           # 0 desativa o corte
    assert set(sc.selecionar(raw, adj, sinais, data, 10)["ticker"]) == {"OK", "CORTE"}


# ---------- item 4: limites de peso ----------

def test_alocar_divide_igualmente_sem_limites_ativos():
    cands = FIIS_TESTE[:2] + [ACAO_TESTE, "VALE3.SA", "WEGE3.SA"]
    aloc = sc.alocar_aporte(200.0, cands, {})
    assert aloc == pytest.approx({t: 40.0 for t in cands})


def test_alocar_nao_aporta_em_ativo_acima_do_limite():
    cands = ["ITUB4.SA", "VALE3.SA", "WEGE3.SA", "PETR4.SA"]
    aloc = sc.alocar_aporte(200.0, cands, {"ITUB4.SA": 1000.0})
    assert "ITUB4.SA" not in aloc
    assert sum(aloc.values()) == pytest.approx(200.0)


def test_alocar_respeita_limite_de_fiis_e_deixa_sobra_em_caixa():
    cands = FIIS_TESTE + [ACAO_TESTE]
    aloc = sc.alocar_aporte(1000.0, cands, {})
    assert sum(aloc[t] for t in FIIS_TESTE) == pytest.approx(600.0)    # 60% de 1000
    assert aloc[ACAO_TESTE] == pytest.approx(250.0)                    # 25% de 1000
    assert sum(aloc.values()) < 1000.0                                  # o resto fica em caixa


def test_alocar_relaxa_limites_quando_nao_ha_alternativa():
    assert sc.alocar_aporte(200.0, ["ITUB4.SA"], {}) == pytest.approx({"ITUB4.SA": 200.0})
    so_fiis = sc.alocar_aporte(300.0, FIIS_TESTE, {})                   # sem ações para receber o excedente
    assert sum(so_fiis.values()) == pytest.approx(300.0)


def test_backtest_respeita_limites_de_peso(monkeypatch):
    tickers = FIIS_TESTE + [ACAO_TESTE]
    raw, adj, div = mercado(tickers)
    monkeypatch.setattr(sc, "selecionar", ranking_fixo(lambda i: tickers))
    curva, ops = sc.backtest(raw, adj, div, 200.0, 4)
    ops = ops.assign(valor=ops.valor_estimado - ops.custo_estimado)    # preços planos: valor = custo líquido
    for _, linha in curva.iterrows():
        ate_agora = ops[ops.data <= linha.data]
        por_ativo = ate_agora.groupby("ticker").valor.sum() / linha.valor_carteira
        assert por_ativo.max() <= sc.MAX_PESO_ATIVO + 1e-9
        assert por_ativo.reindex(FIIS_TESTE).sum() <= sc.MAX_PESO_FII + 1e-9
    assert curva.caixa.iloc[-1] > 0                                     # limites apertados: sobra caixa
    assert (curva.valor_posicoes_fiis / curva.valor_carteira).max() <= sc.MAX_PESO_FII + 1e-9


# ---------- item 1: vender só quando sai dos filtros ----------

def test_ativo_elegivel_fora_do_top_n_nao_e_vendido(monkeypatch):
    raw, adj, div = mercado(["A", "B", "C"])

    def esquema(i):
        if i < 45:
            return ["A", "B", "C"]     # top 2 = A, B
        if i < 52:
            return ["C", "A", "B"]     # B continua elegível, mas fora do top 2
        return ["C", "A"]              # B deixou de passar nos filtros

    monkeypatch.setattr(sc, "selecionar", ranking_fixo(esquema))
    curva, ops = sc.backtest(raw, adj, div, 200.0, 2)
    vendas = ops[ops.tipo == "VENDA_FILTRO"]
    assert vendas.ticker.tolist() == ["B"]
    assert vendas.data.iloc[0] == raw.index[53].date().isoformat()     # sinal 52 -> execução no mês 53
    por_data = curva.set_index("data").ativos_na_carteira
    assert por_data[raw.index[46].date().isoformat()] == 3             # B mantido junto com A e C
    assert por_data[raw.index[52].date().isoformat()] == 3
    assert por_data[raw.index[53].date().isoformat()] == 2


# ---------- item 2: ativo sem cotação não some da carteira ----------

def test_venda_sem_cotacao_usa_ultimo_preco(monkeypatch):
    raw, adj, div = mercado(["A", "B"])
    raw.loc[raw.index[48]:, "B"] = np.nan                               # B some a partir do mês 48
    adj.loc[adj.index[48]:, "B"] = np.nan
    monkeypatch.setattr(sc, "selecionar", ranking_fixo(lambda i: ["A", "B"] if i < 47 else ["A"]))
    curva, ops = sc.backtest(raw, adj, div, 200.0, 2)
    venda = ops[(ops.tipo == "VENDA_FILTRO") & (ops.ticker == "B")]
    assert len(venda) == 1 and venda.data.iloc[0] == raw.index[48].date().isoformat()
    assert "último preço" in venda.observacao.iloc[0]
    v = curva.valor_carteira
    assert v.iloc[48 - 37] - v.iloc[47 - 37] > 190                     # só o aporte (menos custos), sem perda


def test_falta_pontual_de_cotacao_nao_gera_queda_falsa(monkeypatch):
    raw, adj, div = mercado(["A", "B"])
    raw.loc[raw.index[50], "B"] = np.nan
    adj.loc[adj.index[50], "B"] = np.nan
    # B é comprado no início e depois segue elegível, mas fora do top 1
    monkeypatch.setattr(sc, "selecionar", ranking_fixo(lambda i: ["B", "A"] if i < 42 else ["A", "B"]))
    curva, _ = sc.backtest(raw, adj, div, 200.0, 1)
    assert curva.valor_carteira.is_monotonic_increasing                # preços planos: só cresce com aportes


# ---------- composição: ações x FIIs ----------

def test_backtest_registra_o_valor_das_posicoes_por_classe(monkeypatch):
    tickers = ["HGLG11.SA", ACAO_TESTE]
    raw, adj, div = mercado(tickers)
    monkeypatch.setattr(sc, "selecionar", ranking_fixo(lambda i: tickers))
    curva, _ = sc.backtest(raw, adj, div, 200.0, 2)
    assert (curva.valor_posicoes_acoes + curva.valor_posicoes_fiis).to_numpy() == pytest.approx(curva.valor_posicoes.to_numpy())
    assert (curva.valor_posicoes_fiis / curva.valor_posicoes).to_numpy() == pytest.approx(0.5)   # aporte dividido igualmente


def test_ticker_fora_da_lista_conta_como_acao(monkeypatch):
    raw, adj, div = mercado(["A", "B"])
    monkeypatch.setattr(sc, "selecionar", ranking_fixo(lambda i: ["A", "B"]))
    curva, _ = sc.backtest(raw, adj, div, 200.0, 2)
    assert (curva.valor_posicoes_fiis == 0).all() and (curva.valor_posicoes_acoes > 0).all()


def test_composicao_final_media_e_faixa():
    curva = pd.DataFrame({
        "valor_posicoes_acoes": [0.0, 100.0, 60.0, 50.0],
        "valor_posicoes_fiis": [0.0, 100.0, 140.0, 150.0],
        "caixa": [200.0, 0.0, 0.0, 20.0],
        "valor_carteira": [200.0, 200.0, 200.0, 220.0],
    })
    mix = sc.composicao(curva)
    assert mix["fiis_final_pct"] == pytest.approx(75.0) and mix["acoes_final_pct"] == pytest.approx(25.0)
    assert mix["fiis_media_pct"] == pytest.approx((50 + 70 + 75) / 3)       # mês só com caixa não entra
    assert mix["fiis_min_pct"] == pytest.approx(50.0) and mix["fiis_max_pct"] == pytest.approx(75.0)
    assert mix["caixa_final_pct"] == pytest.approx(20 / 220 * 100)


def test_composicao_sem_posicoes_devolve_none():
    curva = pd.DataFrame({"valor_posicoes_acoes": [0.0, 0.0], "valor_posicoes_fiis": [0.0, 0.0],
                          "caixa": [200.0, 400.0], "valor_carteira": [200.0, 400.0]})
    assert sc.composicao(curva) is None


# ---------- diagnóstico de exclusão ----------

def mercado_do_diagnostico():
    """Um ativo de cada tipo de exclusão, em preços planos (10,00) e 60 meses."""
    tickers = ["ITUB4.SA", "BBSE3.SA", "EGIE3.SA", "HGLG11.SA", "XPLG11.SA", "KNRI11.SA", "VISC11.SA", "MXRF11.SA"]
    raw, adj, div = mercado(tickers)
    div["ITUB4.SA"] = 0.05                                      # 6% a.a., estável: aprovado
    div["BBSE3.SA"] = 0.01                                      # 1,2%: abaixo do mínimo
    div["EGIE3.SA"] = 0.30                                      # 36%: acima do máximo
    div["HGLG11.SA"] = 0.125                                    # 15% e corte para ~5% no último ano
    div.loc[div.index[48]:, "HGLG11.SA"] = 0.04167
    div["XPLG11.SA"] = 0.0                                      # nunca pagou
    div["KNRI11.SA"] = 0.0
    div.loc[div.index[48]:, "KNRI11.SA"] = 0.05                 # só pagou no último ano
    div["VISC11.SA"] = 0.05
    raw.loc[:raw.index[39], "VISC11.SA"] = np.nan               # listado há 20 meses
    adj.loc[:adj.index[39], "VISC11.SA"] = np.nan
    div["MXRF11.SA"] = 0.05
    raw.loc[raw.index[-1], "MXRF11.SA"] = np.nan                # sem cotação no mês do sinal
    adj.loc[adj.index[-1], "MXRF11.SA"] = np.nan
    return raw, adj, div, tickers


def test_diagnostico_explica_cada_exclusao():
    raw, adj, div, tickers = mercado_do_diagnostico()
    sinais = sc.metricas_historicas(raw, div)
    diag = sc.diagnosticar(raw, adj, sinais, raw.index[-1], tickers + ["ABEV3.SA"]).set_index("ticker")
    assert sorted(diag.index) == sorted(tickers + ["ABEV3.SA"])                  # cada ativo uma vez
    assert diag.loc["ITUB4.SA", "situacao"] == "APROVADO" and diag.loc["ITUB4.SA", "motivo"] == ""
    assert diag.loc["ITUB4.SA", "posicao_ranking"] == 1 and diag.loc["ITUB4.SA", "score"] > 0
    assert "abaixo do mínimo de 3.0%" in diag.loc["BBSE3.SA", "motivo"]
    assert "acima do máximo de 18.0%" in diag.loc["EGIE3.SA", "motivo"]
    assert "caíram para 33%" in diag.loc["HGLG11.SA", "motivo"]
    assert diag.loc["HGLG11.SA", "classe"] == "FII" and diag.loc["ITUB4.SA", "classe"] == "ACAO"
    assert "abaixo do mínimo" in diag.loc["XPLG11.SA", "motivo"]
    assert "proventos em 0 de 3" in diag.loc["XPLG11.SA", "motivo"]              # dois motivos juntos
    assert "; " in diag.loc["XPLG11.SA", "motivo"]
    assert "proventos em 1 de 3" in diag.loc["KNRI11.SA", "motivo"]
    assert "histórico menor que 36 meses (20 meses de dados)" in diag.loc["VISC11.SA", "motivo"]
    assert diag.loc["VISC11.SA", "meses_de_historico"] == 20
    assert diag.loc["MXRF11.SA", "motivo"] == "sem cotação no mês do sinal"
    assert diag.loc["ABEV3.SA", "motivo"] == "sem dados do Yahoo (falha no download)"
    assert (diag.drop(index="ITUB4.SA").situacao == "EXCLUIDO").all()
    assert diag.loc["BBSE3.SA", "dy_12m_pct"] == pytest.approx(1.2)               # métricas aparecem mesmo excluído


def test_diagnostico_ordena_aprovados_primeiro_por_posicao():
    raw, adj, div, tickers = mercado_do_diagnostico()
    sinais = sc.metricas_historicas(raw, div)
    diag = sc.diagnosticar(raw, adj, sinais, raw.index[-1], tickers)
    assert diag.situacao.tolist() == ["APROVADO"] + ["EXCLUIDO"] * 7
    assert diag.columns.tolist() == sc.COLUNAS_DIAGNOSTICO


def test_motivo_de_falta_do_retorno_de_12_meses():
    raw, adj, div = mercado(["ITUB4.SA"], div_mensal=0.05)
    sinais = sc.metricas_historicas(raw, div)
    sinais["retorno_12m"].loc[raw.index[-1], "ITUB4.SA"] = np.nan
    motivos, _ = sc.motivos_exclusao(raw, adj, sinais, raw.index[-1], "ITUB4.SA")
    assert motivos == ["sem retorno de preço de 12 meses (falta de cotação)"]
    assert sc.selecionar(raw, adj, sinais, raw.index[-1], 5).empty


def mercado_aleatorio(seed=7, n_ativos=40, n_meses=100):
    """Muitas exclusões: yields fora da faixa, cortes, listagens tardias e buracos de cotação."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2016-01-31", periods=n_meses, freq="ME")
    raw, adj, div = {}, {}, {}
    for k in range(n_ativos):
        nome = f"T{k:02d}.SA"
        preco = 20 * np.exp(np.cumsum(rng.normal(0.003, 0.05, n_meses)))
        alvo, freq = rng.choice([0.0, 0.01, 0.035, 0.06, 0.09, 0.15, 0.4]), rng.choice([1, 3, 12])
        d = np.array([preco[i] * alvo / freq if i % (12 // freq) == 0 and rng.random() > 0.15 else 0.0
                      for i in range(n_meses)])
        if rng.random() < 0.35:
            d[rng.integers(40, 90):] *= 0.3
        inicio = rng.choice([0, 0, 20, 50])
        p = pd.Series(preco, index=idx)
        p.iloc[:inicio] = np.nan
        if rng.random() < 0.3:
            p.iloc[rng.integers(max(inicio, 1), n_meses, 3)] = np.nan
        raw[nome], adj[nome], div[nome] = p, p.copy(), pd.Series(d, index=idx)
    return pd.DataFrame(raw), pd.DataFrame(adj), pd.DataFrame(div)


def test_diagnostico_e_ranking_nunca_divergem_em_mercado_aleatorio():
    raw, adj, div = mercado_aleatorio()
    sinais = sc.metricas_historicas(raw, div)
    total_aprovados = total_excluidos = 0
    for data in raw.index[36::5]:
        ranking = sc.selecionar(raw, adj, sinais, data, 999)
        diag = sc.diagnosticar(raw, adj, sinais, data, list(raw.columns))
        aprovados = diag[diag.situacao == "APROVADO"]
        assert aprovados.ticker.tolist() == ranking.ticker.tolist()               # mesmos ativos, mesma ordem
        assert aprovados.posicao_ranking.tolist() == list(range(1, len(ranking) + 1))
        assert aprovados.score.tolist() == pytest.approx(ranking.score.tolist())
        excluidos = diag[diag.situacao == "EXCLUIDO"]
        assert (excluidos.motivo != "").all()                                      # todo excluído tem motivo
        assert len(aprovados) + len(excluidos) == raw.shape[1]
        total_aprovados += len(aprovados)
        total_excluidos += len(excluidos)
    assert total_aprovados > 0 and total_excluidos > total_aprovados               # o teste exercita exclusões de verdade


def test_formatar_diagnostico_resume_por_classe_e_lista_os_excluidos():
    raw, adj, div, tickers = mercado_do_diagnostico()
    sinais = sc.metricas_historicas(raw, div)
    data = raw.index[-1]
    diag = sc.diagnosticar(raw, adj, sinais, data, tickers)
    texto = "\n".join(sc.formatar_diagnostico(diag, data, n_exibidos=10))
    assert "DIAGNÓSTICO DE EXCLUSÃO (sinal de 2024-12)" in texto
    assert "Aprovados: 1 de 8 (ações: 1/3, FIIs: 0/5)" in texto
    assert "Excluídos (7):" in texto and "HGLG11.SA  FII " in texto and "BBSE3.SA" in texto
    assert "fora do ranking exibido" not in texto
    assert "ITUB4.SA (#1)" in "\n".join(sc.formatar_diagnostico(diag, data, n_exibidos=0))   # fora do que foi exibido


def test_formatar_diagnostico_sem_excluidos():
    raw, adj, div = mercado(["ITUB4.SA"], div_mensal=0.05)
    sinais = sc.metricas_historicas(raw, div)
    diag = sc.diagnosticar(raw, adj, sinais, raw.index[-1], ["ITUB4.SA"])
    assert "Nenhum ativo foi excluído." in sc.formatar_diagnostico(diag, raw.index[-1], 10)


# ---------- fluxo completo, offline ----------

class TickerFalso:
    def __init__(self, ticker):
        self.ticker = ticker

    def history(self, start, auto_adjust=False, actions=True, repair=True):
        rng = np.random.default_rng(zlib.crc32(self.ticker.encode()))
        idx = pd.bdate_range(start=start, end=pd.Timestamp.today().normalize(), tz="America/Sao_Paulo")
        close = 20 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, len(idx))))
        div = np.zeros(len(idx))
        meses = idx.tz_localize(None).to_period("M")
        for k in np.flatnonzero(~meses.duplicated()):
            if idx[k].month in (3, 6, 9, 12):
                div[k] = close[k] * 0.06 / 4
        tr = np.cumprod(1 + np.r_[0, (np.diff(close) + div[1:]) / close[:-1]])
        return pd.DataFrame({"Close": close, "Adj Close": tr * close[-1] / tr[-1], "Dividends": div}, index=idx)


def cdi_diario(inicio, fim, taxa_dia=0.0005):
    """Índice do CDI com a mesma taxa em todo dia útil (0,05% a.d. ~ 13,9% a.a.)."""
    dias = pd.bdate_range(inicio, fim)
    return pd.Series((1 + taxa_dia) ** np.arange(1, len(dias) + 1), index=dias)


def rodar_main(monkeypatch, tmp_path, *extra):
    monkeypatch.setattr(sc.yf, "Ticker", TickerFalso)
    monkeypatch.setattr(sc, "UNIVERSO", ["ITUB4.SA", "BBSE3.SA", "EGIE3.SA", "HGLG11.SA", "MXRF11.SA", "KNRI11.SA"])
    monkeypatch.setattr("sys.argv", ["scanner", "--anos", "8", "--top", "3", "--saida", str(tmp_path / "r"), *extra])
    sc.main()


def test_main_gera_os_csv_com_benchmarks(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sc, "baixar_cdi", cdi_diario)
    rodar_main(monkeypatch, tmp_path)
    curva = pd.read_csv(tmp_path / "r_backtest.csv")
    ops = pd.read_csv(tmp_path / "r_operacoes.csv")
    comparacao = pd.read_csv(tmp_path / "r_comparacao.csv")
    assert (tmp_path / "r_ranking.csv").exists()
    assert len(curva) > 12 and curva.total_aportado.iloc[-1] == 200 * len(curva)
    assert {"COMPRA_APORTE"} <= set(ops.tipo) and "observacao" in ops.columns
    assert comparacao.nome.tolist() == ["Estratégia", "CDI", "IBOV (BOVA11)", "IDIV (DIVO11)", "IFIX (XFIX11)", "Misto 50/50 (IBOV+IFIX)"]
    assert comparacao.xirr_aa_pct.notna().all() and (comparacao.max_drawdown_pct <= 0).all()
    assert {"valor_cdi", "valor_ibov_bova11", "valor_idiv_divo11", "valor_ifix_xfix11",
            "valor_misto_50_50_ibov_ifix"} <= set(curva.columns)
    assert comparacao.total_aportado.eq(curva.total_aportado.iloc[-1]).all()
    saida = capsys.readouterr().out
    assert "RESULTADO DO BACKTEST" in saida and "XIRR da estratégia" in saida and "COMPARAÇÃO COM BENCHMARKS" in saida
    assert "Maior drawdown (índice de retorno" in saida
    assert "COMPOSIÇÃO DA CARTEIRA" in saida and "FIIs" in saida
    assert {"valor_posicoes_acoes", "valor_posicoes_fiis"} <= set(curva.columns)
    diagnostico = pd.read_csv(tmp_path / "r_diagnostico.csv")
    assert len(diagnostico) == 6 and diagnostico.columns.tolist() == sc.COLUNAS_DIAGNOSTICO
    assert "DIAGNÓSTICO DE EXCLUSÃO" in saida and "r_diagnostico.csv" in saida
    assert (curva.valor_posicoes_acoes + curva.valor_posicoes_fiis).to_numpy() == pytest.approx(curva.valor_posicoes.to_numpy())


def test_main_sem_benchmark_nao_baixa_nada_alem_do_universo(monkeypatch, tmp_path):
    def proibido(*a, **k):
        raise AssertionError("não deveria baixar o CDI com --sem-benchmark")
    monkeypatch.setattr(sc, "baixar_cdi", proibido)
    rodar_main(monkeypatch, tmp_path, "--sem-benchmark")
    comparacao = pd.read_csv(tmp_path / "r_comparacao.csv")
    assert comparacao.nome.tolist() == ["Estratégia"] and comparacao.xirr_aa_pct.notna().all()
    colunas = pd.read_csv(tmp_path / "r_backtest.csv").columns
    proprias = {"valor_carteira", "valor_posicoes", "valor_posicoes_acoes", "valor_posicoes_fiis"}
    assert [c for c in colunas if c.startswith("valor_") and c not in proprias] == []


def test_dependencia_ausente_para_na_hora_com_mensagem_clara(monkeypatch, capsys):
    chamadas = []

    class SemScipy:
        def __init__(self, ticker):
            chamadas.append(ticker)

        def history(self, **kwargs):
            raise ModuleNotFoundError("No module named 'scipy'")

    monkeypatch.setattr(sc.yf, "Ticker", SemScipy)
    with pytest.raises(RuntimeError, match="requirements.txt") as erro:
        sc.baixar_dados(["ITUB4.SA", "VALE3.SA", "WEGE3.SA"], "2020-01-01")
    assert "scipy" in str(erro.value)
    assert chamadas == ["ITUB4.SA"]                      # não insiste nos demais tickers
    assert "[AVISO]" not in capsys.readouterr().out


def test_falha_comum_de_um_ticker_continua_sendo_so_aviso(monkeypatch, capsys):
    class Instavel(TickerFalso):
        def history(self, **kwargs):
            if self.ticker == "VALE3.SA":
                raise ConnectionError("timeout")
            return super().history(**kwargs)

    monkeypatch.setattr(sc.yf, "Ticker", Instavel)
    _, adj, _ = sc.baixar_dados(["ITUB4.SA", "VALE3.SA"], "2024-01-01")
    assert list(adj.columns) == ["ITUB4.SA"]
    assert "Falha ao baixar VALE3.SA" in capsys.readouterr().out


# ---------- XIRR ----------

def test_xirr_valores_conhecidos():
    d0 = pd.Timestamp("2020-01-01")
    um_ano = d0 + pd.Timedelta(days=365)
    assert sc.xirr([(d0, -1000.0), (um_ano, 1100.0)]) == pytest.approx(0.10, abs=1e-9)
    # -100 em t0 e t1, +231 em t2: 10% a.a. (100 + 100/1,1 = 231/1,21)
    fluxos = [(d0, -100.0), (um_ano, -100.0), (d0 + pd.Timedelta(days=730), 231.0)]
    assert sc.xirr(fluxos) == pytest.approx(0.10, abs=1e-9)
    assert sc.xirr([(d0, -1000.0), (um_ano, 900.0)]) == pytest.approx(-0.10, abs=1e-9)   # perda


def test_xirr_sem_retorno_com_aportes_mensais_e_valor_igual_ao_aportado():
    datas = pd.date_range("2022-01-31", periods=24, freq="ME")
    fluxos = [(d, -200.0) for d in datas] + [(datas[-1], 200.0 * len(datas))]
    assert sc.xirr(fluxos) == pytest.approx(0.0, abs=1e-9)


def test_xirr_indefinido_sem_fluxo_dos_dois_sinais():
    d0 = pd.Timestamp("2020-01-01")
    assert np.isnan(sc.xirr([(d0, -100.0), (d0 + pd.Timedelta(days=30), -50.0)]))
    assert np.isnan(sc.xirr([(d0, -100.0), (d0 + pd.Timedelta(days=30), 0.0)]))   # perda total


# ---------- benchmarks ----------

def curva_sintetica(n=36, mensal=0.01, aporte=200.0):
    """Estratégia que rende exatamente `mensal` ao mês, sem custos, e o índice correspondente."""
    datas = pd.date_range("2022-01-31", periods=n, freq="ME")
    indice = pd.Series((1 + mensal) ** np.arange(n), index=datas)
    valores = sc.simular_aportes(indice, pd.Series(aporte, index=datas))
    curva = pd.DataFrame({"data": [d.date().isoformat() for d in datas], "aporte_mes": aporte,
                          "valor_carteira": valores.to_numpy()})
    return curva, indice


def test_simular_aportes_em_indice_plano_so_acumula_aportes_menos_custo():
    datas = pd.date_range("2022-01-31", periods=6, freq="ME")
    plano = pd.Series(5.0, index=datas)
    valores = sc.simular_aportes(plano, pd.Series(200.0, index=datas), custo=0.0015)
    assert valores.iloc[-1] == pytest.approx(6 * 200.0 / 1.0015)


def test_simular_aportes_recusa_indice_que_nao_cobre_o_periodo():
    datas = pd.date_range("2022-01-31", periods=6, freq="ME")
    curto = pd.Series(5.0, index=datas[2:])
    assert sc.simular_aportes(curto, pd.Series(200.0, index=datas)) is None


def test_baixar_cdi_divide_em_janelas_e_compoe_o_indice():
    chamadas = []

    class Resposta:
        def __init__(self, dados):
            self.corpo = json.dumps(dados).encode()
        def read(self):
            return self.corpo
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def abrir(pedido, timeout):
        q = parse_qs(urlparse(pedido.full_url).query)
        ini = pd.to_datetime(q["dataInicial"][0], format="%d/%m/%Y")
        fim = pd.to_datetime(q["dataFinal"][0], format="%d/%m/%Y")
        chamadas.append((ini, fim))
        return Resposta([{"data": d.strftime("%d/%m/%Y"), "valor": "0.050000"} for d in pd.bdate_range(ini, fim)])

    inicio, fim = pd.Timestamp("2018-01-02"), pd.Timestamp("2026-01-02")
    cdi = sc.baixar_cdi(inicio, fim, abrir=abrir)
    assert len(chamadas) == 2 and all(f - i < pd.Timedelta(days=5 * 366) for i, f in chamadas)
    assert cdi.index.is_monotonic_increasing and cdi.index.is_unique
    assert cdi.iloc[-1] == pytest.approx(1.0005 ** len(pd.bdate_range(inicio, fim)))


def test_resumo_da_estrategia_igual_ao_benchmark_tem_diferenca_zero(monkeypatch):
    curva, indice = curva_sintetica()
    monkeypatch.setattr(sc, "baixar_cdi", lambda ini, fim: cdi_diario(ini, fim))
    monkeypatch.setattr(sc, "CUSTO_TRADE", 0.0)
    ret = pd.DataFrame({t: indice for t in sc.BENCHMARKS_ETF.values()})
    monkeypatch.setattr(sc, "baixar_dados", lambda tickers, inicio: (ret, ret, ret))
    _, resumo = sc.avaliar_benchmarks(curva, "2022-01-01")
    assert resumo.nome.tolist() == ["Estratégia", "CDI", "IBOV (BOVA11)", "IDIV (DIVO11)", "IFIX (XFIX11)", "Misto 50/50 (IBOV+IFIX)"]
    assert resumo.xirr_aa_pct.iloc[0] == pytest.approx(12.68, abs=0.2)     # (1,01^12 - 1)
    assert resumo.xirr_aa_pct.iloc[2] == pytest.approx(resumo.xirr_aa_pct.iloc[0], abs=1e-6)
    assert resumo.diferenca_xirr_pp.iloc[2] == pytest.approx(0.0, abs=1e-6)
    assert np.isnan(resumo.diferenca_xirr_pp.iloc[0])
    assert 13.0 < resumo.xirr_aa_pct.iloc[1] < 14.6                         # CDI de 0,05% por dia útil


def test_diferenca_de_xirr_e_positiva_quando_a_estrategia_rende_mais(monkeypatch):
    curva, _ = curva_sintetica(mensal=0.02)
    _, referencia = curva_sintetica(mensal=0.01)
    ret = pd.DataFrame({t: referencia for t in sc.BENCHMARKS_ETF.values()})
    monkeypatch.setattr(sc, "baixar_dados", lambda tickers, inicio: (ret, ret, ret))
    monkeypatch.setattr(sc, "baixar_cdi", lambda ini, fim: cdi_diario(ini, fim))
    _, resumo = sc.avaliar_benchmarks(curva, "2022-01-01")
    ibov = resumo.set_index("nome").loc["IBOV (BOVA11)"]
    assert resumo.xirr_aa_pct.iloc[0] == pytest.approx(26.8, abs=0.5)             # 1,02^12 - 1
    assert ibov.diferenca_xirr_pp == pytest.approx(resumo.xirr_aa_pct.iloc[0] - ibov.xirr_aa_pct)
    assert ibov.diferenca_xirr_pp > 10


# ---------- drawdown sobre o índice de retorno ----------

def patrimonio_com_retornos(retornos, aporte=200.0):
    """Patrimônio de quem aporta no fim de cada mês e tem o retorno dado no mês."""
    valores, v = [], 0.0
    for r in retornos:
        v = v * (1 + r) + aporte
        valores.append(v)
    return pd.Series(valores), pd.Series(aporte, index=range(len(retornos)))


def test_drawdown_do_indice_de_retorno_nao_e_atenuado_pelos_aportes():
    valores, aportes = patrimonio_com_retornos([0.0] * 60 + [-0.30] + [0.0] * 5)
    assert sc.max_drawdown(sc.indice_retorno(valores, aportes)) == pytest.approx(-30.0)
    assert sc.max_drawdown(valores) > -29.0         # a métrica antiga subestimava a queda


def test_aportes_sozinhos_nao_geram_retorno_nem_drawdown():
    valores, aportes = patrimonio_com_retornos([0.0] * 24)
    indice = sc.indice_retorno(valores, aportes)
    assert (indice == 1.0).all() and sc.max_drawdown(indice) == 0.0


def test_indice_de_retorno_acumula_os_retornos_mensais():
    valores, aportes = patrimonio_com_retornos([0.0, 0.10, -0.05, 0.02])
    esperado = [1.0, 1.10, 1.10 * 0.95, 1.10 * 0.95 * 1.02]
    assert sc.indice_retorno(valores, aportes).tolist() == pytest.approx(esperado)


def test_indice_de_retorno_com_aportes_diferentes_em_cada_mes():
    # mês 1: 100 -> 100*1,10 + 500 = 610; mês 2: 610*0,90 + 50 = 599; mês 3: 599*1,05 + 0 = 628,95
    valores = pd.Series([100.0, 610.0, 599.0, 628.95])
    aportes = pd.Series([100.0, 500.0, 50.0, 0.0])
    assert sc.indice_retorno(valores, aportes).tolist() == pytest.approx([1.0, 1.10, 1.10 * 0.90, 1.10 * 0.90 * 1.05])


def test_indice_de_retorno_nao_divide_por_patrimonio_zero():
    indice = sc.indice_retorno(pd.Series([0.0, 300.0, 330.0]), pd.Series([0.0, 200.0, 0.0]))
    assert indice.tolist() == pytest.approx([1.0, 1.0, 1.10])


def test_resumo_inclui_drawdown_da_estrategia_e_dos_benchmarks(monkeypatch):
    datas = pd.date_range("2022-01-31", periods=8, freq="ME")
    indice = pd.Series([1.0, 1.02, 1.04, 0.832, 0.85, 0.88, 0.90, 0.95], index=datas)   # queda de 20% no 4º mês
    valores = sc.simular_aportes(indice, pd.Series(200.0, index=datas))
    curva = pd.DataFrame({"data": [d.date().isoformat() for d in datas], "aporte_mes": 200.0,
                          "valor_carteira": valores.to_numpy()})
    ret = pd.DataFrame({t: indice for t in sc.BENCHMARKS_ETF.values()})
    monkeypatch.setattr(sc, "baixar_dados", lambda tickers, inicio: (ret, ret, ret))
    monkeypatch.setattr(sc, "baixar_cdi", lambda ini, fim: cdi_diario(ini, fim))
    monkeypatch.setattr(sc, "CUSTO_TRADE", 0.0)
    _, resumo = sc.avaliar_benchmarks(curva, "2022-01-01")
    por_nome = resumo.set_index("nome").max_drawdown_pct
    assert por_nome["Estratégia"] == pytest.approx(-20.0)
    assert por_nome["IBOV (BOVA11)"] == pytest.approx(-20.0) and por_nome["IFIX (XFIX11)"] == pytest.approx(-20.0)
    assert por_nome["CDI"] == pytest.approx(0.0)        # CDI nunca cai


# ---------- benchmark misto ----------

def etfs_com(ibov, ifix):
    """Retornos de ETFs para o monkeypatch de baixar_dados: BOVA11/DIVO11 = ibov, XFIX11 = ifix."""
    ret = pd.DataFrame({"BOVA11.SA": ibov, "DIVO11.SA": ibov, "XFIX11.SA": ifix})
    return lambda tickers, inicio: (ret, ret, ret)


def test_misto_aplica_metade_de_cada_aporte_em_cada_componente(monkeypatch):
    curva, ibov = curva_sintetica(mensal=0.01)
    _, ifix = curva_sintetica(mensal=0.005)
    monkeypatch.setattr(sc, "baixar_dados", etfs_com(ibov, ifix))
    monkeypatch.setattr(sc, "baixar_cdi", lambda ini, fim: cdi_diario(ini, fim))
    monkeypatch.setattr(sc, "CUSTO_TRADE", 0.0)
    curva2, resumo = sc.avaliar_benchmarks(curva, "2022-01-01")
    datas = ibov.index
    metade = pd.Series(100.0, index=datas)                                       # 200 / 2
    esperado = sc.simular_aportes(ibov, metade) + sc.simular_aportes(ifix, metade)
    assert curva2["valor_misto_50_50_ibov_ifix"].to_numpy() == pytest.approx(esperado.to_numpy())
    assert curva2["valor_misto_50_50_ibov_ifix"].to_numpy() == pytest.approx(
        (0.5 * curva2["valor_ibov_bova11"] + 0.5 * curva2["valor_ifix_xfix11"]).to_numpy())
    xirr = resumo.set_index("nome").xirr_aa_pct
    assert xirr["IFIX (XFIX11)"] < xirr["Misto 50/50 (IBOV+IFIX)"] < xirr["IBOV (BOVA11)"]


def test_misto_diversifica_o_drawdown(monkeypatch):
    datas = pd.date_range("2022-01-31", periods=8, freq="ME")
    cai = pd.Series([1.0, 1.0, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8], index=datas)        # IBOV: -20% no 3º mês
    sobe = pd.Series([1.0, 1.0, 1.1, 1.1, 1.1, 1.1, 1.1, 1.1], index=datas)       # IFIX: +10% no mesmo mês
    curva = pd.DataFrame({"data": [d.date().isoformat() for d in datas], "aporte_mes": 200.0,
                          "valor_carteira": sc.simular_aportes(sobe, pd.Series(200.0, index=datas)).to_numpy()})
    monkeypatch.setattr(sc, "baixar_dados", etfs_com(cai, sobe))
    monkeypatch.setattr(sc, "baixar_cdi", lambda ini, fim: cdi_diario(ini, fim))
    monkeypatch.setattr(sc, "CUSTO_TRADE", 0.0)
    _, resumo = sc.avaliar_benchmarks(curva, "2022-01-01")
    queda = resumo.set_index("nome").max_drawdown_pct
    assert queda["IBOV (BOVA11)"] == pytest.approx(-20.0)
    assert -20.0 < queda["Misto 50/50 (IBOV+IFIX)"] < 0.0


def test_misto_e_ignorado_com_aviso_quando_falta_um_componente(monkeypatch, capsys):
    curva, indice = curva_sintetica()
    ret = pd.DataFrame({"BOVA11.SA": indice, "DIVO11.SA": indice})                # sem XFIX11
    monkeypatch.setattr(sc, "baixar_dados", lambda tickers, inicio: (ret, ret, ret))
    monkeypatch.setattr(sc, "baixar_cdi", lambda ini, fim: cdi_diario(ini, fim))
    curva2, resumo = sc.avaliar_benchmarks(curva, "2022-01-01")
    assert "Misto 50/50 (IBOV+IFIX)" not in resumo.nome.tolist()
    assert "valor_misto_50_50_ibov_ifix" not in curva2.columns
    saida = capsys.readouterr().out
    assert "Misto 50/50" in saida and "IFIX (XFIX11)" in saida and "ignorado" in saida


def test_pesos_do_misto_precisam_somar_1(monkeypatch):
    curva, indice = curva_sintetica()
    monkeypatch.setattr(sc, "baixar_dados", etfs_com(indice, indice))
    monkeypatch.setattr(sc, "baixar_cdi", lambda ini, fim: cdi_diario(ini, fim))
    monkeypatch.setattr(sc, "BENCHMARKS_MISTOS", {"Errado": {"IBOV (BOVA11)": 0.5, "IFIX (XFIX11)": 0.6}})
    with pytest.raises(ValueError, match="somar 1"):
        sc.avaliar_benchmarks(curva, "2022-01-01")


def test_benchmark_com_historico_curto_e_falha_do_cdi_viram_aviso(monkeypatch, capsys):
    curva, indice = curva_sintetica()
    curto = indice.copy()
    curto.iloc[:10] = np.nan                                                # ETF listado depois do início
    ret = pd.DataFrame({"BOVA11.SA": indice, "DIVO11.SA": curto})   # sem XFIX11: ticker não encontrado no Yahoo
    monkeypatch.setattr(sc, "baixar_dados", lambda tickers, inicio: (ret, ret, ret))

    def cdi_fora_do_ar(inicio, fim):
        raise ConnectionError("403")
    monkeypatch.setattr(sc, "baixar_cdi", cdi_fora_do_ar)
    curva2, resumo = sc.avaliar_benchmarks(curva, "2022-01-01")
    saida = capsys.readouterr().out
    assert resumo.nome.tolist() == ["Estratégia", "IBOV (BOVA11)"]
    assert "CDI" in saida and "IDIV (DIVO11)" in saida and "ignorado" in saida
    assert "valor_ibov_bova11" in curva2.columns and "valor_cdi" not in curva2.columns


def test_mes_em_andamento_usa_hoje_como_data_do_ultimo_fluxo():
    hoje = pd.Timestamp.today().normalize()
    datas = [hoje + pd.Timedelta(days=60) - pd.Timedelta(days=30 * k) for k in range(11, -1, -1)]   # último rótulo no futuro
    curva = pd.DataFrame({"data": [d.date().isoformat() for d in datas], "aporte_mes": 200.0,
                          "valor_carteira": np.linspace(200.0, 2600.0, 12)})
    _, resumo = sc.avaliar_benchmarks(curva, "2022-01-01", usar_benchmarks=False)
    reais = datas[:-1] + [hoje]
    esperado = sc.xirr([(d, -200.0) for d in reais] + [(hoje, 2600.0)]) * 100
    assert resumo.xirr_aa_pct.iloc[0] == pytest.approx(esperado)
