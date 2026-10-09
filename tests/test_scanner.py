"""Testes offline do scanner: dados sintéticos, sem acesso ao Yahoo Finance."""
import zlib

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


def test_main_gera_os_tres_csv(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sc.yf, "Ticker", TickerFalso)
    monkeypatch.setattr(sc, "UNIVERSO", ["ITUB4.SA", "BBSE3.SA", "EGIE3.SA", "HGLG11.SA", "MXRF11.SA", "KNRI11.SA"])
    monkeypatch.setattr("sys.argv", ["scanner", "--anos", "8", "--top", "3", "--saida", str(tmp_path / "r")])
    sc.main()
    curva = pd.read_csv(tmp_path / "r_backtest.csv")
    ops = pd.read_csv(tmp_path / "r_operacoes.csv")
    assert (tmp_path / "r_ranking.csv").exists()
    assert len(curva) > 12 and curva.total_aportado.iloc[-1] == 200 * len(curva)
    assert {"COMPRA_APORTE"} <= set(ops.tipo) and "observacao" in ops.columns
    assert "RESULTADO DO BACKTEST" in capsys.readouterr().out
