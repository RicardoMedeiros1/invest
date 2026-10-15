# Scanner de dividendos B3

Protótipo quantitativo que ranqueia ações e FIIs da B3 por filtros de dividendos e roda um backtest mensal com aportes fixos. Usa preços e proventos do Yahoo Finance (`yfinance`). **Não envia ordens.**

> Este é um protótipo, não uma análise fundamentalista. Ele não olha balanços, cobertura dos dividendos pelo lucro, endividamento, vacância ou qualidade dos imóveis e recebíveis. "Conservador" descreve os filtros, não garante que os ativos sejam seguros. Não é recomendação de investimento.

## Instalação (Windows / PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

O `requirements.txt` instala `yfinance[repair]`, que inclui `scipy` e `scikit-learn`: o script pede ao Yahoo que corrija erros de preço nos dados (`repair=True`) e isso exige esses módulos. Se aparecer `No module named 'scipy'` (ou `sklearn`), rode `pip install -r requirements.txt` de novo, com o ambiente virtual ativado.

Se o PowerShell bloquear a ativação do ambiente virtual, use `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` e execute os comandos abaixo com `.\.venv\Scripts\python.exe`.

## Painel simples (comece por aqui)

Para quem quer só o essencial, sem benchmarks nem XIRR:

```powershell
python carteira_simples.py --valor 200
```

Abre no navegador um painel (e mostra o mesmo no terminal) com:

- **Mercado agora**: Ibovespa e dólar, quantos dos aprovados sobem no dia, a maior alta e a maior queda e um quadrado colorido para cada ativo aprovado (verde sobe, vermelho cai; o sinal e a seta mostram o mesmo, então não depende só da cor). O topo diz se o **mercado está aberto ou fechado** e a que horas os preços foram atualizados.
- **As melhores agora**, ações e FIIs, na ordem da nota do scanner: preço, variação no dia, no mês e em 12 meses, dividendos pagos nos últimos 12 meses (barra), um gráfico do preço nos últimos 3 meses e quantas unidades cabem no seu aporte. Dá para filtrar (ações, FIIs, busca por nome), ordenar (nota, maior alta do dia, do mês, do ano, mais dividendos) e mostrar também os que não passaram nos filtros.
- **Quem mais subiu e caiu** no dia, no mês ou em 12 meses, entre os aprovados. O dia muda muito de um pregão para o outro; o mês e o ano dizem mais para quem investe a longo prazo.
- **Plano de compra**, que **você ajusta na própria página** (quanto investe por mês, quantos ativos, quantos FIIs, quantos meses; a escolha fica guardada no navegador). Só unidades inteiras (na vida real não existe meia ação). O plano reforça sempre o ativo com menos dinheiro aplicado; se ele ainda não cabe no caixa (um FII de R$ 150 com aporte de R$ 200, por exemplo), guarda o caixa para o mês seguinte. Mostra o que comprar neste mês, como o dinheiro se divide entre os ativos e uma estimativa de dividendos, que repete o rendimento do último ano e não é promessa.
- **Apostas**: ações e fundos (ETFs, como os de cripto, ouro e bolsa dos EUA) em que o preço balança muito e que **não passam pelo filtro de dividendos**. Mostra só o que já aconteceu: quanto o preço oscila por ano, a pior queda dos últimos 12 meses, a distância até o topo do ano, o gráfico de 12 meses e quanto é negociado por dia (avisa quando é pouco, porque pode ser difícil vender). Dá para ordenar (mais fortes em 12 meses ou no mês, mais caídas, mais oscilantes, mais negociadas), filtrar por tipo e dizer quanto do aporte você arriscaria, para ver quantas unidades cabem. **Nada ali prevê se vai subir ou cair**, as apostas não entram no plano de compra e a seção não é recomendação. A lista fica em `apostas.py` (`APOSTAS`) e pode ser editada; ativo que o Yahoo não devolver é ignorado.
- **As melhores no passado**, por ano e nos últimos 12 meses: o que o scanner teria escolhido em cada momento, com os dados da época.
- **Custos das corretoras**, de fontes públicas (arquivo `corretoras.py`, com a data da pesquisa e o link de cada fonte). Tarifas mudam: confirme na tabela oficial antes de abrir conta. O preço de uma ação ou FII é o mesmo em qualquer corretora; o que muda são taxas, aplicativo e atendimento.

Opções: `--valor` (quanto você investe por mês), `--ativos` (quantos no plano, padrão 5), `--fiis` (quantos deles são FIIs, padrão 0), `--meses` (meses do plano, padrão 3), `--sem-abrir` (não abre o navegador). Atualiza a planilha `carteira.xlsx` (veja "Uma só planilha" abaixo) e o painel `carteira.html`, sempre com esses mesmos nomes. O `carteira.html` é um arquivo só (funciona sem internet) e mostra os preços do momento em que você rodou o comando; para ele se atualizar sozinho, use o link da internet abaixo. Usa o mesmo filtro do scanner, então os dois sempre concordam sobre quem está aprovado. Os preços diários vêm do Yahoo Finance e podem ter alguns minutos de atraso: confira a cotação no aplicativo da corretora antes de comprar.

## Abrir o painel em qualquer lugar (link na internet, atualiza sozinho)

O GitHub gera o painel sozinho **de 15 em 15 minutos durante o pregão (das 10h às 18h45 de Brasília, de segunda a sexta)** e publica num link que abre em qualquer aparelho, sem o seu computador ligado. O trabalho é feito por `.github/workflows/painel.yml`.

Como a atualização funciona, sem mistério:

1. A cada 15 minutos o GitHub baixa os preços do Yahoo Finance, refaz as contas e publica a página junto com um arquivo de dados (`dados.json`).
2. Com a página aberta no navegador, ela **relê esse arquivo sozinha a cada minuto** e troca os preços na tela, sem você recarregar. O botão "Atualizar agora" força a busca na hora.
3. O topo mostra "Mercado aberto" ou "Mercado fechado" e "atualizado há X min". Se o mercado estiver aberto e os preços tiverem mais de 45 minutos, aparece um aviso de **atualização atrasada**.

Isso **não é tempo real, tick a tick**: o Yahoo pode atrasar as cotações da B3 em alguns minutos, o GitHub às vezes atrasa execuções agendadas (às vezes por muitos minutos) e a página só enxerga o que o GitHub publicou. Serve para acompanhar o dia com tranquilidade, não para operar no segundo. Confira o preço no aplicativo da corretora antes de comprar.

Para ligar, uma única vez, no site do GitHub:

1. **Torne o repositório público**: Settings > General > Danger Zone > Change visibility. (O GitHub Pages não funciona com repositório privado na conta gratuita; quem tem GitHub Pro não precisa disso.)
2. **Ative o Pages**: Settings > Pages > Build and deployment > Source: **GitHub Actions**.
3. **Gere a primeira vez**: aba Actions > Painel > Run workflow. Ao terminar, o endereço aparece em Settings > Pages (costuma ser `https://ricardomedeiros1.github.io/invest/`).

Pontos de atenção:

- **Qualquer pessoa com o link vê o painel**: preços, a lista do scanner e o plano de compra (por padrão para R$ 200 por mês; cada visitante pode mudar o valor na própria página, sem afetar os outros). Ele não tem as suas posições nem dados pessoais. O código e o histórico do repositório também ficam públicos.
- Para mudar o valor padrão do aporte, edite `--valor 200` em `.github/workflows/painel.yml`. Para pausar, use Actions > Painel > menu `...` > Disable workflow. Para tirar do ar, volte o repositório para privado.
- Para mudar a frequência, edite a linha `cron` (por exemplo, `*/30` para de 30 em 30 minutos) e o `--intervalo-min` do comando, para o aviso de atraso acompanhar. O GitHub não aceita menos de 5 minutos.
- Se o Yahoo Finance falhar numa execução, a página anterior continua no ar (só aparece o aviso de atraso) e a execução aparece como falha na aba Actions; o GitHub pode mandar um e-mail sobre isso.
- Cada execução leva cerca de 1 minuto: são 36 por dia útil, bem dentro do limite gratuito de repositórios públicos.
- O GitHub pausa execuções agendadas de repositórios sem nenhuma atividade por 60 dias. Se o painel parar de atualizar, abra a aba Actions e reative o fluxo (ou faça qualquer commit).
- Só o painel simples é publicado. O scanner completo (backtest e benchmarks) continua rodando no seu computador, com a planilha local.

## Uso do scanner completo

```powershell
python scanner_dividendos_b3.py --anos 8 --aporte 200 --top 5
python scanner_dividendos_b3.py --anos 10 --aporte 200 --top 5 --saida minha_planilha   # outro nome de planilha (use o mesmo sempre)
python scanner_dividendos_b3.py --top 5 --fiis 3   # 2 ações + 3 FIIs entre os 5 escolhidos
python scanner_dividendos_b3.py --sem-cota         # os 5 melhores do ranking geral, sem vagas por classe
python scanner_dividendos_b3.py --sem-benchmark    # não baixa CDI nem ETFs de referência
```

O período simulado é menor que o baixado, porque os filtros exigem 36 meses de histórico. A lista de ativos (`ACOES` e `FIIS`) e os parâmetros ficam no início do arquivo.

### Uma só planilha

Tudo vai para **um único arquivo, `carteira.xlsx`**, com uma aba para cada coisa. Ele é sempre o mesmo: cada execução atualiza as abas que produz e **mantém as outras**, então você nunca acumula arquivos. O scanner e o painel simples gravam na mesma planilha (o nome padrão é `carteira`; mude com `--saida`, usando sempre o mesmo).

| Aba | De onde vem | Conteúdo |
|---|---|---|
| Melhores agora, Quanto subiu, Plano de compra, Apostas, Passado, Corretoras, Todos os ativos | painel simples | O mesmo do relatório, em tabelas que você pode filtrar e ordenar no Excel |
| Ranking | scanner | Ativos aprovados no último mês fechado, com posição no ranking geral, classe (ação ou FII), marca dos escolhidos para o próximo aporte (`escolhido_hoje`), yield de 12 meses, períodos com proventos, retorno de preço em 12 meses e pontuação |
| Diagnóstico | scanner | Situação de cada ativo do universo: aprovado (com a posição no ranking) ou excluído (com os motivos) |
| Comparação | scanner | Valor final, ganho, XIRR e drawdown da estratégia e de cada benchmark, e a diferença de XIRR em pontos percentuais |
| Backtest | scanner | Patrimônio mensal, total aportado, caixa, ganho/perda, ativos mantidos, quantidade de elegíveis e os selecionados, valor das posições em ações e em FIIs, mais uma coluna `valor_*` por benchmark |
| Operações | scanner | Compras dos aportes e vendas por saída dos filtros (coluna `observacao` marca vendas feitas pelo último preço conhecido) |
| Histórico | scanner | **Uma linha por execução**: quando rodou, a configuração (por exemplo, "5 ativos: 3 ações + 2 FIIs"), retorno por ano, maior queda, valor final, peso médio de FIIs e o retorno do IBOV. Serve para comparar versões na mesma planilha, sem criar arquivos novos |

- **Feche o arquivo no Excel antes de rodar.** O Windows não deixa o programa gravar numa planilha aberta. Nesse caso o resultado aparece no terminal e o programa avisa para fechar o arquivo e rodar de novo.
- A aba Histórico só cresce; para recomeçar, apague essa aba (ou o arquivo) no Excel.
- Os números saem arredondados a 2 casas (3 nas notas).
- Se você tem arquivos de versões antigas (`resultado_*.csv`, `carteira_simples.csv`), eles não são mais usados e podem ser apagados. No PowerShell, dentro da pasta `invest`, confira primeiro com `dir resultado_*, carteira_simples.*` e depois apague com `Remove-Item resultado_*.csv, carteira_simples.csv`.

## XIRR, drawdown e benchmarks

O **XIRR** é a taxa de retorno anual dos fluxos de caixa datados: cada aporte mensal sai com sinal negativo e o patrimônio final entra com sinal positivo (base de 365 dias, a mesma do Excel). Diferente do "retorno vs. soma dos aportes", ele leva em conta *quando* cada real foi investido, e por isso é a medida para comparar com benchmarks. Se o último mês ainda não terminou, o patrimônio final é datado de hoje, e não do fim do mês.

A comparação coloca **os mesmos aportes, nas mesmas datas**, em cada referência:

| Benchmark | Fonte | Observação |
|---|---|---|
| CDI | Série diária 12 do Banco Central (SGS) | Sem custo de negociação |
| IBOV (BOVA11) | Yahoo Finance, preço ajustado do ETF | Aproxima o retorno total do Ibovespa, já líquido da taxa do ETF; aplica o mesmo custo de negociação da estratégia |
| IDIV (DIVO11) | Yahoo Finance, preço ajustado do ETF | Índice de dividendos; é um ETF recente, com histórico curto, e é ignorado (com aviso) se não cobrir todo o período do backtest |
| IFIX (XFIX11) | Yahoo Finance, preço ajustado do ETF | Benchmark de FIIs: ETF que replica o IFIX (algumas fontes citam a versão IFIX-L, mais líquida), negociado desde nov/2020. Ignorado com aviso se o Yahoo não tiver o ticker |

| Misto (IBOV+IFIX) | Combinação dos dois ETFs acima | Com a cota por classe (padrão), o peso acompanha as vagas: 3 de 5 em ações = **Misto 60/40**. Com `--sem-cota`, vale o 50/50 de `BENCHMARKS_MISTOS`. Cada aporte é dividido entre BOVA11 e XFIX11 nesses pesos, **sem vender para rebalancear** (como a estratégia, que só compra); o peso de cada um deriva com o tempo. O patrimônio é a média dos patrimônios dos dois. Só entra se os dois existirem |

Um benchmark que não puder ser baixado também gera apenas um aviso, e o resto segue normalmente. A lista de ETFs fica em `BENCHMARKS_ETF`, no início do arquivo; para usar outra referência (por exemplo, outro ETF de FIIs), troque ou acrescente o ticker ali. O misto 50/50 de `BENCHMARKS_MISTOS` (pesos que somam 1; dá para criar outros, como um 70/30) só é usado com `--sem-cota`.

O **drawdown** (maior queda de um pico até o vale) é calculado sobre um **índice de retorno ponderado pelo tempo**, e não sobre o patrimônio. Os aportes empurram o patrimônio para cima e escondem parte da queda: num teste com 5 anos de aportes e uma queda de 30% em um mês, o patrimônio mostrava −28,3%, enquanto o índice de retorno mostra os −30,0% reais. O retorno de cada mês é `(patrimônio − aporte) / patrimônio do mês anterior − 1`; os custos de negociação ficam dentro do retorno, e o caixa parado rende zero. A mesma conta vale para a estratégia e para os benchmarks, então os números são comparáveis. Como os pontos são mensais, quedas dentro do mês não aparecem.

## Filtros e pontuação

| Critério | Regra inicial |
|---|---|
| Dividend yield de 12 meses | Entre 3% e 18% |
| Histórico de proventos | Pagamentos em pelo menos 2 de 3 períodos consecutivos de 12 meses |
| Corte de proventos | Proventos dos últimos 12 meses ≥ 50% dos 12 meses anteriores (`MIN_RAZAO_PROVENTOS`; `0` desativa) |
| Histórico de preços | Pelo menos 36 meses |

Pontuação: consistência dos proventos (45%), proximidade de um yield de referência de 6% (35%) e tendência de preço em 12 meses (20%). Os pesos são parâmetros iniciais para testes, não valores comprovadamente ótimos. Um yield de 17% não é necessariamente sustentável: pode vir de queda forte no preço ou de pagamento extraordinário.

### Diagnóstico de exclusão

O ranking mostra só os melhores aprovados. O diagnóstico responde à pergunta contrária: *por que este ativo não aparece?* Ele lista cada ativo da lista que ficou de fora e **todos** os filtros que reprovou (não só o primeiro), por exemplo `yield de 12m 2.10% abaixo do mínimo de 3.0%` ou `proventos caíram para 33% do período anterior (mínimo 50%)`. Também aparecem os ativos que nem foram baixados (`sem dados do Yahoo`), os sem cotação no mês, os com menos de 36 meses de histórico e os aprovados que ficaram abaixo da posição exibida no ranking. A aba Diagnóstico traz as métricas de todos, inclusive dos excluídos. O ranking e o diagnóstico usam a mesma função de decisão, então não podem divergir.

## Regras de compra, manutenção e venda (como implementadas)

- **Comprar/reforçar:** o aporte do mês (mais o caixa de vendas) é dividido em partes iguais entre os `--top` escolhidos, respeitando os limites de peso abaixo. Os escolhidos seguem uma **cota por classe**: por padrão, 40% das vagas são de FIIs (2 de 5) e as demais de ações, cada classe pelo seu melhor ranqueamento. Mude com `--fiis N`. Se uma classe não tiver aprovados suficientes, a vaga vai para o próximo melhor do ranking geral, e o script avisa. Com `--sem-cota`, valem os `--top` melhores do ranking geral, que na prática foram quase só ações (os FIIs pontuam menos: pagam mais dividendo e o preço deles andou mais fraco).
  Por que a cota: sem ela, os FIIs quase não entravam (5,5% da carteira em média numa execução real). A cota é uma decisão de composição, não uma melhoria comprovada de retorno: rode as duas versões e compare.
- **Manter:** um ativo que continua passando nos filtros é mantido, mesmo que saia do top. Ele só deixa de receber aportes.
- **Vender:** só quando o ativo deixa de passar nos filtros. No backtest isso gera uma venda simulada, com custo estimado. Na prática, complemente com análise dos resultados, da dívida e dos riscos do ativo.
- **Sem cotação no mês:** a posição é avaliada e, se necessário, liquidada pelo último preço conhecido (e sinalizada em `observacao`), nunca descartada.

Limites de peso (`MAX_PESO_*`), medidos sobre o patrimônio após o aporte: 25% por ativo, 60% em FIIs, 80% em ações. Eles só controlam os aportes: não há venda para rebalancear. O que não couber nos limites fica em caixa para o mês seguinte. Para não deixar caixa parado sem necessidade, o limite por ativo vira 1/n quando há poucos candidatos, e o limite de uma classe só vale se houver candidatos da outra classe.

Não há regra de "comprar porque o preço caiu": uma queda pode ser oportunidade ou deterioração.

## Limitações do backtest

1. **Viés de sobrevivência:** a lista de ativos é fixa e foi escolhida depois do fato.
2. **Preços ajustados:** as quantidades são sintéticas, não lotes negociáveis.
3. **Fundamentos:** os filtros não usam balanços disponíveis em cada data, então o ranking histórico pode não refletir o que se sabia na época.
4. **Custos e tributação:** a taxa é uma estimativa. Impostos, spreads, liquidez e lotes fracionários não estão modelados.
5. **Métricas:** o drawdown usa pontos mensais, então não captura quedas dentro do mês. Não há volatilidade nem outras medidas de risco.
6. **Benchmarks:** todos sem imposto de renda (CDI bruto). O IBOV e o IFIX entram como ETFs, com a taxa de administração embutida. O misto só é uma boa referência se a carteira tiver, de fato, a proporção dele. Com a cota por classe, o script monta o misto com o peso das vagas (60/40 para 3 ações + 2 FIIs); sem cota, a proporção depende do que passa nos filtros em cada mês. O script imprime a proporção real na seção **Composição da carteira** (ações x FIIs entre as posições, sem o caixa): ao final, a média mensal e a faixa de variação do peso de FIIs. Se ela for bem diferente da do misto, compare com o IBOV e o IFIX separadamente. Ticker fora das listas `ACOES` e `FIIS` conta como ação. O resultado depende muito do período: um backtest curto compara a estratégia com cada benchmark em um único ciclo de mercado.
7. **Dados do Yahoo Finance:** podem ter falhas e lacunas, principalmente em proventos de FIIs. Para FIIs, a CVM publica informes mensais estruturados.

## Testes

```powershell
pip install pytest
python -m pytest
```

Se você tiver o Node.js instalado, os testes também conferem a lógica do painel em JavaScript (as contas da página, como o plano de compra, precisam dar o mesmo resultado das do Python); sem o Node, esses testes são pulados.

Os testes são offline (dados sintéticos e respostas simuladas do Banco Central) e cobrem o painel simples (variações, plano de compra, a página, os dados que ela mostra e a publicação automática) e a contagem de proventos, o corte de proventos, os limites de peso, a regra de venda, o tratamento de ativos sem cotação, o XIRR, o drawdown, os benchmarks (inclusive o misto), a composição da carteira e o diagnóstico de exclusão.
