# Modelo econômico

Quanto a plataforma e o parceiro ganham por cliente, a partir de quantos clientes a
plataforma se paga, se a garantia em outra instituição compensa e o que mais pesa no
resultado. A página é `/negocio`; a API, `/v1/negocio/...`.

> **Todos os números deste documento são ilustrativos.** São premissas editáveis, não
> dado de nenhuma empresa. Só têm fonte os itens da seção
> [Dados públicos](#dados-publicos); tudo o mais é "premissa ilustrativa". Não é
> projeção, não é recomendação e não é parecer contábil, tributário ou jurídico.

## O modelo em linguagem simples

**A unidade é um cliente ativo médio, por mês.** O ano vale 12 meses. O parceiro médio é
o mês vezes os clientes por parceiro, e a carteira é o mês vezes o total de clientes.
Os totais anuais e da carteira saem das linhas do mês, já arredondadas, multiplicadas
por inteiros: a conta fecha centavo por centavo.

**Quem ganha o quê.**

- O **lojista** paga o intercâmbio, e quem o recebe é a plataforma (a emissora). A
  plataforma repassa uma parte ao parceiro.
- O **parceiro** paga à plataforma um fee por garantia ativa e um fee por operação de
  gravame (mais caro quando a garantia está em outra instituição).
- O **cliente** paga os juros do rotativo, quando entra nele, e deixa a plataforma ficar
  com uma pequena fração do rendimento do saldo em garantia.
- O **FIDC** (fundo de recebíveis) paga uma taxa à plataforma pela parte do saldo
  financiado que compra, e cobra funding (CDI mais spread) por ela.
- A plataforma ainda paga funding de balanço pela parte que não cedeu, processamento
  do cartão, custos de gravame e de execução, perda esperada e tributos.

A plataforma é a credora: recebe os juros e arca com a perda esperada, inclusive da
parte cedida ao FIDC (como se retivesse a cota subordinada). Esta é a leitura prudente
e está em `docs/DECISOES.md`, item 64.

## Quem paga quem

Por cliente, por mês. `origem → destino` diz quem paga e quem recebe. Receita é
arredondada para baixo e custo para cima, uma única vez por linha. Os totais são a soma
das linhas arredondadas. G é o gasto mensal, S o saldo em garantia, fi e fe as frações
interna e externa, fc a fração cedida ao FIDC, R o que vai para o rotativo no mês e n
os meses no rotativo.

| id | Nome | origem → destino | Conta | Arredonda |
|---|---|---|---|---|
| `intercambio` | Intercâmbio | lojista → plataforma | G × taxa de intercâmbio | para baixo |
| `repasse_intercambio` | Repasse do intercâmbio ao parceiro | plataforma → parceiro (transferência) | G × taxa × (1 − fração da plataforma) | para baixo |
| `fee_garantia_ativa` | Fee por garantia ativa | parceiro → plataforma (transferência) | garantias por cliente × fee | para baixo |
| `fee_gravame` | Fee de gravame | parceiro → plataforma (transferência) | gravames/mês × (fi × fee interna + fe × fee externa) | para baixo |
| `receita_sobre_investimento` | Receita sobre o investimento em garantia | cliente → plataforma | S × fi × taxa ao ano convertida para o mês | para baixo |
| `juros_rotativo` | Juros do rotativo | cliente → plataforma | R × min((1+i)^n − 1, 100%) | para baixo |
| `taxa_fidc` | Taxa do FIDC | fidc → plataforma | saldo financiado × fc × taxa ao ano convertida para o mês | para baixo |
| `funding_fidc` | Funding do FIDC | plataforma → fidc | saldo financiado × fc × (CDI ⊕ spread do FIDC) no mês | para cima |
| `funding_balanco` | Funding de balanço | plataforma → financiadores | saldo financiado × (1 − fc) × (CDI ⊕ spread de balanço) no mês | para cima |
| `perda_esperada` | Perda esperada | plataforma → inadimplência | saldo financiado × taxa anual de perda convertida para o mês | para cima |
| `processamento` | Processamento do cartão | plataforma → fornecedores | custo por cartão ativo | para cima |
| `gravame_custodiante` | Gravame no custodiante | plataforma → custodiante | gravames/mês × fe × custo do gravame externo | para cima |
| `execucao_interna` | Execução de garantia interna | plataforma → fornecedores | execuções/ano ÷ 12 × fi × custo interno | para cima |
| `execucao_externa` | Execução de garantia externa | plataforma → custodiante | execuções/ano ÷ 12 × fe × custo externo | para cima |
| `tributos_plataforma` | Tributos da plataforma | plataforma → governo | alíquota × receita da plataforma (linhas já arredondadas) | para cima |
| `tributos_parceiro` | Tributos do parceiro | parceiro → governo | alíquota × receita do parceiro | para cima |

- **Saldo financiado** = G × dias financiados ÷ (365/12), mais R × n.
- **R** = G × fração dos clientes no rotativo × fração da fatura rolada.
- **⊕** é a composição "CDI + spread": (1 + CDI) × (1 + spread) − 1.
- Toda taxa ao ano vira mensal por juros compostos, (1 + a)^(1/12) − 1. Frequência de
  eventos (vezes por ano) vira mensal dividindo por 12, porque frequência não se
  compõe. Dias corridos viram mês por 365/12.

**As transferências entre plataforma e parceiro.** São três linhas:
`repasse_intercambio`, `fee_garantia_ativa` e `fee_gravame`. Cada uma é receita de um
lado e custo do outro, com o mesmo valor (arredondado uma só vez). Na visão da
plataforma e na do parceiro elas aparecem. Na visão do **arranjo** (plataforma e
parceiro juntos) elas são ignoradas, porque o dinheiro só muda de bolso dentro do
arranjo: contá-las somaria a mesma quantia como receita e como custo. É por construção
que margem da plataforma + margem do parceiro = margem do arranjo, e há teste de
propriedade para isso.

## Premissas

Gerada do `app/premissas_economia.toml` por um script, não digitada. "Aceita" é o que a
validação deixa passar; "razoável" são os extremos que o tornado testa. "Base" é o
valor do conjunto Base. Frações e taxas são decimais (0,1365 = 13,65%).

| Premissa | Unidade | Base | Aceita | Razoável (tornado) | Origem e fonte |
|---|---|---|---|---|---|
| Gasto mensal (`gasto_mensal`) | R$/mês | 1500 | 0 a 50000 | 500 a 4000 | **Ilustrativa.** ordem de grandeza do gasto mensal no cartão de um cliente que usa o limite garantido; sem fonte de mercado. |
| Saldo em garantia (`saldo_em_garantia`) | R$ | 8000 | 100 a 1000000 | 2000 a 30000 | **Ilustrativa.** saldo médio aplicado em garantia por cliente, de ordem de grandeza compatível com as faixas da carteira sintética; sem fonte de mercado. |
| LTV médio (`ltv_medio`) | fração | 0,85 | 0,60 a 1,00 | 0,70 a 0,95 | **Ilustrativa.** média dentro dos LTVs da política de referência do projeto, que vão de 0,60 (ações) a 1,00 (depósito). |
| Fração interna (`fracao_interna`) | fração | 0,70 | 0 a 1 | fora do tornado | **Ilustrativa.** parcela das garantias custodiadas na própria plataforma; soma com a fração externa e dá 1. |
| Fração externa (`fracao_externa`) | fração | 0,30 | 0 a 1 | 0 a 1 | **Ilustrativa.** parcela das garantias em custódia externa; o valor base repete a fração da carteira sintética do painel de risco (0,30). |
| Garantias por cliente (`garantias_por_cliente`) | garantia/cliente | 1,5 | 1 a 10 | 1 a 3 | **Ilustrativa.** média da carteira sintética do painel de risco (pesos 60/30/10 para 1, 2 e 3 garantias). |
| Gravames ao mês (`gravames_ao_mes`) | operação/cliente/mês | 0,10 | 0 a 5 | 0,05 a 0,30 | **Ilustrativa.** operações de gravame (constituir ou baixar) por cliente por mês; sem fonte de mercado. |
| Dias financiados (`dias_financiados`) | dia | 10 | 0 a 60 | 0 a 30 | **Ilustrativa.** dias entre pagar o arranjo de pagamentos e receber do cliente, período em que a plataforma financia o gasto. |
| Taxa de intercâmbio (`taxa_de_intercambio`) | fração do gasto | 0,012 | 0 a 0,05 | 0,008 a 0,018 | **Ilustrativa.** taxa média de intercâmbio sobre o gasto no crédito; o teto regulatório existe só para débito e pré-pago. Fonte: Res. BCB 246/2022 (teto só para débito e pré-pago). |
| Fração do intercâmbio da plataforma (`fracao_do_intercambio_da_plataforma`) | fração | 0,50 | 0 a 1 | 0,30 a 0,70 | **Ilustrativa.** parte do intercâmbio que fica com a plataforma, o resto é repassado ao parceiro. |
| Fee por garantia ativa (`fee_por_garantia_ativa`) | R$/garantia/mês | 2,00 | 0 a 100 | 0,50 a 5,00 | **Ilustrativa.** tarifa mensal que o parceiro paga à plataforma por garantia ativa; sem fonte de mercado. |
| Fee de gravame interno (`fee_gravame_interna`) | R$/operação | 1,00 | 0 a 500 | 0 a 3 | **Ilustrativa.** tarifa por operação de gravame em garantia interna, cobrada do parceiro; sem fonte de mercado. |
| Fee de gravame externo (`fee_gravame_externa`) | R$/operação | 8,00 | 0 a 500 | 3 a 20 | **Ilustrativa.** tarifa por operação de gravame em garantia externa, mais alta que a interna por repassar o custo do custodiante; sem fonte de mercado. |
| Taxa sobre investimento ao ano (`taxa_sobre_investimento_ao_ano`) | 1/ano | 0,003 | 0 a 0,03 | 0,001 a 0,006 | **Ilustrativa.** parte do rendimento do saldo em garantia que a plataforma retém, ao ano; sem fonte de mercado. |
| Fração de clientes no rotativo (`fracao_de_clientes_no_rotativo`) | fração | 0,05 | 0 a 1 | 0 a 0,15 | **Ilustrativa.** fração dos clientes que não paga a fatura inteira e entra no rotativo no mês; sem fonte de mercado. |
| Fração da fatura rolada (`fracao_da_fatura_rolada`) | fração | 0,30 | 0 a 1 | 0,10 a 0,60 | **Ilustrativa.** fração da fatura que quem entra no rotativo deixa de pagar; sem fonte de mercado. |
| Taxa do rotativo ao mês (`taxa_do_rotativo_ao_mes`) | 1/mês | 0,08 | 0 a 0,30 | 0,03 a 0,15 | **Ilustrativa.** taxa de juros do rotativo ao mês, abaixo da média de mercado observada pelo BC; o total cobrado respeita o teto legal. Fonte: BCB, taxa média do rotativo, ago/2026: 15,18% a.m.; Lei 14.690/2023 e Res. CMN 5.112/2023: juros e encargos limitados a 100% do valor original. |
| Meses no rotativo (`meses_no_rotativo`) | mês | 2 | 1 a 24 | 1 a 6 | **Ilustrativa.** meses seguidos que um cliente fica no rotativo antes de regularizar; o teto legal de 100% do valor original vale para o total. |
| Fração cedida ao FIDC (`fracao_cedida_ao_fidc`) | fração | 0,60 | 0 a 1 | 0 a 0,90 | **Ilustrativa.** parcela do saldo financiado cedida a um fundo de recebíveis (FIDC); o resto fica no balanço da plataforma. |
| Taxa do FIDC ao ano (`taxa_fidc_ao_ano`) | 1/ano | 0,005 | 0 a 0,05 | 0,002 a 0,01 | **Ilustrativa.** taxa ao ano que o FIDC paga à plataforma pela originação e gestão dos recebíveis cedidos; sem fonte de mercado. |
| CDI ao ano (`cdi_ao_ano`) | 1/ano | 0,1365 | 0 a 0,50 | 0,10 a 0,16 | Taxa DI-B3 vigente na data da análise; o DI acompanha a Selic, definida pelo Copom. Fonte: DI-B3 13,65% a.a. em 06/11/2026, ANBIMA. |
| Spread do FIDC ao ano (`spread_fidc_ao_ano`) | 1/ano | 0,03 | 0 a 0,30 | 0,015 a 0,05 | **Ilustrativa.** spread sobre o CDI que o FIDC cobra pelo funding, ao ano; sem fonte de mercado. |
| Spread de balanço ao ano (`spread_balanco_ao_ano`) | 1/ano | 0,04 | 0 a 0,30 | 0,02 a 0,08 | **Ilustrativa.** spread sobre o CDI do funding com recursos do balanço, ao ano; sem fonte de mercado. |
| Taxa base de perda ao ano (`taxa_base_de_perda_ao_ano`) | 1/ano | 0,005 | 0 a 0,50 | 0,002 a 0,02 | **Ilustrativa.** perda anual de fundo sobre o saldo financiado, fora dos eventos do cenário de risco; sem fonte de mercado. |
| Frequência do cenário ao ano (`frequencia_do_cenario_ao_ano`) | vez/ano | 0,05 | 0 a 12 | 0 a 0,5 | **Ilustrativa.** quantas vezes por ano o cenário de risco escolhido acontece; a perda do evento vem do painel de risco. |
| Custo de processamento por cartão (`custo_de_processamento_por_cartao`) | R$/cartão/mês | 3,00 | 0 a 100 | 1 a 8 | **Ilustrativa.** custo mensal de processadora por cartão ativo; sem fonte de mercado. |
| Custo do gravame externo (`custo_do_gravame_externo`) | R$/operação | 5,00 | 0 a 500 | 2 a 15 | **Ilustrativa.** custo por operação de gravame pago ao custodiante externo; sem fonte de mercado. |
| Execuções ao ano (`execucoes_ao_ano`) | execução/cliente/ano | 0,02 | 0 a 12 | 0 a 0,10 | **Ilustrativa.** execuções de garantia por cliente ao ano, quando a garantia é executada por inadimplência; sem fonte de mercado. |
| Custo de execução interna (`custo_de_execucao_interna`) | R$/execução | 15,00 | 0 a 5000 | 5 a 50 | **Ilustrativa.** custo operacional por execução de garantia na própria plataforma; sem fonte de mercado. |
| Custo de execução externa (`custo_de_execucao_externa`) | R$/execução | 60,00 | 0 a 5000 | 20 a 200 | **Ilustrativa.** custo por execução de garantia em custodiante externo, maior que o interno por envolver terceiros; sem fonte de mercado. |
| Alíquota sobre a receita (`aliquota_sobre_receita`) | fração | 0,10 | 0 a 0,50 | 0,06 a 0,17 | **Ilustrativa.** alíquota única de tributos sobre a receita; não representa o regime tributário de nenhuma empresa. |
| Custo fixo mensal (`custo_fixo_mensal`) | R$/mês | 400000 | 0 a 100000000 | 200000 a 1000000 | **Ilustrativa.** custo fixo mensal da plataforma (equipe, infraestrutura), usado no ponto de equilíbrio; sem fonte de mercado. |
| Parceiros (`parceiros`) | parceiro | 5 | 1 a 1000 | 1 a 20 | **Ilustrativa.** número de parceiros (emissores) que distribuem o produto; sem fonte de mercado. |
| Clientes por parceiro (`clientes_por_parceiro`) | cliente/parceiro | 20000 | 1 a 10000000 | 2000 a 100000 | **Ilustrativa.** clientes ativos por parceiro; sem fonte de mercado. |

O CSV da API traz a mesma informação, com as taxas como fração decimal.

## Da perda do cenário à perda anual

O painel de risco calcula a perda **condicional** a um choque, não uma perda por ano.
Para o modelo, a taxa anual de perda é:

> taxa base ao ano + frequência do cenário ao ano × taxa condicional do cenário

A taxa condicional é `perda_esperada_total / exposicao` do `simular_painel` na carteira
sintética (1.000 clientes, semente 42), medida duas vezes: com toda a garantia interna e
com toda a externa. Para uma fração externa fe, a condicional é a mistura linear:
fi × condicional interna + fe × condicional externa. Variar a fração não roda o motor de
novo.

**Conta do conjunto Base** (cenário Custodiante fora do ar):

1. Condicional interna: 0,0052%; condicional externa: 8,5520%.
2. Condicional mista: 70% × 0,0052% + 30% × 8,5520% = 2,5692%.
3. Taxa anual: 0,50% + 0,05 evento/ano × 2,5692% = 0,6285% ao ano.
4. Taxa mensal: (1 + 0,6285%)^(1/12) − 1 = 0,0522% ao mês.
5. Saldo financiado: R$ 1.500,00 × 10 ÷ (365/12) = R$ 493,1507, mais
   R$ 1.500,00 × 5% × 30% = R$ 22,50 por mês no rotativo × 2 meses = R$ 45,00;
   total de R$ 538,1507, que a linha arredonda para cima: R$ 538,16.
6. Perda esperada: R$ 538,16 × 0,0522% = R$ 0,28, que a linha arredonda para cima:
   **R$ 0,29 por cliente por mês**.

A carteira sintética usa entre 0% e 95% do limite. Se o cliente médio usar menos, a taxa
medida tende a ser maior que a dele: erra para o lado prudente.

## Ponto de equilíbrio

N = ⌈custo fixo mensal ÷ margem da plataforma por cliente por mês⌉, em centavos
inteiros. Se a margem for zero ou negativa, nenhum número de clientes paga o custo fixo,
e o resultado é "não existe", com o motivo.

No conjunto Base: R$ 400.000,00 ÷ R$ 4,13 = 96.852,3, ou seja, **96.853 clientes**. A
carteira Base tem 100.000. Fica pouco acima do ponto de equilíbrio, de propósito.

## Interna x externa

O modelo calcula a margem da plataforma por cliente com toda a garantia interna (fi = 1)
e com toda a externa (fe = 1). A diferença é o custo a mais da garantia em outra
instituição. Para empatar, ele procura, em centavos:

- o **fee de gravame externo** que zera a diferença, se houver gravames;
- o **gasto mensal** que zera a diferença, dizendo se esse gasto cabe no limite que a
  garantia gera (S × LTV).

Conjunto Base (ilustrativo): margem interna R$ 4,71, externa R$ 2,79, diferença
R$ 1,92. A externa empata com um fee de gravame externo de R$ 29,30 (a base é R$ 8,00)
ou com um gasto mensal de R$ 2.465,84, que cabe no limite. Para os outros conjuntos:

| | Conservador | Base | Otimista |
|---|---:|---:|---:|
| Margem da plataforma, toda interna | R$ -3,05 | R$ 4,71 | R$ 19,84 |
| Margem da plataforma, toda externa | R$ -5,08 | R$ 2,79 | R$ 16,45 |
| Custo a mais da externa | R$ 2,03 | R$ 1,92 | R$ 3,39 |
| Fee de gravame externo que empata | R$ 30,50 | R$ 29,30 | R$ 45,70 |
| Gasto mensal que empata | não existe | R$ 2.465,84 | R$ 3.030,72 |

No Conservador, nenhum gasto empata (a margem é negativa nos dois lados).

## Tornado

Cada premissa numérica varia até os extremos da sua faixa razoável, uma de cada vez,
mantendo a coerência: o gasto não passa de S × LTV e o saldo não fica abaixo de G ÷ LTV,
então um extremo que quebraria isso é cortado, e a barra avisa. A métrica é a margem da
plataforma por cliente por mês. A barra vai do menor ao maior entre {baixo, base, alto},
porque algumas premissas não são monótonas (meses no rotativo: o teto legal trava os
juros e o funding continua crescendo). As barras são ordenadas pela amplitude.

No conjunto Base, as cinco que mais mexem (margem em R$ por cliente por mês, no extremo
baixo e no alto da faixa razoável; a base é R$ 4,13):

| Premissa | Faixa | Margem no baixo | Margem no alto |
|---|---|---:|---:|
| Dias financiados | 0 a 30 | R$ 10,95 | R$ -9,52 |
| Meses no rotativo | 1 a 6 | R$ 2,69 | R$ 11,40 |
| Fração de clientes no rotativo | 0 a 0,15 | R$ 1,39 | R$ 9,63 |
| Gasto mensal | R$ 500 a R$ 4.000 | R$ 2,04 | R$ 9,33 |
| Fração do intercâmbio da plataforma | 0,30 a 0,70 | R$ 0,53 | R$ 7,73 |

Quem financia o gasto por mais dias perde margem (funding), e quem tem mais gente no
rotativo ganha (juros). Com o rotativo limitado por lei, parte dessa margem é frágil.

## Os três conjuntos

Valores prontos no TOML, todos ilustrativos, obtidos rodando `POST /v1/negocio/simulacoes`
em cada conjunto (com todas as fontes de receita ligadas).

| | Conservador | Base | Otimista |
|---|---:|---:|---:|
| Cenário de risco | Custodiante fora do ar | Custodiante fora do ar | Crash da bolsa (ações -30%) |
| Clientes (parceiros × clientes por parceiro) | 30.000 | 100.000 | 320.000 |
| Receita da plataforma por cliente por mês | R$ 15,17 | R$ 26,57 | R$ 51,16 |
| Custo da plataforma por cliente por mês | R$ 19,24 | R$ 22,44 | R$ 31,84 |
| **Margem da plataforma por cliente por mês** | **R$ -4,07** | **R$ 4,13** | **R$ 19,32** |
| **Margem do parceiro por cliente por mês** | **R$ 1,05** | **R$ 4,79** | **R$ 12,55** |
| Margem do arranjo por cliente por mês | R$ -3,02 | R$ 8,92 | R$ 31,87 |
| Custo fixo mensal | R$ 600.000,00 | R$ 400.000,00 | R$ 300.000,00 |
| Ponto de equilíbrio (clientes) | não existe | 96.853 | 15.528 |
| Resultado mensal da plataforma na carteira (margem total menos custo fixo) | R$ -722.100,00 | R$ 13.000,00 | R$ 5.882.400,00 |
| Resultado anual da plataforma na carteira | R$ -8.665.200,00 | R$ 156.000,00 | R$ 70.588.800,00 |
| Resultado mensal do parceiro médio | R$ 10.500,00 | R$ 95.800,00 | R$ 502.000,00 |
| Taxa anual de perda | 1,86% | 0,63% | 0,30% |

- **Conservador** dá margem negativa para a plataforma e não tem ponto de equilíbrio.
  Foi mantido assim de propósito: com premissas ruins (gasto e saldo baixos, mais gente
  na externa, custos e perda altos), o negócio não para de pé, e o modelo diz isso em vez
  de esconder.
- **Base** fica pouco acima do equilíbrio: a plataforma ganha R$ 13.000,00 por mês sobre
  100.000 clientes. O resultado é sensível ao que o tornado mostra.
- **Otimista** usa o crash da bolsa, cuja perda condicional é pequena (a margem de
  segurança das ações absorve a queda; ver o painel de risco). Por isso a perda anual
  cai e a margem sobe.

Os conjuntos são pontos de partida. A página permite editar cada premissa.

<a id="dados-publicos"></a>
## Dados públicos

Só entra aqui o que tem fonte. O resto é "premissa ilustrativa".

| Dado | Valor | Fonte e data |
|---|---|---|
| CDI / DI-B3 | 13,65% ao ano em 06/11/2026, após a decisão do Copom de 04/11/2026 que fixou a Selic em 13,75% | ANBIMA, indicadores: https://www.anbima.com.br/informacoes/indicadores/ |
| Teto de intercâmbio | 0,5% para débito e 0,7% para pré-pago, vigente desde 01/04/2023 (Resolução BCB nº 246/2022). A norma trata de débito e pré-pago; não encontrei teto para crédito | https://www.mattosfilho.com.br/unico/regras-tarifas-intercambio-prazos-liquidacao/ e https://regulacaoemnumeros-direitorio.fgv.br/post/banco-central-aprova-resolucao-que-disciplina-tarifa-de-intercambio-de-cartao-de-debito-e-pre |
| Teto do rotativo | Juros e encargos do rotativo e do parcelamento da fatura limitados a 100% do valor original da dívida, para dívidas a partir de 03/01/2024 (Lei 14.690/2023, art. 28, e Resolução CMN nº 5.112/2023) | https://www.mattosfilho.com.br/unico/banco-lei-desenrola/ |
| Taxa média do rotativo | 451,7% ao ano em setembro de 2026 (444,9% em agosto) e, em agosto, 15,18% ao mês, segundo a imprensa citando o Banco Central; conferir na série das Estatísticas Monetárias e de Crédito do BC | https://wscom.com.br/economia/2026/10/30/juro-do-rotativo-do-cartao-de-credito-sobe-para-4517-ao-ano-em-setembro-aponta-banco-central/ |

- Como o intercâmbio de crédito não tem teto na norma que encontrei, a taxa de
  intercâmbio do modelo é **premissa ilustrativa**; os tetos de débito e pré-pago não se
  aplicam a ela.
- A taxa média do rotativo passa de 100% ao ano porque o BC anualiza a taxa mensal. Isso
  não contradiz o teto legal, que é sobre o total de juros e encargos em relação ao valor
  original da dívida, não sobre a taxa anualizada. O modelo usa taxa abaixo da média e
  aplica o teto de 100%.
- Os valores de imprensa (rotativo) não foram conferidos na série primária do BC por
  mim; fica em "Próximos passos" do README.

## Limitações

- Tudo o que não está na tabela de dados públicos é ilustrativo. Trocar os valores muda
  as conclusões, e o tornado mostra por quanto.
- Um cliente médio, em regime, sem dispersão: uma carteira real tem clientes muito
  diferentes, e a média esconde a cauda.
- Sem capital regulatório, sem custo de aquisição de cliente nem churn, sem modelagem
  tributária detalhada (alíquota única sobre a receita), sem projeção de vários anos nem
  valuation. Estão em "Próximos passos" do README.
- A perda vem de uma carteira sintética de 1.000 clientes, e a conversão em perda anual
  depende da frequência escolhida para o cenário, que é premissa.
- Os juros do rotativo entram em regime (uma leva por mês), e o IOF fica de fora por ser
  do governo.
- Não é parecer jurídico, tributário ou contábil.

As decisões de desenho estão em `docs/DECISOES.md`, itens 57 a 71.
