# Modelo econômico (`/negocio`): plano de implementação

> **Para agentes:** use superpowers:subagent-driven-development para executar este plano
> tarefa a tarefa. Os passos usam `- [ ]` para acompanhar o progresso.

**Objetivo.** Mostrar, com um modelo simples, transparente e editável, quanto a
plataforma e o parceiro ganham por cliente ativo, onde fica o ponto de equilíbrio e como
a economia muda entre garantia interna e externa. Toda saída tem de ser rastreável até
uma premissa visível.

**Arquitetura.**
- **Domínio.** O motor é um pacote puro, `app/dominio/economia/`, sem FastAPI e sem
  banco. Ele recebe premissas já validadas e as taxas de perda condicional de um
  cenário, e devolve as linhas de receita e custo, as visões por parte, o ponto de
  equilíbrio, a comparação interna x externa e o tornado.
- **Infra.** As premissas e os conjuntos ficam em `app/premissas_economia.toml`. As
  taxas de perda saem do motor de risco que já existe (`simular_painel`), rodando duas
  carteiras sintéticas por cenário, com cache.
- **Borda.** A API fica em `/v1/negocio/...`, com resposta em JSON ou CSV. A tela
  `/negocio` segue o estilo do `/risco`.

**Tecnologia.** Python 3.12, `decimal`, `tomllib`, FastAPI e Pydantic 2, Hypothesis e
Playwright. Nenhuma dependência nova.

**Especificação.** O pedido "Modelo econômico: quanto a plataforma e o parceiro ganham
(página /negocio)" (seções 0 a 9). As decisões de desenho estão em
[DECISOES.md](DECISOES.md), itens 57 em diante.

## Desenho em uma página

**Unidade.** Um cliente ativo médio por mês. O ano vale 12 vezes o mês. O parceiro
médio é o mês vezes os clientes por parceiro, e a carteira é o mês vezes o total de
clientes. Os totais anuais e da carteira saem das linhas mensais já arredondadas,
multiplicadas por inteiros, então continuam fechando centavo por centavo.

**Partes.** `cliente`, `lojista`, `parceiro`, `plataforma`, `fidc`, `custodiante`,
`fornecedores`, `financiadores`, `governo` e `inadimplencia` (o que não volta). Cada
linha tem origem e destino. As visões funcionam assim:
- a da plataforma soma as linhas com `destino == plataforma` como receita e as linhas
  com `origem == plataforma` como custo;
- a do parceiro faz o mesmo com o parceiro;
- a do arranjo (plataforma e parceiro juntos) ignora as linhas que vão de um para o
  outro. É por construção que plataforma + parceiro = arranjo.

**Linhas** (por cliente por mês). G é o gasto mensal, S o saldo em garantia, fi e fe as
frações interna e externa, e fc a fração cedida ao FIDC.

| id | origem → destino | conta | arredonda |
|---|---|---|---|
| `intercambio` | lojista → plataforma | G × taxa de intercâmbio | para baixo |
| `repasse_intercambio` | plataforma → parceiro | G × taxa × (1 − fração da plataforma) | para baixo |
| `fee_garantia_ativa` | parceiro → plataforma | garantias por cliente × fee | para baixo |
| `fee_gravame` | parceiro → plataforma | gravames/mês × (fi × fee interna + fe × fee externa) | para baixo |
| `receita_sobre_investimento` | cliente → plataforma | S × fi × taxa ao ano convertida para o mês | para baixo |
| `juros_rotativo` | cliente → plataforma | R × min((1+i)^n − 1, 100%) | para baixo |
| `taxa_fidc` | fidc → plataforma | saldo financiado × fc × taxa ao ano convertida para o mês | para baixo |
| `funding_fidc` | plataforma → fidc | saldo financiado × fc × (CDI ⊕ spread do FIDC) convertido para o mês | para cima |
| `funding_balanco` | plataforma → financiadores | saldo financiado × (1 − fc) × (CDI ⊕ spread de balanço) no mês | para cima |
| `perda_esperada` | plataforma → inadimplencia | saldo financiado × taxa anual de perda convertida para o mês | para cima |
| `processamento` | plataforma → fornecedores | custo por cartão ativo | para cima |
| `gravame_custodiante` | plataforma → custodiante | gravames/mês × fe × custo do gravame externo | para cima |
| `execucao_interna` | plataforma → fornecedores | execuções/ano ÷ 12 × fi × custo interno | para cima |
| `execucao_externa` | plataforma → custodiante | execuções/ano ÷ 12 × fe × custo externo | para cima |
| `tributos_plataforma` | plataforma → governo | alíquota × receita da plataforma (linhas já arredondadas) | para cima |
| `tributos_parceiro` | parceiro → governo | alíquota × receita do parceiro | para cima |

Termos da tabela:
- **Saldo financiado** = G × dias financiados ÷ (365/12), mais R × n.
- **R** é o que vai para o rotativo por mês: G × fração dos clientes no rotativo × fração
  da fatura rolada.
- **n** é o número de meses no rotativo.
- **⊕** é a composição "CDI + spread": (1 + CDI) × (1 + spread) − 1.

As transferências entre plataforma e parceiro (`repasse_intercambio`, os dois fees)
aparecem dos dois lados com o mesmo valor, arredondado uma vez só.

**Convenções.**
- Toda taxa ao ano vira mensal por juros compostos: (1 + a)^(1/12) − 1. É o mesmo que
  21 dos 252 dias úteis do CDI.
- Frequência de eventos (vezes por ano) vira mensal dividindo por 12, porque frequência
  não se compõe.
- Dias corridos viram mês por 365/12.

**Perda.** A taxa anual de perda é a taxa base ao ano mais a frequência anual do cenário
vezes a taxa condicional do cenário. A taxa condicional é fi × condicional interna mais
fe × condicional externa. Cada condicional é `perda_esperada_total / exposicao` do
`simular_painel` sobre a carteira sintética (1.000 clientes, semente 42), gerada com
fração externa 0 e com fração externa 1. Assim são duas rodadas do motor por cenário,
guardadas em cache. Variar a fração externa não roda o motor de novo, porque a mistura
é linear.

**Rotativo.** O teto da Lei 14.690/2023, regulamentada pela Resolução CMN 5.112/2023,
entra como `TETO_DE_JUROS_E_ENCARGOS = 1`: juros e encargos de cada mês de rotativo
nunca passam de 100% do valor original. A fórmula mostra quando o teto foi aplicado.

**Ponto de equilíbrio.** N = ⌈custo fixo mensal ÷ margem da plataforma por cliente por
mês⌉, em centavos inteiros. Se a margem for zero ou negativa, o resultado é `None`, com
o motivo.

**Interna x externa.** As margens por cliente são calculadas com fi = 1 e com fe = 1. A
diferença é o custo a mais da externa. Para empatar, o modelo procura em centavos:
- o fee de gravame externo que zera a diferença, se houver gravames;
- o gasto mensal que zera a diferença, dizendo se ele cabe no limite que a garantia
  gera.

**Tornado.** Cada premissa numérica varia até os extremos da sua faixa razoável,
respeitando a coerência: o gasto não passa de S × LTV e o saldo não fica abaixo de
G ÷ LTV, então os extremos são cortados para caber. A métrica é a margem da plataforma
por cliente por mês. A barra vai do mínimo ao máximo de {baixo, base, alto}, porque há
premissas não monótonas, como meses no rotativo. As barras são ordenadas pela
amplitude.

## Restrições gerais

- Dinheiro sempre em `Dinheiro` (centavos inteiros), nunca `float`. Taxas em `Decimal`,
  com a unidade no tipo (`TaxaAoAno`, `TaxaAoMes`, `Fracao`, `FrequenciaAoAno`).
- Cada linha é arredondada uma única vez: receita para baixo, custo para cima. Os
  totais são a soma das linhas já arredondadas.
- Código, nomes e documentação em português. Nenhuma marca real como dona do produto,
  nenhum dado de empresa, nenhum número, lei ou citação inventados: o que não tem fonte
  é "premissa ilustrativa".
- O domínio fica sem FastAPI e sem banco, e passa no mypy estrito.
- Premissas num TOML lido com `tomllib`. Nenhuma dependência nova.
- A API usa a mesma autenticação do `/v1/risco`, e os erros saem em RFC 7807.
- A tela segue o padrão das outras: sem `innerHTML`, SVG via `createElementNS` e CSP
  inalterada.

## Pontos de atenção na revisão

1. **Gasto no limite.** Gasto igual a S × LTV, com o arredondamento do produto, tem de
   ser aceito. Teste: `test_gasto_igual_ao_limite_e_aceito`.
2. **Carteira gigante.** Parceiros × clientes acima de 100 milhões tem de ser recusado,
   para nenhuma multiplicação explodir a resposta. Teste:
   `test_carteira_acima_do_teto_e_recusada`.
3. **Taxa digitada como percentual.** O usuário pode mandar "13.65" querendo dizer 13,65%.
   A faixa máxima de cada taxa recusa isso com uma mensagem que mostra a unidade. Teste:
   `test_taxa_em_percentual_e_recusada_com_unidade`.
4. **CSV com número negativo.** "-1234,56" é número e não pode virar texto; texto que
   começa com `=`, `+`, `-`, `@`, tab ou CR é neutralizado. Teste:
   `test_csv_negativo_continua_numero`.
5. **Rotativo com taxa zero ou um mês só.** Fator zero ou exato, sem divisão nem
   potência estranha. Teste: `test_rotativo_sem_juros`.

---

### Tarefa 1: Taxas com unidade e conversão de período

**Arquivos.** Criar `app/dominio/taxas.py` e o teste `tests/unidade/test_taxas.py`.

**Interfaces.** Produz:
- `Fracao(valor: Decimal)`: valor entre 0 e 1.
- `TaxaAoAno(valor: Decimal)`, com `.ao_mes() -> TaxaAoMes` e
  `.composta_com(spread: TaxaAoAno) -> TaxaAoAno`.
- `TaxaAoMes(valor: Decimal)`.
- `FrequenciaAoAno(valor: Decimal)`, com `.ao_mes() -> Decimal`.
- As constantes `MESES_NO_ANO = 12`, `DIAS_UTEIS_NO_ANO = 252` e
  `DIAS_CORRIDOS_NO_ANO = 365`, e a função `dias_para_meses(dias: Decimal) -> Decimal`.

Todas são dataclasses `frozen` que recusam valor negativo ou não finito com
`ValorInvalido`, e as contas usam precisão de 34 dígitos.

- [ ] **Passo 1.** Escrever os testes:
  - `test_taxa_ao_ano_vira_mensal_composta`: `TaxaAoAno(Decimal("0.1365")).ao_mes()`,
    elevada a 12, volta a 0.1365 com erro menor que 1e-20.
  - `test_ao_mes_e_o_mesmo_que_21_dias_uteis`: igual a (1 + a)^(21/252) − 1.
  - `test_composicao_cdi_mais_spread`: 0.10 composta com 0.03 dá exatamente 0.133.
  - `test_frequencia_divide_por_12`.
  - `test_dias_para_meses`: 365/12 dias dão 1 mês.
  - `test_recusa_negativo_e_infinito`.
  - Hypothesis: `ao_mes` é monótona e `ao_mes(0) == 0`.
- [ ] **Passo 2.** Rodar `uv run pytest tests/unidade/test_taxas.py` e ver os testes
  falharem.
- [ ] **Passo 3.** Implementar, com as unidades anotadas nos comentários (`{1/ano}`,
  `{1/mês}`).
- [ ] **Passo 4.** Rodar os testes e o mypy e ver os dois passarem.
- [ ] **Passo 5.** Commit `feat(economia): taxas com unidade e conversão de período`.

### Tarefa 2: Premissas, esquema e validação

**Arquivos.** Criar `app/dominio/economia/__init__.py` e
`app/dominio/economia/premissas.py`. Teste em
`tests/unidade/test_premissas_economia.py`.

**Interfaces.** Consome a Tarefa 1. Produz:
- `NomeDaPremissa(StrEnum)`, com os 34 nomes da tabela de premissas de
  [economia.md](economia.md).
- `TipoDaPremissa(StrEnum)`: `dinheiro`, `fracao`, `taxa_ao_ano`, `taxa_ao_mes`,
  `frequencia_ao_ano`, `quantidade` e `inteiro`.
- `FonteDeReceita(StrEnum)`: `intercambio`, `fee_garantia_ativa`, `fee_gravame`,
  `receita_sobre_investimento`, `juros_rotativo` e `taxa_fidc`.
- `EsquemaDaPremissa(nome, nome_exibicao, tipo, unidade, minimo, maximo,
  razoavel_minimo, razoavel_maximo, origem, fonte | None)`.
- `Premissas`: dataclass frozen com um campo tipado por premissa (dinheiro em
  `Dinheiro`, taxas nos tipos da Tarefa 1), mais `receitas_ligadas:
  frozenset[FonteDeReceita]` e `cenario_de_risco: str`. Tem
  `com(**mudancas) -> Premissas` e `valores() -> dict[NomeDaPremissa, Decimal]`.
- `montar_premissas(valores: Mapping[NomeDaPremissa, Decimal], receitas_ligadas,
  cenario_de_risco, esquema: Mapping[NomeDaPremissa, EsquemaDaPremissa]) -> Premissas`.
- `PremissasInvalidas(ValorInvalido)`, com o código `premissas_invalidas` e
  `detalhes["erros"] = [{"campo": ..., "mensagem": ...}]`.

- [ ] **Passo 1.** Escrever os testes:
  - `test_fora_da_faixa_lista_campo_e_unidade`.
  - `test_taxa_em_percentual_e_recusada_com_unidade`: `cdi_ao_ano=13.65` é recusado e a
    mensagem cita "ao ano" e o máximo.
  - `test_interna_mais_externa_precisa_dar_100`.
  - `test_gasto_acima_do_limite_da_garantia`, com mensagem que mostra S × LTV.
  - `test_gasto_igual_ao_limite_e_aceito`.
  - `test_carteira_acima_do_teto_e_recusada`: parceiros × clientes acima de 100.000.000.
  - `test_varios_erros_voltam_juntos`.
  - `test_dinheiro_com_mais_de_duas_casas_e_recusado`.
  - `test_inteiro_com_casas_e_recusado`.
  - `test_premissa_faltando_e_recusada`.
- [ ] **Passo 2.** Ver os testes falharem.
- [ ] **Passo 3.** Implementar. A validação junta todos os erros antes de levantar.
- [ ] **Passo 4.** Ver os testes passarem, com mypy limpo.
- [ ] **Passo 5.** Commit `feat(economia): premissas com unidade, faixa e coerência`.

### Tarefa 3: Linhas de receita e custo e as visões

**Arquivos.** Criar `app/dominio/economia/calculo.py`. Testes em
`tests/unidade/test_calculo_economia.py` e
`tests/propriedade/test_economia_prop.py`.

**Interfaces.** Consome a Tarefa 2. Produz:
- `Parte(StrEnum)`, com as 10 partes do desenho.
- `TaxasDePerda(cenario_id: str, nome: str, condicional_interna: Fracao,
  condicional_externa: Fracao)`.
- `Linha(id, nome, fonte: FonteDeReceita | None, origem: Parte, destino: Parte,
  valor: Dinheiro, formula: str, premissas: tuple[NomeDaPremissa, ...])`.
- `Visao(parte, receita, custo, margem)`, com `margem = receita - custo`.
- `ContaDaPerda(taxa_base_ao_ano, frequencia_ao_ano, condicional_interna,
  condicional_externa, condicional_mista, taxa_ao_ano, taxa_ao_mes,
  saldo_financiado: Dinheiro)`.
- `ResultadoDoMes(linhas: tuple[Linha, ...], plataforma: Visao, parceiro: Visao,
  arranjo: Visao, perda: ContaDaPerda, rotativo_no_teto: bool)`.
- `calcular_mes(premissas: Premissas, perdas: TaxasDePerda) -> ResultadoDoMes`.
- A constante `TETO_DE_JUROS_E_ENCARGOS = Decimal(1)`.

O valor exato de cada linha é calculado em `Decimal`, em centavos, e arredondado uma vez
com `ROUND_FLOOR` (receita) ou `ROUND_CEILING` (custo). A fórmula vem em texto
legível, por exemplo `"R$ 1.500,00 × 1,20%"`. Fonte desligada não gera linha.

- [ ] **Passo 1.** Escrever os testes:
  - `test_tudo_desligado_e_custos_zerados_da_margem_zero`.
  - `test_plataforma_mais_parceiro_e_o_arranjo`.
  - `test_transferencias_nao_entram_no_arranjo`.
  - `test_intercambio_exemplo`: R$ 1.500,00 a 1,20% dá R$ 18,00, com o repasse de 50% em
    R$ 9,00.
  - `test_juros_do_rotativo_respeitam_o_teto`: 30% ao mês por 6 meses passa do teto, e
    o fator fica em 1.
  - `test_rotativo_sem_juros`.
  - `test_receita_sobre_investimento_so_na_interna`.
  - `test_perda_usa_mistura_das_condicionais`.
  - Propriedades, com Hypothesis sobre premissas válidas (estratégia em
    `tests/propriedade/estrategias_economia.py`):
    - a soma das linhas bate com a margem, centavo por centavo, para as três visões;
    - mais perda nunca aumenta a margem da plataforma;
    - mais CDI ou mais spread nunca aumenta a margem da plataforma;
    - mais intercâmbio nunca diminui a receita da plataforma nem a do parceiro;
    - plataforma + parceiro = arranjo.
- [ ] **Passo 2.** Ver os testes falharem.
- [ ] **Passo 3.** Implementar `calcular_mes`.
- [ ] **Passo 4.** Ver os testes passarem, com mypy limpo.
- [ ] **Passo 5.** Commit `feat(economia): linhas com origem e destino e as três visões`.

### Tarefa 4: Ponto de equilíbrio, interna x externa e tornado

**Arquivos.** Criar `app/dominio/economia/analises.py`. Testes em
`tests/unidade/test_analises_economia.py` e mais propriedades em
`tests/propriedade/test_economia_prop.py`.

**Interfaces.** Consome a Tarefa 3. Produz:
- `PontoDeEquilibrio(clientes: int | None, motivo: str | None)` e
  `ponto_de_equilibrio(margem_por_cliente_mes: Dinheiro, custo_fixo_mes: Dinheiro) ->
  PontoDeEquilibrio`.
- `ResultadoDaCarteira(clientes_ativos, plataforma_mes, plataforma_ano,
  parceiro_por_parceiro_mes, parceiro_por_parceiro_ano, arranjo_mes)`.
- `ComparacaoInternaExterna(margem_interna, margem_externa, custo_a_mais_da_externa,
  fee_de_gravame_externo_para_empatar: Dinheiro | None, gasto_para_empatar: Dinheiro |
  None, gasto_cabe_no_limite: bool | None, parceiro_interna, parceiro_externa)`.
- `BarraDoTornado(premissa, valor_baixo, valor_alto, margem_no_baixo, margem_no_alto,
  minimo, maximo, amplitude, cortado_pela_coerencia: bool)`.
- `ResultadoEconomico(premissas, mes: ResultadoDoMes, ano: dict[Parte, Visao],
  carteira, equilibrio, interna_x_externa, tornado: tuple[BarraDoTornado, ...])`.
- `simular_negocio(premissas, perdas, esquema, incluir_tornado: bool = True) ->
  ResultadoEconomico`.

- [ ] **Passo 1.** Escrever os testes:
  - `test_equilibrio_n_paga_e_n_menos_1_nao` (propriedade): com N clientes, N × m − F ≥ 0;
    com N − 1, o resultado é negativo.
  - `test_sem_equilibrio_com_margem_zero_ou_negativa`.
  - `test_custo_fixo_zero_equilibrio_em_zero_clientes`.
  - `test_externa_custa_mais_no_base`.
  - `test_fee_para_empatar_empata`: recalculado com esse fee, a margem externa fica
    maior ou igual à interna, e um centavo a menos não empata.
  - `test_gasto_para_empatar_avisa_quando_nao_cabe`.
  - `test_tornado_contem_a_base` (propriedade).
  - `test_tornado_ordenado_por_amplitude`.
  - `test_tornado_corta_gasto_no_limite`.
  - `test_ano_e_12_vezes_o_mes`.
- [ ] **Passo 2.** Ver os testes falharem.
- [ ] **Passo 3.** Implementar. A busca do valor de empate parte da inclinação medida em
  dois pontos e ajusta centavo a centavo, com no máximo 1.000 passos e confirmação
  final. Se a inclinação não for positiva, o resultado é `None`.
- [ ] **Passo 4.** Ver os testes passarem, com mypy limpo e cobertura do pacote acima de
  90%.
- [ ] **Passo 5.** Commit `feat(economia): equilíbrio, interna x externa e tornado`.

### Tarefa 5: Premissas em TOML, conjuntos e perda vinda do motor de risco

**Arquivos.** Criar `app/premissas_economia.toml` e `app/infra/economia.py`. Teste em
`tests/unidade/test_premissas_toml.py`.

**Interfaces.** Consome as Tarefas 2 a 4, `app/infra/carteira_sintetica.py` e
`simular_painel`. Produz:
- `Conjunto(id, nome, descricao, valores, receitas_ligadas, cenario_de_risco)`.
- `ParametrosDaEconomia(esquema: Mapping[NomeDaPremissa, EsquemaDaPremissa],
  conjuntos: Mapping[str, Conjunto])`.
- `carregar_parametros_da_economia(arquivo=ARQUIVO_PADRAO) -> ParametrosDaEconomia`,
  com `lru_cache`, que confere que o TOML tem exatamente os nomes do enum.
- `taxas_de_perda(cenario_id: str) -> TaxasDePerda`, com `lru_cache`: duas rodadas de
  `simular_painel`, com a fração externa em 0 e em 1, 1.000 clientes e semente 42. Um
  cenário desconhecido levanta `PremissasInvalidas`.
- `premissas_do_conjunto(conjunto_id: str) -> Premissas`.

- [ ] **Passo 1.** Escrever os testes:
  - `test_toml_tem_todas_as_premissas`.
  - `test_conjunto_dentro_das_faixas`.
  - `test_tres_conjuntos_rodam_e_ficam_em_ordem`: margem da plataforma e do parceiro com
    conservador ≤ base ≤ otimista, e base positiva.
  - `test_faixa_razoavel_dentro_da_aceita`.
  - `test_taxas_de_perda_do_custodiante_fora_do_ar_maior_na_externa`.
  - `test_taxas_de_perda_em_cache`.
  - `test_cenario_desconhecido`.
- [ ] **Passo 2.** Ver os testes falharem.
- [ ] **Passo 3.** Escrever o TOML com valor, unidade, faixa aceita, faixa razoável e
  origem em uma linha para cada uma das 34 premissas, mais os três conjuntos.
  Implementar o carregador.
- [ ] **Passo 4.** Ver os testes passarem. Ajustar os valores dos conjuntos, nunca o
  teste, até a ordem valer, e registrar o ajuste em DECISOES.
- [ ] **Passo 5.** Commit `feat(economia): premissas em TOML, três conjuntos e perda do
  motor de risco`.

### Tarefa 6: API e CSV

**Arquivos.**
- Criar `app/api/rotas/negocio.py` e `app/api/csv_negocio.py`.
- Modificar `app/api/esquemas.py` (os esquemas do negócio), `app/api/erros.py` (código
  `premissas_invalidas`, 422) e `app/main.py` ou o lugar onde os routers são incluídos.
- Teste em `tests/integracao/test_negocio_api.py`.

**Interfaces.** Consome a Tarefa 5. Produz:
- `GET /v1/negocio/premissas`, que devolve `{premissas: [esquema], conjuntos: [...],
  cenarios_de_risco: [{id, nome}], fontes_de_receita: [...]}`.
- `POST /v1/negocio/simulacoes`, com corpo `SimulacaoDeNegocio`:
  - `conjunto` (padrão `base`);
  - `premissas: dict[NomeDaPremissa, Decimal]`, no máximo 40, sobrescrevendo o conjunto;
  - `receitas_ligadas: dict[FonteDeReceita, bool]`;
  - `cenario_de_risco`;
  - `incluir_tornado` (padrão `true`).
  
  Devolve `ResultadoDoNegocio` em JSON, ou CSV com `Accept: text/csv`.
- `gerar_csv(resultado: ResultadoEconomico, esquema) -> bytes`: UTF-8 com BOM,
  separador `;`, vírgula decimal sem separador de milhar e `\r\n`. Tem seções para as
  premissas, as linhas e os resultados. Texto que começa com `= + - @ \t \r` ganha `'` na
  frente; números saem do formatador e nunca são neutralizados.

- [ ] **Passo 1.** Escrever os testes:
  - `test_get_premissas_traz_unidade_faixa_origem`.
  - `test_post_base_devolve_linhas_margens_equilibrio_tornado`.
  - `test_post_sem_autenticacao_401`.
  - `test_premissa_fora_da_faixa_vira_rfc7807`: 422,
    `application/problem+json`, código `premissas_invalidas` e `erros[0].campo`.
  - `test_premissa_desconhecida_422`.
  - `test_cenario_desconhecido_422`.
  - `test_resposta_rapida`: menos de 1 s com o tornado.
  - `test_csv_abre_no_excel`: BOM, `;` e vírgula decimal.
  - `test_csv_neutraliza_formula`: na função, um texto `=HYPERLINK(...)` sai com `'`.
  - `test_csv_negativo_continua_numero`.
  - `test_csv_numeros_batem_com_json`.
  - `test_openapi_tem_exemplos_e_descricoes`.
- [ ] **Passo 2.** Ver os testes falharem.
- [ ] **Passo 3.** Implementar. A rota monta as premissas a partir do conjunto mais o
  que vier no corpo, chama `simular_negocio` e apresenta. Valores em dinheiro saem como
  `"1234.56"` e taxas como texto `Decimal`.
- [ ] **Passo 4.** Ver os testes passarem, em SQLite e em Postgres.
- [ ] **Passo 5.** Commit `feat(api): simulação econômica em JSON e CSV`.

### Tarefa 7: Tela `/negocio` e navegação comum

**Arquivos.**
- Criar `app/api/estatico/negocio.html`, `negocio.css` e `negocio.js`.
- Modificar `app/api/demo.py` (rota `/negocio`), `index.html` e `risco.html`
  (navegação) e `demo.css` (estilo da navegação).
- Testes em `tests/e2e/test_negocio.py` e `tests/integracao/test_demo.py`.

**Interfaces.** Consome a Tarefa 6.
- A navegação é `<nav class="paginas" aria-label="Páginas">`, com os links "Demonstração",
  "Risco" e "Negócio" e `aria-current="page"` na página atual.
- Os destaques são `#margem-plataforma`, `#margem-parceiro`, `#equilibrio` e
  `#resultado-carteira`, todos com `data-valor` igual ao texto da API.
- Os botões dos conjuntos são `#conjunto-conservador`, `#conjunto-base` e
  `#conjunto-otimista`.
- Os controles são `#gasto`, `#saldo`, `#externa` (%), `#cdi` (% a.a.), `#intercambio`
  (%), `#fee-api` (R$ por garantia por mês) e `#cenario-risco`.
- Os gráficos em SVG são `#grafico-fontes`, `#grafico-cascata` e `#grafico-tornado`.
- O CSV sai pelo botão `#exportar`: `fetch` com `Accept: text/csv`, depois um `Blob` e um
  link com `download`.
- O aviso de números ilustrativos fica em `#aviso-ilustrativo`.

- [ ] **Passo 1.** Escrever os testes:
  - `test_negocio_conjuntos_batem_com_a_api`: para cada conjunto, o `data-valor` dos
    destaques é igual ao da resposta do POST.
  - `test_negocio_valor_extremo_mostra_erro_da_api`: gasto acima do limite mostra a
    mensagem RFC 7807.
  - `test_negocio_sem_equilibrio`: com o fee e o intercâmbio zerados, a tela diz "não há
    ponto de equilíbrio".
  - `test_navegacao_entre_as_tres_paginas`.
  - `test_exportar_csv`: o download tem BOM e `;`.
  - `test_pagina_negocio_servida_com_csp` (integração).
- [ ] **Passo 2.** Ver os testes falharem.
- [ ] **Passo 3.** Implementar com a skill frontend-design, reaproveitando os tokens do
  `demo.css`. Os gráficos são desenhados na largura real e redesenhados ao redimensionar.
- [ ] **Passo 4.** Ver os testes passarem, inclusive os e2e antigos.
- [ ] **Passo 5.** Commit `feat(negocio): página /negocio e navegação comum`.

### Tarefa 8: Documentação e telas

**Arquivos.**
- Criar `docs/economia.md`.
- Modificar `README.md`, `docs/roteiro-video.md`, `docs/DECISOES.md` e
  `docs/arquitetura.md` (o diagrama, se couber).
- Criar `docs/img/negocio-*.png`.

- [ ] **Passo 1.** Escrever `docs/economia.md`:
  - o modelo em linguagem simples;
  - a tabela de quem paga quem;
  - as premissas com unidade, faixa, origem e fonte;
  - a conta da perda anual;
  - a lógica do ponto de equilíbrio;
  - a comparação interna x externa;
  - os dados públicos com fonte e data, e o que é "premissa ilustrativa".
- [ ] **Passo 2.** No README, a nova página, como abrir e o aviso. No roteiro, 20 a 30 s
  no `/negocio`, com o vídeo inteiro em até 3 min. Em DECISOES, os itens novos e as
  entradas em "O que os testes pegaram".
- [ ] **Passo 3.** Subir a aplicação, percorrer `/demo`, `/risco` e `/negocio` com o
  Playwright (os três conjuntos e valores extremos) e salvar as telas em `docs/img/`.
- [ ] **Passo 4.** Commit `docs: modelo econômico, roteiro e telas`.

### Tarefa 9: Revisão final e PR

- [ ] Rodar `superpowers:verification-before-completion`: ruff, mypy, pytest em SQLite
  e Postgres, `pytest -m e2e` e cobertura do domínio acima de 90%.
- [ ] Rodar `/code-review`, `/review-pr`, `dimensional-analysis` nas taxas,
  `insecure-defaults` e `sharp-edges` nas entradas e no CSV. Corrigir o que for achado e
  listar o resto em "Próximos passos".
- [ ] Push e PR para `main`.
