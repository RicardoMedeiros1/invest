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
```

O período simulado é menor que o baixado, porque os filtros exigem 36 meses de histórico. A lista de ativos (`ACOES` e `FIIS`) e os parâmetros ficam no início do arquivo.

Arquivos gerados (prefixo `resultado_dividendos`, ou o valor de `--saida`):

| Arquivo | Conteúdo |
|---|---|
| `*_ranking.csv` | Ativos aprovados no último mês fechado, com yield de 12 meses, períodos com proventos, retorno de preço em 12 meses e pontuação |
| `*_backtest.csv` | Patrimônio mensal, total aportado, caixa, ganho/perda, ativos mantidos, quantidade de elegíveis e os selecionados |
| `*_operacoes.csv` | Compras dos aportes e vendas por saída dos filtros (coluna `observacao` marca vendas feitas pelo último preço conhecido) |

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
5. **Métricas:** não há XIRR nem comparação com benchmark. O drawdown é calculado sobre o patrimônio, que cresce com os aportes.
6. **Dados do Yahoo Finance:** podem ter falhas e lacunas, principalmente em proventos de FIIs. Para FIIs, a CVM publica informes mensais estruturados.

## Testes

```powershell
pip install pytest
python -m pytest
```

Os testes são offline (dados sintéticos) e cobrem a contagem de proventos, o corte de proventos, os limites de peso, a regra de venda e o tratamento de ativos sem cotação.
