"""A página do painel (painel_web/ + painel_pagina.py) e os dados que ela mostra."""
import json
import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

import carteira_simples as cs
import painel_pagina
from test_carteira_simples import HOJE, painel_tabela_e_fechamentos, serie_diaria

AGORA = datetime(2026, 10, 15, 14, 35, 12, tzinfo=timezone(timedelta(hours=-3)))
CONFIG = {"valor": 200.0, "ativos": 5, "fiis": 0, "meses": 3}


def dados_de_teste(**extra):
    painel, tabela, fech = painel_tabela_e_fechamentos()
    return cs.dados_do_painel(painel, tabela, fech, [], AGORA, CONFIG, **extra)


def dados_embutidos(pagina: str) -> dict:
    m = re.search(r'<script type="application/json" id="dados-iniciais">(.*?)</script>', pagina, re.S)
    return json.loads(m.group(1))


# ---------- os dados ----------

def test_dados_trazem_cada_ativo_sem_o_sufixo_do_yahoo_e_viram_json_valido():
    d = dados_de_teste()
    texto = painel_pagina.dados_json(d)                                    # allow_nan=False: NaN no JSON levantaria erro
    assert ".SA" not in texto and json.loads(texto) == d
    assert d["versao"] == painel_pagina.VERSAO_DADOS and d["gerado_em"] == "2026-10-15T14:35:12-03:00"
    assert d["data_precos"] == "2026-10-15" and d["config"] == CONFIG and d["n_aprovados"] == 5 and d["n_total"] == 5
    petr = next(a for a in d["ativos"] if a["t"] == "PETR4")
    assert petr["classe"] == "ação" and petr["ok"] and petr["pos"] == 1 and petr["preco"] == pytest.approx(49.12)
    assert petr["dy"] == pytest.approx(7.0) and petr["div12"] == pytest.approx(49.12 * 0.07, abs=1e-4)
    assert len(petr["serie"]) == cs.SERIE_PREGOES and petr["serie"][-1] == pytest.approx(49.12)


def test_dados_deixam_vazio_o_que_nao_existe_em_vez_de_nan():
    fech = {"PETR4.SA": serie_diaria(49.12, dias=1), "VALE3.SA": serie_diaria(70.0)}
    ranking = pd.DataFrame({"ticker": ["PETR4.SA"], "preco_referencia": 1.0, "dy_12m_pct": 7.0, "anos_com_div_ult_3": 3,
                            "retorno_preco_12m_pct": 5.0, "score": [0.9]})
    tabela = cs.montar_tabela(fech, {}, ranking, 200.0)
    painel = cs.montar_painel(tabela, ["PETR4.SA"], {}, 200.0, 3, HOJE)
    d = cs.dados_do_painel(painel, tabela, fech, [], AGORA, CONFIG)
    petr, vale = d["ativos"]
    assert petr["dia"] is None and petr["mes"] is None and petr["ano"] is None          # um pregão só: sem variação
    assert vale["ok"] is False and vale["pos"] is None and vale["nota"] is None          # não passou nos filtros
    painel_pagina.dados_json(d)                                                          # nenhum NaN escapou


def test_dados_do_passado_e_das_corretoras():
    d = dados_de_teste()
    assert d["passado"]["meses"][-1] == {"mes": "2026-09", "escolhidas": ["PETR4", "CXSE3", "ABEV3", "WEGE3", "ISAE4"]}
    assert d["passado"]["anos"][0]["ano"] == 2026 and d["passado"]["anos"][0]["top"][0] == ["ABEV3", 2]
    assert len(d["corretoras"]["lista"]) == len(cs.CORRETORAS) and d["corretoras"]["pesquisado_em"] and d["aviso_final"]


def test_dados_de_publicacao():
    d = dados_de_teste(link_planilha="index.xlsx", dados_url="dados.json", intervalo_min=30)
    assert (d["planilha_url"], d["dados_url"], d["intervalo_min"]) == ("index.xlsx", "dados.json", 30)
    local = dados_de_teste()
    assert local["planilha_url"] is None and local["dados_url"] is None and local["intervalo_min"] == cs.INTERVALO_PADRAO_MIN


def test_json_recusa_nan_em_vez_de_publicar_json_invalido():
    with pytest.raises(ValueError):
        painel_pagina.dados_json({"preco": float("nan")})


# ---------- Ibovespa e dólar ----------

def test_indices_sao_opcionais(monkeypatch, capsys):
    def sem_internet(tickers, dias=0):
        raise RuntimeError("Nenhum preço diário foi baixado.")
    monkeypatch.setattr(cs, "baixar_diario", sem_internet)
    assert cs.baixar_indices() == [] and "[AVISO] Sem Ibovespa e dólar" in capsys.readouterr().out


def test_indices_trazem_valor_variacao_e_grafico(monkeypatch):
    serie = pd.Series([100.0, 110.0], index=pd.bdate_range(end=HOJE, periods=2))
    monkeypatch.setattr(cs, "baixar_diario", lambda tickers, dias=0: ({"^BVSP": serie, "BRL=X": serie.iloc[:1]}, {}))
    indices = cs.baixar_indices()
    assert [i["nome"] for i in indices] == ["Ibovespa"]                                  # o dólar veio com um dia só: fica de fora
    assert indices[0]["valor"] == 110.0 and indices[0]["dia_pct"] == pytest.approx(10.0) and indices[0]["serie"] == [100.0, 110.0]
    assert indices[0]["unidade"] == "pts"


# ---------- a página ----------

def test_pagina_e_um_arquivo_so_com_os_dados_dentro():
    d = dados_de_teste(dados_url="dados.json")
    pagina = painel_pagina.pagina_html(d)
    assert pagina.startswith("<!doctype html>") and "<title>Minha carteira</title>" in pagina
    assert dados_embutidos(pagina) == d
    assert not re.search(r"__(META|CSS|JS|DADOS)__", pagina)
    assert "<script src" not in pagina and "<link rel=\"stylesheet\"" not in pagina        # nada de fora: funciona sem internet
    for secao in ("mercado", "ranking", "movimento", "plano", "passado", "corretoras"):
        assert f'<section id="{secao}"' in pagina
    assert "VERSAO_PAGINA" in pagina and ".SA" not in pagina


def test_texto_de_fora_nao_consegue_fechar_o_script(monkeypatch):
    perigoso = '</script><script>alert(1)</script> & <!-- __JS__'
    monkeypatch.setattr(cs, "CORRETORAS", [{"nome": perigoso, "corretagem": "a", "custodia": "c", "atencao": "d", "fonte": "https://x.test"}])
    pagina = painel_pagina.pagina_html(dados_de_teste())
    assert "alert(1)" in pagina and "<script>alert" not in pagina and pagina.count("</script>") == 2    # só os dois scripts da página
    assert dados_embutidos(pagina)["corretoras"]["lista"][0]["nome"] == perigoso                      # e o texto chega inteiro à página
    bloco = re.search(r'id="dados-iniciais">(.*?)</script>', pagina, re.S).group(1)
    assert not any(c in bloco for c in "<>&")                                                          # nem um caractere que o HTML leia como marca
    assert pagina.count("const VERSAO_PAGINA") == 1                                                    # __JS__ nos dados não duplicou o script


def test_script_e_estilo_nao_tem_sequencias_que_quebram_o_html():
    js = (painel_pagina.PASTA_WEB / "painel.js").read_text(encoding="utf-8")
    css = (painel_pagina.PASTA_WEB / "estilo.css").read_text(encoding="utf-8")
    for texto in (js, css):
        assert "</script" not in texto.lower() and "<!--" not in texto and "</style" not in texto.lower()


def test_versao_do_script_e_a_mesma_dos_dados():
    js = (painel_pagina.PASTA_WEB / "painel.js").read_text(encoding="utf-8")
    assert int(re.search(r"const VERSAO_PAGINA = (\d+);", js).group(1)) == painel_pagina.VERSAO_DADOS


def test_ids_que_o_script_usa_existem_na_pagina():
    pasta = painel_pagina.PASTA_WEB
    js, modelo = (pasta / "painel.js").read_text(encoding="utf-8"), (pasta / "modelo.html").read_text(encoding="utf-8")
    usados = set(re.findall(r'\$\("([\w-]+)"\)', js))
    existentes = set(re.findall(r'id="([\w-]+)"', modelo))
    assert usados and usados <= existentes, usados - existentes
