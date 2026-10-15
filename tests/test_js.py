"""A lógica do painel em JavaScript (painel_web/painel.js) tem de fazer as mesmas contas do Python.

Usa o Node (`node --test`); sem o Node instalado, estes testes são pulados.
"""
import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

import carteira_simples as cs
import scanner_dividendos_b3 as sc

RAIZ = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js não instalado")


def casos_de_paridade() -> dict:
    rng = random.Random(7)
    escolha, plano, cotas = [], [], []
    for _ in range(300):
        n = rng.randint(0, 12)
        tickers = [f"T{i}" for i in range(n)]
        classes = {t: rng.choice(["ACAO", "FII"]) for t in tickers}
        top_n = rng.randint(1, 8)
        n_fiis = rng.choice([None, *range(0, top_n + 1)])
        antigo = dict(sc.CLASSE)
        sc.CLASSE.update(classes)                                  # o Python consulta a classe pelo dicionário do scanner
        try:
            esperado = sc.escolher_candidatos(tickers, top_n, n_fiis)
        finally:
            sc.CLASSE.clear(); sc.CLASSE.update(antigo)
        escolha.append({"ranking": [{"t": t, "classe": "FII" if classes[t] == "FII" else "ação"} for t in tickers],
                        "top_n": top_n, "n_fiis": n_fiis, "esperado": esperado})
    for _ in range(300):
        n = rng.randint(1, 8)
        ordem = [f"T{i}" for i in range(n)]
        precos = {t: round(rng.uniform(5, 220), 2) for t in ordem}
        valor = float(rng.choice([50, 100, 200, 500, 1000, 123.45]))
        meses = rng.randint(1, 12)
        plano.append({"precos": precos, "ordem": ordem, "valor": valor, "meses": meses,
                      "esperado": cs.plano_de_compras(precos, ordem, valor, meses)})
    for _ in range(500):
        valor, preco = float(rng.choice([50, 200, 29.46, 1000])), round(rng.uniform(1, 400), 2)
        tabela = cs.montar_tabela({"X.SA": pd.Series([preco], index=[pd.Timestamp("2026-10-15")])},
                                  {}, pd.DataFrame({"ticker": [], "score": []}), valor)
        cotas.append({"valor": valor, "preco": preco, "esperado": int(tabela.cotas.iloc[0])})
    for valor, preco in ((25.15, 5.03), (25.14, 5.03), (100.6, 5.03), (200.0, 1.6), (200.0, 49.12)):   # unidades exatas e quase exatas
        t = cs.montar_tabela({"X.SA": pd.Series([preco], index=[pd.Timestamp("2026-10-15")])}, {}, pd.DataFrame({"ticker": [], "score": []}), valor)
        cotas.append({"valor": valor, "preco": preco, "esperado": int(t.cotas.iloc[0])})
    brl = [{"x": x, "esperado": cs.brl(x)} for x in (0, 5, 49.12, 196.65, 1234.5, 99999.99, 1234567.891, -12.3, 0.4)]
    pct = [{"x": x, "sinal": s, "esperado": cs.pct(x, s)} for x in (7.46, 12.04, -2.34, 54.9, 100, -0.8) for s in (False, True)]
    pct.append({"x": 0, "sinal": False, "esperado": cs.pct(0)})       # zero com sinal é proposital: o painel mostra "0,0%", o terminal "+0,0%"
    return {"escolha": escolha, "plano": plano, "cotas": cotas, "brl": brl, "pct": pct}


def rodar_node(env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([NODE, "--test", "tests/js/painel.test.js"], cwd=RAIZ, env=env, capture_output=True, text=True, timeout=120)


def test_logica_do_painel_em_javascript_passa_e_bate_com_o_python(tmp_path):
    arquivo = tmp_path / "casos.json"
    arquivo.write_text(json.dumps(casos_de_paridade()), encoding="utf-8")
    r = rodar_node({"CASOS_PARIDADE": str(arquivo)})
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]
    assert "# skipped 0" in r.stdout                                  # a paridade não foi pulada


def test_horario_de_brasilia_nao_depende_do_fuso_do_computador():
    for fuso in ("Pacific/Auckland", "America/Los_Angeles", "Asia/Tokyo"):
        r = rodar_node({"TZ": fuso})
        assert r.returncode == 0, f"{fuso}: " + r.stdout[-2000:]
