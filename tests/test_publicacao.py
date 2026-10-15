"""A publicação automática do painel (GitHub Actions + GitHub Pages) e o que ela exige dos comandos."""
import json
import shlex
from pathlib import Path

import pandas as pd
import pytest

import carteira_simples as cs
from test_carteira_simples import rodar_painel

RAIZ = Path(__file__).resolve().parent.parent
FLUXO = (RAIZ / ".github" / "workflows" / "painel.yml").read_text(encoding="utf-8")


def comando_do_fluxo() -> list[str]:
    linha = next(l for l in FLUXO.splitlines() if "python carteira_simples.py" in l and not l.strip().startswith("#"))
    return shlex.split(linha.split("carteira_simples.py", 1)[1])


def cron_do_fluxo() -> str:
    return next(l for l in FLUXO.splitlines() if l.strip().startswith("- cron:")).split('"')[1]


def test_o_comando_do_fluxo_e_aceito_pelo_painel():
    args = cs.criar_parser().parse_args(comando_do_fluxo())
    assert args.saida == "site/index" and args.sem_abrir and args.link_planilha == "index.xlsx" and args.valor == 200
    assert args.dados_json == "dados.json" and args.intervalo_min == 15


def test_o_fluxo_publica_a_mesma_pasta_que_o_painel_gera():
    assert "path: site" in FLUXO and "--saida site/index" in FLUXO                      # site/index.html é a página inicial
    assert "deploy-pages" in FLUXO and "upload-pages-artifact" in FLUXO


def test_o_fluxo_roda_de_15_em_15_minutos_no_pregao_e_sob_demanda():
    assert 'cron: "7,22,37,52 13-21 * * 1-5"' in FLUXO and "workflow_dispatch:" in FLUXO   # 10h07 às 18h52 de Brasília, seg a sex
    assert "pages: write" in FLUXO and "id-token: write" in FLUXO and "contents: read" in FLUXO
    assert "pip install -r requirements.txt" in FLUXO                                  # as mesmas dependências do uso local


def test_publicar_so_depois_de_gerar_o_painel():
    assert "needs: construir" in FLUXO


def test_o_intervalo_do_fluxo_bate_com_o_cron_e_com_o_aviso_da_pagina():
    args = cs.criar_parser().parse_args(comando_do_fluxo())
    minutos = [int(m) for m in cron_do_fluxo().split()[0].split(",")]
    folgas = {b - a for a, b in zip(minutos, minutos[1:] + [minutos[0] + 60])}
    assert folgas == {args.intervalo_min}                                              # os minutos do cron andam sempre o mesmo passo


def test_o_cron_evita_os_horarios_de_pico_do_github():
    minutos = {int(m) for m in cron_do_fluxo().split()[0].split(",")}
    assert not minutos & {0, 15, 30, 45}                                               # o GitHub atrasa ou descarta execuções nesses minutos
    horas = cron_do_fluxo().split()[1]
    assert horas == "13-21" and cron_do_fluxo().split()[4] == "1-5"                    # 10h às 18h59 de Brasília, de segunda a sexta


def test_o_fluxo_nao_interrompe_uma_publicacao_em_andamento():
    assert "cancel-in-progress: false" in FLUXO


def test_painel_grava_em_uma_pasta_nova_com_dados_e_link_para_baixar_a_planilha(monkeypatch, tmp_path):
    destino = tmp_path / "site" / "index"                                              # a pasta "site" ainda não existe
    rodar_painel(monkeypatch, tmp_path, "--saida", str(destino), "--sem-abrir", "--ativos", "3", "--link-planilha", "index.xlsx",
                 "--dados-json", "dados.json")
    assert sorted(f.name for f in (tmp_path / "site").iterdir()) == ["dados.json", "index.html", "index.xlsx"]
    dados = json.loads((tmp_path / "site" / "dados.json").read_text(encoding="utf-8"))
    assert dados["planilha_url"] == "index.xlsx" and dados["dados_url"] == "dados.json" and dados["intervalo_min"] == 15
    pagina = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert '"dados_url":"dados.json"' in pagina                                         # a página sabe onde reler os dados
    assert "Melhores agora" in pd.ExcelFile(tmp_path / "site" / "index.xlsx").sheet_names


def test_sem_publicar_nao_cria_o_arquivo_de_dados_nem_o_link(monkeypatch, tmp_path):
    rodar_painel(monkeypatch, tmp_path, "--sem-abrir", "--ativos", "3")
    assert sorted(f.name for f in tmp_path.iterdir()) == ["p.html", "p.xlsx"]           # uso local: só planilha e página
    pagina = (tmp_path / "p.html").read_text(encoding="utf-8")
    assert '"dados_url":null' in pagina and '"planilha_url":null' in pagina


def test_dados_json_so_aceita_um_nome_de_arquivo(monkeypatch, tmp_path):
    for ruim in ("../fora.json", "sub/dados.json", "dados.txt"):
        with pytest.raises(SystemExit, match="dados-json"):
            rodar_painel(monkeypatch, tmp_path, "--sem-abrir", "--dados-json", ruim)
