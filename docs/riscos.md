# Riscos e respostas

O que pode dar errado com a ideia e com o sistema, e o que existe (ou falta) para
responder. Onde a resposta já está no código, digo onde. Onde não está, digo que não
está.

## Produto

| Risco | Resposta |
|---|---|
| **"Já existe parecido."** Bancos e fintechs grandes já oferecem limite garantido. | O que existe é feito com o ativo da própria casa, para o cliente da própria casa. O diferencial é ser API para quem não é banco, com investimento que continua rendendo e, nos trilhos regulados, com alguns ativos de fora. Ver [mercado.md](mercado.md). |
| **Não entrar na fila de prioridades da plataforma.** | Piloto pequeno e barato: um parceiro, um produto, teto de limite. A fase 1 reusa o que a plataforma já tem (conta, investimento, cartão). |
| **O parceiro não integra.** | A API segue o que o mercado já usa: chave por parceiro, idempotência, erros RFC 7807, webhooks assinados, sandbox para testar atraso e queda de preço sem esperar. |
| **O cliente não entende.** "Por que reservar R$ 5 mil para ter R$ 4.250 de limite?" | Explicar o LTV é trabalho do app do parceiro. A API devolve em toda resposta o valor da garantia, o LTV e o limite gerado, para a tela conseguir mostrar a conta. |
| **Canibalizar o crédito sem garantia do parceiro.** | O limite garantido é complementar: atende quem não tem limite ou quer mais do que o modelo de crédito daria. A API não substitui a análise de crédito do parceiro. |

## Crédito

| Risco | Resposta |
|---|---|
| **O ativo cai e a garantia deixa de cobrir a dívida.** | LTV conservador por tipo de ativo. Marcação a mercado diária. Quando a dívida passa do limite garantido, abre chamada de margem com prazo; sem reforço nem pagamento, a conta é bloqueada para novas compras. |
| **Queda brusca de um dia para o outro.** Com a dívida no limite, qualquer queda do ativo abre margem; o haircut do LTV só absorve a queda entre o limite e o valor. | O simulador de stress (`POST /v1/risco/simulacoes-de-stress`) mede, para um choque dado, quantas contas entram em margem e quanto ficaria descoberto. O painel `/risco` confere a concentração por conglomerado contra o teto de R$ 250 mil do FGC; não há limite de concentração por ativo na API. |
| **Inadimplência.** | Alienação ou cessão fiduciária: no 7º dia de atraso do cartão (configurável) a garantia é executada por inteiro, sem leilão, na ordem do ativo mais líquido para o menos; o excedente volta ao cliente com demonstrativo. A garantia reduz a perda; restam o risco do emissor, de preço, de liquidez e de execução. |
| **Execução insuficiente.** A garantia inteira não paga a dívida. | A conta fica bloqueada com motivo `divida_descoberta` e o saldo segue para cobrança. O sistema não modela cobrança, juros, multa nem IOF do atraso do cartão: a perda real seria maior que a calculada. |
| **Custo de vender.** O preço de execução pode ser pior que o de marcação. | Não modelado. Na vida real entra como haircut adicional no LTV ou como desconto na execução. |
| **Parceiro cresce além do combinado.** | Teto de carteira por parceiro, conferido a cada compra com a linha do parceiro travada. O teste de concorrência prova que ele vale mesmo com compras simultâneas em contas diferentes. |
| **Liberar garantia que ainda cobre dívida.** | A liberação só passa se o restante cobrir a dívida inteira (`409 garantia_cobrindo_divida`). |

## Jurídico, regulatório e de mercado

Não sou advogado. Tudo aqui precisa de validação de quem é. Cada linha traz a norma e o
artigo quando há; o que é leitura do projeto está marcado como interpretação.

| Tipo | Risco | Mitigação |
|---|---|---|
| Jurídico | Gravame só no sistema interno ou só no contrato, sem registro na registradora ou no depositário: ineficaz perante terceiros (Lei 12.810, art. 26; norma) | O limite só nasce com o gravame registrado (decisão 75); arquivar o comprovante e a certidão (no TD, a B3 e o Selic podem emitir certidão do gravame) |
| Jurídico | Penhor de aplicações de tomador PJ ou MEI em recuperação judicial vai para conta vinculada (Lei 11.101, art. 49, §5º; norma) | Preferir cessão ou alienação fiduciária, que fica fora da recuperação (art. 49, §3º). Crédito PJ não está no projeto |
| Jurídico | Quebra do emissor do CDB: o ativo vira crédito contra a massa, e o FGC paga ao titular registrado, não ao credor (Regulamento do FGC, art. 2º, §4º, I; norma) | Limite por emissor dentro do teto do FGC (interpretação do projeto) e haircut por prazo. Uma cláusula que estenda a garantia ao valor pago pelo FGC é interpretação, a validar com o jurídico. O painel mede o cenário `quebra_de_emissor` |
| Jurídico | Previdência: execução antes de 90 dias ou sem os avisos de 45 e de 10 dias é irregular (Res. Conj. 12, arts. 6º e 12; norma) | Régua específica por ativo, com os prazos embutidos (decisão 80) |
| Regulatório | Contrato de BaaS fora da Res. Conjunta 16: a prestadora responde pelos serviços (arts. 9º, 10 e 16) e a tomadora não pode atuar em nome da prestadora como correspondente (art. 6º, I; norma) | Contratos adequados até 31/12/2026, com as cláusulas mínimas do art. 8º. Não modelado no código |
| Regulatório e dados | Repasse de dados do Open Finance para a fintech sem contrato de parceria (Res. Conj. 1/2020, arts. 36 a 38-A, na redação da Res. Conj. 21/2026; norma) | Contrato de parceria, consentimento que nomeia a parceira, vedação de repasse, finalidade expressa (LGPD), auditoria anual (art. 38, XX) e lista ao BCB até 01/12/2026 (art. 52-A, I). Não modelado |
| Consumidor | Execução da reserva do cliente contestada (CDC e regras de superendividamento) | Transparência na contratação, simulação, aviso antes de executar, opção de pagar antes; evitar depender de execução judicial. Se a cessão fiduciária conta como garantia real para o art. 104-A, §1º do CDC é ponto a confirmar com advogado |
| Operacional | Contraparte recusa ou atrasa (o cenário dos trilhos do protótipo) | Não vender trava externa para CDB, ações e fundos; usar a portabilidade para a casa (Res. CVM 210) ou só os trilhos obrigatórios |
| Operacional | Eventos de custódia durante o gravame (juros, amortização, vencimento) | Regra de vínculo; no TD, os eventos vão ao agente do garantido. O projeto vincula o cupom do Tesouro à garantia (decisão 84); reaplicação fica nos próximos passos |
| Operacional | Gravame esquecido após a quitação | Baixa pedida na hora, com prazo de 2 dias úteis, e conciliação diária (decisão 77) |
| Operacional | Custodiante perde a condição de participante | No Selic, as contas de clientes vão para outro participante em 10 dias úteis (Res. BCB 409/2024; Regulamento do Selic, art. 15, §1º); na B3, o regulamento prevê, em situação especial do participante, a transferência da custódia com os ônus (Regulamento do Balcão, art. 212, X); desde 31/08/2026 a B3 pode movimentar ativos e gravames dos clientes sem instrução do participante (Ofício Circular B3 018/2026). Prever quem comanda a liberação nesse intervalo; o cenário `agente_perde_participacao` do painel mede o atraso |
| Crédito | Queda de preço (IPCA+ e prefixados longos, ações) | Haircut por volatilidade e prazo, marcação diária e corte automático do limite disponível |
| Crédito | Liquidez: LCI e LCA com carência, fundos com cotização longa, COE | Elegibilidade pelo prazo de resgate comparado com o prazo de execução; o COE é marcado como não recomendado |
| Crédito | Juros e encargos do rotativo e do parcelado limitados ao valor original (Lei 14.690, art. 28; norma) | O modelo econômico usa o teto e o colateral deve cobrir principal mais juros até a execução |
| Reputacional | Cliente não entende que o dinheiro fica bloqueado e pode ser usado | Linguagem clara, extrato do gravame e aviso antes de executar |
| Reputacional | IR ou IOF inesperado na execução | Informar os tributos na contratação (exigência expressa na previdência: Res. Conj. 12, art. 7º). O demonstrativo da execução traz a retenção; o IOF regressivo do rendimento não é calculado |
| Reputacional e marca | "Limite Garantido" já é nome de produto de outras instituições (prática de mercado) | Checar o INPI antes de usar o nome como marca; aqui ele é descrição |
| Reputacional | Afirmar mais do que o projeto faz (integração, investimento de outra instituição para todo ativo, Drex) | Textos e telas dizem que o registro é simulado; um teste varre os textos públicos atrás das expressões proibidas (decisão 92) |

## Operacional

| Risco | Resposta |
|---|---|
| **Custodiante ou contraparte de fora não coopera.** | A fase 1 não depende dele. A fase 2 só usa trilho regulado. A fase 3 espera o padrão de gravames. Os riscos dos trilhos estão na seção abaixo. |
| **Rotina diária não roda.** Atraso não é marcado, garantia não é executada. | As rotinas podem rodar de novo sem repetir efeito e processam uma conta por transação. Saem com código de erro se alguma conta falhar, para o agendador alertar. Não há alerta automático de "rotina não rodou hoje". |
| **Preço errado do provedor.** Um preço absurdo derruba limites e dispara chamadas de margem em massa. | Não tratado. Na vida real: limites de variação diária, segunda fonte de preço e trava manual antes de aplicar. Hoje o provedor é fake e determinístico. |
| **Parceiro fora do ar não recebe webhooks.** | Retentativa com backoff exponencial, dead letter depois de N tentativas, reenvio manual e o log de eventos em `GET /v1/eventos` para reconciliar. |
| **Erro humano na política.** Um LTV digitado errado. | A política é validada (LTV entre 0 e 1, prazos positivos, ordem de execução completa). Não há aprovação em duas etapas nem histórico de versões da política. |
| **Fechamento de fatura e cartão são de outro sistema.** | A API expõe fechamento e pagamento, mas num produto real quem manda nisso é a processadora. A integração não existe. |

## Trilhos de fora (simulados)

Os trilhos deste projeto são simulados: as respostas abaixo são o que o código faz com as
contrapartes simuladas. Com contrapartes reais, a mesma lógica vale, mas os prazos, os
motivos de recusa e o canal de aviso viriam de cada trilho, e não sabemos como cada um
os define (ver [arquitetura](arquitetura.md#onde-entraria-o-trilho-real)).

| Risco | Resposta |
|---|---|
| **Demora.** A contraparte leva dias para responder. | O gravame espera em `solicitado`, sem gerar limite. O parceiro recebe o evento do pedido e depois a resposta. O worker consulta periodicamente como reserva do callback. |
| **Recusa.** Ativo já onerado, titularidade divergente, bloqueio judicial. | `recusado` (TD Garantia) ou `cancelado`, com o motivo, nenhum lançamento e o webhook correspondente. O limite nunca existiu, então não há nada a desfazer. |
| **Silêncio.** A contraparte não responde. | No prazo, em dias úteis, o gravame é cancelado ou marcado `garantia.prazo_regulatorio_vencido`. |
| **Resposta tardia.** Resposta que chega depois do cancelamento. | Fica registrada como `obsoleta` e não muda nada. **Lacuna:** não há rotina para desfazer o registro do lado da contraparte. |
| **Indisponibilidade.** A contraparte sai do ar. | O pedido que ainda não saiu fica `solicitado` e o worker tenta de novo; cada garantia é tratada numa transação própria. O painel `/risco` mede o cenário `agente_perde_participacao`. |
| **Fraude no callback.** Alguém finge ser a contraparte. | O callback só é aceito com assinatura HMAC-SHA256 do corpo exato, com o segredo daquela contraparte e timestamp dentro de 5 minutos. Comparação em tempo constante. Qualquer falha responde o mesmo `401`; contraparte inexistente também, com o mesmo custo de cálculo. |
| **Reapresentação do callback.** | Fora da janela, `401`. Dentro dela, o mesmo `evento_id` já está registrado e a resposta é `duplicada`, sem efeito. |
| **Callback e consulta ao mesmo tempo.** | Trava de linha na conta e chave única `(contraparte, evento)` no banco. Testado com dez callbacks simultâneos contra Postgres. |
| **Segredo de callback fraco ou de exemplo em produção.** | A aplicação se recusa a subir, e o simulador de contrapartes fica desligado em produção. |
| **Simulador confundido com integração real.** | O nome de cada contraparte termina em "(simulador)", a API diz `simulado: true`, e a documentação e as telas avisam. Os perfis são fictícios. |

## Tecnológico

| Risco | Resposta |
|---|---|
| **Duas compras simultâneas estouram o limite.** | Trava de linha na conta e, por baixo, versão otimista. Teste com 40 compras simultâneas contra Postgres. |
| **Operação repetida por falha de rede.** | Idempotência persistida, obrigatória em bloqueio, compra e pagamento. A mesma chave com outro payload é recusada. |
| **Saldo divergente.** | Não existe campo de saldo: tudo é soma do ledger. No Postgres, gatilhos impedem alterar o ledger e recusam lançamento que não fecha. |
| **Evento perdido ou evento de operação desfeita.** | Outbox: o evento é gravado na mesma transação da operação. |
| **Vazamento entre parceiros.** | Toda consulta filtra por parceiro; recurso de outro parceiro responde 404. Há teste que percorre todas as rotas com a chave errada. |
| **API key vazada.** | Só o hash fica no banco; a chave pode ser rotacionada. Não há expiração automática, escopos por chave nem lista de IPs. |
| **Webhook usado para atacar a rede interna (SSRF).** | A URL é validada no cadastro e de novo a cada envio: só https, sem IP privado, sem redirecionamento. Resta a janela entre resolver o DNS e conectar. |
| **Segredo de webhook em texto no banco.** | É preciso ler o segredo para assinar, então hash não serve. Em produção ele deveria ser cifrado com chave fora do banco (KMS). Não implementado. |
| **Rate limit por instância.** | O contador fica em memória: com várias instâncias, o limite efetivo se multiplica. Precisa de um contador compartilhado. |
| **Ledger cresce e somar fica caro.** | Hoje o saldo é somado a cada operação. Com volume, a resposta é um saldo consolidado por período, conferido contra o ledger. Não implementado. |
| **Indisponibilidade do banco.** | `/health` responde 503 e o worker continua tentando sem morrer. Não há réplica de leitura, failover nem plano de recuperação descritos. |
