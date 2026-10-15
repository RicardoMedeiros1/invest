"""Apostas: ativos em que o preço balança muito, com os números de risco do último ano (só o que já aconteceu)."""
import json

import numpy as np
import pandas as pd
import pytest

import apostas as ap
import carteira_simples as cs
import painel_pagina
import scanner_dividendos_b3 as sc
from test_carteira_simples import painel_tabela_e_fechamentos, rodar_painel
from test_pagina import AGORA, CONFIG
from test_scanner import TickerFalso


def serie(valores, fim="2026-10-15"):
    return pd.Series(valores, index=pd.bdate_range(end=fim, periods=len(valores)), dtype=float)


# ---------- as contas ----------

def test_oscilacao_de_retornos_conhecidos():
    n = 200
    passos = np.where(np.arange(n) % 2 == 0, 0.01, -0.01)                       # +1% e -1% em dias alternados
    f = serie(100 * np.exp(np.r_[0, np.cumsum(passos)]))
    m = ap.metricas(f)
    assert m["oscilacao"] == pytest.approx(0.01 * np.sqrt(252) * 100, abs=0.3)   # ~15,9% ao ano
    assert ap.metricas(serie(np.full(200, 50.0)))["oscilacao"] == 0.0            # preço parado não oscila


def test_pior_queda_e_distancia_do_topo():
    m = ap.metricas(serie([100, 200, 100, 150]))
    assert m["queda12"] == -50.0 and m["topo"] == -25.0
    assert m["oscilacao"] is None                                                # poucos pregões para medir a oscilação
    assert ap.metricas(serie([100, 120, 150]))["queda12"] == 0.0 and ap.metricas(serie([100, 120, 150]))["topo"] == 0.0


def test_so_o_ultimo_ano_conta():
    antigo = np.r_[np.full(100, 1000.0), np.full(252, 100.0)]                   # o pico de 1000 ficou para trás do último ano
    m = ap.metricas(serie(antigo))
    assert m["queda12"] == 0.0 and m["topo"] == 0.0


def test_historico_curto_vira_vazio_em_vez_de_inventar():
    assert ap.metricas(serie([5.0])) == {"oscilacao": None, "queda12": None, "topo": None, "liquidez": None}
    assert ap.metricas(serie([]))["topo"] is None


def test_liquidez_e_a_mediana_do_valor_negociado():
    f = serie(np.full(100, 50.0))
    v = serie(np.full(100, 10_000.0))
    v.iloc[-1] = 10_000_000.0                                                   # um dia fora do normal não muda a mediana
    assert ap.metricas(f, v)["liquidez"] == 500_000.0
    assert ap.metricas(f)["liquidez"] is None and ap.metricas(f, serie([np.nan] * 100))["liquidez"] is None


def test_serie_do_ano_termina_no_preco_de_hoje_e_e_curta():
    f = serie(np.arange(1, 401, dtype=float))
    s = ap.serie_do_ano(f)
    assert s[-1] == 400.0 and len(s) == 63 and s == sorted(s)
    assert ap.serie_do_ano(serie([3.0, 4.0])) == [4.0]                          # série curta: só o preço de agora


def test_lista_de_apostas_e_valida_e_nao_repete_o_scanner():
    tickers = [t for t, _, _ in ap.APOSTAS]
    assert len(tickers) == len(set(tickers)) and all(t.endswith(".SA") for t in tickers)
    assert {tipo for _, tipo, _ in ap.APOSTAS} <= {"ação", "ETF", "FII"} and all(setor for _, _, setor in ap.APOSTAS)
    assert not set(tickers) & set(sc.UNIVERSO)                                  # quem está no scanner não é aposta


# ---------- baixar e montar ----------

def test_montar_apostas_tira_o_sufixo_ignora_quem_nao_veio_e_nao_deixa_nan(monkeypatch):
    monkeypatch.setattr(ap, "APOSTAS", [("AAA3.SA", "ação", "varejo"), ("BBB11.SA", "ETF", "ouro"), ("CCC3.SA", "ação", "x")])
    f = serie(np.linspace(10, 20, 300))
    lista = cs.montar_apostas({"AAA3.SA": f, "BBB11.SA": serie([5.0])}, {"AAA3.SA": serie(np.full(300, 1000.0))})
    assert [a["t"] for a in lista] == ["AAA3"]                                  # BBB11 tem um pregão só; CCC3 nem veio
    a = lista[0]
    assert a["classe"] == "ação" and a["setor"] == "varejo" and a["preco"] == 20.0 and a["ano"] > 0
    assert a["liquidez"] == pytest.approx(median_financeiro(f), abs=1)      # arredondado para reais inteiros and len(a["serie"]) == 63
    painel_pagina.dados_json({"apostas": lista})                                # sem NaN


def median_financeiro(f):
    return float((f * 1000.0).tail(60).median())


def test_baixar_apostas_nao_inclui_quem_ja_esta_no_scanner(monkeypatch):
    chamados = []

    class Registra(TickerFalso):
        def __init__(self, ticker):
            super().__init__(ticker)
            chamados.append(ticker)

    monkeypatch.setattr(cs.yf, "Ticker", Registra)
    monkeypatch.setattr(sc, "UNIVERSO", ["MGLU3.SA"])                           # fingindo que o scanner já cobre a MGLU3
    lista = cs.baixar_apostas()
    assert "MGLU3.SA" not in chamados and "MGLU3" not in [a["t"] for a in lista]
    assert len(lista) == len(ap.APOSTAS) - 1 and all(a["oscilacao"] is not None and a["liquidez"] is not None for a in lista)


def test_apostas_sao_opcionais(monkeypatch, capsys):
    class Falha:
        def __init__(self, ticker): pass
        def history(self, **kwargs): raise ConnectionError("fora do ar")

    monkeypatch.setattr(cs.yf, "Ticker", Falha)
    assert cs.baixar_apostas() == [] and "[AVISO] Sem a seção de apostas" in capsys.readouterr().out


def test_baixar_diario_so_devolve_volume_quando_pedem(monkeypatch):
    monkeypatch.setattr(cs.yf, "Ticker", TickerFalso)
    volumes = {}
    fech, _ = cs.baixar_diario(["PETR4.SA"], volumes=volumes)
    assert list(volumes) == ["PETR4.SA"] and volumes["PETR4.SA"].index.equals(fech["PETR4.SA"].index) and volumes["PETR4.SA"].gt(0).all()
    cs.baixar_diario(["PETR4.SA"])                                              # sem pedir: funciona como antes


# ---------- na página e na planilha ----------

def lista_de_teste():
    f = serie(np.linspace(10, 30, 300))
    return cs.montar_apostas({"MGLU3.SA": f, "HASH11.SA": f * 2}, {"MGLU3.SA": serie(np.full(300, 50_000.0))})


def test_dados_levam_as_apostas_e_o_limite_de_liquidez():
    painel, tabela, fech = painel_tabela_e_fechamentos()
    d = cs.dados_do_painel(painel, tabela, fech, [], AGORA, CONFIG, lista_apostas=lista_de_teste())
    assert [a["t"] for a in d["apostas"]] == ["MGLU3", "HASH11"] and d["liquidez_baixa"] == ap.LIQUIDEZ_BAIXA
    assert json.loads(painel_pagina.dados_json(d)) == d
    sem = cs.dados_do_painel(painel, tabela, fech, [], AGORA, CONFIG)
    assert sem["apostas"] == []                                                 # sem dados do Yahoo: a página mostra o aviso


def test_aba_de_apostas_na_planilha_so_existe_com_dados():
    painel, tabela, _ = painel_tabela_e_fechamentos()
    com = cs.abas_do_painel(painel, tabela, lista_de_teste())
    assert list(com["Apostas"]["Ativo"]) == ["MGLU3", "HASH11"] and "Oscilação ao ano (%)" in com["Apostas"].columns
    assert "Apostas" not in cs.abas_do_painel(painel, tabela) and "Apostas" not in cs.abas_do_painel(painel, tabela, [])


def test_main_publica_as_apostas_na_pagina_nos_dados_e_na_planilha(monkeypatch, tmp_path):
    rodar_painel(monkeypatch, tmp_path, "--sem-abrir", "--ativos", "3", "--dados-json", "dados.json")
    dados = json.loads((tmp_path / "dados.json").read_text(encoding="utf-8"))
    assert len(dados["apostas"]) == len(ap.APOSTAS) and dados["apostas"][0]["serie"]
    assert '<section id="apostas"' in (tmp_path / "p.html").read_text(encoding="utf-8")
    abas = pd.read_excel(tmp_path / "p.xlsx", sheet_name=None)
    assert len(abas["Apostas"]) == len(ap.APOSTAS) and pd.ExcelFile(tmp_path / "p.xlsx").sheet_names[:4] == [
        "Melhores agora", "Quanto subiu", "Plano de compra", "Apostas"]


def test_o_limite_de_pouca_liquidez_bate_com_o_texto_da_pagina():
    modelo = (painel_pagina.PASTA_WEB / "modelo.html").read_text(encoding="utf-8")
    assert ap.LIQUIDEZ_BAIXA == 1_000_000 and "menos de R$ 1 milhão por dia" in modelo


def test_pagina_avisa_que_apostas_nao_sao_previsao_nem_recomendacao():
    modelo = (painel_pagina.PASTA_WEB / "modelo.html").read_text(encoding="utf-8")
    trecho = modelo[modelo.index('<section id="apostas"'):modelo.index('<section id="passado"')]
    for aviso in ("já aconteceu", "não passam pelo filtro de dividendos", "não entram no plano de compra", "Não é recomendação de compra",
                  "dinheiro que você aceita perder"):
        assert aviso in trecho
