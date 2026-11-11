# Plano de implementação: fase 2 simulada e painel de risco

> **Para quem for executar:** este plano foi escrito para execução tarefa a tarefa, com
> teste primeiro (TDD). Os passos usam caixas de seleção (`- [ ]`) para acompanhamento.
> As escolhas feitas pelo caminho vão para [DECISOES.md](DECISOES.md), a partir do item 39.

**Objetivo.** Garantia em custodiante externo (simulado, assíncrono e que pode falhar)
e um painel de risco sobre uma carteira sintética, sem mudar o comportamento da fase 1.

**Arquitetura.** A máquina de estados da garantia ganha uma origem (`interna` ou
`externa`) e uma tabela de transições por origem. A porta `Custodiante` passa a pedir
gravame, liberação e execução e a consultar status; a implementação é um simulador
determinístico, configurado em arquivo. As respostas do custodiante chegam por callback
assinado (HMAC) ou por polling, e passam pelo mesmo caso de uso, idempotente por
`(custodiante_id, evento_id)` com restrição única no banco. O painel reaproveita o
cálculo do simulador de stress e acrescenta as premissas da garantia externa.

**Stack.** A mesma da v1.0: Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, Pydantic 2,
Hypothesis, Playwright. Nenhuma dependência nova.

**Especificação.** O pedido da fase 2 (seções 1 a 7) está resumido em
[DECISOES.md, item 39](DECISOES.md#39-fase-2-pedido-e-ponto-de-partida).

## Restrições globais

- O fluxo interno, os endpoints atuais e os payloads de webhook atuais não mudam de
  comportamento. `origem` entra com padrão `interna`. Campos novos em respostas são só
  acréscimos.
- Mudança de esquema só em migração nova (`0002`). A `0001` não é editada.
- Dinheiro é `Dinheiro` (centavos inteiros). Valores de garantia arredondam para baixo,
  estimativas de perda para cima.
- Simulador é chamado de simulador no código, na API e na documentação. Nenhum nome de
  instituição real fora de `docs/mercado.md`.
- Nada de `sleep` real nem aleatoriedade sem seed: o tempo vem da porta `Relogio`, o
  acaso de `random.Random(seed)` ou de hash.
- Código, nomes e docs em português.

## Desenho (decidido antes de codar)

### Estados da garantia

Interna, sem mudança: `pendente → bloqueada → liberada | executada |
parcialmente_executada`. O `pendente` continua sendo o estado da interna e ganha uma
saída nova, `pendente → gravame_expirado`, usada só pela rotina quando o modo legado
`CUSTODIA_MODO=assincrona` deixa uma garantia sem confirmação além do prazo. Na fase 1
padrão (`interna`) o `pendente` nunca chega ao banco, então nada muda.

Externa:

| De | Para |
|---|---|
| `solicitada` | `gravame_pendente`, `gravame_recusado`, `gravame_expirado` |
| `gravame_pendente` | `gravame_confirmado`, `gravame_recusado`, `gravame_expirado` |
| `gravame_confirmado` | `bloqueada` |
| `bloqueada`, `parcialmente_executada` | `liberacao_pendente`, `execucao_solicitada` |
| `liberacao_pendente` | `liberada`, `bloqueada` (recusa ou sem resposta) |
| `execucao_solicitada` | `executada`, `parcialmente_executada`, `bloqueada` (recusa ou sem resposta) |

`solicitada` = pedido gravado, ainda não aceito pelo custodiante (pode estar fora do ar).
`gravame_pendente` = custodiante aceitou e vai responder. `gravame_confirmado` é estado
de passagem: a confirmação e o bloqueio acontecem na mesma transação, mas nenhum caminho
chega a `bloqueada` sem passar por ele.

### O que conta para o limite

- `gera_limite`: `bloqueada` e `parcialmente_executada`. Entra em `limite_garantido`,
  `limite_total` e `limite_disponivel`.
- `execucao_solicitada`: não gera limite, mas o ativo ainda está penhorado e sendo
  vendido para pagar a dívida. Ele entra no cálculo do `deficit` (para não abrir uma
  chamada de margem por causa da própria execução) e em `valor_das_garantias`.
- `liberacao_pendente`: sai de tudo na hora do pedido.

### Ledger da garantia externa

- Bloqueio: o mesmo lançamento da interna, só na confirmação do gravame.
- Recusa e expiração: nenhum lançamento.
- Liberação: o lançamento de liberação só na confirmação.
- Execução: no pedido, só a mudança de estado e o valor pedido (`valor_em_execucao`).
  Na confirmação, um lançamento com consumo da garantia pelo valor pedido e entrada do
  valor efetivamente recebido, limitado à dívida da conta (fatura primeiro, depois o que
  ainda não foi faturado). Se a venda render mais que a dívida, o excedente fica com o
  cliente no próprio custodiante e aparece no evento, fora do ledger da plataforma.
- A rotina diária não pede execução de valor já em execução: o devido é a fatura menos
  o que já está pedido.

### Porta `Custodiante`

`solicitar_bloqueio` fica como está (fase 1). Entram `solicitar_gravame`,
`solicitar_liberacao`, `solicitar_execucao` e `consultar`, todos recebendo um
`PedidoAoCustodiante` com `solicitacao_id` (idempotência) e devolvendo
`RespostaDoCustodiante(status, evento_id, valor_obtido, motivo)`; e `custodiantes()`,
que lista os conhecidos. O simulador decide o destino de cada pedido por hash de
`(seed, solicitacao_id)` e o tempo pela porta `Relogio`. O `evento_id` é derivado do
pedido e do desfecho, então callback e polling da mesma confirmação têm o mesmo id.

### Callback

`POST /v1/custodiantes/{custodiante_id}/callbacks`, sem API key, com
`X-Custodiante-Assinatura: t=<unix>,v1=<hmac>` sobre `"{t}.{corpo}"` (o mesmo formato
dos webhooks de saída, reaproveitando `app/infra/webhooks/assinatura.py`). Segredo por
custodiante em `CUSTODIA_SEGREDOS` (JSON). Custodiante desconhecido, sem segredo,
assinatura errada ou fora da janela: todos respondem o mesmo `401
assinatura_invalida`, sem detalhe. Só depois de autenticado é que se diz se a
solicitação existe.

### Painel

`simular_painel` em `app/dominio/stress.py` reaproveita o estresse por garantia do
`simular_stress`. Perda com execução imediata (todas as garantias vendidas ao preço
estressado) mais a perda adicional da garantia externa (custodiante indisponível
recupera zero no horizonte; prazo extra aplica um desconto diário por ativo). As duas
parcelas são multiplicadas pela taxa de clientes que não reforçam e arredondadas para
cima.

## Pontos para o revisor olhar com lupa

1. Callback repetido, ou callback e polling da mesma confirmação, aplicando duas vezes.
2. Limite aparecendo antes de `gravame_confirmado`, inclusive por remarcação ou rotina.
3. Execução externa somando, com a execução interna, mais do que a fatura.
4. Resposta da rota de callback que permita descobrir ids (custodiante ou solicitação)
   sem ter o segredo.
5. Carteira sintética mudando com a mesma seed (dicionário iterado fora de ordem,
   `hash()` de string, que varia entre processos).

## Tarefas

### 1. Ponto de partida
- [x] Branch reiniciado a partir do `main` (PR #1 e #2 mergeados).
- [x] Suíte de partida: ruff, mypy e 277 testes no Postgres passando; e2e passa com
  `CHROMIUM_EXECUTAVEL=/opt/pw-browsers/chromium` (o Playwright do projeto pede outra
  revisão do navegador do que a instalada nesta máquina).
- [x] DECISOES 39 a 41: pedido, como a execução foi conduzida, `pendente`.

### 2. Domínio: máquina da garantia externa
**Arquivos:** `app/dominio/garantia.py`, `tests/unidade/test_garantia_externa.py`.
- [x] Testes: cada transição válida da tabela acima passa; cada par inválido (produto
  cartesiano menos as válidas) levanta `TransicaoInvalida`; `externa` sem
  `custodiante_id` levanta `ValorInvalido`; interna mantém a tabela antiga.
- [x] `OrigemGarantia(StrEnum)`: `interna`, `externa`. Novos estados em
  `EstadoGarantia`. `TRANSICOES_INTERNAS`, `TRANSICOES_EXTERNAS`. Na `Garantia`:
  `origem`, `custodiante_id`, `solicitacao: Solicitacao | None`, `valor_em_execucao`,
  `motivo`; propriedades `gera_limite`, `cobre_divida`, `aguardando_custodiante`.
  `Solicitacao(id, tipo: TipoSolicitacao, criada_em, prazo_ate, enviada_em)`.
- [x] Commit.

### 3. Domínio: fluxos externos na conta
**Arquivos:** `app/dominio/conta.py`, `app/dominio/eventos.py`,
`tests/unidade/test_conta_externa.py`, `tests/propriedade/test_custodia_externa_prop.py`.
- [x] Testes de unidade: limite zero até a confirmação; confirmação gera o mesmo
  lançamento de bloqueio da interna; recusa e expiração sem lançamento; liberação tira
  o limite no pedido e só lança na confirmação; falha de liberação devolve o limite;
  execução pede só o devido, não abate dívida no pedido, abate o valor recebido na
  confirmação, nunca pede de novo o que já está pedido; venda acima da dívida não
  credita além dela.
- [x] Teste de propriedade (máquina de estados do Hypothesis): qualquer sequência de
  respostas do custodiante, válidas ou não, deixa limite zero antes da confirmação e o
  ledger fechando em zero.
- [x] `Conta.registrar_garantia(..., custodiante_id=None, prazo_ate=None)`;
  `aceitar_solicitacao`, `confirmar_gravame`, `recusar_gravame`, `expirar_solicitacao`,
  `solicitar_liberacao`, `confirmar_liberacao`, `falhar_solicitacao`,
  `confirmar_execucao`. Execução externa dentro de `_executar_garantias`.
- [x] Eventos novos: `gravame.solicitado`, `gravame.confirmado`, `gravame.recusado`,
  `gravame.expirado`, `liberacao.solicitada`, `liberacao.confirmada`,
  `liberacao.falhou`, `execucao.solicitada`, `execucao.confirmada`, `execucao.falhou`.
  Os eventos antigos continuam saindo nos mesmos momentos lógicos e com o mesmo formato.
- [x] Commit.

### 4. Porta e simulador de custodiantes
**Arquivos:** `app/aplicacao/portas.py`, `app/infra/custodia.py`,
`app/custodiantes_simulados.toml`, `tests/unidade/test_custodia_simulada.py`.
- [x] Testes: mesmo pedido, mesmo destino; antes da latência, pendente; depois,
  confirmada ou recusada; "sem resposta" nunca responde; `evento_id` estável;
  proporções de recusa próximas do perfil em 2.000 pedidos; perfil inválido recusado.
- [x] Dois perfis: `custodiante_agil` ("Custodiante Ágil (simulador)": latência 5 s,
  0% de recusa, 0% sem resposta) e `custodiante_lento` ("Custodiante Lento
  (simulador)": latência 6 h, 15% de recusa, 10% sem resposta, prazo 24 h). Números
  ilustrativos.
- [x] Commit.

### 5. Persistência
**Arquivos:** `app/infra/banco/modelos.py`, `alembic/versions/0002_custodia_externa.py`,
`app/infra/banco/repositorios.py`, testes de banco.
- [x] Colunas novas em `garantias` (origem com padrão `interna`, custodiante, dados da
  solicitação, valor em execução, motivo) e a tabela `respostas_de_custodiante` com
  chave `(custodiante_id, evento_id)`.
- [x] Repositório: carrega e grava os campos novos; `obter_pela_solicitacao_para_atualizar`;
  `garantias_aguardando_custodiante`; `registrar_resposta` (levanta `RespostaDuplicada`).
- [x] Teste: migração sobe no Postgres e uma garantia antiga lê como `interna`.
- [x] Commit.

### 6. Casos de uso e rotina
**Arquivos:** `app/aplicacao/custodia.py`, `app/aplicacao/contas.py`,
`app/aplicacao/risco.py`, `app/rotinas.py`, `app/worker.py`.
- [x] `despachar_solicitacao`, `aplicar_resposta(canal=callback|polling|sandbox|admin)`,
  `processar_custodia` (envia o que ficou em `solicitada`, consulta o que espera, expira
  o que passou do prazo, uma conta por transação).
- [x] Commit.

### 7. API
**Arquivos:** `app/api/rotas/custodia.py`, `app/api/esquemas.py`,
`app/api/apresentacao.py`, `app/api/erros.py`, `app/config.py`, `.env.example`.
- [x] Testes de integração: fluxo feliz, recusa, timeout, callback duplicado, callback
  e polling da mesma confirmação, assinatura inválida, timestamp fora da janela,
  custodiante desconhecido igual a assinatura errada, liberação externa, execução
  externa, atalho administrativo pelo mesmo caso de uso, fase 1 inalterada.
- [x] Concorrência no Postgres: dois callbacks iguais ao mesmo tempo, um efeito.
- [x] Commit.

### 8. Painel de risco: cálculo
**Arquivos:** `app/dominio/stress.py`, `app/infra/carteira_sintetica.py`,
`app/carteira_sintetica.toml`, testes.
- [x] Propriedades: perda entre zero e a exposição; choque mais forte nunca diminui a
  perda; custodiante fora do ar só mexe na parcela externa; choque zero não põe em
  chamada quem estava saudável; mesma seed, mesmo resultado; seed diferente, carteira
  diferente; 1.000 clientes em menos de 1 s.
- [x] Commit.

### 9. Painel de risco: API e `/risco`
- [x] `POST /v1/risco/simulacoes-de-carteira` com cenários prontos em
  `GET /v1/risco/cenarios`; erro 422 RFC 7807 para choque fora de ±100% e carteira
  acima de 5.000 clientes.
- [x] Página `/risco` (HTML, CSS e JS sem framework, gráficos em SVG à mão).
- [x] Commit.

### 10. `/demo`
- [x] Opção "Investimento em outra instituição" com escolha do custodiante, linha do
  tempo do gravame, botões de sandbox (confirma, recusa, não responde) para gravame,
  liberação e execução; avisos antigos somem quando o estado muda; resumo fixo no topo.
- [x] E2E: fase 2 com confirmação, recusa e timeout; aviso de chamada de margem some
  depois da execução; `/risco` com os 4 cenários conferidos contra a API.
- [x] Commit.

### 11. `fatura.em_atraso` uma vez
- [x] Teste: vários dias de atraso, um evento só. DECISOES com o porquê.

### 12. Documentação
- [x] README, arquitetura (Mermaid), riscos, mercado (trilhos reais), roteiro, plano.

### 13. Verificação e revisão
- [x] Suíte completa, `scripts/dev.sh`, Playwright pelo `/demo` e `/risco`, screenshots.
- [ ] Revisões pedidas; correções; o resto em "Próximos passos".
- [ ] Push, PR.
