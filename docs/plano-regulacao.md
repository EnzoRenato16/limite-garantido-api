# Alinhamento regulatório — plano de implementação

> **Para quem executa:** use superpowers:subagent-driven-development, tarefa por tarefa,
> com TDD (superpowers:test-driven-development). Cada tarefa tem um implementador e um
> revisor. Passos com caixa (`- [ ]`) para acompanhar.

**Objetivo:** trocar a fase 2 ("pedir gravame a outro custodiante") pelos trilhos que
existem de fato, separar gravame de garantia, criar o empréstimo com garantia e alinhar
painel, conta do negócio, textos e documentação à auditoria regulatória de 20/11/2026,
revisada em 22/11/2026.

**Arquitetura:** um catálogo regulatório em TOML (`app/regulacao.toml`) é a fonte única do
que cada ativo aceita: infraestrutura, instrumento, trilho de entrada, produto, prazos,
LTV de partida e as afirmações com norma, artigo e categoria. O domínio lê esse catálogo
para validar combinações, a API o publica, a página `/regulacao` e `docs/regulacao.md` o
mostram. O gravame vira uma entidade com máquina de estados própria dentro da garantia; o
limite e o valor de empréstimo só existem com gravame `registrado`. Cada trilho de fora
tem uma contraparte simulada, determinística, que responde pelo mesmo callback assinado da
fase 2.

**Tecnologia:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2 e Alembic, Hypothesis,
Playwright, JavaScript puro nas páginas.

**Fonte:** o documento "Limite Garantido as a Service — checagem regulatória", auditado em
20/11/2026 e revisado em 22/11/2026 (fora do repositório). Quem executa recebe um resumo
dele, com os papéis já genéricos. Quando este plano e o documento divergirem, vale o
documento, e a divergência entra em `docs/DECISOES.md`.

## Desenho em uma página

**Papéis.** SCD do grupo: concede o crédito (cartão e empréstimo), emite o cartão e é a
beneficiária do gravame. CTVM do grupo: custodia e comanda o gravame. DTVM do grupo: fundos
DI. Plataforma autorizada da Res. Conjunta 12: troca de dados e bloqueio da previdência.
A fintech parceira é entidade tomadora de BaaS (Res. Conjunta 16/2025) e só cuida da
interface. O projeto **simula** o registro na B3, no Selic e na plataforma; não integra
com nenhuma delas.

**Ativos** (`TipoAtivo`): `cdb` (de banco parceiro), `lci`, `lca`, `coe`, `fundo_di`,
`tesouro_selic`, `tesouro_prefixado`, `tesouro_ipca`, `acoes`, `fii`, `etf`,
`previdencia`. O tipo `deposito` sai: a SCD não capta do público (Res. CMN 5.050, art.
10, I), e "CDB do próprio emissor" só vale se a casa for banco. As garantias `deposito`
existentes viram `cdb` com liquidez diária.

**Trilhos de entrada** (`Trilho`):

| Trilho | Ativos | Prazo | Pode recusar? | Base |
|---|---|---|---|---|
| `casa` | cdb, fundo_di (e os demais quando já custodiados na CTVM do grupo) | gravame no dia | — | prática de mercado |
| `portabilidade_cvm_210` | acoes, fii, etf (2 dias úteis), fundo_di (até 9 dias úteis) | regulatório | só impedimento (art. 19, §1º, exemplos) | Res. CVM 210, arts. 8º, 16 e 19 |
| `transferencia_de_custodia` | cdb, lci, lca de outro banco | operacional configurável (5 dias úteis, ilustrativo) | sim | prática de mercado; fora da CVM 210 por entendimento (Lei 6.385, art. 2º, §1º) |
| `td_garantia` | tesouro_* | operacional configurável (3 dias úteis, ilustrativo; o regulamento não fixa prazo) | sim: agente do cliente não opera o serviço ou não confirma | Portaria MF 1.748/2024, itens 100, 105, 108, 109, 111, 112, 116 a 119 e 127 |
| `res_conjunta_12` | previdencia (PGBL, VGBL, capitalização; FAPI fora) | informações em até 2 dias úteis | não (art. 18) | Res. Conjunta 12/2024, arts. 1º, 4º, 6º a 10, 12, 13 e 18 |

**Produto permitido** (seção 4 e anexo D3): cartão aceita `cdb` só com liquidez diária,
`fundo_di` só com resgate em D+0 ou D+1, e `tesouro_*` só se a CTVM do grupo for agente de
custódia do TD (chave explícita no catálogo, `false` por padrão, porque não há confirmação
pública). `lci` e `lca`: só empréstimo, com vencimento casado (última parcela até o
vencimento do título). `acoes`, `fii`, `etf`: só empréstimo, LTV de referência 0,50.
`previdencia`: só empréstimo ou financiamento (art. 1º, §2º, VII). `coe`: não
recomendado, recusado sempre.

**Gravame** (`EstadoGravame`): `solicitado → registrado → liberado_para_excussao →
executado → baixado`; também `registrado → baixado`, `solicitado → recusado` (só no TD
Garantia) e `solicitado → cancelado`. A garantia gera limite (cartão) ou valor de
empréstimo só com gravame `registrado` e sem baixa pedida.

**Execução.** O ativo é vendido ou resgatado por inteiro, porque o credor fiduciário vende,
abate a dívida e devolve o saldo com demonstrativo (Lei 4.728, art. 66-B, §3º). Quem paga
o rendimento retém o IR. Na renda fixa, a alíquota sai da tabela regressiva da Lei 11.033,
art. 1º; LCI e LCA são isentas para PF (art. 3º); em ações, FIIs e ETFs a corretora retém
0,005% (art. 2º, §1º) e o cliente apura o resto. Na previdência, a operadora paga líquido
de tributos e carregamento (Res. Conj. 12, art. 13), com retenção ilustrativa de 10%. O
líquido abate a dívida e o excedente volta ao cliente. A execução parcial deixa de existir.

**Baixa.** Depois da quitação, da liberação ou da execução, a baixa é pedida
automaticamente com prazo de 2 dias úteis. A regra é da previdência e a recomendação de
aplicá-la a todos os ativos é do documento; o artigo não é citado lá, então fica como não
verificado. O worker faz a baixa na infraestrutura simulada. A conciliação diária aponta
`gravame_sem_divida` e `divida_sem_gravame`.

**Empréstimo com garantia.** Tabela Price, com taxa ao mês ilustrativa na política.
Contagem de atraso em dias corridos. A previdência executa só com atraso acima de 90 dias
(art. 12), depois do aviso aos 45 dias de atraso e do aviso 10 dias antes (art. 6º, §2º,
IV). A execução nunca acontece antes de 10 dias depois do segundo aviso. O prazo do
crédito fica limitado ao fim da acumulação (art. 6º, §3º). Os demais empréstimos executam
pelo prazo contratual da política: 30 dias, ilustrativo. Não há rotativo, refinanciamento
nem portabilidade de crédito.

**Dias úteis.** O calendário nacional fica em `app/feriados_nacionais.toml` (2026 e 2027,
com a fonte). Ele vale para os prazos regulatórios, os prazos operacionais dos trilhos e a
baixa. O atraso do cartão e do empréstimo continua em dias corridos (decisão 20).

**Contrapartes simuladas** (substituem Ágil e Lento):
- `corretora_de_origem_simulada`: portabilidade.
- `banco_de_origem_simulado`: transferência de custódia.
- `agente_td_simulado`: opera o TD Garantia e confirma.
- `agente_td_sem_servico_simulado`: não opera o serviço e recusa.
- `entidade_operadora_simulada`: Res. Conj. 12, nunca recusa.

O callback assinado (HMAC, janela contra replay, idempotência por `evento_id`) é o mesmo
da fase 2.

**Rotas.**
- Novas:
  - `POST /v1/contas/{id}/portabilidades`
  - `POST /v1/contas/{id}/transferencias-de-custodia`
  - `POST /v1/contas/{id}/garantias/tesouro`
  - `POST /v1/contas/{id}/garantias/previdencia`
  - `POST /v1/contas/{id}/emprestimos`
  - `GET /v1/contas/{id}/emprestimos`
  - `GET /v1/emprestimos/{id}`
  - `POST /v1/emprestimos/{id}/pagamentos`
  - `POST /v1/garantias/{id}/cancelamento`
  - `POST /v1/contrapartes/{id}/callbacks`
  - `GET /v1/contrapartes`
  - `GET /v1/regulacao/matriz` (pública)
  - `POST /v1/sandbox/garantias/{id}/contraparte`
  - `POST /v1/admin/rotinas/conciliacao`
- Antigas de custodiante externo: respondem `410` com problem+json que aponta a rota nova.
- Combinações inválidas: `422` com código estável e mensagem que indica o trilho e o
  produto certos.

**Fora de escopo** (vai para "Próximos passos"):
- integração real com B3, Selic ou plataforma;
- IOF regressivo sobre o rendimento (Decreto 6.306, art. 32), porque o documento não traz
  a tabela;
- execução parcial;
- agente de garantia (Lei 14.711);
- reaplicação automática de cupom do Tesouro: a escolha é manter o cupom vinculado como
  garantia, sem reaplicar;
- crédito PJ.

## Restrições globais

- Código, nomes e documentação em português; termos técnicos consagrados podem ficar em
  inglês.
- Dinheiro sempre em `Dinheiro` (centavos inteiros) ou `Decimal`, nunca `float`.
  Arredondamento sempre a favor da segurança: limite e valor de garantia para baixo,
  retenção e dívida para cima.
- Não inventar norma, artigo, prazo ou número. Toda afirmação regulatória leva uma
  categoria: `norma` (com norma e artigo), `pratica_de_mercado`, `interpretacao`,
  `imprensa` ou `nao_verificado`.
- Afirmação negativa sempre como "não encontramos norma que...".
- Empresas reais só em `docs/mercado.md` e `docs/regulacao.md`, com fonte e categoria.
  Nenhum nome da empresa a que o documento se destina, das entidades do grupo dela ou do
  empregador do autor, em nenhum arquivo nem mensagem de commit. O PDF não entra no
  repositório.
- Vocabulário:
  - custodiado, custodiar, custodiante (nunca "guardado" ou "guardar");
  - "gravame registrado na B3 ou no Selic";
  - "a registradora ou o depositário central registra; o custodiante comanda";
  - agente de custódia (no Tesouro); entidade operadora (Res. Conj. 12); entidade
    tomadora de BaaS;
  - "transferência de custódia de CDB (fora da CVM 210)";
  - "a garantia reduz a perda".
- Cada simulador é marcado como simulador no código, na API (campo `simulado: true` nas
  contrapartes) e na tela. Nunca dizer que o projeto integra com a B3 ou o Selic.
- Os testes existentes continuam passando, adaptados quando a regra muda. Teste que sai é
  listado no PR com a regra que o substituiu.
- Migração nova: `alembic/versions/0003_trilhos_e_gravame.py`, sem editar as anteriores.
- Decisões novas em `docs/DECISOES.md`, numeradas a partir da 72. Decisão antiga que deixa
  de valer ganha a nota "substituída pela decisão N".

## Foco da revisão

1. Callback de contraparte com `contraparte_id` de outro trilho, por exemplo a operadora
   respondendo a um pedido de portabilidade: deve ser recusado sem mudar nada. O teste fica
   na Tarefa 7.
2. Pagamento ou execução que quita o empréstimo no mesmo dia em que a baixa é pedida: o
   gravame não pode continuar gerando valor de empréstimo, e o ledger fecha em zero. O
   teste fica na Tarefa 5.
3. Prazo em dias úteis que cai fora da cobertura do calendário (depois de 2027): erro
   claro, não um prazo errado em silêncio. O teste fica na Tarefa 2.
4. Avanço de relógio de 91 dias de uma vez na previdência: os dois avisos saem no mesmo
   dia e a execução só ocorre 10 dias depois. O teste fica na Tarefa 5.
5. Dados antigos na migração (garantia `deposito`, externa `gravame_pendente`,
   `parcialmente_executada`): migram sem perder valor no ledger. O teste fica na Tarefa 6.

---

### Tarefa 1: Catálogo regulatório (fonte única)

**Arquivos:**
- Criar: `app/regulacao.toml`, `app/dominio/regulacao.py`, `app/infra/regulacao.py`.
- Modificar: `app/dominio/politica.py` (TipoAtivo), `app/politica_referencia.toml`,
  `app/dominio/erros.py`, `app/api/erros.py` (catálogo de códigos),
  `app/api/fabrica.py` (carrega e valida o catálogo na subida).
- Testes: `tests/unidade/test_regulacao.py`, `tests/unidade/test_regulacao_toml.py`.

**Interfaces:**
- Produz, em `app.dominio.regulacao`:
  - os enums `Infraestrutura`, `Instrumento`, `Trilho`, `Produto`, `Categoria`,
    `CarteiraDeProvisao` e `CondicaoNoCartao` (`liquidez_diaria`, `resgate_ate_d1`,
    `ctvm_agente_do_td`);
  - `Afirmacao(texto, categoria, norma: str | None, artigo: str | None)`;
  - `FaixaDeLtv(ate_anos: int | None, minimo: Decimal, maximo: Decimal)`;
  - `RegraDoTrilho(trilho, nome, prazo_dias_uteis: int | None, prazo_e_regulatorio:
    bool, pode_recusar: bool, afirmacoes)`;
  - `RegraDoAtivo(tipo, nome, infraestrutura, instrumentos: tuple[Instrumento, ...],
    trilhos, produtos, condicao_no_cartao: CondicaoNoCartao | None,
    dias_minimos_execucao: int | None, faixas_de_ltv,
    carteira_de_provisao: Mapping[Instrumento, CarteiraDeProvisao], recomendado: bool,
    afirmacoes)`; o primeiro instrumento é o padrão;
  - `CatalogoRegulatorio(ativos, trilhos, ctvm_do_grupo_e_agente_do_td: bool,
    prazo_da_baixa_dias_uteis: int, pisos_de_provisao, teto_do_fgc: Dinheiro)`, com os
    métodos:
    - `regra(tipo) -> RegraDoAtivo`;
    - `exigir_combinacao(tipo, trilho, produto, *, liquidez_dias: int | None, plano: str
      | None = None, ja_em_garantia: bool = False) -> None`;
    - `ltv_maximo(tipo, anos_ate_o_vencimento: Decimal | None) -> Decimal`.
- Produz, em `app.dominio.erros`: `CombinacaoNaoPermitida(ErroDominio)`, com os campos
  `codigo` e `sugestao`.
- Produz, em `app.infra.regulacao`: `carregar_catalogo(arquivo: Path = ARQUIVO_PADRAO) ->
  CatalogoRegulatorio`, com cache e validação, que levanta `ErroNoCatalogoRegulatorio`.

- [ ] **Passo 1: testes que falham.** Cada combinação abaixo levanta
  `CombinacaoNaoPermitida`, com este código:
  - previdência no cartão: `previdencia_so_no_emprestimo`;
  - ações, FII ou ETF no cartão: `ativo_so_no_emprestimo`;
  - LCI ou LCA no cartão: `ativo_so_no_emprestimo`;
  - CDB no cartão com `liquidez_dias=30`: `cartao_exige_liquidez_diaria`;
  - fundo DI no cartão com `liquidez_dias=2`: `fundo_no_cartao_so_ate_d1`;
  - Tesouro no cartão com a chave falsa: `tesouro_no_cartao_exige_agente_do_td`;
  - CDB, LCI, LCA ou Tesouro em `portabilidade_cvm_210`: `trilho_nao_atende_o_ativo`;
  - portabilidade com `ja_em_garantia=True`: `ativo_ja_em_garantia`;
  - COE em qualquer trilho ou produto: `coe_nao_recomendado`;
  - previdência com plano `fapi`: `plano_fora_da_res_conjunta_12`.

  A `sugestao` de cada uma cita a rota certa. Exemplos: previdência no cartão sugere
  `POST /v1/contas/{conta_id}/garantias/previdencia` com um empréstimo; CDB na
  portabilidade sugere `POST /v1/contas/{conta_id}/transferencias-de-custodia`.

  `ltv_maximo`: `tesouro_prefixado` com 1, 3 e 10 anos dá 0,85, 0,80 e 0,70; `cdb` sem
  prazo dá 0,95. O carregamento do TOML real passa. Um TOML com afirmação de categoria
  `norma` sem `norma` ou sem `artigo` falha com `ErroNoCatalogoRegulatorio`.
- [ ] **Passo 2:** rodar `uv run pytest tests/unidade/test_regulacao.py
  tests/unidade/test_regulacao_toml.py -q`. Esperado: FAIL.
- [ ] **Passo 3: escrever `app/regulacao.toml`** a partir da seção 4, do anexo A, do
  anexo D3 e da seção 7 do resumo da fonte, com uma tabela `[ativos.<tipo>]` por ativo.
  Valores:
  - LTV de partida (interpretação do D3, hipótese de calibração): Tesouro Selic 0,90 a
    0,95; prefixado e IPCA+ 0,85 até 2 anos, 0,80 de 2 a 5 anos, 0,70 acima de 5 anos;
    CDB 0,85 a 0,95; LCI e LCA 0,80 a 0,90; fundo DI 0,85 a 0,95; ações, FII e ETF 0,50
    a 0,70; previdência 0,60 a 0,80.
  - Carteira de provisão:
    - CDB → C2 (art. 81, II, "c": depósito a prazo, qualquer instrumento; interpretação);
    - alienação fiduciária → C2 (II, "b");
    - cessão fiduciária e penhor → C3 (III, "b");
    - direito de resgate → C3 (interpretação, não verificado).
  - Pisos do 1º mês após o inadimplemento: C2 30%, C3 45%, C5 50%. Pisos do 13º mês:
    C2 70,8%, C3 89,4%, C5 90,8%.
  - Teto do FGC: R$ 250.000,00.
  - Prazo da baixa: 2 dias úteis.
  - `ctvm_do_grupo_e_agente_do_td = false`.
- [ ] **Passo 4: implementar o domínio e o carregador.**
  - Validação do carregador: categoria `norma` exige norma e artigo; faixas de LTV
    crescentes em prazo e entre 0 e 1; todo `TipoAtivo` tem regra; cada instrumento
    listado tem carteira.
  - `TipoAtivo` passa a ter os 12 tipos do desenho, sem `deposito`.
  - `politica_referencia.toml`: LTVs no início de cada faixa (cdb 0,85; lci e lca 0,80;
    fundo_di 0,85; tesouro_selic 0,90; prefixado e IPCA+ 0,85; acoes, fii e etf 0,50;
    previdencia 0,60), sem `coe` e na ordem de execução do mais líquido ao menos líquido.
  - `PoliticaRisco` ganha `dias_para_execucao_emprestimo: int = 30` e
    `taxa_do_emprestimo_ao_mes: Decimal = Decimal("0.0199")`, os dois ilustrativos. O
    `de_dict` aceita política antiga sem esses campos.
  - Novos códigos no catálogo de erros da API: todos com status 422, exceto
    `coe_nao_recomendado`, também 422.
- [ ] **Passo 5:** rodar os testes. Esperado: PASS. Rodar `uv run mypy app` e
  `uv run ruff check .`.
- [ ] **Passo 6:** commit `feat(regulacao): catálogo regulatório como fonte única`.

### Tarefa 2: Calendário de dias úteis

**Arquivos:**
- Criar: `app/feriados_nacionais.toml`, `app/dominio/calendario.py`,
  `app/infra/calendario.py`.
- Testes: `tests/unidade/test_calendario.py`, `tests/propriedade/test_calendario_prop.py`.

**Interfaces:**
- Produz:
  - `DiasUteis(quantidade: int)` e `DiasCorridos(quantidade: int)`, tipos distintos para a
    análise dimensional;
  - `CalendarioDeDiasUteis(feriados: frozenset[date], cobertura_inicio: date,
    cobertura_fim: date)`, com os métodos `e_dia_util(d) -> bool`, `somar(d: date, dias:
    DiasUteis) -> date` e `dias_uteis_entre(inicio: date, fim: date) -> int`;
  - `ForaDaCobertura(ErroDominio)`;
  - `carregar_calendario() -> CalendarioDeDiasUteis`.

- [ ] **Passo 1: testes que falham.**
  - `somar(date(2026, 11, 19), DiasUteis(2)) == date(2026, 11, 24)`, porque 20/11 é feriado
    nacional (Lei 14.759/2023) e 21 e 22 caem em fim de semana.
  - `somar(date(2026, 12, 24), DiasUteis(1)) == date(2026, 12, 28)`.
  - `somar(date(2027, 12, 30), DiasUteis(5))` levanta `ForaDaCobertura`.
  - Propriedade: para todo `d` e todo `n` dentro da cobertura, `somar(d, n)` é dia útil e
    `dias_uteis_entre(d, somar(d, n)) == n`.
- [ ] **Passo 2:** rodar e ver FAIL.
- [ ] **Passo 3: montar a lista de feriados nacionais de 2026 e 2027 e conferir na web.**
  Usar o calendário de feriados nacionais da ANBIMA como fonte e conferir também as leis:
  Lei 662/1949 na redação da Lei 10.607/2002, Lei 6.802/1980 e Lei 14.759/2023.
  - Carnaval e Corpus Christi entram porque não são dia útil bancário pelo calendário da
    ANBIMA. Escrever isso como prática de mercado.
  - Registrar no TOML a fonte e a data da consulta.
  - Nada de data de cabeça: cada data é conferida.
- [ ] **Passo 4:** implementar. A cobertura vai de 01/01/2026 a 31/12/2027.
- [ ] **Passo 5:** rodar os testes e ver PASS. Rodar mypy e ruff.
- [ ] **Passo 6:** commit `feat(calendario): dias úteis nacionais para prazos regulatórios`.

### Tarefa 3: Gravame e garantia

**Arquivos:**
- Criar: `app/dominio/gravame.py`.
- Modificar: `app/dominio/garantia.py`, `app/dominio/eventos.py`, `app/dominio/ledger.py`.
- Testes: `tests/unidade/test_gravame.py`, `tests/propriedade/test_gravame_prop.py`.

**Interfaces:**
- Consome: Tarefa 1 (`Infraestrutura`, `Instrumento`, `Trilho`, `Produto`).
- Produz, em `app.dominio.gravame`:
  - `EstadoGravame`, com os 7 estados, e `TRANSICOES`;
  - `Gravame(id, infraestrutura, instrumento, trilho, estado, solicitado_em, registrado_em,
    protocolo, baixa_pedida_em, baixa_prazo: date | None, encerrado_em, motivo)`, com os
    métodos `registrar(agora, protocolo)`, `recusar(agora, motivo)`, `cancelar(agora,
    motivo)`, `liberar_para_excussao(agora)`, `marcar_executado(agora)`, `pedir_baixa(agora,
    prazo: date)` e `confirmar_baixa(agora)`, e com as propriedades `vigente` (registrado e
    sem baixa pedida) e `encerrado`.
- Produz, em `app.dominio.garantia`:
  - a `Garantia` reescrita: `id, conta_id, tipo_ativo, valor_aplicado, criada_em,
    marcada_em, produto, gravame, emissor, vencimento, liquidez_dias, contraparte_id,
    solicitacao, emprestimo_id, plano, fim_da_acumulacao, informacoes`;
  - as propriedades `gera_limite`, `cobre_divida`, `disponivel_para_emprestimo` e `estado`
    (o estado do gravame);
  - `Solicitacao`, que fica, com `TipoSolicitacao.ENTRADA`;
  - `InformacoesDaOperadora(valor_disponivel, tributos_informados, recebidas_em)`.

  `EstadoGarantia` e `OrigemGarantia` saem.
- Produz, em `app.dominio.ledger`: as contas `EMPRESTIMOS_A_RECEBER` (devedora),
  `DESEMBOLSOS_A_LIQUIDAR`, `RECEITA_DE_JUROS` e `EXCEDENTE_A_DEVOLVER` (as três credoras),
  e os lançamentos `DESEMBOLSO`, `PAGAMENTO_DE_PARCELA` e `DEVOLUCAO_DE_EXCEDENTE`.
- Produz, em `app.dominio.eventos`:
  - novos: `GRAVAME_REGISTRADO`, `GRAVAME_RECUSADO`, `GRAVAME_CANCELADO`,
    `GRAVAME_LIBERADO_PARA_EXCUSSAO`, `GRAVAME_BAIXA_PEDIDA`, `GRAVAME_BAIXADO`,
    `PORTABILIDADE_SOLICITADA`, `PORTABILIDADE_CONCLUIDA`, `TRANSFERENCIA_SOLICITADA`,
    `TRANSFERENCIA_CONCLUIDA`, `PREVIDENCIA_INFORMACOES_RECEBIDAS`,
    `PREVIDENCIA_AVISO_45_DIAS`, `PREVIDENCIA_AVISO_10_DIAS`, `EMPRESTIMO_CONTRATADO`,
    `EMPRESTIMO_PARCELA_PAGA`, `EMPRESTIMO_EM_ATRASO` e `EMPRESTIMO_QUITADO`;
  - saem os de liberação e execução externas.

- [ ] **Passo 1: testes de exemplo que falham.**
  - Cada transição válida funciona e cada uma inválida levanta `TransicaoInvalida`. Por
    exemplo, de `liberado_para_excussao` para `baixado` direto.
  - `recusar` fora do trilho `td_garantia` levanta `TransicaoInvalida`.
  - Um gravame `registrado` com baixa pedida não é `vigente`.
- [ ] **Passo 2: propriedade.** Para qualquer sequência de comandos sorteada, o estado
  sempre está em `TRANSICOES`. `vigente` implica `registrado`. Um gravame encerrado
  (`baixado`, `recusado` ou `cancelado`) não muda mais.
- [ ] **Passo 3:** implementar.
- [ ] **Passo 4:** rodar os testes novos e ver PASS. A suíte antiga quebra aqui, e a
  Tarefa 4 a conserta. Por isso as Tarefas 3 e 4 vão juntas para o mesmo implementador e
  o mesmo revisor.
- [ ] **Passo 5:** commit `feat(gravame): máquina de estados do registro separada da
  garantia`.

### Tarefa 4: Conta do cartão sobre o gravame

**Arquivos:**
- Modificar: `app/dominio/conta.py`, `app/dominio/stress.py` (tipos), `app/dominio/eventos.py`.
- Testes: reescrever `tests/unidade/test_conta.py`, `tests/unidade/test_conta_externa.py`
  (passa a se chamar `test_conta_trilhos.py`) e `tests/propriedade/test_conta_maquina.py`.

**Interfaces:**
- Consome: Tarefas 1, 2 e 3.
- Produz, em `Conta`:
  - `custodiar_na_casa(*, tipo, valor, produto, instrumento, emissor, vencimento,
    liquidez_dias, protocolo: str | None, agora, hoje) -> Garantia`, que registra na hora
    quando `protocolo` vem preenchido e deixa `solicitado` no modo assíncrono;
  - `receber_de_fora(*, trilho, tipo, valor, produto, contraparte_id, prazo_ate: datetime,
    ..., agora, hoje) -> Garantia`;
  - `concluir_entrada(garantia_id, protocolo, agora)`;
  - `recusar_entrada(garantia_id, motivo, agora)`, que leva a `recusado` no TD Garantia e
    a `cancelado` nos demais trilhos;
  - `registrar_informacoes_da_operadora(garantia_id, informacoes, agora)`;
  - `cancelar_pedido(garantia_id, motivo, agora)`;
  - `liberar(garantia_id, politica, agora, hoje, prazo_da_baixa: date)`, que pede a baixa;
  - `confirmar_baixa(garantia_id, agora)`;
  - `gravames_com_baixa_pendente() -> list[Garantia]`.

  `virar_o_dia` ganha o parâmetro `prazo_da_baixa: date` e passa a executar por inteiro,
  com demonstrativo.
- Produz: `Conta.receber_evento_de_custodia(garantia_id, valor: Dinheiro, agora)` — cupom
  ou amortização de Tesouro gravado chega ao agente do garantido (item 127) e fica
  vinculado como garantia: D `cupons_vinculados` (nova conta devedora, por garantia) / C
  `garantias_custodiadas`; o valor da garantia para limite e execução soma título e cupom
  vinculado. Só para `tesouro_*`; outro tipo levanta `ValorInvalido`.
- Produz: `ResultadoDaExecucao(valor_de_mercado, retencao, liquido, divida_abatida,
  excedente_devolvido)`, nos dados do evento `garantia.executada`, e
  `retencao_na_execucao(garantia, valor, hoje) -> Dinheiro`, em
  `app/dominio/tributos.py`, com as regras do desenho, arredondada para cima.

- [ ] **Passo 1: testes que falham.**
  - CDB de R$ 5.000,00 com liquidez diária custodiado na casa, com protocolo, dá limite
    de R$ 4.250,00 com LTV 0,85.
  - O mesmo CDB sem protocolo (modo assíncrono) fica `solicitado` e com limite zero.
  - Uma garantia de fora em `solicitado` nunca gera limite.
  - `liberar` derruba o limite na hora e deixa a baixa pedida com o prazo informado.
  - No 7º dia de atraso de uma fatura de R$ 4.000,00, um CDB de R$ 5.000,00 aplicado há
    400 dias com valor de mercado de R$ 5.200,00 é vendido por inteiro. A retenção é
    17,5% sobre R$ 200,00, ou seja, R$ 35,00. O líquido é R$ 5.165,00, a dívida abatida é
    R$ 4.000,00 e o excedente devolvido é R$ 1.165,00. O ledger fecha em zero.
  - `liberado_para_excussao` não gera limite novo.
  - LCI executada: retenção zero.
  - Ações: retenção de 0,005% sobre a venda, arredondada para cima.
  - Cupom de R$ 300,00 num Tesouro de R$ 10.000,00 gravado: o limite é o mesmo antes e
    depois (R$ 9.700,00 de título mais R$ 300,00 vinculados), e a execução vende os dois.
- [ ] **Passo 2:** a propriedade de `test_conta_maquina.py` passa a sortear também
  liberação e execução sobre o gravame. Invariantes: o ledger fecha em zero, o limite
  nunca vem de garantia sem gravame vigente, e o excedente nunca é negativo.
- [ ] **Passo 3:** implementar. Remover os métodos externos (`aceitar_solicitacao`,
  `confirmar_gravame`, `solicitar_liberacao`, `confirmar_execucao` etc.) e a execução
  parcial.
- [ ] **Passo 4:** rodar `uv run pytest tests/unidade tests/propriedade -q` e ver PASS.
  Anotar cada teste removido e a regra que o substitui.
- [ ] **Passo 5:** commit `feat(conta): cartão sobre o gravame registrado; execução
  integral com retenção`.

### Tarefa 5: Empréstimo com garantia

**Arquivos:**
- Criar: `app/dominio/emprestimo.py`.
- Modificar: `app/dominio/conta.py` (delegação).
- Testes: `tests/unidade/test_emprestimo.py`, `tests/propriedade/test_emprestimo_prop.py`.

**Interfaces:**
- Consome: Tarefas 1 a 4.
- Produz:
  - `EstadoEmprestimo` (`ativo`, `em_atraso`, `quitado`, `liquidado_por_execucao`);
  - `Parcela(numero, vencimento, valor, juros, amortizacao, paga_em)`;
  - `Emprestimo(id, conta_id, principal, taxa_ao_mes, parcelas, garantia_ids,
    contratado_em, estado, dias_minimos_execucao, aviso_45_em, aviso_10_em)`;
  - `cronograma_price(principal, taxa_ao_mes, quantidade, primeiro_vencimento) ->
    list[Parcela]`;
  - `valor_maximo_do_emprestimo(garantias, politica, catalogo, hoje) -> Dinheiro`.
- Produz, em `Conta`:
  - `contratar_emprestimo(*, valor, parcelas, garantia_ids, politica, catalogo,
    primeiro_vencimento, agora, hoje, protocolos_da_previdencia: Mapping[str, str]) ->
    Emprestimo`; a formalização da previdência registra o gravame (art. 8º) no mesmo
    passo;
  - `pagar_parcela(emprestimo_id, valor, agora, hoje, prazo_da_baixa)`.

  `virar_o_dia` passa a tratar também os empréstimos.

- [ ] **Passo 1: testes que falham.**
  - `cronograma_price(R$ 10.000,00, 0,0199, 12, ...)`: soma das amortizações igual a
    R$ 10.000,00; parcela arredondada para cima ao centavo; juros do mês arredondados para
    baixo; a última parcela fecha o saldo em zero.
  - Valor acima de `valor_maximo_do_emprestimo` levanta `ValorInvalido`.
  - Garantia em `solicitado` ou de produto cartão não entra no empréstimo.
  - Previdência com última parcela depois de `fim_da_acumulacao` levanta
    `CombinacaoNaoPermitida("prazo_alem_da_acumulacao")`.
  - LCI com última parcela depois do vencimento do título levanta
    `CombinacaoNaoPermitida("vencimento_nao_casado")`.
  - Linha do tempo da previdência:
    - 44 dias de atraso: nenhum evento;
    - dia 45: `previdencia.aviso_45_dias`;
    - dia 81: `previdencia.aviso_10_dias`;
    - dia 90: sem execução;
    - dia 91: execução, com o líquido abatendo o saldo devedor e o excedente devolvido;
    - baixa pedida com o prazo de 2 dias úteis.
  - Saltar 91 dias de uma vez emite os dois avisos no mesmo dia e a execução só acontece
    10 dias depois.
  - Empréstimo garantido por ações executa no 30º dia, o contratual da política.
  - Quitar o empréstimo pede a baixa de todas as garantias e zera o valor de empréstimo
    disponível no mesmo dia.
  - O ledger fecha em zero em cada caminho.
- [ ] **Passo 2: propriedade.** Para sequências sorteadas de pagamentos e viradas de dia:
  - o ledger fecha;
  - a previdência nunca executa com 90 dias ou menos de atraso nem antes de 10 dias depois
    do `aviso_10_em`;
  - o saldo devedor nunca fica negativo.
- [ ] **Passo 3:** implementar.
  - O saldo devedor é a soma das parcelas vencidas em aberto mais a amortização das
    vincendas, sem juros futuros.
  - Lançamentos:
    - desembolso: D `emprestimos_a_receber`, C `desembolsos_a_liquidar`;
    - parcela: D `caixa`, C `emprestimos_a_receber` (amortização), C `receita_de_juros`;
    - execução: D `caixa` (líquido), C `emprestimos_a_receber`, C `receita_de_juros`
      (juros vencidos), C `excedente_a_devolver`; depois D `excedente_a_devolver`,
      C `caixa`.
- [ ] **Passo 4:** rodar os testes e ver PASS. Rodar mypy e ruff.
- [ ] **Passo 5:** commit `feat(emprestimo): empréstimo parcelado com garantia e régua da
  Res. Conjunta 12`.

### Tarefa 6: Persistência e migração 0003

**Arquivos:**
- Criar: `alembic/versions/0003_trilhos_e_gravame.py`.
- Modificar: `app/infra/banco/modelos.py`, `app/infra/banco/repositorios.py`,
  `app/aplicacao/portas.py`.
- Testes: `tests/integracao/test_banco.py`, `tests/integracao/test_migracao_0003.py`
  (SQLite e Postgres).

**Interfaces:**
- Consome: Tarefas 3 a 5.
- Produz:
  - `RepositorioDeContas`: salva e carrega gravame, empréstimos e parcelas;
  - `garantias_aguardando_contraparte(limite) -> list[tuple[str, str, str]]`;
  - `contas_com_baixa_pendente() -> list[tuple[str, str]]`;
  - `contas_com_emprestimo_a_avaliar(hoje, parceiro_id) -> list[tuple[str, str]]`.

- [ ] **Passo 1: teste da migração que falha.** Montar um banco na revisão 0002 com uma
  garantia de cada:
  - `deposito` bloqueada;
  - `parcialmente_executada`;
  - externa `gravame_pendente`;
  - externa `bloqueada` de Tesouro;
  - `liberacao_pendente`;
  - `execucao_solicitada`.

  Rodar `upgrade` e conferir o mapeamento:
  - `deposito` → `cdb` com `liquidez_dias=0`;
  - interna `bloqueada` ou `parcialmente_executada` → `registrado`;
  - `pendente` → `solicitado`;
  - `liberada` → `baixado`;
  - `executada` → `baixado`;
  - externa `solicitada` ou `gravame_pendente` → `cancelado`, motivo
    `trilho_substituido`;
  - `gravame_recusado`: Tesouro → `recusado`; demais → `cancelado`;
  - `gravame_expirado` → `cancelado`;
  - externa `bloqueada` → `registrado`, no trilho de fora do catálogo para o ativo;
  - `liberacao_pendente` → `registrado` com baixa pedida;
  - `execucao_solicitada` → `liberado_para_excussao`.

  A soma do ledger continua zero. As políticas dos parceiros perdem `deposito`, e os
  tipos novos entram com os valores da referência. `downgrade` sai sem erro.
- [ ] **Passo 2:** rodar e ver FAIL.
- [ ] **Passo 3:** implementar o modelo e a migração, com postgres-pro: índice parcial nas
  garantias que aguardam contraparte e nas que têm baixa pendente. Usar `CHECK` só onde o
  SQLite também aceita.
- [ ] **Passo 4:** rodar a suíte com `DATABASE_URL_TESTE` (Postgres local) e sem
  (SQLite). Esperado: PASS.
- [ ] **Passo 5:** commit `feat(banco): migração 0003 para gravame, trilhos e empréstimo`.

### Tarefa 7: Contrapartes, worker, baixa e conciliação

**Arquivos:**
- Criar: `app/contrapartes_simuladas.toml`, `app/aplicacao/trilhos.py`,
  `app/aplicacao/emprestimos.py`, `app/infra/contrapartes.py`,
  `app/infra/infraestrutura_simulada.py`.
- Remover: `app/custodiantes_simulados.toml` e as partes externas de `app/infra/custodia.py`.
- Modificar: `app/aplicacao/custodia.py`, que passa a se chamar `contrapartes.py`;
  `app/aplicacao/risco.py` (avanço de dias também nos empréstimos); `app/worker.py`;
  `app/config.py` (`CONTRAPARTES_SEGREDOS`, que substitui `CUSTODIA_SEGREDOS`, e
  `CONTRAPARTES=simulador|desligada`); `docker-compose.yml`; `.env.example`.
- Testes: reescrever `tests/unidade/test_custodia_simulada.py` como
  `test_contrapartes_simuladas.py` e `tests/propriedade/test_custodia_externa_prop.py` como
  `test_trilhos_prop.py`; criar `tests/unidade/test_conciliacao.py`.

**Interfaces:**
- Consome: Tarefas 1 a 6.
- Produz, em `app.aplicacao.portas`:
  - `Contraparte` (Protocol), com `contrapartes() -> list[InfoDaContraparte]`,
    `conhece(id)`, `solicitar_entrada(pedido: PedidoDeEntrada) -> RespostaDaContraparte` e
    `consultar(pedido) -> RespostaDaContraparte`;
  - `InfraestruturaDeRegistro` (Protocol), com `registrar(garantia) -> str` (o
    protocolo), `executar(garantia, valor_de_mercado) -> Dinheiro` (o valor bruto da
    venda) e `baixar(garantia) -> None`;
  - `StatusDaResposta` (`pendente`, `concluida`, `recusada`, `impedimento`).
- Produz:
  - `aplicar_resposta(...)`, que é o caminho único, igual ao de hoje, com idempotência por
    (`contraparte_id`, `evento_id`);
  - `processar_contrapartes(abrir_unidade)`, que envia, consulta e encerra pelos prazos do
    trilho:
    - transferência e TD sem resposta no prazo → `cancelado` e `recusado`;
    - portabilidade e previdência sem resposta → continuam e ficam marcadas com
      `prazo_regulatorio_vencido=true`;
  - `processar_baixas(abrir_unidade)`;
  - `conciliar(contas, hoje) -> list[Divergencia(tipo, conta_id, garantia_id |
    emprestimo_id, detalhe)]`, com os tipos `gravame_sem_divida`, `divida_sem_gravame` e
    `baixa_atrasada`.

- [ ] **Passo 1: testes que falham.**
  - Simuladores determinísticos: mesma semente e mesmo pedido dão a mesma resposta.
  - Callback da `entidade_operadora_simulada` com `recusada` é rejeitado como resposta
    inválida (art. 18).
  - Callback com `contraparte_id` de outro trilho não muda nada (Foco 1).
  - O worker baixa a garantia com baixa pedida.
  - A conciliação aponta um gravame vigente de empréstimo quitado e uma dívida de cartão
    sem gravame vigente.
- [ ] **Passo 2:** a propriedade dos trilhos, com respostas em qualquer ordem e
  duplicadas, garante que o limite e o valor de empréstimo só nascem em `registrado` e que
  a mesma resposta duas vezes vale uma vez.
- [ ] **Passo 3:** implementar.
  - Perfis: `corretora_de_origem_simulada`, `banco_de_origem_simulado`,
    `agente_td_simulado`, `agente_td_sem_servico_simulado` e
    `entidade_operadora_simulada`, cada um com `trilhos`, `latencia_segundos`, semente e
    probabilidades.
  - A operadora e a corretora nunca recusam por sorteio.
  - A infraestrutura simulada devolve o protocolo `SIM-<infraestrutura>-<id>` e vende ao
    valor de mercado.
- [ ] **Passo 4:** rodar a suíte inteira e ver PASS.
- [ ] **Passo 5:** commit `feat(trilhos): contrapartes simuladas por trilho, baixa e
  conciliação`.

### Tarefa 8: API dos trilhos e do empréstimo

**Arquivos:**
- Criar: `app/api/rotas/trilhos.py`, `app/api/rotas/emprestimos.py`,
  `app/api/rotas/regulacao.py`.
- Modificar: `app/api/rotas/contas.py`, `app/api/rotas/custodia.py` (só os 410 e o
  callback novo), `app/api/esquemas.py`, `app/api/apresentacao.py`, `app/api/erros.py`,
  `app/api/fabrica.py`, `app/api/rotas/admin.py` (conciliação) e `app/api/rotas/risco.py`
  (sandbox).
- Testes: reescrever `tests/integracao/test_custodia_externa.py` como
  `test_trilhos_api.py`; criar `tests/integracao/test_emprestimos_api.py` e
  `tests/integracao/test_regulacao_api.py`; ajustar `test_contas_e_garantias.py`.

**Interfaces:**
- Consome: Tarefas 1 a 7.
- Produz: as rotas do desenho e `POST /v1/sandbox/garantias/{id}/evento-de-custodia`
  (simula cupom do Tesouro). Cada operação POST de dinheiro exige `Idempotency-Key`.
  O `description` do OpenAPI de cada rota traz o trilho, o instrumento, a infraestrutura,
  o produto permitido, o prazo e a norma com o artigo. As respostas de garantia trazem
  `gravame{estado, infraestrutura, instrumento, trilho, protocolo, baixa_prazo}`,
  `produto` e `alertas`; penhor traz o alerta da Lei 11.101, art. 49, §5º.

- [ ] **Passo 1: testes que falham.**
  - Previdência no cartão: 422 `previdencia_so_no_emprestimo`, com `sugestao`.
  - CDB em `/portabilidades`: 422 `trilho_nao_atende_o_ativo`.
  - `GET /v1/custodiantes`, `POST /v1/custodiantes/x/callbacks` e
    `POST /v1/sandbox/garantias/x/custodiante`: 410, `codigo` `rota_substituida`,
    `rota_nova` preenchida.
  - `custodiante_id` em `POST /garantias`: 410.
  - Portabilidade de ações: 202 com `prazo_ate` igual a hoje mais 2 dias úteis. O callback
    concluído deixa `registrado`. `POST /emprestimos` com essa garantia: 201 com o
    cronograma.
  - `GET /v1/regulacao/matriz` é igual à serialização do catálogo.
  - O OpenAPI de `/portabilidades` contém "Res. CVM 210" e "art. 16".
- [ ] **Passo 2:** implementar com api-designer, seguindo o padrão REST multi-tenant das
  rotas atuais (prefixo `/v1`, parceiro autenticado, idempotência, paginação onde houver
  lista).
- [ ] **Passo 3:** rodar a suíte de integração e ver PASS.
- [ ] **Passo 4:** commit `feat(api): rotas dos trilhos, do empréstimo e da matriz;
  rotas antigas em 410`.

### Tarefa 9: Painel de risco

**Arquivos:**
- Modificar: `app/carteira_sintetica.toml`, `app/infra/carteira_sintetica.py`,
  `app/dominio/stress.py`, `app/api/rotas/risco.py`, `app/api/esquemas.py`,
  `app/api/estatico/risco.{html,js,css}`, `app/premissas_economia.toml` (cenário de
  cada conjunto).
- Testes: `tests/integracao/test_risco.py`, `tests/propriedade/test_painel_prop.py`,
  `tests/unidade/test_painel_regulatorio.py`.

**Interfaces:**
- Consome: Tarefas 1 e 2.
- Produz:
  - `GarantiaNaCarteira`, que ganha `emissor`, `vencimento` e `produto`; `custodiante_id`
    vira `onde_esta`, com `None` para a casa, um agente de custódia fictício para Tesouro
    de fora ou a entidade operadora para previdência;
  - `PainelRegulatorio(haircut_por_classe_e_prazo, concentracao_por_conglomerado,
    carteiras_de_provisao)` no resultado de `simular_painel`;
  - cenários: `dia_normal`, `crash_da_bolsa` (ações, FII e ETF −30%), `alta_de_juros`,
    `quebra_de_emissor` (CDB, LCI e LCA do conglomerado fictício B valem zero para o
    credor, porque o FGC paga o titular, não o credor) e `agente_perde_participacao`
    (títulos de um agente fictício atrasam 14 dias corridos, cerca de 10 dias úteis, Res.
    BCB 409/2024, art. 15, §1º do Regulamento do Selic, sem perda total).
  - `custodiante_fora_do_ar` sai; os conjuntos Conservador e Base da economia passam a
    usar `agente_perde_participacao`.

- [ ] **Passo 1: testes que falham.**
  - Na carteira sintética, ações e previdência só aparecem em conta de empréstimo, e
    cartão só tem CDB com liquidez diária e fundo DI até D+1.
  - A concentração soma CDB, LCI e LCA por cliente e conglomerado e marca o excesso
    acima de R$ 250.000,00.
  - Provisão:
    - cada conta cai na carteira de menor provisão entre as garantias (art. 81, §1º);
    - conta sem garantia cai na C5;
    - piso do 1º mês: C2 30%, C3 45%, C5 50%.
  - A resposta tem `aviso_sintetico` e nenhum texto com "perda zero".
- [ ] **Passo 2:** implementar. Gerar a carteira com prazo, emissor e produto por ativo.
  Faixa de valor nova de R$ 100.000 a R$ 400.000, com peso pequeno, para a concentração
  aparecer. O painel mostra o haircut por classe e prazo, a concentração, a provisão, a
  marcação a mercado diária e o aviso de carteira sintética.
- [ ] **Passo 3:** rodar a suíte e ver PASS. Os números do `/negocio` mudam; os testes de
  propriedade da economia continuam passando.
- [ ] **Passo 4:** commit `feat(risco): haircut por prazo, FGC, provisão e cenários
  regulatórios`.

### Tarefa 10: Conta do negócio

**Arquivos:**
- Modificar: `app/dominio/economia/calculo.py`, `app/premissas_economia.toml`,
  `app/api/estatico/negocio.{html,js}`, `docs/economia.md`.
- Testes: `tests/unidade/test_calculo_economia.py`, `tests/propriedade/test_economia_prop.py`.

**Interfaces:**
- Consome: Tarefa 9 (cenários).
- Produz:
  - linha `iof_credito`, de `cliente` para a nova `Parte.governo`, que não entra na margem
    de ninguém;
  - premissas `iof_diario` = 0,000082 e `iof_adicional` = 0,0038, com fonte no Decreto
    6.306, art. 7º, I e §15 (norma);
  - campo `notas` no resultado, com três notas: o IR da execução é do cliente, retido por
    quem paga o rendimento; o alívio de capital vale só se o colateral cumprir a Circular
    3.809 (art. 4º); o teto da Lei 14.690, art. 28 vale para o rotativo e o parcelado.

- [ ] **Passo 1: testes que falham.**
  - A base do IOF é só o saldo financiado; compra paga integralmente não gera IOF.
  - A margem da plataforma e a do parceiro não mudam com o IOF.
  - O teto de juros continua valendo.
  - "interna" e "externa" passam a ser rotuladas "dentro de casa" e "de fora" na resposta
    e na tela.
- [ ] **Passo 2:** implementar. Conferir como o modelo trata hoje o parcelado: se não
  houver linha de parcelado, registrar na nota e em "Próximos passos".
- [ ] **Passo 3:** regerar os números de `docs/economia.md` e rodar a suíte. Esperado:
  PASS.
- [ ] **Passo 4:** commit `feat(economia): IOF repassado, notas de IR e capital`.

### Tarefa 11: Página `/regulacao` e `docs/regulacao.md`

**Arquivos:**
- Criar: `app/api/estatico/regulacao.{html,css,js}`, `docs/regulacao.md`,
  `scripts/gerar_regulacao_md.py`.
- Modificar: `app/api/demo.py` (rota `/regulacao` com o mesmo CSP); a navegação de
  `index.html`, `risco.html` e `negocio.html`.
- Testes: `tests/integracao/test_regulacao_doc.py`, `tests/e2e/test_regulacao.py`.

**Interfaces:**
- Consome: `GET /v1/regulacao/matriz` (Tarefa 8).
- Produz:
  - a página com a matriz por ativo (o que funciona, trilho, instrumento,
    infraestrutura, produto, norma e artigo, prazo) e um selo por categoria;
  - o aviso "O autor não é advogado; a modelagem jurídica precisa ser validada";
  - `docs/regulacao.md`, com a matriz entre `<!-- matriz:inicio -->` e
    `<!-- matriz:fim -->`, as fontes e a seção de vocabulário entre
    `<!-- vocabulario:inicio -->` e `<!-- vocabulario:fim -->`.

- [ ] **Passo 1: testes que falham.**
  - O bloco da matriz em `docs/regulacao.md` é igual ao que o script gera a partir do
    TOML.
  - Na página, cada célula da tabela é igual ao valor do catálogo, ativo por ativo.
  - A navegação das quatro páginas tem os quatro links.
- [ ] **Passo 2:** implementar com frontend-design, no mesmo estilo das páginas atuais,
  sem `innerHTML`, com o CSP atual.
- [ ] **Passo 3:** rodar os testes e ver PASS.
- [ ] **Passo 4:** commit `feat(regulacao): página e documento gerados do catálogo`.

### Tarefa 12: Página `/demo` com os trilhos

**Arquivos:**
- Modificar: `app/api/estatico/index.html`, `demo.js` e `demo.css`; `app/api/demo.py`
  (configuração da demo com as contrapartes).
- Testes: reescrever `tests/e2e/test_fase2_e_risco.py` como `tests/e2e/test_trilhos.py`;
  ajustar `tests/e2e/test_demo.py`.

**Interfaces:**
- Consome: Tarefas 7 e 8.
- Produz: na coluna do app, três blocos:
  - "Cartão: garantia dentro de casa": CDB de banco parceiro com liquidez diária ou fundo
    DI da DTVM do grupo; o gravame é registrado na B3, de forma simulada.
  - "Trazer de fora": portabilidade CVM 210, transferência de custódia, TD Garantia e
    previdência. Cada um mostra o prazo com a data e os dias úteis, a linha do tempo do
    gravame e os botões de sandbox marcados como simulação.
  - "Empréstimo com garantia": contratar, pagar parcela, cronograma; avanço de relógio com
    "+45 dias", "+36 dias" e "+10 dias"; a régua 45/10/90, o demonstrativo da execução e a
    baixa em 2 dias úteis.

  Combinação inválida mostra a mensagem e a sugestão da API.

- [ ] **Passo 1: testes que falham (e2e).**
  - Portabilidade de ações: o prazo mostra a data de hoje mais 2 dias úteis; a
    conclusão registra o gravame; termina num empréstimo.
  - Transferência de custódia de CDB.
  - TD Garantia com o agente sem serviço: recusado.
  - Previdência: os avisos e a execução, avançando o relógio.
  - Baixa pedida com prazo e depois baixada.
  - Previdência no cartão e CDB na portabilidade: mensagens de erro.
- [ ] **Passo 2:** implementar com frontend-design.
- [ ] **Passo 3:** rodar `CHROMIUM_EXECUTAVEL=/opt/pw-browsers/chromium uv run pytest -m
  e2e -q` e ver PASS.
- [ ] **Passo 4:** commit `feat(demo): trilhos, empréstimo e régua da previdência na demo`.

### Tarefa 13: Textos, documentação e expressões proibidas

**Arquivos:**
- Modificar:
  - `README.md`: seção "Mudanças" com rotas e eventos novos e renomeados; INPI; BaaS;
    Open Finance; Drex; "Próximos passos";
  - `docs/arquitetura.md`: papéis e Mermaid dos trilhos, do gravame e do empréstimo;
  - `docs/riscos.md`: a tabela da seção 7;
  - `docs/roadmap.md`: fases e piloto sem nomes;
  - `docs/mercado.md`: seções 6 e 12, com fonte e categoria;
  - `docs/post-linkedin.md`: seção 10;
  - `docs/roteiro-video.md`: até 3 minutos;
  - `docs/DECISOES.md`, incluindo a entrada de economia que fala de perda zero
    (reescrever sem a expressão);
  - o "correspondente" de `app/api/rotas/risco.py`: é adjetivo, mas trocar para evitar
    ambiguidade;
  - os textos de `app/api/estatico/`.
- Criar: `tests/unidade/test_expressoes_proibidas.py`.
- Imagens: `docs/img/` (demo, regulação, risco, negócio, celular).

- [ ] **Passo 1: teste que falha.** Procura, sem diferenciar maiúsculas e com fronteira
  de palavra, as expressões "guardado", "guardar", "perda zero", "risco zero", "elimina o
  risco", "qualquer investimento", "Drex em 2026", "bloqueio no app", "a B3 registra" e
  "nenhuma norma". O alcance é `app/api/estatico/`, `README.md`, `docs/` (fora
  `DECISOES.md` e o bloco de vocabulário de `docs/regulacao.md`) e o post. Também confere
  que nenhum arquivo do repositório contém os nomes proibidos da lista do brief.
- [ ] **Passo 2:** reescrever os textos até o teste passar. As afirmações vão com norma e
  artigo ao lado, ou com a categoria.
- [ ] **Passo 3:** tirar as telas com Playwright (`deviceScaleFactor` 2) depois de subir a
  aplicação.
- [ ] **Passo 4:** commit `docs: textos alinhados à auditoria regulatória`.

### Tarefa 14: Verificação final e PR

- [ ] Rodar ruff, mypy, pytest com SQLite e com Postgres, `pytest -m e2e` e a cobertura
  do domínio, que precisa passar de 90%.
- [ ] Subir a aplicação e percorrer as quatro páginas com Playwright.
- [ ] Rodar as revisões: verification-before-completion, /code-review, /review-pr,
  claude-security, insecure-defaults e sharp-edges, com atenção às rotas novas e aos
  callbacks.
- [ ] Buscar os nomes proibidos no repositório inteiro, inclusive no histórico de commits
  do branch; o resultado tem que ser vazio.
- [ ] Fazer o push e abrir o PR para `main`, com a lista de testes removidos e a regra que
  substituiu cada um.
