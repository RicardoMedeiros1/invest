"""A publicação automática do painel (GitHub Actions + GitHub Pages) e o que ela exige dos comandos."""
import shlex
from pathlib import Path

import pandas as pd

import carteira_simples as cs
from test_carteira_simples import painel_de_teste, rodar_painel

RAIZ = Path(__file__).resolve().parent.parent
FLUXO = (RAIZ / ".github" / "workflows" / "painel.yml").read_text(encoding="utf-8")


def comando_do_fluxo() -> list[str]:
    linha = next(l for l in FLUXO.splitlines() if "python carteira_simples.py" in l and not l.strip().startswith("#"))
    return shlex.split(linha.split("carteira_simples.py", 1)[1])


def test_o_comando_do_fluxo_e_aceito_pelo_painel():
    args = cs.criar_parser().parse_args(comando_do_fluxo())
    assert args.saida == "site/index" and args.sem_abrir and args.link_planilha == "index.xlsx" and args.valor == 200


def test_o_fluxo_publica_a_mesma_pasta_que_o_painel_gera():
    assert "path: site" in FLUXO and "--saida site/index" in FLUXO                      # site/index.html é a página inicial
    assert "deploy-pages" in FLUXO and "upload-pages-artifact" in FLUXO


def test_o_fluxo_roda_nos_dias_uteis_depois_do_fechamento_e_sob_demanda():
    assert 'cron: "30 21 * * 1-5"' in FLUXO and "workflow_dispatch:" in FLUXO           # 18h30 de Brasília, seg a sex
    assert "pages: write" in FLUXO and "id-token: write" in FLUXO and "contents: read" in FLUXO
    assert "pip install -r requirements.txt" in FLUXO                                  # as mesmas dependências do uso local


def test_publicar_so_depois_de_gerar_o_painel():
    assert "needs: construir" in FLUXO


def test_painel_grava_em_uma_pasta_nova_com_link_para_baixar_a_planilha(monkeypatch, tmp_path):
    destino = tmp_path / "site" / "index"                                              # a pasta "site" ainda não existe
    rodar_painel(monkeypatch, tmp_path, "--saida", str(destino), "--sem-abrir", "--ativos", "3", "--link-planilha", "index.xlsx")
    assert sorted(f.name for f in (tmp_path / "site").iterdir()) == ["index.html", "index.xlsx"]
    pagina = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "<a href='index.xlsx'>Baixar a planilha (Excel)</a>" in pagina
    assert "Melhores agora" in pd.ExcelFile(tmp_path / "site" / "index.xlsx").sheet_names


def test_sem_o_parametro_a_pagina_nao_tem_link_de_download():
    assert "Baixar a planilha" not in cs.html_painel(painel_de_teste())
    assert "Baixar a planilha" in cs.html_painel(painel_de_teste(), "dados.xlsx")


def test_link_da_planilha_e_escapado():
    pagina = cs.html_painel(painel_de_teste(), "a'><script>x</script>.xlsx")
    assert "<script>x</script>" not in pagina
