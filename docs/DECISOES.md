# Decisões

Este projeto foi construído numa sessão autônoma: quando aparecia uma escolha em aberto,
ela era feita pensando num produto financeiro de produção e registrada aqui, com o
contexto, as opções, a decisão e o motivo. Quem discordar de alguma sabe onde mexer.

As decisões estão agrupadas por assunto. No fim há duas listas que valem a leitura
mesmo para quem pular o resto: [o que os testes pegaram](#o-que-os-testes-pegaram) e
[o que ficou para você decidir](#o-que-ficou-para-voce-decidir).

## Processo

### 1. O repositório só tinha o README
- **Contexto.** O briefing descrevia uma versão 0.1 (FastAPI em memória, 6 testes,
  `demo.py`, Dockerfile), mas no branch só existia o `README.md`.
- **Decisão.** Reconstruí a 0.1 a partir da descrição como primeiro commit, com os 6
  testes, e só então evoluí. Os 6 testes continuam existindo, adaptados à nova
  interface, em `tests/integracao/test_historia_v01.py`.
- **Motivo.** Era o que o briefing pedia para esse caso, e deixa o histórico do git
  contar a evolução de verdade.

### 2. Plugins e skills
- **Contexto.** O `.claude/settings.json` com os plugins estava em outro branch. Trouxe
  o arquivo, adicionei os três marketplaces e instalei os 20 plugins com
  `--scope project`. Todos aparecem como ativos em `claude plugin list`.
- **Limitação.** Plugin instalado no meio de uma sessão não vira skill invocável nela.
  Durante quase toda a construção, as skills dos plugins (superpowers,
  fullstack-dev-skills, Trail of Bits, frontend-design) não puderam ser chamadas.
- **Decisão.** Não fingir. Li os `SKILL.md` direto do disco e segui o método de cada um
  onde fazia sentido: `writing-plans` para o [plano](plano.md), `property-based-testing`
  para os testes com Hypothesis, `dimensional-analysis` para anotar unidades nas contas
  com dinheiro, `modern-python` para o projeto, `frontend-design` para a página `/demo`.
- **Na revisão final** a sessão foi reiniciada e as skills passaram a estar
  disponíveis. O que foi usado nessa etapa, e o que cada uma encontrou, está em
  [Revisão final](#revisao-final).
- **O que não foi feito.** Não usei o `context7`: conferi as APIs rodando o código
  contra as versões instaladas, o que pegou duas mudanças reais (ver itens 3 e 4 de
  [o que os testes pegaram](#o-que-os-testes-pegaram)). Também não rodei o fluxo
  completo de agentes da `dimensional-analysis`.

### 3. TDD, com honestidade
- **Decisão.** No domínio, os testes foram escritos antes ou junto do código. Como
  teste que nasce passando prova pouco, conferi a suíte quebrando o código de propósito:
  nove mutações (trocar `>=` por `>` na execução, vender a garantia inteira, liberar sem
  checar dívida, arredondar para cima, tirar a reavaliação de margem, entre outras).
  Todas foram pegas.
- **Na API e na infraestrutura** o ciclo foi mais curto: código e teste no mesmo passo.
  Foi aí que os testes encontraram os bugs mais interessantes.

### 4. mypy e pre-commit, e não ty e prek
- **Contexto.** A skill `modern-python` recomenda `ty` e `prek`. O briefing pede mypy
  estrito no domínio e pre-commit.
- **Decisão.** Vale o briefing. mypy roda no projeto inteiro e em modo `strict` em
  `app/dominio`. O pre-commit usa Ruff e mypy pelo `uv run`, na versão travada no
  `uv.lock`: o que passa localmente é o que passa no CI.

## Arquitetura

### 5. Pacote `app/` em quatro camadas
- **Opções.** Layout `src/` com outro nome de pacote, ou manter `app/`.
- **Decisão.** Manter `app/` e dividir em `dominio`, `aplicacao`, `infra` e `api`.
- **Motivo.** Continuidade com a 0.1 (`uvicorn app.main:app` continua valendo) e
  Dockerfile simples. A regra que importa é a de dependência: o domínio só importa a
  biblioteca padrão.

### 6. SQLAlchemy síncrono
- **Opções.** `asyncio` com asyncpg, ou sessões síncronas com psycopg 3.
- **Decisão.** Síncrono. Os endpoints são funções comuns, que o FastAPI roda em threads.
- **Motivo.** A dificuldade deste sistema é transação e trava, não número de conexões
  abertas. Código síncrono deixa explícito onde a transação começa e termina, e o teste
  de concorrência exercita travas reais do Postgres com threads. O custo: cada
  requisição ocupa uma thread enquanto fala com o banco.

### 7. Dinheiro é um tipo, guardado em centavos inteiros
- **Opções.** `Decimal` puro, ou um tipo próprio.
- **Decisão.** `Dinheiro`, uma dataclass imutável com centavos inteiros. No banco,
  `BIGINT`. Na API, texto decimal (`"4250.00"`).
- **Motivo.** Com um tipo próprio, somar dinheiro com LTV não compila, e multiplicar
  por uma razão obriga a escolher o arredondamento (`aplicar_razao`, para baixo por
  padrão). `BIGINT` evita o `NUMERIC` do SQLite, que por baixo é ponto flutuante. Texto
  na API evita `float` em qualquer cliente.
- **Arredondamento.** Limite e valor de garantia sempre para baixo. A estimativa de
  perda do simulador de stress arredonda para cima: nos dois casos, o lado conservador.

### 8. Identificadores com prefixo, ordenáveis
- **Decisão.** `gar_`, `cta_`, `cmp_` e assim por diante, seguidos de um ULID
  monotônico. Ordenar por id é ordenar por criação, e é isso que sustenta a paginação
  por cursor.

## Ledger e modelo de dados

### 9. Saldo nunca é coluna
- **Decisão.** Dívida e valor de garantia são sempre somados das partidas. Não existe
  campo de saldo, e um teste falha se alguém criar uma coluna com cara de saldo.
- **Custo.** Somar a cada operação fica caro quando a conta tiver milhares de
  lançamentos. A saída conhecida (saldo consolidado por período, conferido contra o
  ledger) está nos próximos passos.

### 10. Plano de contas
- **Decisão.** Seis contas por cliente. A garantia fica em duas contas de compensação
  (`garantias_custodiadas` e `garantias_a_devolver`), separadas do crédito.
- **Motivo.** A garantia é do cliente: não é ativo da plataforma. Registrá-la em
  compensação permite derivar o valor do ledger sem inflar o balanço.
- **O que não está modelado.** A liquidação com a rede de adquirência
  (`liquidacao_a_pagar` só cresce) e a cessão dos recebíveis ao FIDC.

### 11. O valor de mercado mora no ledger
- **Opções.** Guardar quantidade e multiplicar por preço, ou lançar no ledger cada
  ajuste de marcação.
- **Decisão.** Lançar os ajustes. A rotina aplica a variação de preço desde a última
  marcação sobre o valor atual e lança a diferença.
- **Motivo.** Uma fonte de verdade só. De brinde, o ledger vira o histórico de
  marcação.
- **Custo.** O arredondamento para baixo de cada marcação se acumula: em muitos dias
  seguidos a garantia vale alguns centavos a menos do que valeria. É a favor da
  segurança, mas um produto real marcaria por quantidade e preço.

### 12. O banco também defende o ledger
- **Decisão.** No Postgres, a migração cria um gatilho que impede `UPDATE` e `DELETE`
  em `lancamentos` e `partidas`, e outro, adiado para o commit, que recusa lançamento
  com débitos diferentes de créditos.
- **Motivo.** O domínio já impede. Mas quem escreve SQL na mão, um script de correção
  ou um bug futuro não passam pelo domínio.

## Regras de crédito

### 13. Estados da garantia
- **Decisão.** Os cinco do briefing. `pendente` é real, não decorativo: quando a
  custódia não confirma na hora, a garantia espera sem gerar limite e a API responde
  `202`. A confirmação só entra pela API administrativa, porque o parceiro não pode
  atestar a própria garantia.
- **Mudança em relação à 0.1.** Lá a garantia era vendida inteira e a sobra virava uma
  garantia nova. Agora vende-se só o necessário e a mesma garantia fica
  `parcialmente_executada`.
- **Lacuna.** Não existe estado de recusa: uma garantia que a custódia nunca confirmar
  fica `pendente` para sempre.

### 14. Uma fatura em aberto por conta
- **Decisão.** A dívida tem duas partes: a faturar e faturada. Fechar a fatura move de
  uma para a outra. Só pode haver uma fatura em aberto; o pagamento abate primeiro a
  fatura e antecipa o resto.
- **Motivo.** É o mínimo que dá sentido a "atraso" sem construir um motor de faturas.
  Com vencimento em 10 dias e execução no 7º dia de atraso, a fatura anterior está
  resolvida antes de o ciclo seguinte fechar.

### 15. A execução cobre só a fatura vencida
- **Opções.** Vencimento antecipado da dívida inteira, ou só o que está em atraso.
- **Decisão.** Só a fatura vencida, vendendo o mínimo, do ativo mais líquido para o
  menos (a ordem vem da política).
- **Motivo.** É proporcional e é o que o mercado descreve. A lei manda devolver o saldo
  ao devedor: não vender o que não precisa é a forma mais simples de cumprir.
- **A validar.** Se o contrato previr vencimento antecipado, a regra muda. É decisão
  jurídica e de risco.

### 16. Chamada de margem bloqueia, não liquida
- **Decisão.** Dívida acima do limite garantido abre chamada com prazo (5 dias corridos
  na referência). Sem reforço nem pagamento, a conta é bloqueada para novas compras.
  Quando o déficit zera, ela destrava sozinha.
- **Motivo.** Era o que o briefing pedia. Vender a garantia por uma dívida que ainda
  não venceu é outra discussão jurídica.
- **Efeito que vale saber.** Com prazo de margem menor que o de execução, uma conta que
  cai em margem e depois atrasa é bloqueada antes de ser executada. É o que aparece no
  `demo.py`: bloqueio no 6º dia, execução no 7º.

### 17. Compra negada responde 402 e fica registrada
- **Decisão.** `402 Payment Required` com `application/problem+json`, o motivo em
  `codigo`, a compra gravada como `negada` e o evento `compra.negada` emitido.
- **Motivo.** Mantém o comportamento da 0.1. Negar é resultado de negócio: precisa de
  rastro e precisa ser idempotente.

### 18. Teto do parceiro é sobre a carteira
- **Contexto.** "Teto de limite por parceiro" pode ser a soma dos limites concedidos ou
  a soma do que está de fato emprestado.
- **Decisão.** A soma dos saldos devedores de todas as contas do parceiro. É conferido
  a cada compra, com a linha do parceiro travada.
- **Motivo.** É o número que interessa a quem financia a carteira, e sai direto do
  ledger. Limite concedido oscila com o preço dos ativos.
- **Custo.** Com teto configurado, as compras do mesmo parceiro são autorizadas uma por
  vez. Num piloto, correção vale mais que vazão. Com volume, a alternativa é reservar
  exposição num contador atômico reconciliado contra o ledger.

### 19. Política de referência em arquivo, tetos ilustrativos
- **Decisão.** Os LTVs do briefing ficam em `app/politica_referencia.toml`. Cada
  parceiro tem a sua cópia, alterável pela API administrativa. O domínio só valida.
- **Atenção.** Os tetos do arquivo (R$ 50 mil por cliente, R$ 5 milhões por parceiro)
  são valores que eu escolhi para um piloto hipotético. Não são dados de mercado.

### 20. Datas de negócio e dias corridos
- **Decisão.** Atraso e prazo contam pela data de Brasília, em dias corridos, sem
  calendário de feriados.

## API

### 21. Idempotência
- **Decisão.** Obrigatória em bloqueio de garantia, compra e pagamento. O registro da
  chave entra na mesma transação da operação. Mesma chave com outro payload: `422`.
  Validade de 24 horas, configurável.
- **Motivo.** Na mesma transação não existe "executou e não registrou". O `422` segue o
  rascunho do IETF para o cabeçalho `Idempotency-Key`.

### 22. Erros RFC 7807 com `type` que resolve
- **Decisão.** `type` é um caminho relativo (`/v1/erros/limite_insuficiente`) e esse
  endereço existe: `GET /v1/erros` serve o catálogo.
- **Motivo.** Um domínio inventado em `type` apontaria para um site que não é meu.

### 23. API key com SHA-256
- **Decisão.** Formato `lgk_<identificador>_<segredo>`. No banco, o prefixo e o SHA-256
  da chave. Comparação em tempo constante.
- **Motivo.** O segredo tem 256 bits aleatórios: não existe dicionário a atacar, então
  bcrypt ou Argon2 só encareceriam cada requisição.
- **Lacunas.** Sem expiração, sem escopos, sem restrição por IP.

### 24. 404, não 403, para recurso de outro parceiro
- **Motivo.** Responder 403 confirmaria que o recurso existe.

### 25. Rate limit em memória
- **Decisão.** Janela fixa de um minuto por parceiro, por instância. O briefing pedia
  simples; com mais de uma instância o contador precisa ser compartilhado. O limite
  vale depois da autenticação: tentativas com chave errada não são contadas, e isso
  deve ser barrado na borda.

### 26. API administrativa com token único
- **Decisão.** Um token em variável de ambiente. Sem ele, `/v1/admin` responde 404.
- **Lacuna.** Sem usuários, papéis nem trilha de quem alterou a política.

### 27. Sandbox que mexe nas datas
- **Decisão.** `POST /v1/sandbox/contas/{id}/avanco-de-dias` puxa o vencimento da
  fatura e o prazo da chamada de margem para trás e roda a rotina diária da conta.
- **Efeito colateral.** As datas absolutas daquela conta ficam no passado. O `demo.py`
  não usa esse atalho: ele avança o relógio e roda a rotina de verdade.

## Webhooks

### 28. Outbox, pelo menos uma vez
- **Decisão.** Evento e entrega gravados na transação da operação. O worker reserva com
  `FOR UPDATE SKIP LOCKED`, envia fora de transação e grava o resultado. O reenvio
  manual cria uma entrega nova e preserva o histórico da antiga.

### 29. Assinatura
- **Decisão.** `X-LG-Assinatura: t=<unix>,v1=<hmac>`, com HMAC-SHA256 sobre
  `"<t>.<corpo>"`. O receptor recusa `t` fora de 5 minutos.
- **Motivo.** Com o timestamp dentro do que é assinado, não dá para "renovar" uma
  entrega capturada.

### 30. SSRF
- **Decisão.** A URL é validada no cadastro e a cada envio: só https, sem credencial na
  URL, sem endereço privado, sem seguir redirecionamento.
- **Resta.** A janela entre resolver o DNS e conectar. Fechar exige fixar o IP
  resolvido na conexão ou sair por um proxy de saída.

### 31. Segredo de webhook em texto no banco
- **Motivo.** Para assinar é preciso ler o segredo. Em produção ele deveria ser cifrado
  com chave fora do banco. Não implementado.

## Risco

### 32. Provedor de preços fake por hash
- **Decisão.** O preço de um ativo num dia sai de um SHA-256, não de gerador aleatório:
  é o mesmo em qualquer máquina. Fica atrás da interface `ProvedorDePrecos`.

### 33. O que o simulador de stress mede
- **Decisão.** Três números: contas que entrariam em chamada de margem, déficit de
  margem (dívida acima do limite estressado) e exposição descoberta (dívida acima do
  valor de mercado estressado). A perda esperada é a exposição descoberta vezes uma
  probabilidade de inadimplência informada, que por padrão é 1.
- **Motivo.** Inventar uma probabilidade seria inventar número.

## Demonstração

### 34. Página `/demo`
- **Decisão.** HTML, CSS e JavaScript sem framework. A fonte (Bricolage Grotesque,
  licença OFL) vai junto no repositório. A página tem uma política de segurança de
  conteúdo que só aceita recursos do próprio servidor.
- **Atenção.** `/demo/config` entrega a API key do parceiro de exemplo ao navegador. É
  o que permite a página funcionar sem login, e é por isso que a aplicação se recusa a
  subir com a demo ligada em produção.

### 35. Testes de navegador em Python
- **Decisão.** O teste e2e usa Playwright para Python e sobe a API e o worker por conta
  própria. Fica fora do `pytest` comum; roda com `pytest -m e2e`.

## Entrega

### 36. Docker foi validado aqui, com duas ressalvas
- **O que funcionou.** O `dockerd` subiu neste ambiente. A imagem construiu a partir do
  `uv.lock`, o `docker compose up` trouxe Postgres, migrações, seed, API e worker, o
  teste de fumaça passou e o Playwright percorreu o `/demo` contra os contêineres. O
  processo roda como usuário comum, sem capabilities.
- **Ressalvas.** O Docker Hub recusou os downloads deste IP por limite de requisições,
  então as imagens-base vieram de espelhos públicos. E o build precisou passar pelo
  proxy deste ambiente, com uma variante local do Dockerfile que só acrescenta o
  certificado do proxy.
- **No GitHub.** O job `Docker Compose` do CI passou: ele constrói o `Dockerfile` do
  repositório exatamente como está, baixando do Docker Hub, sobe tudo e roda o teste de
  fumaça até o webhook ser entregue.
- **O que falta.** Só a sua máquina. **Rode `docker compose up --build` uma vez** antes
  de gravar o vídeo.

### 37. Imagens por tag, actions por SHA
- **Decisão.** As GitHub Actions estão fixadas pelo hash do commit, conferido no
  repositório de cada uma. As imagens Docker estão por tag, porque o Docker Hub não
  respondeu às consultas de digest daqui.

### 38. O que eu não confirmei em `docs/mercado.md`
- **Decisão.** Cada afirmação diz se foi confirmada em fonte oficial, em fonte
  secundária ou se ficou a confirmar.
- **Ajustes em relação ao briefing.**
  - Nubank: o teto de R$ 150 mil é do limite total do cartão, não só do Tesouro.
  - Nubank: a garantia pode ser usada "em até sete dias após o vencimento" se nem o
    mínimo for pago.
  - Itaú: o produto usa "Cofrinhos".
  - C6: mínimo de R$ 100 e proporção 1 para 1 batem; rendimento e teto divergem.
  - XP, credor do TD Garantia, processo manual nas seguradoras e registro da B3 por
    API: não consegui confirmar.
  - Drex: as fontes falam em plataforma descontinuada em novembro de 2025 e foco em
    reconciliação de gravames com entrega prevista para 2026. Não achei base para 2027.
  - O site do Planalto bloqueou o acesso daqui; três dispositivos de lei foram lidos em
    repositório não oficial e estão resumidos, não transcritos.

## Fase 2: garantia em custodiante externo (simulado) e painel de risco

<a id="39-fase-2-pedido-e-ponto-de-partida"></a>
### 39. Fase 2: pedido e ponto de partida
- **Contexto.** Depois da v1.0 (PR #1) e do ajuste de plugins (PR #2), o pedido foi:
  garantia em custodiante externo, assíncrona e que pode falhar, com callback assinado,
  polling de reserva e prazo; um painel de risco sobre carteira sintética em `/risco`;
  três ajustes pequenos no `/demo` e na emissão de `fatura.em_atraso`. Tudo simulado,
  sem integração real. O plano está em [plano-fase2.md](plano-fase2.md).
- **Branch.** O branch de trabalho foi reiniciado a partir do `main` atualizado, sem
  reaproveitar o histórico já mergeado.
- **Suíte de partida.** Ruff, mypy e pytest verdes (265 testes em SQLite, 277 em
  Postgres), cobertura do domínio em 99%. O teste de navegador falhou na primeira
  execução por ambiente, não por código: o Playwright 1.65 do projeto procura uma
  revisão do Chromium que esta máquina não tem. Com `CHROMIUM_EXECUTAVEL` apontando
  para o Chromium instalado, passou.

### 40. Como a construção foi conduzida
- **Contexto.** O pedido previa as skills de brainstorming e de plano antes de codar e
  depois desenvolvimento por subagentes com TDD.
- **O que foi feito.** O brainstorming termina quando o dono do projeto dispensa as
  perguntas, que foi o caso: as decisões de desenho foram tomadas por mim e estão
  aqui e no plano. O plano seguiu a skill de plano. A implementação foi feita nesta
  sessão, tarefa a tarefa, com teste antes do código; os subagentes ficaram para as
  revisões independentes no fim.
- **Motivo.** As tarefas do núcleo dependem muito umas das outras (estado da garantia,
  ledger, porta do custodiante, repositório e rota mudam juntos), e a construção da
  v1.0 perdeu as revisões por agentes no limite de uso da conta. Gastar esse limite na
  revisão, que é onde um olhar de fora mais acrescenta, me pareceu a melhor troca.
- **Context7.** Não estava autenticado nesta sessão. Para documentação de bibliotecas,
  conferi rodando contra as versões instaladas, como na v1.0.

### 41. O que acontece com o `pendente`
- **Contexto.** A garantia interna nasce `pendente` e, com a custódia interna, vira
  `bloqueada` na mesma transação. Com `CUSTODIA_MODO=assincrona` ela ficava `pendente`
  até a confirmação administrativa, para sempre se ninguém confirmasse (item 13).
- **Opções.** (a) Migrar a interna para os estados novos. (b) Manter o `pendente` só
  para a interna e dar à externa a máquina completa. (c) Fazer o `pendente` virar
  sinônimo de `gravame_pendente`.
- **Decisão.** (b), com uma saída nova: `pendente → gravame_expirado`, aplicada pela
  rotina de custódia quando o prazo padrão passa. A fase 1 com custódia interna não
  muda, porque o `pendente` nunca chega ao banco.
- **Motivo.** Migrar estados persistidos de uma fase que funciona traz risco sem
  ganho, e mudaria o que os parceiros já leem. O modo `assincrona` era um ensaio da
  fase 2; agora que a fase 2 existe, ele fica por compatibilidade, mas também não
  deixa garantia pendurada.

### 42. O que cada estado externo conta para o limite
- **Contexto.** A regra pedida: o limite só nasce em `bloqueada`; na liberação ele sai
  no pedido; em `execucao_solicitada` não há limite novo.
- **Opções para a garantia em execução.** (a) Tirá-la de tudo, como a que está sendo
  liberada. (b) Tirá-la do limite, mas deixá-la cobrindo a dívida até a venda.
- **Decisão.** (b). Ela não entra em `limite_total` nem em `limite_disponivel`, mas
  entra em `valor_das_garantias` e no cálculo do `deficit`.
- **Motivo.** O ativo continua penhorado e está sendo vendido justamente para pagar a
  dívida. Com (a), pedir a execução abriria uma chamada de margem contra o próprio
  cliente que está sendo executado, e o parceiro receberia um aviso sem sentido.

### 43. Execução externa: o que se pede e o que se abate
- **Decisão.** No pedido, nada vai para o ledger: só o estado e o valor mandado vender
  (`valor_em_execucao`), que sai do devido para a rotina não pedir de novo. Na
  confirmação, um lançamento consome da garantia o valor pedido e abate da dívida o
  valor efetivamente recebido, limitado à dívida da conta (fatura primeiro, depois o
  que ainda não foi faturado).
- **Se a venda render menos**, a diferença continua devida e a rotina do dia seguinte
  pede só ela. **Se render mais**, o excedente não passa pela plataforma: fica com o
  cliente no próprio custodiante e aparece no evento `execucao.confirmada`
  (`valor_devolvido_ao_cliente`).
- **Motivo.** O ledger registra dinheiro que passou pela plataforma. Abater no pedido
  seria registrar um recebimento que ainda não aconteceu.

### 44. O pedido sai depois do commit, pelo worker
- **Contexto.** Pedir o gravame dentro da transação da API parece mais rápido.
- **Decisão.** A API grava a garantia `solicitada` e responde `202`. Quem envia o
  pedido é a rotina de custódia, no worker, alguns segundos depois.
- **Motivo.** Se o pedido saísse antes do commit e o commit falhasse, o custodiante
  teria um gravame que a plataforma não conhece. Do jeito escolhido, o pior caso é o
  pedido sair atrasado, e ele é idempotente pelo `solicitacao_id`. É a mesma ideia da
  outbox dos webhooks.

### 45. Histórico de pedidos e resposta obsoleta
- **Decisão.** Cada pedido fica gravado em `solicitacoes_de_custodia`, mesmo depois de
  encerrado. Uma resposta que chega para um pedido que já não está em aberto (por
  exemplo, uma confirmação depois de a garantia expirar) é reconhecida, registrada como
  `obsoleta` e não muda nada.
- **Motivo.** Sem o histórico ela cairia no vazio como "pedido inexistente". Com ele,
  fica o rastro para a reconciliação. Essa reconciliação (desfazer o gravame que o
  custodiante confirmou tarde) não está implementada e está em "Próximos passos".

### 46. Segurança do callback
- **Decisão.**
  - Autenticação só pela assinatura HMAC-SHA256 do corpo exato, com timestamp e janela
    de 5 minutos, no mesmo formato dos webhooks de saída.
  - Comparação em tempo constante; para custodiante desconhecido o HMAC é calculado
    mesmo assim, com um segredo descartável, para o tempo de resposta não dizer se o
    id existe.
  - Qualquer falha de autenticação devolve o mesmo `401 assinatura_invalida`, sem
    dizer qual foi.
  - Só depois de autenticado se diz se o pedido existe (`404`), e "não existe" e "é de
    outro custodiante" respondem igual.
  - Uma resposta reapresentada dentro da janela é barrada pela idempotência: mesmo
    `evento_id`, nenhum efeito.

### 47. Simulador determinístico e sem estado
- **Decisão.** O destino de cada pedido (confirma, recusa ou nunca responde) sai de um
  SHA-256 da semente do perfil com o id do pedido. A resposta fica pronta quando o
  relógio injetado passa da latência. O `evento_id` também é derivado do pedido e do
  desfecho, então o callback e a consulta da mesma resposta têm o mesmo id.
- **Consequência honesta.** Por não guardar estado, o simulador responde a qualquer id
  bem formado, até de pedido que nunca recebeu. Um custodiante real guardaria os
  pedidos. Para o que o simulador precisa mostrar, isso não muda nada.
- **Sandbox e confirmação administrativa** fabricam a resposta pelo próprio simulador,
  com o mesmo `evento_id` que ele daria. Se a resposta "de verdade" chegar depois, é a
  mesma e não é aplicada de novo.

### 48. Simulador fora de produção
- **Decisão.** Sem `CUSTODIA_EXTERNA` explícito, o simulador fica ligado fora de
  produção e desligado em produção. Pedir `simulador` em produção impede a aplicação
  de subir, assim como um segredo de callback curto ou de exemplo.
- **Motivo.** Um simulador que confirma gravames não pode, por engano, atender
  parceiros de verdade.

### 49. Liberação ou execução que falham voltam a `bloqueada`
- **Decisão.** Recusa ou falta de resposta num pedido de liberação ou de execução
  devolve a garantia para `bloqueada`, com o evento `liberacao.falhou` ou
  `execucao.falhou`. Na execução, a rotina do dia seguinte pede de novo.
- **Simplificação.** Uma garantia que estava `parcialmente_executada` também volta a
  `bloqueada`. As duas geram limite do mesmo jeito; perde-se só o rótulo, e o histórico
  continua nos eventos.

<a id="revisao-final"></a>
## Revisão final

O briefing pedia uma revisão com as skills de revisão e de segurança. Parte dela foi
feita, parte foi interrompida. Fica registrado o que é cada coisa.

**O que rodou até o fim**

- **Revisão própria**, com olhar de crédito e de API. Dela saíram o teto de 64 kB para
  o corpo da requisição, os cabeçalhos `Cache-Control: no-store` e
  `X-Content-Type-Options: nosniff`, a remoção dos parâmetros de SQL dos logs de erro e
  a lista de "Próximos passos" do README.
- **`pip-audit`**: nenhuma vulnerabilidade conhecida nas dependências.
- **Semgrep** (regras `python`, `security-audit`, `secrets`, `dockerfile` e
  `github-actions`): dois achados, os dois em `scripts/fumaca.py`, sobre `urllib` com
  URL dinâmica. A URL ali vem de quem roda o script de fumaça, na linha de comando.
  Aceito como está.
- **Regras de segurança do Ruff** em todo o código, sem pendências.
- **CI do GitHub** verde nos quatro jobs: lint e tipos, testes em SQLite e Postgres,
  navegador com Playwright e Docker Compose.

**O que foi disparado e não terminou**

- As skills `security-review` e `pr-review-toolkit:review-pr` (revisão geral de código
  e caça a falhas silenciosas) e o analisador `sharp-edges` chegaram a ser lançados,
  mas os agentes foram interrompidos pelo limite de uso da conta antes de entregar
  relatório. **Nenhum achado deles entrou no projeto.**
- Não rodei `claude-security`, `insecure-defaults:audit`, `supply-chain-risk-auditor`
  nem `code-review:code-review`.

A decisão de parar aqui e entregar foi do dono do projeto, com a suíte e o CI verdes.
Para completar depois, numa sessão nova: `/pr-review-toolkit:review-pr`,
`/security-review` e `/insecure-defaults:audit`.

## O que os testes pegaram

1. **`Decimal` virando `float`.** Na reconstrução da 0.1, o `jsonable_encoder` do
   FastAPI devolvia `4250.0`. O primeiro teste escrito pegou.
2. **Deadlock na compra com teto de parceiro.** A chave de idempotência tem chave
   estrangeira para o parceiro, o que já trava a linha em modo compartilhado; pedir
   `FOR UPDATE` por cima fazia duas compras se travarem. A correção foi
   `FOR NO KEY UPDATE`.
3. **Pool de conexões esgotado.** A autenticação pegava a conexão numa thread e a
   segurava até o endpoint rodar em outra. Com mais requisições que conexões, travava
   por 30 segundos. Agora a autenticação devolve a conexão.
4. **API depreciada no SQLAlchemy 2.1.** `Result.tuples()`: a suíte trata aviso de
   depreciação no código do projeto como erro.
5. **Erro 500 sem `request_id`.** O tratador genérico do framework roda fora do
   middleware. Erro inesperado passou a ser tratado dentro dele.
6. **Log com `extra={"parceiro_id": ...}` quebrava.** Colidia com o carimbo de contexto
   do registro. Apareceu ao rodar o seed de verdade.
7. **Webhooks fora de ordem.** Duas entregas da mesma transação saíam em ordem
   aleatória. O worker passou a ordenar pela sequência do evento.
8. **`hidden` que não escondia.** Na página, um `display: grid` vencia o atributo
   `hidden`. Apareceu no screenshot.
9. **Corpo cortado é pior que corpo recusado.** A primeira versão do teto de tamanho
   cortava o corpo: um JSON válido seguido de lixo seria executado e o cliente
   receberia 413. Agora a leitura falha inteira e o endpoint não roda.

<a id="o-que-ficou-para-voce-decidir"></a>
## O que ficou para você decidir

- **Licença.** O repositório não tem arquivo de licença. Sem ele, vale "todos os
  direitos reservados". Para portfólio, MIT ou Apache-2.0 são as escolhas comuns, mas a
  decisão é sua.
- **Validar o compose na sua máquina** (item 36).
- **Conferir os pontos "a confirmar"** de `docs/mercado.md` antes de citá-los.
- **Parecer jurídico.** Tudo o que este projeto diz sobre alienação fiduciária,
  execução e superendividamento é leitura de leigo.
