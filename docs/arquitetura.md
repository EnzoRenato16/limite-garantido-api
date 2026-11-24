# Arquitetura

Este documento mostra os papéis, como o código está organizado e o que acontece, passo a
passo, nas operações que mais importam: registrar o gravame, autorizar uma compra,
executar a garantia, entregar um webhook e trazer um ativo de fora.

> O projeto **simula** o registro na B3 e no Selic, a plataforma da Res. Conjunta 12 e
> as contrapartes. Nenhuma integração real existe.

## Papéis

| Papel | O que faz | Observação |
|---|---|---|
| Entidade tomadora de BaaS | A fintech parceira: só interface e distribuição | Não concede o crédito nem emite o cartão (Res. Conjunta 16/2025, art. 6º, I) |
| Plataforma BaaS (esta API) | Presta o serviço de garantia ao parceiro | Responde pelos serviços que presta (Res. Conjunta 16/2025, arts. 9º, 10 e 16) |
| SCD do grupo | Concede o crédito, emite o cartão e é a beneficiária do gravame | Papel genérico; não nomeia empresa |
| CTVM do grupo | Custodia o ativo e comanda o gravame | O custodiante comanda; a registradora registra |
| DTVM do grupo | Administra os fundos DI | |
| Registradora ou depositário central | Registra o gravame (B3 ou Selic) | Lei 12.810/2013, art. 26. **Simulado aqui** |
| Agente de custódia | Custodia o Tesouro Direto do cliente (TD Garantia) | Simulado |
| Entidade operadora | Seguradora, EAPC ou sociedade de capitalização da previdência | Simulada; troca informações pela plataforma autorizada da Res. Conjunta 12 |

## Os trilhos

Cada tipo de ativo chega à garantia por um caminho. A regra de qual caminho serve a qual
ativo e produto mora no catálogo (`app/regulacao.toml`), e o domínio só pergunta a ele.

```mermaid
flowchart LR
    CLI["Cliente"] --> PAR["Parceiro<br/>(tomadora de BaaS)"]
    PAR --> API["API da plataforma"]
    API --> CAT{{"Catálogo regulatório<br/>ativo · produto · trilho"}}
    CAT -- "custodiado na casa" --> CASA["Trilho casa<br/>gravame no mesmo dia"]
    CAT -- "ações, FIIs, ETFs, fundos" --> POR["Portabilidade<br/>Res. CVM 210"]
    CAT -- "CDB, LCI, LCA" --> TRA["Transferência de custódia<br/>(fora da CVM 210)"]
    CAT -- "Tesouro Direto" --> TD["TD Garantia<br/>agente de custódia"]
    CAT -- "PGBL, VGBL" --> RC["Res. Conjunta 12<br/>entidade operadora"]
    CASA --> INF["Infraestrutura simulada<br/>(B3 ou Selic)"]
    POR --> INF
    TRA --> INF
    TD --> INF
    RC --> PLA["Plataforma autorizada<br/>(simulada)"]
    INF --> GRA["Gravame registrado"]
    PLA --> GRA
    GRA --> LIM["Cartão: limite<br/>Empréstimo: crédito"]
```

## Camadas

```mermaid
flowchart TB
    subgraph API["API · app/api"]
        rotas["Rotas FastAPI<br/>autenticação, RFC 7807, idempotência,<br/>paginação, rate limit"]
    end
    subgraph APLICACAO["Aplicação · app/aplicacao"]
        casos["Casos de uso<br/>contas, trilhos, empréstimos,<br/>conciliação, parceiros, risco"]
        portas["Portas<br/>Relógio, Contraparte, InfraestruturaDeRegistro,<br/>ProvedorDePrecos, Repositórios"]
    end
    subgraph DOMINIO["Domínio · app/dominio (só biblioteca padrão)"]
        conta["Conta (agregado)"]
        garantia["Garantia"]
        gravame["Gravame<br/>(máquina de estados)"]
        emprestimo["Empréstimo<br/>e parcelas"]
        ledger["Ledger<br/>(partidas dobradas)"]
        politica["PoliticaRisco"]
        dinheiro["Dinheiro<br/>(centavos inteiros)"]
    end
    subgraph INFRA["Infraestrutura · app/infra"]
        sql["Repositórios SQL<br/>SQLAlchemy 2 + Alembic"]
        worker["Worker: webhooks, contrapartes,<br/>baixas"]
        rotinas["Rotinas: marcação, virada do dia,<br/>conciliação"]
        simulados["Contrapartes e infraestrutura<br/>simuladas"]
        precos["Provedor de preços fake"]
    end
    BD[("Postgres")]
    PARCEIRO["Servidor do parceiro"]

    rotas --> casos
    casos --> conta
    casos --> portas
    conta --> garantia & gravame & emprestimo & ledger & politica & dinheiro
    sql -. implementa .-> portas
    precos -. implementa .-> portas
    simulados -. implementa .-> portas
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
| `app/dominio` | `Conta`, `Garantia`, `Gravame`, `Emprestimo`, `Lancamento`, `PoliticaRisco`, `Dinheiro`, o catálogo regulatório, simulador de stress, modelo econômico (`economia/`, ver [economia.md](economia.md)) | Importar qualquer coisa fora da biblioteca padrão |
| `app/aplicacao` | Casos de uso e as portas (interfaces) de que eles precisam | Falar SQL ou HTTP |
| `app/infra` | SQLAlchemy, Alembic, worker, rotinas, logs, métricas, provedor de preços, contrapartes e infraestrutura simuladas | Decidir regra de crédito |
| `app/api` | Rotas, esquemas, erros, idempotência, páginas `/demo`, `/regulacao`, `/risco` e `/negocio` | Ter regra de negócio |

## O dinheiro: ledger de partidas dobradas

Não existe coluna de saldo no banco. Dívida e valor de garantia são sempre a soma das
partidas do ledger. Toda movimentação vira um lançamento em que débitos e créditos
somam o mesmo valor.

| Conta contábil | Natureza | O que representa |
|---|---|---|
| `garantias_custodiadas` | devedora | Valor de mercado dos ativos com gravame registrado |
| `cupons_vinculados` | devedora | Cupom e amortização de Tesouro com gravame, vinculados à garantia |
| `excedente_a_devolver` | credora | Sobra da execução que volta ao cliente |
| `emprestimos_a_receber` | devedora | Saldo do empréstimo com garantia |
| `desembolsos_a_liquidar` | credora | Desembolso do empréstimo ainda não pago ao cliente |
| `receita_de_juros` | credora | Juros do empréstimo recebidos |
| `garantias_a_devolver` | credora | Contrapartida: a garantia é do cliente e volta para ele |
| `recebiveis_a_faturar` | devedora | Compras aprovadas que ainda não entraram em fatura |
| `recebiveis_faturados` | devedora | Fatura fechada e ainda não paga |
| `liquidacao_a_pagar` | credora | O que a plataforma deve à rede pelas compras aprovadas |
| `caixa_recebimentos` | devedora | O que entrou: pagamentos e execução de garantia |

As duas primeiras (e `cupons_vinculados`) são contas de compensação: registram a garantia
recebida sem misturar com o dinheiro da operação de crédito.

| Operação | Débito | Crédito |
|---|---|---|
| Gravame registrado | `garantias_custodiadas` | `garantias_a_devolver` |
| Marcação a mercado (queda) | `garantias_a_devolver` | `garantias_custodiadas` |
| Compra aprovada | `recebiveis_a_faturar` | `liquidacao_a_pagar` |
| Fechamento de fatura | `recebiveis_faturados` | `recebiveis_a_faturar` |
| Pagamento | `caixa_recebimentos` | `recebiveis_faturados` (e o que sobrar em `recebiveis_a_faturar`) |
| Execução (por inteiro) | `garantias_a_devolver` e `caixa_recebimentos` | `garantias_custodiadas`, `recebiveis_faturados` e `excedente_a_devolver` (a sobra) |
| Devolução do excedente | `excedente_a_devolver` | `caixa_recebimentos` |
| Baixa pedida (liberação) | `garantias_a_devolver` | `garantias_custodiadas` |
| Empréstimo contratado | `emprestimos_a_receber` | `desembolsos_a_liquidar` |

A partir daí, tudo é leitura:

- **dívida** = saldo de `recebiveis_a_faturar` + `recebiveis_faturados`;
- **valor de uma garantia** = saldo de `garantias_custodiadas` (mais `cupons_vinculados`) daquela garantia;
- **limite** = soma de `valor de mercado × LTV` de cada garantia ativa, cada parcela
  arredondada para baixo, limitada pelo teto por cliente;
- **limite disponível** = `max(0, limite − dívida)`, e zero se a conta estiver bloqueada.

O ledger é protegido em três lugares. No domínio, um `Lancamento` desbalanceado não
chega a existir (o construtor recusa). No Postgres, um gatilho confere no commit que
cada lançamento fecha, e outro impede `UPDATE` e `DELETE` nas tabelas do ledger. E um
teste de propriedade sorteia sequências de operações e confere que a soma é sempre zero.

## Estados

### Gravame

O gravame é o registro na infraestrutura (a registradora ou o depositário central) e tem
a sua própria máquina de estados. A garantia é a relação de crédito: produto, valor, LTV,
instrumento e a dívida que cobre. O limite do cartão e o valor do empréstimo só existem
com o gravame `registrado` e sem baixa pedida (decisão 75).

```mermaid
stateDiagram-v2
    [*] --> solicitado: pedido gravado
    solicitado --> registrado: infraestrutura registra, com protocolo
    solicitado --> recusado: contraparte recusa (só TD Garantia)
    solicitado --> cancelado: prazo vence ou pedido cancelado
    registrado --> baixado: quitação ou liberação, baixa em até 2 dias úteis
    registrado --> liberado_para_excussao: atraso na régua
    liberado_para_excussao --> executado: venda ou resgate por inteiro
    executado --> baixado: baixa na infraestrutura
    recusado --> [*]
    cancelado --> [*]
    baixado --> [*]
```

| Estado | Gera limite | Cobre a dívida | Espera a infraestrutura ou a contraparte |
|---|---|---|---|
| `solicitado` | não | não | sim |
| `registrado` (sem baixa pedida) | sim | sim | não |
| `registrado` (com baixa pedida) | não, desde o pedido | não | sim, até a baixa |
| `liberado_para_excussao`, `executado` | não | sim, até a venda | sim |
| `recusado`, `cancelado`, `baixado` | não | não | não |

Recusa e cancelamento nunca geram lançamento. Uma garantia na casa nasce `solicitado` e
`registrado` na mesma requisição, porque o registro é do mesmo grupo; nos trilhos de fora,
fica `solicitado` até a contraparte responder. Qualquer transição fora do desenho responde
`409 transicao_de_estado_invalida`, com o estado atual e o pedido.

A baixa é pedida na hora, com prazo de 2 dias úteis (decisão 77), e uma rotina
de conciliação aponta gravame sem dívida, dívida sem gravame e baixa atrasada.

### Empréstimo com garantia

```mermaid
stateDiagram-v2
    [*] --> ativo: contratado (gravame registrado e teto conferido)
    ativo --> em_atraso: parcela vencida e não paga
    ativo --> quitado: última parcela paga
    em_atraso --> quitado: pagamento total
    em_atraso --> liquidado_por_execucao: atraso passa da régua e a garantia é executada
```

A régua de atraso vem da política (30 dias na maior parte dos ativos). Na previdência, a
execução só vem depois de 90 dias de atraso (Res. Conjunta 12, art. 12), com avisos aos 45
dias e 10 dias antes da execução. Os juros do saldo devedor correm pro rata, arredondados
para cima.

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

### 1. Registrar o gravame na casa

```mermaid
sequenceDiagram
    autonumber
    participant P as Parceiro
    participant A as API
    participant K as Catálogo regulatório
    participant D as Domínio (Conta)
    participant I as Infraestrutura (simulada)
    participant B as Postgres

    P->>A: POST /v1/contas/{id}/garantias<br/>Idempotency-Key, tipo_ativo, valor, liquidez_dias
    A->>K: exigir_combinacao(ativo, cartão, liquidez)
    K-->>A: ok, ou 422 com código e sugestão
    A->>B: BEGIN · reserva a chave de idempotência
    A->>B: SELECT conta FOR NO KEY UPDATE + saldos do ledger
    A->>D: custodiar_na_casa (gravame nasce solicitado)
    D->>I: registrar o gravame
    I-->>D: protocolo
    D-->>A: gravame registrado + lançamento + eventos gravame.registrado e limite.atualizado
    A->>B: garantia, gravame, lançamento, partidas, eventos e entregas de webhook
    A->>B: guarda a resposta na chave · COMMIT
    A-->>P: 201 com a garantia, o gravame e o novo limite
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

### 5. Trazer um ativo de fora: portabilidade, caminho feliz

```mermaid
sequenceDiagram
    autonumber
    participant P as Parceiro
    participant A as API
    participant D as Domínio
    participant B as Banco
    participant W as Worker (rotina de contrapartes)
    participant C as Contraparte (simulada)
    P->>A: POST /v1/contas/{id}/portabilidades {contraparte_id, ativo, valor}
    A->>D: pedir entrada (gravame solicitado, com prazo em dias úteis)
    A->>B: garantia + gravame + pedido + evento portabilidade.solicitada
    A-->>P: 202, limite ainda zero
    Note over W,C: O pedido sai depois do commit: nunca há registro lá sem registro aqui
    W->>C: solicitar a entrada
    alt o callback chega
        C->>A: POST /v1/contrapartes/{id}/callbacks (assinado)
        A->>A: confere HMAC e janela do timestamp
    else o callback não chega
        W->>C: consultar
        C-->>W: concluída, evento_id
    end
    A->>B: trava a conta; (contraparte, evento_id) já existe?
    alt primeira vez
        A->>D: concluir a entrada e registrar o gravame na infraestrutura
        A->>B: lançamento + resposta registrada + eventos
        B-->>P: webhooks portabilidade.concluida, gravame.registrado, limite.atualizado
    else mesma resposta de novo
        A-->>C: 200 {"resultado": "duplicada"}, nada muda
    end
```

### 6. Entrada recusada ou com impedimento

```mermaid
sequenceDiagram
    autonumber
    participant W as Worker
    participant C as Contraparte (simulada)
    participant A as Caso de uso
    participant B as Banco
    participant P as Parceiro
    W->>C: consultar
    C-->>W: impedimento ou recusada, motivo
    W->>A: aplicar_resposta (canal polling)
    A->>B: gravame recusado (TD Garantia) ou cancelado, sem lançamento
    B-->>P: webhook gravame.recusado ou gravame.cancelado {motivo}
```

Na portabilidade da CVM 210, só `impedimento` é aceito como resposta negativa e as demais
são recusa (art. 19); resposta de um trilho no outro dá `422`.

### 7. Contraparte sem resposta no prazo

```mermaid
sequenceDiagram
    autonumber
    participant W as Worker
    participant C as Contraparte (simulada)
    participant B as Banco
    participant P as Parceiro
    loop a cada CONTRAPARTES_INTERVALO_SEGUNDOS
        W->>C: consultar
        C-->>W: pendente
    end
    Note over W: agora > prazo_ate do pedido (dias úteis)
    W->>B: gravame cancelado, ou marcado garantia.prazo_regulatorio_vencido
    B-->>P: webhook gravame.cancelado ou garantia.prazo_regulatorio_vencido
    Note over B: Se a resposta chegar depois, é registrada como obsoleta e não muda nada
```

### 8. Baixa e conciliação

```mermaid
sequenceDiagram
    autonumber
    participant D as Domínio
    participant W as Worker (rotina de baixas)
    participant I as Infraestrutura (simulada)
    participant R as Conciliação diária
    D->>D: quitação, liberação ou execução: baixa pedida, prazo de 2 dias úteis
    W->>I: pedir a baixa do gravame
    I-->>W: baixado
    W->>D: gravame baixado + evento gravame.baixado
    R->>D: gravame sem dívida? dívida sem gravame? baixa atrasada?
```

## Onde entraria o trilho real

A integração real pararia em duas portas (`app/aplicacao/portas.py`): `Contraparte`
(a outra ponta do pedido de entrada: corretora, banco de origem, agente de custódia,
entidade operadora) e `InfraestruturaDeRegistro` (a B3 ou o Selic). Os simuladores
(`app/infra/contrapartes.py` e `app/infra/infraestrutura_simulada.py`) são uma
implementação delas; uma real seria outra, com os mesmos métodos.

| Ativo | Trilho que existe | O que as fontes públicas dizem | O que não sabemos |
|---|---|---|---|
| Tesouro Direto | TD Garantia | Anunciado em 25/11/2024 por B3, Tesouro Nacional e Banco Central; permite ao investidor pessoa física usar títulos do Tesouro Direto como garantia (prática de mercado). O Selic passou a registrar gravames desses títulos (fonte secundária). | Quem pode receber a garantia e como a instituição se habilita, o protocolo, se há callback ou só consulta, os prazos. |
| Previdência, seguro de pessoas, capitalização | Lei 14.652/2023 e Resolução Conjunta CNSP/CMN nº 12/2024 | O direito de resgate pode ser dado em garantia de operações de crédito; a operadora informa em 2 dias úteis e não recusa pedido que siga a norma (arts. 4º, §1º, e 18); a execução só vem após 90 dias de atraso (art. 12). | O RC Garantias da B3, anunciado em 11/11/2025 para o início de 2026, e se já opera: não verificado. |
| Ações, FIIs, ETFs, cotas de fundos | Portabilidade da Res. CVM 210/2024 | Prazo de 2, 5 ou até 9 dias úteis (art. 16). | Como o custodiante de origem trata o gravame durante a portabilidade. |
| CDB, LCI, LCA | Transferência de custódia, fora da CVM 210 | Entendimento corrente: ficam fora da CVM 210 (Lei 6.385, art. 2º, §1º), interpretação. | Não encontramos norma que obrigue o custodiante de origem a registrar gravame para credor de outra instituição, nem prazo regulatório. |

As fontes, e o que foi confirmado em fonte oficial ou só em fonte secundária, estão em
[mercado.md](mercado.md#trilhos-regulados-que-já-existem) e, por ativo, em
[regulacao.md](regulacao.md). Nada nesta tabela descreve o funcionamento interno desses
trilhos: o desenho das portas (pedido idempotente, resposta assíncrona, prazo,
confirmação por evento) é uma suposição do projeto sobre o que qualquer integração desse
tipo precisa, não uma descrição de como eles funcionam.

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

**Respostas da contraparte.** Callback e consulta podem trazer a mesma resposta ao mesmo
tempo, e a contraparte pode repetir o callback. As duas defesas
aqui são a trava de linha da conta (quem chega depois espera e já enxerga a resposta
registrada) e a chave primária `(contraparte_id, evento_id)` em
`respostas_de_custodiante` (tabela mantida; a coluna `custodiante_id` guarda o
`contraparte_id`), gravada num savepoint. Um teste dispara dez callbacks
iguais ao mesmo tempo contra Postgres e exige uma resposta `aplicada`, nove
`duplicada` e um único lançamento; outro mistura callbacks e rodadas da rotina de
consulta.

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
