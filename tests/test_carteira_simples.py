"""Testes offline do painel simples: dados sintéticos, sem acesso ao Yahoo Finance."""
import numpy as np
import pandas as pd
import pytest

import carteira_simples as cs
import corretoras
import scanner_dividendos_b3 as sc
from test_scanner import TickerFalso, cdi_diario, mercado, ranking_fixo, rodar_main

HOJE = pd.Timestamp("2026-10-15")


def serie_diaria(preco=100.0, dias=400, fim=HOJE):
    idx = pd.bdate_range(end=fim, periods=dias)
    return pd.Series(preco, index=idx)


# ---------- formatação ----------

def test_formatacao_em_portugues():
    assert cs.brl(1234.5) == "R$ 1.234,50" and cs.brl(0) == "R$ 0,00" and cs.brl(196.65) == "R$ 196,65"
    assert cs.pct(7.46) == "7,5%" and cs.pct(2.1, True) == "+2,1%" and cs.pct(-3.04, True) == "-3,0%"
    assert cs.pct(np.nan) == "n/d" and cs.pct(None) == "n/d"


def test_nome_tira_o_sufixo_do_yahoo():
    assert cs.nome("PETR4.SA") == "PETR4" and cs.nome("KNRI11.SA") == "KNRI11" and cs.nome("PETR4") == "PETR4"


# ---------- variações e dividendos ----------

def test_variacoes_no_pregao_no_mes_e_em_12_meses():
    f = serie_diaria()
    f.iloc[-1] = 110.0                                  # hoje
    f.iloc[-3] = 80.0                                   # o pregão antes do anterior: não pode ser a base do "dia"
    f.loc["2026-09-15"] = 88.0                          # 1 mês antes
    f.loc["2025-10-15"] = 55.0                          # 12 meses antes
    v = cs.variacoes(f)
    assert v["dia"] == pytest.approx(10.0) and v["mes"] == pytest.approx(25.0) and v["ano"] == pytest.approx(100.0)


def test_variacao_usa_o_ultimo_pregao_quando_a_data_de_referencia_cai_no_fim_de_semana():
    f = serie_diaria(100.0, fim=pd.Timestamp("2026-10-12"))      # segunda-feira: 1 mês antes é sábado, 12/09
    f.loc["2026-09-11"] = 50.0                                   # sexta anterior
    assert cs.variacoes(f)["mes"] == pytest.approx(100.0)        # 100 / 50: usa o último fechamento até a data


def test_variacao_sem_historico_suficiente_vira_nd():
    curta = serie_diaria(dias=40)                       # não tem 12 meses
    v = cs.variacoes(curta)
    assert not np.isnan(v["dia"]) and not np.isnan(v["mes"]) and np.isnan(v["ano"])
    assert all(np.isnan(x) for x in cs.variacoes(serie_diaria(dias=1)).values())


def test_dividendos_sem_nenhum_pagamento_valem_zero():
    assert cs.dividendos_12m(pd.Series(dtype=float), HOJE) == 0.0
    assert cs.dividendos_12m(pd.Series(0.0, index=pd.bdate_range(end=HOJE, periods=50)), HOJE) == 0.0


def test_dividendos_de_12_meses_contam_so_a_janela():
    idx = pd.to_datetime(["2025-09-30", "2025-10-16", "2026-03-10", "2026-10-15", "2026-10-16"])
    prov = pd.Series([9.0, 1.0, 2.0, 3.0, 99.0], index=idx)       # fora, dentro, dentro, dentro (no limite), depois
    assert cs.dividendos_12m(prov, HOJE) == pytest.approx(6.0)


# ---------- plano de compra ----------

PRECOS = {"PETR4.SA": 49.12, "CXSE3.SA": 19.88, "ABEV3.SA": 15.36, "WEGE3.SA": 49.46, "ISAE4.SA": 27.59,
          "KNRI11.SA": 157.01, "HGLG11.SA": 147.95}


def test_plano_do_primeiro_mes_com_200_reais_so_com_acoes():
    ordem = ["PETR4.SA", "CXSE3.SA", "ABEV3.SA", "WEGE3.SA", "ISAE4.SA"]
    m1 = cs.plano_de_compras(PRECOS, ordem, 200.0, 1)[0]
    assert m1["compras"] == {"PETR4.SA": 1, "CXSE3.SA": 2, "ABEV3.SA": 2, "WEGE3.SA": 1, "ISAE4.SA": 1}
    assert m1["gasto"] == pytest.approx(196.65) and m1["sobra"] == pytest.approx(3.35)


def test_plano_guarda_o_caixa_ate_caber_o_fii_caro():
    ordem = ["PETR4.SA", "CXSE3.SA", "ABEV3.SA", "KNRI11.SA", "HGLG11.SA"]
    m1, m2 = cs.plano_de_compras(PRECOS, ordem, 200.0, 2)
    assert m1["compras"] == {"PETR4.SA": 1, "CXSE3.SA": 1, "ABEV3.SA": 1}                  # o FII de R$ 157 ainda não cabe
    assert m1["sobra"] == pytest.approx(115.64)
    assert m2["compras"] == {"KNRI11.SA": 1, "HGLG11.SA": 1} and m2["sobra"] == pytest.approx(10.68)


def test_plano_nunca_gasta_mais_que_o_caixa_e_fecha_a_conta():
    rng = np.random.default_rng(3)
    for _ in range(40):
        ordem = [f"T{i}.SA" for i in range(rng.integers(1, 7))]
        precos = {t: float(rng.uniform(3, 220)) for t in ordem}
        valor, meses = float(rng.choice([50, 200, 500])), int(rng.integers(1, 7))
        plano = cs.plano_de_compras(precos, ordem, valor, meses)
        assert all(m["sobra"] >= -1e-9 for m in plano)
        assert sum(m["gasto"] for m in plano) + plano[-1]["sobra"] == pytest.approx(valor * meses)
        for m in plano:
            assert m["gasto"] == pytest.approx(sum(precos[t] * n for t, n in m["compras"].items()))


def test_plano_equilibra_o_dinheiro_entre_os_ativos():
    ordem = ["PETR4.SA", "CXSE3.SA"]
    plano = cs.plano_de_compras(PRECOS, ordem, 1000.0, 3)
    investido = {t: sum(m["compras"].get(t, 0) for m in plano) * PRECOS[t] for t in ordem}
    assert abs(investido["PETR4.SA"] - investido["CXSE3.SA"]) <= max(PRECOS[t] for t in ordem)   # diferença menor que 1 unidade


def test_plano_sem_ativos_e_vazio():
    assert cs.plano_de_compras({}, [], 200.0, 3) == []


def test_estimativa_de_dividendos():
    assert cs.estimativa_dividendos([6.0, 8.0], 2400.0) == pytest.approx(168.0)
    assert cs.estimativa_dividendos([np.nan], 2400.0) == 0.0 and cs.estimativa_dividendos([], 2400.0) == 0.0


# ---------- tabela e listas ----------

def fechamentos_de_teste():
    precos = {"PETR4.SA": 49.12, "HGLG11.SA": 147.95, "VALE3.SA": 69.91}
    fech, prov = {}, {}
    for t, p in precos.items():
        f = serie_diaria(p)
        prov[t] = pd.Series([p * 0.07], index=[pd.Timestamp("2026-03-10")])        # 7% de dividendos no ano
        fech[t] = f
    fech["VALE3.SA"].iloc[-1] = 90.0                                              # VALE3 disparou hoje, mas não foi aprovada
    return fech, prov


def ranking_de_teste():
    return pd.DataFrame({"ticker": ["PETR4.SA", "HGLG11.SA"], "preco_referencia": 1.0, "dy_12m_pct": 7.0,
                         "anos_com_div_ult_3": 3, "retorno_preco_12m_pct": 5.0, "score": [0.95, 0.80]})


def test_tabela_junta_preco_dividendos_aprovacao_e_cotas():
    fech, prov = fechamentos_de_teste()
    t = cs.montar_tabela(fech, prov, ranking_de_teste(), 200.0).set_index("ticker")
    assert cs.COLUNAS_TABELA[0] == "ticker" and list(cs.montar_tabela(fech, prov, ranking_de_teste(), 200.0).columns) == cs.COLUNAS_TABELA
    assert t.loc["PETR4.SA", "classe"] == "ação" and t.loc["HGLG11.SA", "classe"] == "FII"
    assert t.loc["PETR4.SA", "dy_12m_pct"] == pytest.approx(7.0) and t.loc["PETR4.SA", "dividendo_12m"] == pytest.approx(49.12 * 0.07)
    assert t.loc["PETR4.SA", "cotas"] == 4 and t.loc["PETR4.SA", "custo"] == pytest.approx(4 * 49.12)
    assert t.loc["HGLG11.SA", "cotas"] == 1 and t.loc["VALE3.SA", "cotas"] == 2
    assert bool(t.loc["PETR4.SA", "aprovado"]) and not bool(t.loc["VALE3.SA", "aprovado"])
    assert t.loc["PETR4.SA", "posicao"] == 1 and t.loc["HGLG11.SA", "posicao"] == 2


def test_cota_zero_quando_a_unidade_custa_mais_que_o_valor():
    fech = {"HGLG11.SA": serie_diaria(250.0)}
    t = cs.montar_tabela(fech, {"HGLG11.SA": pd.Series(dtype=float)}, ranking_de_teste(), 200.0)
    assert t.cotas.iloc[0] == 0 and t.custo.iloc[0] == 0
    assert cs._unidades_texto(next(t.itertuples()), 200.0) == "não cabe (1 custa R$ 250,00)"


def test_melhores_por_periodo_so_entre_aprovados_e_ignora_sem_dado():
    fech, prov = fechamentos_de_teste()
    t = cs.montar_tabela(fech, prov, ranking_de_teste(), 200.0)
    t.loc[t.ticker == "HGLG11.SA", "dia_pct"] = np.nan
    melhores = cs.melhores_por_periodo(t, "dia_pct")
    assert melhores.ticker.tolist() == ["PETR4.SA"]                               # VALE3 subiu mais, mas não passou nos filtros
    assert cs.melhores_por_classe(t, "FII", 3).ticker.tolist() == ["HGLG11.SA"]
    assert cs.melhores_por_classe(t, "ação", 3).ticker.tolist() == ["PETR4.SA"]


def test_melhores_por_periodo_ordena_do_que_mais_subiu():
    t = pd.DataFrame({"ticker": list("ABCD"), "aprovado": True, "dia_pct": [1.0, 3.0, -2.0, 2.0]})
    assert cs.melhores_por_periodo(t, "dia_pct", 3).ticker.tolist() == ["B", "D", "A"]


# ---------- o passado ----------

def test_escolhidas_por_mes_exclui_o_mes_atual_e_respeita_a_cota(monkeypatch):
    ranking = ["PETR4.SA", "VALE3.SA", "ITUB4.SA", "ABEV3.SA", "WEGE3.SA", "HGLG11.SA", "KNRI11.SA"]
    raw, adj, div = mercado(ranking, n=60)
    monkeypatch.setattr(sc, "selecionar", ranking_fixo(lambda i: ranking))
    sinais = sc.metricas_historicas(raw, div)
    hist = cs.escolhidas_por_mes(raw, adj, sinais, 5, 2)
    assert raw.index[-1] not in hist and raw.index[-2] in hist and len(hist) == 59
    assert hist[raw.index[-2]] == ["PETR4.SA", "VALE3.SA", "ITUB4.SA", "HGLG11.SA", "KNRI11.SA"]
    sem_fii = cs.escolhidas_por_mes(raw, adj, sinais, 5, 0)
    assert sem_fii[raw.index[-2]] == ranking[:5]


def test_meses_sem_aprovados_ficam_fora_do_passado(monkeypatch):
    raw, adj, div = mercado(["PETR4.SA"], n=50)
    monkeypatch.setattr(sc, "selecionar", ranking_fixo(lambda i: ["PETR4.SA"] if i >= 40 else []))
    hist = cs.escolhidas_por_mes(raw, adj, sc.metricas_historicas(raw, div), 3, 0)
    assert min(hist) == raw.index[40]


def test_resumo_por_ano_conta_meses_e_desempata_por_nome():
    h = {pd.Timestamp("2024-11-30"): ["B.SA", "C.SA"], pd.Timestamp("2024-12-31"): ["B.SA", "A.SA"],
         pd.Timestamp("2025-01-31"): ["C.SA"]}
    r = cs.resumo_por_ano(h, n=2)
    assert list(r) == [2024, 2025]
    assert r[2024] == {"meses": 2, "top": [("B.SA", 2), ("A.SA", 1)]}              # empate entre A e C: alfabética, não a ordem de entrada
    assert r[2025] == {"meses": 1, "top": [("C.SA", 1)]}


# ---------- painel em texto e em HTML ----------

def painel_de_teste():
    return painel_e_tabela()[0]


def painel_e_tabela():
    ordem = ["PETR4.SA", "CXSE3.SA", "ABEV3.SA", "WEGE3.SA", "ISAE4.SA"]
    precos = {"PETR4.SA": 49.12, "CXSE3.SA": 19.88, "ABEV3.SA": 15.36, "WEGE3.SA": 49.46, "ISAE4.SA": 27.59}
    fech = {t: serie_diaria(p) for t, p in precos.items()}
    prov = {t: pd.Series([p * 0.07], index=[pd.Timestamp("2026-03-10")]) for t, p in precos.items()}
    ranking = pd.DataFrame({"ticker": ordem, "preco_referencia": 1.0, "dy_12m_pct": 7.0, "anos_com_div_ult_3": 3,
                            "retorno_preco_12m_pct": 5.0, "score": np.linspace(0.95, 0.85, 5)})
    tabela = cs.montar_tabela(fech, prov, ranking, 200.0)
    hist = {pd.Timestamp("2026-08-31"): ordem, pd.Timestamp("2026-09-30"): ordem}
    return cs.montar_painel(tabela, ordem, hist, 200.0, 2, HOJE), tabela


def test_texto_do_painel_traz_as_secoes_e_o_plano_em_reais():
    texto = cs.texto_painel(painel_de_teste())
    for secao in ("AS MELHORES AGORA (preços de 15/10/2026", "QUANTO CADA UM SUBIU", "PLANO DOS PRIMEIROS 2 MESES",
                  "AS MELHORES NO PASSADO", "CUSTOS DAS CORRETORAS"):
        assert secao in texto
    assert "Mês 1: 1 PETR4 + 2 CXSE3 + 2 ABEV3 + 1 WEGE3 + 1 ISAE4  =  R$ 196,65  (sobra R$ 3,35)" in texto
    assert "cerca de R$ 168,00 por ano (R$ 14,00 por mês)" in texto and "não uma promessa" in texto
    assert "Usa os preços de hoje nos 2 meses. O gasto de um mês pode passar de R$ 200,00 porque inclui a sobra" in texto
    assert "R$ 200,00 compram: 4 (R$ 196,48)" in texto                                # PETR4 a R$ 49,12
    assert ".SA" not in texto and "2026-09:" in texto and "Nubank" in texto


def test_html_do_painel_e_autonomo_e_nao_mostra_o_sufixo_do_yahoo():
    pagina = cs.html_painel(painel_de_teste())
    for titulo in ("As melhores agora", "Quanto cada um subiu", "Plano dos primeiros 2 meses", "As melhores no passado", "Custos das corretoras"):
        assert f"<h2>{titulo}</h2>" in pagina
    assert pagina.startswith("<!doctype html>") and "<script" not in pagina and "http" in pagina   # links das fontes
    assert ".SA" not in pagina and "PETR4" in pagina and "R$ 196,65" in pagina
    assert "pode passar de R$ 200,00 porque inclui a sobra dos meses anteriores" in pagina


def test_html_escapa_textos_de_fora(monkeypatch):
    monkeypatch.setattr(cs, "CORRETORAS", [{"nome": "<script>alert(1)</script>", "corretagem": "a&b", "custodia": "c",
                                            "atencao": "d", "fonte": "https://x.test/?a=1&b=2"}])
    pagina = cs.html_painel(painel_de_teste())
    assert "<script>alert" not in pagina and "&lt;script&gt;" in pagina and "a&amp;b" in pagina and "a=1&amp;b=2" in pagina


def test_quadro_de_corretoras_tem_fonte_e_avisos():
    assert corretoras.PESQUISADO_EM and len(corretoras.CORRETORAS) >= 4
    for c in corretoras.CORRETORAS:
        assert {"nome", "corretagem", "custodia", "atencao", "fonte"} <= set(c) and c["fonte"].startswith("https://")
    assert "FGC" in corretoras.AVISO and "mesmo em qualquer corretora" in corretoras.AVISO
    assert any("confirme" in c["custodia"] for c in corretoras.CORRETORAS)           # onde a fonte não é oficial, o texto avisa


# ---------- download diário e fluxo completo ----------

def test_baixar_diario_ignora_ticker_que_falha_e_tira_o_fuso(monkeypatch, capsys):
    class Instavel(TickerFalso):
        def history(self, **kwargs):
            if self.ticker == "VALE3.SA":
                raise ConnectionError("timeout")
            return super().history(**kwargs)

    monkeypatch.setattr(cs.yf, "Ticker", Instavel)
    fech, prov = cs.baixar_diario(["ITUB4.SA", "VALE3.SA"], dias=120)
    assert list(fech) == ["ITUB4.SA"] and fech["ITUB4.SA"].index.tz is None
    assert "Falha ao baixar preços diários de VALE3.SA" in capsys.readouterr().out and prov["ITUB4.SA"].sum() > 0


def test_baixar_diario_sem_nenhum_preco_e_dependencia_ausente(monkeypatch):
    class Falha:
        def __init__(self, ticker): pass
        def history(self, **kwargs): raise ConnectionError("fora do ar")

    monkeypatch.setattr(cs.yf, "Ticker", Falha)
    with pytest.raises(RuntimeError, match="Nenhum preço diário"):
        cs.baixar_diario(["ITUB4.SA"])

    class SemScipy:
        def __init__(self, ticker): pass
        def history(self, **kwargs): raise ModuleNotFoundError("No module named 'scipy'")

    monkeypatch.setattr(cs.yf, "Ticker", SemScipy)
    with pytest.raises(RuntimeError, match="requirements.txt"):
        cs.baixar_diario(["ITUB4.SA"])


UNIVERSO_TESTE = ["ITUB4.SA", "BBSE3.SA", "EGIE3.SA", "HGLG11.SA", "MXRF11.SA", "KNRI11.SA"]


def rodar_painel(monkeypatch, tmp_path, *extra):
    monkeypatch.setattr(cs.yf, "Ticker", TickerFalso)
    monkeypatch.setattr(sc, "UNIVERSO", UNIVERSO_TESTE)
    monkeypatch.setattr("sys.argv", ["carteira_simples", "--saida", str(tmp_path / "p"), *extra])
    cs.main()


def test_main_atualiza_a_planilha_e_o_html_e_mostra_o_painel(monkeypatch, tmp_path, capsys):
    abertos = []
    monkeypatch.setattr(cs.webbrowser, "open", lambda url: abertos.append(url))
    rodar_painel(monkeypatch, tmp_path, "--sem-abrir", "--ativos", "3")
    assert abertos == []                                                           # --sem-abrir não abre o navegador
    saida = capsys.readouterr().out
    for secao in ("AS MELHORES AGORA", "QUANTO CADA UM SUBIU", "PLANO DOS PRIMEIROS 3 MESES", "AS MELHORES NO PASSADO", "CUSTOS DAS CORRETORAS"):
        assert secao in saida
    assert sorted(f.name for f in tmp_path.iterdir()) == ["p.html", "p.xlsx"]      # só dois arquivos, sem CSV
    abas = pd.read_excel(tmp_path / "p.xlsx", sheet_name=None)
    assert set(abas) == {"Melhores agora", "Quanto subiu", "Plano de compra", "Passado", "Corretoras", "Todos os ativos"}
    assert len(abas["Todos os ativos"]) == 6 and not abas["Todos os ativos"]["Ativo"].str.contains(".SA", regex=False).any()
    assert "Sua carteira em poucos passos" in (tmp_path / "p.html").read_text(encoding="utf-8")
    assert "Planilha atualizada" in saida and "Relatório atualizado" in saida


def test_abas_do_painel_em_portugues_e_sem_o_sufixo_do_yahoo():
    painel, tabela = painel_e_tabela()
    abas = cs.abas_do_painel(painel, tabela)
    assert list(abas) == ["Melhores agora", "Quanto subiu", "Plano de compra", "Passado", "Corretoras", "Todos os ativos"]
    for nome_aba, df in abas.items():
        assert not df.astype(str).apply(lambda col: col.str.contains(".SA", regex=False)).any().any(), nome_aba
    assert abas["Melhores agora"]["Ativo"].tolist() == ["PETR4", "CXSE3", "ABEV3", "WEGE3", "ISAE4"]
    assert abas["Melhores agora"]["Unidades que cabem no aporte"].tolist()[0] == 4
    plano = abas["Plano de compra"]
    assert plano["Comprar"].iloc[0] == "1 PETR4 + 2 CXSE3 + 2 ABEV3 + 1 WEGE3 + 1 ISAE4"
    assert plano["Gasto (R$)"].iloc[0] == pytest.approx(196.65) and plano["Sobra (R$)"].iloc[0] == pytest.approx(3.35)
    assert set(abas["Passado"]["Tipo"]) == {"Ano", "Mês"} and len(abas["Corretoras"]) == len(cs.CORRETORAS)
    assert set(abas["Quanto subiu"]["Período"]) == {"No último pregão", "No último mês", "Em 12 meses"}
    assert abas["Todos os ativos"]["Passou nos filtros"].eq("sim").all()


def test_abas_do_painel_sem_nenhum_aprovado_nao_quebra():
    fech = {"PETR4.SA": serie_diaria(49.12)}
    vazio = pd.DataFrame(columns=["ticker", "preco_referencia", "dy_12m_pct", "anos_com_div_ult_3", "retorno_preco_12m_pct", "score"])
    tabela = cs.montar_tabela(fech, {"PETR4.SA": pd.Series(dtype=float)}, vazio, 200.0)
    painel = cs.montar_painel(tabela, [], {}, 200.0, 3, HOJE)
    abas = cs.abas_do_painel(painel, tabela)
    assert abas["Melhores agora"].empty and abas["Plano de compra"].empty and abas["Quanto subiu"].empty
    assert abas["Todos os ativos"]["Passou nos filtros"].tolist() == ["não"]


def test_main_abre_o_navegador_a_menos_que_peca_para_nao(monkeypatch, tmp_path):
    abertos = []
    monkeypatch.setattr(cs.webbrowser, "open", lambda url: abertos.append(url))
    rodar_painel(monkeypatch, tmp_path, "--ativos", "3")
    assert len(abertos) == 1 and abertos[0].startswith("file://") and abertos[0].endswith("p.html")


def test_main_com_fiis_inclui_fii_no_plano(monkeypatch, tmp_path, capsys):
    rodar_painel(monkeypatch, tmp_path, "--sem-abrir", "--ativos", "3", "--fiis", "1")
    linha = next(l for l in capsys.readouterr().out.splitlines() if l.startswith("Ativos do plano:"))
    ativos = [a.strip() for a in linha.split(":")[1].split(",")]
    assert len(ativos) == 3 and sum(a in ("HGLG11", "MXRF11", "KNRI11") for a in ativos) == 1


def test_main_recusa_argumentos_invalidos(monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match="--valor"):
        rodar_painel(monkeypatch, tmp_path, "--valor", "0")
    with pytest.raises(SystemExit, match="--fiis"):
        rodar_painel(monkeypatch, tmp_path, "--ativos", "3", "--fiis", "4")


# ---------- uma só planilha para tudo ----------

def test_scanner_e_painel_atualizam_a_mesma_planilha(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "baixar_cdi", cdi_diario)
    rodar_main(monkeypatch, tmp_path, "--sem-cota")                                 # scanner, versão A: tmp/r.xlsx
    rodar_painel(monkeypatch, tmp_path, "--saida", str(tmp_path / "r"), "--sem-abrir", "--ativos", "3")
    rodar_main(monkeypatch, tmp_path)                                               # scanner de novo, versão B
    assert sorted(f.name for f in tmp_path.iterdir()) == ["r.html", "r.xlsx"]       # continua tudo em um arquivo (mais o relatório)
    abas = pd.read_excel(tmp_path / "r.xlsx", sheet_name=None)
    assert set(abas) == {"Melhores agora", "Quanto subiu", "Plano de compra", "Passado", "Corretoras", "Todos os ativos",
                         "Ranking", "Diagnóstico", "Comparação", "Backtest", "Operações", "Histórico"}
    assert pd.ExcelFile(tmp_path / "r.xlsx").sheet_names[0] == "Melhores agora"     # ordem combinada


def test_historico_guarda_uma_linha_por_execucao_do_scanner(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "baixar_cdi", cdi_diario)
    rodar_main(monkeypatch, tmp_path, "--sem-cota")
    rodar_main(monkeypatch, tmp_path)
    rodar_main(monkeypatch, tmp_path, "--fiis", "0")
    h = pd.read_excel(tmp_path / "r.xlsx", sheet_name="Histórico")
    assert h.configuracao.tolist() == ["3 melhores do ranking geral (sem cota)", "3 ativos: 2 ações + 1 FIIs", "3 ativos: 3 ações + 0 FIIs"]
    assert h.aporte_mensal.eq(200).all() and h.executado_em.str.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}").all()
    ultima = pd.read_excel(tmp_path / "r.xlsx", sheet_name="Comparação").set_index("nome")
    assert h.retorno_por_ano_pct.iloc[-1] == pytest.approx(ultima.loc["Estratégia", "xirr_aa_pct"], abs=0.01)
    assert h.ibov_retorno_por_ano_pct.iloc[-1] == pytest.approx(ultima.loc["IBOV (BOVA11)", "xirr_aa_pct"], abs=0.01)
    assert h.maior_queda_pct.iloc[-1] <= 0 and h.valor_final.iloc[-1] > 0 and h.fiis_na_carteira_media_pct.notna().all()


def test_historico_sem_benchmark_deixa_a_coluna_em_branco(monkeypatch, tmp_path):
    rodar_main(monkeypatch, tmp_path, "--sem-benchmark")
    h = pd.read_excel(tmp_path / "r.xlsx", sheet_name="Histórico")
    assert len(h) == 1 and h.ibov_retorno_por_ano_pct.isna().all() and h.retorno_por_ano_pct.notna().all()


def test_scanner_mostra_o_resultado_mesmo_com_a_planilha_aberta_no_excel(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sc, "baixar_cdi", cdi_diario)

    def bloqueado(*a, **k):
        raise SystemExit("Não consegui gravar r.xlsx. Se ele estiver aberto no Excel, feche-o e rode de novo.")
    monkeypatch.setattr(sc, "atualizar_planilha", bloqueado)
    with pytest.raises(SystemExit, match="feche"):
        rodar_main(monkeypatch, tmp_path)
    assert "RESULTADO DO BACKTEST" in capsys.readouterr().out                      # o resultado já tinha sido mostrado


def test_painel_gera_o_relatorio_mesmo_com_a_planilha_aberta_no_excel(monkeypatch, tmp_path, capsys):
    def bloqueado(*a, **k):
        raise SystemExit("Não consegui gravar p.xlsx. Se ele estiver aberto no Excel, feche-o e rode de novo.")
    monkeypatch.setattr(cs, "atualizar_planilha", bloqueado)
    with pytest.raises(SystemExit, match="Excel"):
        rodar_painel(monkeypatch, tmp_path, "--sem-abrir", "--ativos", "3")
    assert (tmp_path / "p.html").exists() and "AS MELHORES AGORA" in capsys.readouterr().out
