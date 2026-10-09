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

Se o PowerShell bloquear a ativação do ambiente virtual, use `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` e execute os comandos abaixo com `.\.venv\Scripts\python.exe`.

## Uso

```powershell
python scanner_dividendos_b3.py --anos 8 --aporte 200 --top 5
python scanner_dividendos_b3.py --anos 10 --aporte 200 --top 5 --saida minha_carteira
python scanner_dividendos_b3.py --sem-benchmark    # não baixa CDI nem ETFs de referência
```

O período simulado é menor que o baixado, porque os filtros exigem 36 meses de histórico. A lista de ativos (`ACOES` e `FIIS`) e os parâmetros ficam no início do arquivo.

Arquivos gerados (prefixo `resultado_dividendos`, ou o valor de `--saida`):

| Arquivo | Conteúdo |
|---|---|
| `*_ranking.csv` | Ativos aprovados no último mês fechado, com yield de 12 meses, períodos com proventos, retorno de preço em 12 meses e pontuação |
| `*_backtest.csv` | Patrimônio mensal, total aportado, caixa, ganho/perda, ativos mantidos, quantidade de elegíveis e os selecionados, mais uma coluna `valor_*` por benchmark |
| `*_operacoes.csv` | Compras dos aportes e vendas por saída dos filtros (coluna `observacao` marca vendas feitas pelo último preço conhecido) |
| `*_comparacao.csv` | Valor final, ganho, XIRR e drawdown da estratégia e de cada benchmark, e a diferença de XIRR em pontos percentuais |

## XIRR, drawdown e benchmarks

O **XIRR** é a taxa de retorno anual dos fluxos de caixa datados: cada aporte mensal sai com sinal negativo e o patrimônio final entra com sinal positivo (base de 365 dias, a mesma do Excel). Diferente do "retorno vs. soma dos aportes", ele leva em conta *quando* cada real foi investido, e por isso é a medida para comparar com benchmarks. Se o último mês ainda não terminou, o patrimônio final é datado de hoje, e não do fim do mês.

A comparação coloca **os mesmos aportes, nas mesmas datas**, em cada referência:

| Benchmark | Fonte | Observação |
|---|---|---|
| CDI | Série diária 12 do Banco Central (SGS) | Sem custo de negociação |
| IBOV (BOVA11) | Yahoo Finance, preço ajustado do ETF | Aproxima o retorno total do Ibovespa, já líquido da taxa do ETF; aplica o mesmo custo de negociação da estratégia |
| IDIV (DIVO11) | Yahoo Finance, preço ajustado do ETF | Índice de dividendos; é um ETF recente, com histórico curto, e é ignorado (com aviso) se não cobrir todo o período do backtest |
| IFIX (XFIX11) | Yahoo Finance, preço ajustado do ETF | Benchmark de FIIs: ETF que replica o IFIX (algumas fontes citam a versão IFIX-L, mais líquida), negociado desde nov/2020. Ignorado com aviso se o Yahoo não tiver o ticker |

Um benchmark que não puder ser baixado também gera apenas um aviso, e o resto segue normalmente. A lista de ETFs fica em `BENCHMARKS_ETF`, no início do arquivo; para usar outra referência (por exemplo, outro ETF de FIIs), troque ou acrescente o ticker ali.

O **drawdown** (maior queda de um pico até o vale) é calculado sobre um **índice de retorno ponderado pelo tempo**, e não sobre o patrimônio. Os aportes empurram o patrimônio para cima e escondem parte da queda: num teste com 5 anos de aportes e uma queda de 30% em um mês, o patrimônio mostrava −28,3%, enquanto o índice de retorno mostra os −30,0% reais. O retorno de cada mês é `(patrimônio − aporte) / patrimônio do mês anterior − 1`; os custos de negociação ficam dentro do retorno, e o caixa parado rende zero. A mesma conta vale para a estratégia e para os benchmarks, então os números são comparáveis. Como os pontos são mensais, quedas dentro do mês não aparecem.

## Filtros e pontuação

| Critério | Regra inicial |
|---|---|
| Dividend yield de 12 meses | Entre 3% e 18% |
| Histórico de proventos | Pagamentos em pelo menos 2 de 3 períodos consecutivos de 12 meses |
| Corte de proventos | Proventos dos últimos 12 meses ≥ 50% dos 12 meses anteriores (`MIN_RAZAO_PROVENTOS`; `0` desativa) |
| Histórico de preços | Pelo menos 36 meses |

Pontuação: consistência dos proventos (45%), proximidade de um yield de referência de 6% (35%) e tendência de preço em 12 meses (20%). Os pesos são parâmetros iniciais para testes, não valores comprovadamente ótimos. Um yield de 17% não é necessariamente sustentável: pode vir de queda forte no preço ou de pagamento extraordinário.

## Regras de compra, manutenção e venda (como implementadas)

- **Comprar/reforçar:** o aporte do mês (mais o caixa de vendas) é dividido entre os `--top` melhores do ranking, respeitando os limites de peso abaixo.
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
6. **Benchmarks:** todos sem imposto de renda (CDI bruto). O IBOV e o IFIX entram como ETFs, com a taxa de administração embutida. Não há um benchmark único para uma carteira mista de ações e FIIs; compare com os dois. O resultado depende muito do período: um backtest curto compara a estratégia com cada benchmark em um único ciclo de mercado.
7. **Dados do Yahoo Finance:** podem ter falhas e lacunas, principalmente em proventos de FIIs. Para FIIs, a CVM publica informes mensais estruturados.

## Testes

```powershell
pip install pytest
python -m pytest
```

Os testes são offline (dados sintéticos e respostas simuladas do Banco Central) e cobrem a contagem de proventos, o corte de proventos, os limites de peso, a regra de venda, o tratamento de ativos sem cotação, o XIRR, o drawdown e os benchmarks.
