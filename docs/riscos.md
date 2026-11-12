# Riscos e respostas

O que pode dar errado com a ideia e com o sistema, e o que existe (ou falta) para
responder. Onde a resposta já está no código, digo onde. Onde não está, digo que não
está.

## Produto

| Risco | Resposta |
|---|---|
| **"Já existe parecido."** Bancos e fintechs grandes já oferecem limite garantido. | O que existe é feito com o ativo da própria casa, para o cliente da própria casa. O diferencial é ser API para quem não é banco, com investimento que continua rendendo e, mais adiante, com ativos de fora. Ver [mercado.md](mercado.md). |
| **Não entrar na fila de prioridades da plataforma.** | Piloto pequeno e barato: um parceiro, um produto, teto de limite. A fase 1 reusa o que a plataforma já tem (conta, investimento, cartão). |
| **O parceiro não integra.** | A API segue o que o mercado já usa: chave por parceiro, idempotência, erros RFC 7807, webhooks assinados, sandbox para testar atraso e queda de preço sem esperar. |
| **O cliente não entende.** "Por que guardar R$ 5 mil para ter R$ 4.250 de limite?" | Explicar o LTV é trabalho do app do parceiro. A API devolve em toda resposta o valor da garantia, o LTV e o limite gerado, para a tela conseguir mostrar a conta. |
| **Canibalizar o crédito sem garantia do parceiro.** | O limite garantido é complementar: atende quem não tem limite ou quer mais do que o modelo de crédito daria. A API não substitui a análise de crédito do parceiro. |

## Crédito

| Risco | Resposta |
|---|---|
| **O ativo cai e a garantia deixa de cobrir a dívida.** | LTV conservador por tipo de ativo. Marcação a mercado diária. Quando a dívida passa do limite garantido, abre chamada de margem com prazo; sem reforço nem pagamento, a conta é bloqueada para novas compras. |
| **Queda brusca de um dia para o outro.** O LTV de 60% em ações aguenta 40% de queda, não mais. | O simulador de stress (`POST /v1/risco/simulacoes-de-stress`) mede, para um choque dado, quantas contas entram em margem e quanto ficaria descoberto. Não há limite de concentração por ativo: está nos próximos passos. |
| **Inadimplência.** | Alienação fiduciária: no 7º dia de atraso (configurável) a garantia é executada sem leilão. Vende-se só o valor da fatura vencida, do ativo mais líquido para o menos. |
| **Execução insuficiente.** A garantia inteira não paga a fatura. | A conta fica bloqueada com motivo `divida_descoberta` e o saldo segue para cobrança. O sistema não modela cobrança, juros, multa nem IOF: a perda real seria maior que a calculada. |
| **Custo de vender.** O preço de execução pode ser pior que o de marcação. | Não modelado. Na vida real entra como haircut adicional no LTV ou como desconto na execução. |
| **Parceiro cresce além do combinado.** | Teto de carteira por parceiro, conferido a cada compra com a linha do parceiro travada. O teste de concorrência prova que ele vale mesmo com compras simultâneas em contas diferentes. |
| **Liberar garantia que ainda cobre dívida.** | A liberação só passa se o restante cobrir a dívida inteira (`409 garantia_cobrindo_divida`). |

## Jurídico e regulatório

Não sou advogado. Tudo aqui precisa de validação de quem é.

| Risco | Resposta |
|---|---|
| **A execução extrajudicial ser contestada.** | A base é a alienação fiduciária de ativos financeiros (Lei 4.728, art. 66-B, § 3º), que admite a venda sem leilão e manda devolver o saldo ao devedor. O sistema registra cada execução no ledger, com o valor vendido e o remanescente, e emite evento: é o demonstrativo da operação. |
| **Superendividamento.** | Crédito com garantia real fica fora da repactuação (CDC, art. 104-A, § 1º). Isso não dispensa crédito responsável nem informação clara ao cliente. |
| **Quem é o credor e quem registra a garantia.** | Em aberto. Alguém precisa ser a instituição credora. Com FIDC comprando os recebíveis, a cessão e a garantia precisam de estruturação própria. O código não modela a cessão. |
| **Regras do trilho mudarem.** TD Garantia e previdência como garantia são recentes. | A custódia está atrás de uma interface (`Custodiante`), e a fase 2 só começa depois de a fase 1 funcionar. |
| **Dados pessoais.** | A API só conhece `cliente_ref`, um identificador do parceiro. CPF, nome e contato não entram. Logs não registram corpo de requisição. |

## Operacional

| Risco | Resposta |
|---|---|
| **Custodiante externo não coopera.** | A fase 1 não depende dele. A fase 2 só usa trilho regulado. A fase 3 espera o padrão de gravames. Os riscos do custodiante na fase 2 estão na seção abaixo. |
| **Rotina diária não roda.** Atraso não é marcado, garantia não é executada. | As rotinas podem rodar de novo sem repetir efeito e processam uma conta por transação. Saem com código de erro se alguma conta falhar, para o agendador alertar. Não há alerta automático de "rotina não rodou hoje". |
| **Preço errado do provedor.** Um preço absurdo derruba limites e dispara chamadas de margem em massa. | Não tratado. Na vida real: limites de variação diária, segunda fonte de preço e trava manual antes de aplicar. Hoje o provedor é fake e determinístico. |
| **Parceiro fora do ar não recebe webhooks.** | Retentativa com backoff exponencial, dead letter depois de N tentativas, reenvio manual e o log de eventos em `GET /v1/eventos` para reconciliar. |
| **Erro humano na política.** Um LTV digitado errado. | A política é validada (LTV entre 0 e 1, prazos positivos, ordem de execução completa). Não há aprovação em duas etapas nem histórico de versões da política. |
| **Fechamento de fatura e cartão são de outro sistema.** | A API expõe fechamento e pagamento, mas num produto real quem manda nisso é a processadora. A integração não existe. |

## Custodiante externo (fase 2)

A fase 2 deste projeto é simulada: as respostas abaixo são o que o código faz com o
custodiante simulado. Com um custodiante real, a mesma lógica vale, mas os prazos,
os motivos de recusa e o canal de aviso viriam do trilho, e não sei como cada trilho
os define (ver [arquitetura](arquitetura.md#onde-entraria-o-trilho-real)).

| Risco | Resposta |
|---|---|
| **Demora.** O custodiante leva horas para confirmar o gravame. | A garantia espera em `gravame_pendente`, sem gerar limite. O parceiro recebe `gravame.solicitado` e depois a resposta. O worker consulta o pedido periodicamente como reserva do callback. Cada custodiante tem o seu prazo de resposta. |
| **Recusa.** Ativo já onerado, titularidade divergente, bloqueio judicial. | `gravame_recusado`, com o motivo, nenhum lançamento e webhook `gravame.recusado`. Limite nunca existiu, então não há nada a desfazer. |
| **Silêncio.** O custodiante não responde nunca. | No prazo, a rotina expira o pedido (`gravame_expirado`, webhook `gravame.expirado`). Na liberação e na execução, a garantia volta a `bloqueada`; a execução é pedida de novo no dia seguinte. |
| **Resposta tardia.** Confirmação que chega depois da expiração. | Fica registrada como `obsoleta` e não muda nada. **Lacuna:** não há rotina de reconciliação para pedir ao custodiante que desfaça um gravame confirmado tarde. |
| **Indisponibilidade.** O custodiante sai do ar. | O pedido que ainda não saiu fica `solicitada` e o worker tenta de novo; cada garantia é tratada numa transação própria, então um custodiante fora do ar não trava os outros. O painel `/risco` mede o efeito no cenário "custodiante fora do ar": o que está com ele não recupera nada no horizonte. |
| **Venda que rende menos que o pedido.** | A dívida só cai pelo valor efetivamente recebido. A diferença continua devida e a rotina pede a venda de novo no dia seguinte. |
| **Fraude no callback.** Alguém finge ser o custodiante e "confirma" um gravame. | O callback só é aceito com assinatura HMAC-SHA256 do corpo exato, com o segredo daquele custodiante e timestamp dentro de 5 minutos. Comparação em tempo constante. Qualquer falha responde o mesmo `401`, sem dizer o quê; custodiante inexistente também, e com o mesmo custo de cálculo. A existência do pedido só é revelada depois de autenticar. |
| **Reapresentação do callback.** Alguém captura um callback válido e reenvia. | Fora da janela, `401`. Dentro dela, o mesmo `evento_id` já está registrado e a resposta é `duplicada`, sem efeito. |
| **Callback e consulta ao mesmo tempo.** A mesma confirmação aplicada duas vezes. | Trava de linha na conta e chave única `(custodiante, evento)` no banco. Testado com dez callbacks simultâneos contra Postgres. |
| **Segredo de callback fraco ou de exemplo em produção.** | A aplicação se recusa a subir. E o simulador de custodiantes fica desligado em produção. |
| **Simulador confundido com integração real.** | O nome de cada custodiante termina em "(simulador)", a API diz `simulado: true`, e a documentação e as telas avisam. Os perfis são fictícios. |

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
