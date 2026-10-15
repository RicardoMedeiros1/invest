# -*- coding: utf-8 -*-
"""A página do painel: junta o modelo, o estilo e o script de painel_web/ com os dados, num HTML só.

Os dados vão dentro da própria página (abre com duplo clique, sem internet) e, quando o painel é publicado,
também em um arquivo .json que a página relê sozinha de tempos em tempos.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PASTA_WEB = Path(__file__).resolve().parent / "painel_web"
VERSAO_DADOS = 1   # precisa ser igual a VERSAO_PAGINA em painel_web/painel.js
DESCRICAO = "Painel da carteira: as melhores ações e FIIs agora, quanto cada um subiu, plano de compra e custos das corretoras."


def dados_json(dados: dict, para_html: bool = False) -> str:
    """JSON compacto. Para dentro de <script>, troca <, > e & para que um texto de fora nunca feche a tag."""
    texto = json.dumps(dados, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if para_html:
        texto = texto.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return texto


def pagina_html(dados: dict) -> str:
    """O painel como página HTML autônoma (um arquivo, sem depender de nada da internet)."""
    partes = {
        "META": f'<meta name="description" content="{DESCRICAO}">\n<link rel="icon" href="data:,">',
        "CSS": (PASTA_WEB / "estilo.css").read_text(encoding="utf-8"),
        "JS": (PASTA_WEB / "painel.js").read_text(encoding="utf-8"),
        "DADOS": dados_json(dados, para_html=True),
    }
    modelo = (PASTA_WEB / "modelo.html").read_text(encoding="utf-8")
    return re.sub(r"__(META|CSS|JS|DADOS)__", lambda m: partes[m.group(1)], modelo)   # uma passada só: o texto inserido não é relido
