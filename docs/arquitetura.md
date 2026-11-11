# Arquitetura

Este documento mostra como o código está organizado e o que acontece, passo a passo, nas
quatro operações que mais importam: bloquear uma garantia, autorizar uma compra, executar
a garantia e entregar um webhook.

## Camadas

```mermaid
flowchart TB
    subgraph API["API · app/api"]
        rotas["Rotas FastAPI<br/>autenticação, RFC 7807, idempotência,<br/>paginação, rate limit"]
    end
    subgraph APLICACAO["Aplicação · app/aplicacao"]
        casos["Casos de uso<br/>contas, parceiros, risco"]
        portas["Portas<br/>Relógio, Custodiante, ProvedorDePrecos,<br/>Repositórios"]
    end
    subgraph DOMINIO["Domínio · app/dominio (só biblioteca padrão)"]
        conta["Conta (agregado)"]
        garantia["Garantia<br/>(máquina de estados)"]
        ledger["Ledger<br/>(partidas dobradas)"]
        politica["PoliticaRisco"]
        dinheiro["Dinheiro<br/>(centavos inteiros)"]
    end
    subgraph INFRA["Infraestrutura · app/infra"]
        sql["Repositórios SQL<br/>SQLAlchemy 2 + Alembic"]
        worker["Worker de webhooks"]
        rotinas["Rotinas de risco"]
        precos["Provedor de preços fake"]
    end
    BD[("Postgres")]
    PARCEIRO["Servidor do parceiro"]

    rotas --> casos
    casos --> conta
    casos --> portas
    conta --> garantia & ledger & politica & dinheiro
    sql -. implementa .-> portas
    precos -. implementa .-> portas
    sql --> BD
    worker --> BD
    rotinas --> casos
    worker -- "POST assinado" --> PARCEIRO
```

A regra de dependência é uma só: as setas apontam para dentro. O domínio não importa
FastAPI, SQLAlchemy nem relógio; `agora` e `hoje` chegam como argumento. Isso tem um
efeito prático: as regras de crédito são testadas em milissegundos, sem banco, e a
máquina de estados do Hypothesis consegue sortear milhares de sequências de operações.

| Camada | O que mora ali | O que não pode fazer |
|---|---|---|
| `app/dominio` | `Conta`, `Garantia`, `Lancamento`, `PoliticaRisco`, `Dinheiro`, simulador de stress | Importar qualquer coisa fora da biblioteca padrão |
| `app/aplicacao` | Casos de uso e as portas (interfaces) de que eles precisam | Falar SQL ou HTTP |
| `app/infra` | SQLAlchemy, Alembic, worker, rotinas, logs, métricas, provedor de preços | Decidir regra de crédito |
| `app/api` | Rotas, esquemas, erros, idempotência, página `/demo` | Ter regra de negócio |

## O dinheiro: ledger de partidas dobradas

Não existe coluna de saldo no banco. Dívida e valor de garantia são sempre a soma das
partidas do ledger. Toda movimentação vira um lançamento em que débitos e créditos
somam o mesmo valor.

| Conta contábil | Natureza | O que representa |
|---|---|---|
| `garantias_custodiadas` | devedora | Valor de mercado dos investimentos bloqueados |
| `garantias_a_devolver` | credora | Contrapartida: a garantia é do cliente e volta para ele |
| `recebiveis_a_faturar` | devedora | Compras aprovadas que ainda não entraram em fatura |
| `recebiveis_faturados` | devedora | Fatura fechada e ainda não paga |
| `liquidacao_a_pagar` | credora | O que a plataforma deve à rede pelas compras aprovadas |
| `caixa_recebimentos` | devedora | O que entrou: pagamentos e execução de garantia |

As duas primeiras são contas de compensação: registram a garantia recebida sem misturar
com o dinheiro da operação de crédito.

| Operação | Débito | Crédito |
|---|---|---|
| Bloqueio de garantia | `garantias_custodiadas` | `garantias_a_devolver` |
| Marcação a mercado (queda) | `garantias_a_devolver` | `garantias_custodiadas` |
| Compra aprovada | `recebiveis_a_faturar` | `liquidacao_a_pagar` |
| Fechamento de fatura | `recebiveis_faturados` | `recebiveis_a_faturar` |
| Pagamento | `caixa_recebimentos` | `recebiveis_faturados` (e o que sobrar em `recebiveis_a_faturar`) |
| Execução | `garantias_a_devolver` e `caixa_recebimentos` | `garantias_custodiadas` e `recebiveis_faturados` |
| Liberação | `garantias_a_devolver` | `garantias_custodiadas` |

A partir daí, tudo é leitura:

- **dívida** = saldo de `recebiveis_a_faturar` + `recebiveis_faturados`;
- **valor de uma garantia** = saldo de `garantias_custodiadas` daquela garantia;
- **limite** = soma de `valor de mercado × LTV` de cada garantia ativa, cada parcela
  arredondada para baixo, limitada pelo teto por cliente;
- **limite disponível** = `max(0, limite − dívida)`, e zero se a conta estiver bloqueada.

O ledger é protegido em três lugares. No domínio, um `Lancamento` desbalanceado não
chega a existir (o construtor recusa). No Postgres, um gatilho confere no commit que
cada lançamento fecha, e outro impede `UPDATE` e `DELETE` nas tabelas do ledger. E um
teste de propriedade sorteia sequências de operações e confere que a soma é sempre zero.

## Estados

### Garantia

```mermaid
stateDiagram-v2
    [*] --> pendente: pedido registrado
    pendente --> bloqueada: custódia confirma o gravame
    bloqueada --> liberada: cliente resgata
    bloqueada --> parcialmente_executada: execução vende uma parte
    bloqueada --> executada: execução vende tudo
    parcialmente_executada --> parcialmente_executada: nova execução parcial
    parcialmente_executada --> liberada: cliente resgata a sobra
    parcialmente_executada --> executada: execução vende o resto
    liberada --> [*]
    executada --> [*]
```

Só `bloqueada` e `parcialmente_executada` contam para o limite. Qualquer transição fora
do desenho responde `409 transicao_de_estado_invalida`, com o estado atual e o pedido.

`pendente` existe por causa das fases 2 e 3 do [roadmap](roadmap.md): quando o
investimento está fora de casa, o gravame é confirmado por um terceiro e pode demorar.
Dentro de casa a confirmação é imediata e a garantia já nasce `bloqueada` na resposta.

### Fatura, chamada de margem e conta

```mermaid
stateDiagram-v2
    direction LR
    state "Fatura" as F {
        [*] --> fechada
        fechada --> paga: pagamento total
        fechada --> em_atraso: 1 dia depois do vencimento
        em_atraso --> paga: pagamento total
        em_atraso --> liquidada_por_execucao: 7º dia de atraso
    }
    state "Chamada de margem" as M {
        [*] --> aberta: dívida > limite garantido
        aberta --> atendida: reforço ou pagamento
        aberta --> vencida: prazo esgotado
    }
    state "Conta" as C {
        [*] --> ativa
        ativa --> bloqueada: chamada vencida ou dívida descoberta
        bloqueada --> ativa: déficit zerado e sem atraso
    }
```

## Sequências

### 1. Bloquear uma garantia

```mermaid
sequenceDiagram
    autonumber
    participant P as Parceiro
    participant A as API
    participant D as Domínio (Conta)
    participant C as Custódia
    participant B as Postgres

    P->>A: POST /v1/contas/{id}/garantias<br/>Idempotency-Key, tipo_ativo, valor
    A->>B: BEGIN · reserva a chave de idempotência
    A->>B: SELECT conta FOR NO KEY UPDATE + saldos do ledger
    A->>D: registrar_garantia (nasce pendente)
    A->>C: solicitar_bloqueio
    C-->>A: confirmado (custódia interna)
    A->>D: confirmar_bloqueio
    D-->>A: lançamento + eventos garantia.bloqueada e limite.atualizado
    A->>B: garantia, lançamento, partidas, eventos e entregas de webhook
    A->>B: guarda a resposta na chave · COMMIT
    A-->>P: 201 com a garantia e o novo limite
```

### 2. Autorizar uma compra

```mermaid
sequenceDiagram
    autonumber
    participant P as Parceiro
    participant A as API
    participant D as Domínio (Conta)
    participant B as Postgres

    P->>A: POST /v1/contas/{id}/compras<br/>Idempotency-Key, valor
    A->>B: BEGIN · reserva a chave de idempotência
    opt política com teto do parceiro
        A->>B: trava a linha do parceiro · soma a carteira
    end
    A->>B: SELECT conta FOR NO KEY UPDATE
    Note over A,B: Uma segunda compra na mesma conta espera aqui.<br/>Quando passa, já lê a dívida atualizada pela primeira.
    A->>B: soma as partidas (dívida e garantias)
    A->>D: autorizar_compra(valor)
    alt cabe no limite disponível
        D-->>A: compra aprovada + lançamento + eventos
        A->>B: grava tudo · COMMIT
        A-->>P: 201
    else não cabe, conta bloqueada, fatura em atraso ou teto do parceiro
        D-->>A: compra negada (sem lançamento) + evento compra.negada
        A->>B: grava a compra negada e o evento · COMMIT
        A-->>P: 402 problem+json com o motivo em "codigo"
    end
```

Compra negada não é exceção: é um resultado de negócio, gravado e idempotente como a
compra aprovada. Repetir a mesma `Idempotency-Key` devolve a mesma decisão.

### 3. Executar a garantia no 7º dia

```mermaid
sequenceDiagram
    autonumber
    participant R as Rotina diária
    participant D as Domínio (Conta)
    participant B as Postgres

    R->>B: contas com fatura vencida ou chamada de margem vencida
    loop uma conta por transação
        R->>B: BEGIN · SELECT conta FOR NO KEY UPDATE
        R->>D: virar_o_dia(hoje)
        alt 1º dia de atraso
            D-->>R: fatura passa a em_atraso + evento fatura.em_atraso
        else atraso ≥ dias_para_execucao da política
            D->>D: ordena as garantias: as mais líquidas primeiro
            D->>D: vende só o necessário para quitar a fatura
            D-->>R: lançamentos de execução + garantia.executada + limite.atualizado
        end
        R->>B: grava · COMMIT
    end
```

Três escolhas estão embutidas aqui. A execução cobre só a fatura vencida, não as compras
que ainda nem venceram. Vende-se o mínimo: o que sobra da garantia continua bloqueado e
rendendo. E cada conta tem a sua transação, de modo que uma conta com problema não
impede as outras.

### 4. Entregar um webhook

```mermaid
sequenceDiagram
    autonumber
    participant O as Operação (API ou rotina)
    participant B as Postgres
    participant W as Worker
    participant P as Servidor do parceiro

    O->>B: na MESMA transação: lançamento + evento + entrega (pendente)
    loop a cada meio segundo
        W->>B: reserva um lote (FOR UPDATE SKIP LOCKED) · COMMIT
        W->>W: confere o destino (anti-SSRF) e assina
        W->>P: POST · X-LG-Assinatura: t=...,v1=HMAC-SHA256(t.corpo)
        alt 2xx
            W->>B: entregue
        else erro ou timeout
            W->>B: tentativas += 1 · próxima em base × 2^(n−1), até o teto
            Note over W,B: Depois de N tentativas vira "morta" (dead letter).<br/>O parceiro pode pedir reenvio manual.
        end
    end
    P->>P: confere o HMAC e recusa t com mais de 5 minutos
```

O que o padrão outbox garante: não existe operação confirmada sem evento, nem evento de
operação desfeita. O que ele não garante: entrega exatamente uma vez. A entrega é "pelo
menos uma vez", e por isso todo evento tem um `id` estável para o receptor reconhecer
repetição. A requisição HTTP acontece fora de qualquer transação: o worker reserva,
envia e só então grava o resultado, sem segurar conexão do banco durante a chamada.

## Concorrência

O risco clássico de um autorizador é duas compras lerem o mesmo limite e as duas
passarem. Aqui há duas defesas, e a segunda existe para o caso de a primeira ser
esquecida numa alteração futura.

1. **Trava de linha.** Toda escrita carrega a conta com `SELECT ... FOR NO KEY UPDATE`.
   A segunda transação espera a primeira terminar e só então soma o ledger.
2. **Versão otimista.** A conta tem uma coluna `versao`. A gravação é um
   `UPDATE ... WHERE versao = <a que eu li>`; se não afetar nenhuma linha, a operação é
   desfeita e responde `409 conflito_de_concorrencia`.

O teste de concorrência dispara 40 compras de R$ 100 ao mesmo tempo num limite de
R$ 1.000, contra Postgres de verdade, e exige exatamente 10 aprovadas. Foi ele que
encontrou os dois problemas descritos em [DECISOES.md](DECISOES.md): um deadlock por
*upgrade* de lock e um esgotamento do pool de conexões.

Ordem das travas, sempre a mesma para não haver deadlock: parceiro (só quando a política
tem teto de carteira) e depois conta.

## Multi-tenant

Cada parceiro tem a sua API key. No banco ficam o prefixo (para achar a linha) e o
SHA-256 da chave; a chave inteira aparece uma vez, na criação. Toda consulta filtra por
`parceiro_id`, e pedir um recurso de outro parceiro responde `404`, nunca `403`: para
quem pergunta, ele simplesmente não existe.

A política de risco é do parceiro (LTV por ativo, ordem de execução, prazos, tetos) e
quem a altera é a plataforma, pela API administrativa.

## Observabilidade

- **Logs** em JSON, uma linha por evento, sempre com `request_id` e `parceiro_id`.
- **`X-Request-Id`** em toda resposta, inclusive nos erros 500. O mesmo id está no log e
  no corpo do erro.
- **`/metrics`** no formato do Prometheus: requisições por rota e status (com o template
  da rota, não o caminho real), tempo de resposta, eventos de negócio por tipo e tamanho
  da fila de webhooks.
- **`/health`** responde 200 quando a API fala com o banco e 503 quando não.
