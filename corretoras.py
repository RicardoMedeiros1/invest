# -*- coding: utf-8 -*-
"""Custos de corretoras para ações e FIIs, reunidos de fontes públicas.

ATENÇÃO: tarifas mudam. Este quadro é um ponto de partida, não a tabela oficial: antes de abrir conta,
confira a tabela de tarifas no site ou no aplicativo da corretora. Onde a fonte não é a página oficial
ou as fontes divergem, o texto diz "confirme". Atualize PESQUISADO_EM ao revisar.
"""

PESQUISADO_EM = "outubro/2026"

AVISO = ("Em todas você ainda paga as taxas da B3 (emolumentos) e o imposto sobre o lucro, e nenhuma garante retorno. "
         "O dinheiro parado na corretora não tem a garantia do FGC (que cobre CDB, LCI e LCA). "
         "O preço da ação ou do FII é o mesmo em qualquer corretora: o que muda são taxas, aplicativo e atendimento.")

CORRETORAS = [
    {"nome": "Nubank",
     "corretagem": "R$ 0 em renda fixa e renda variável (página oficial)",
     "custodia": "sem taxa de custódia nem de manutenção de conta (página oficial)",
     "atencao": "Transferir a custódia para outra corretora custa R$ 50 por transferência (página oficial).",
     "fonte": "https://nubank.com.br/sobre-investimentos/taxas-e-precos"},
    {"nome": "Inter",
     "corretagem": "R$ 0 em ações, FIIs, ETFs e BDRs (fontes não oficiais; confirme)",
     "custodia": "R$ 0 (fontes não oficiais; confirme)",
     "atencao": "Você paga só as taxas da B3. As tarifas podem mudar por trimestre.",
     "fonte": "https://guiadoinvestidor.com.br/guias/corretoras/inter-corretora/"},
    {"nome": "Íon (Itaú)",
     "corretagem": "R$ 0 em ações, ETFs e BDRs pelo aplicativo e canais digitais; FIIs: confirme",
     "custodia": "confirme na tabela oficial",
     "atencao": "Operações pela mesa de atendimento têm cobrança.",
     "fonte": "https://www.idinheiro.com.br/investimentos/corretoras/corretoras-corretagem-zero/"},
    {"nome": "Ágora (Bradesco)",
     "corretagem": "R$ 0 em ações, BDRs, ETFs, FIIs e opções pelo aplicativo e pelo site (tabela oficial)",
     "custodia": "a tabela oficial cita isenção para ações; FIIs não aparecem nela (sites de terceiros dizem que sim): confirme",
     "atencao": "Pela mesa de atendimento: 0,5% do volume do dia mais R$ 25,21. A taxa de custódia da B3 é repassada.",
     "fonte": "https://arevista.com.br/dividendos/agora-investimentos-corretora-do-bradesco-com-taxa-zero-e-seguranca-para-iniciantes/"},
    {"nome": "Toro",
     "corretagem": "R$ 0 nas operações manuais, em ações e ETFs",
     "custodia": "um artigo antigo diz que não há; pode estar desatualizado: confirme",
     "atencao": "Nas operações automatizadas, cobra 10% do lucro.",
     "fonte": "https://www.idinheiro.com.br/investimentos/corretoras/corretoras-corretagem-zero/"},
]
