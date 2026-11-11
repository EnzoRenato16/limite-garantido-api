# Referências de mercado

Limite garantido por investimento já é um produto provado no varejo brasileiro. O que
este projeto discute é outra coisa: todo mundo que faz, faz com o ativo da própria casa.
Não achei ninguém oferecendo isso como serviço para quem não é banco.

Este documento reúne os casos e a base legal que sustentam essa leitura, com a fonte de
cada afirmação.

> Projeto pessoal de estudo, feito só com informação pública. As empresas aparecem aqui
> como referência de mercado; não tenho vínculo com nenhuma delas, e nada no código usa
> nome ou marca de terceiros.

## Como ler

Conferi as fontes em 11/11/2026. Cada linha diz até onde consegui chegar:

- **Confirmado**: li a informação na página oficial da própria empresa ou do órgão.
- **Fonte secundária**: li em imprensa, em blog especializado ou num repositório de
  legislação que não é o oficial.
- **A confirmar**: é o que eu tinha anotado, mas não consegui abrir a fonte ou as fontes
  divergem. Não use sem checar.

Condições comerciais (rendimento, teto, valor mínimo) mudam com frequência. O que
importa para a tese é o desenho do produto, não o número do mês.

## Quem já faz, e com o quê

| Quem | Quando | O que vira limite | Situação |
|---|---|---|---|
| Inter | nov/2020 | CDB do próprio banco | Confirmado |
| Nubank | abr/2023 | Caixinha do próprio banco; desde out/2024, Tesouro Direto custodiado nele | Confirmado |
| PicPay | jul/2023 | Saldo em conta ou Cofrinho do próprio app | Fonte secundária |
| C6 | — | CDB do próprio banco | Fonte secundária |
| Itaú | jan/2026 | Cofrinho do próprio banco | Fonte secundária |
| XP | — | Investimentos custodiados na própria XP | A confirmar |

### Inter: CDB Mais Limite de Crédito

- Lançado em novembro de 2020. **Confirmado.**
- Em 21/05/2021, cerca de seis meses depois, o banco publicou que a modalidade tinha
  passado de R$ 1 bilhão investido, com 388 mil clientes e cerca de R$ 205 milhões em
  uso como limite. **Confirmado.**

Fonte: [blog do Inter, 21/05/2021](https://blog.inter.co/cdb-mais-limite-de-credito-do-inter-supera-a-marca-de-1-bilhao/).

### Nubank: Nu Limite Garantido

- Anunciado em 24/04/2023: o dinheiro guardado numa Caixinha aumenta o limite do cartão,
  a partir de R$ 1. **Confirmado.**
- O limite aumenta no mesmo valor reservado como garantia (1 para 1). **Fonte
  secundária** (página do produto, lida pelo resumo da busca; o comunicado de lançamento
  não traz a proporção por escrito).
- Em 03/10/2024 passou a aceitar Tesouro Direto custodiado no próprio Nubank (Selic,
  IPCA+ e Prefixado). **Confirmado.**
- No mesmo comunicado: mais de 6,7 milhões de usuários de Caixinhas usavam o recurso, e
  o banco tinha mais de 18 milhões de clientes investidores ativos no fim do 2º
  trimestre de 2024. **Confirmado.**
- O teto de R$ 150 mil é do limite total do cartão, somando o pré-aprovado e o
  garantido. Não é um teto só da parte garantida. **Confirmado.**
- Se o cliente não pagar nem o mínimo da fatura, a garantia pode ser usada em até sete
  dias depois do vencimento. **Confirmado.**

Fontes: [comunicado de 24/04/2023](https://nu.com/pt/sala-de-imprensa/consumidores/nubank-lanca-recurso-para-aumentar-limite-do-cartao-de-credito-a-partir-de-investimentos-colateralizados),
[comunicado de 03/10/2024](https://nu.com/pt/sala-de-imprensa/consumidores/nubank-evolui-recurso-para-aumentar-limite-do-cartao-com-investimentos-em-tesouro-direto),
[blog do Nubank](https://blog.nubank.com.br/nu-limite-garantido/) e
[página do produto](https://nubank.com.br/nu/cartao/nu-limite-garantido).

### PicPay

- Em 25/07/2023 a imprensa noticiou o cartão em que o valor que rende na conta ou num
  Cofrinho vira limite para usar na hora. **Fonte secundária.**

Fonte: [Money Times, 25/07/2023](https://www.moneytimes.com.br/picpay-une-liberacao-de-credito-e-rendimento-acima-da-poupanca-em-um-so-novo-servico-confira/).

### C6: Limite Garantido (antigo CDB Cartão de Crédito)

- Investimento mínimo de R$ 100, e cada R$ 1 investido vira R$ 1 de limite. **Fonte
  secundária.**
- Rendimento e teto: **a confirmar.** Encontrei 100% e 102% do CDI, e tetos de
  R$ 100 mil e R$ 200 mil, conforme a página. Não consegui abrir a página oficial para
  desempatar.

Fontes: [blog do C6](https://c6bank.com.br/blog/o-que-e-limite-garantido) e
[Nord Investimentos](https://www.nordinvestimentos.com.br/blog/cdb-cartao-de-credito-c6-limite-garantido/).

### Itaú: Limite Garantido

- Noticiado em 14/01/2026: cada R$ 1 guardado num Cofrinho específico vira R$ 1 de
  limite adicional no cartão. **Fonte secundária.**

Fontes: [Olhar Digital, 14/01/2026](https://olhardigital.com.br/2026/01/14/pro/igual-ao-nubank-itau-libera-aumento-de-limite-no-cartao-sem-analise-de-credito-pelo-app/)
e [página de limites do Itaú](https://www.itau.com.br/cartoes/limites).

### XP: crédito colateralizado

- Empréstimo que usa como garantia os investimentos custodiados na própria XP, com
  exemplo de valor mínimo de R$ 50 mil. **A confirmar:** a informação aparece no resumo
  da busca sobre um material da XP, mas não consegui abrir o documento.

Fonte indicada pela busca: [material da XP (PDF)](https://portal-rs.xpi.com.br/arquivos/BXP/Credito_colateralizado_CF.pdf).

### O padrão

Em todos os casos o ativo que vira garantia está guardado na mesma instituição que
concede o crédito. Faz sentido: é ela que enxerga o ativo, consegue bloqueá-lo e
consegue executá-lo. Quem não é banco nem custodiante fica de fora.

A ideia deste projeto é a plataforma de Banking as a Service fazer esse papel uma vez,
para todos os parceiros: ela guarda o investimento, registra a garantia e libera o
limite, e o parceiro só chama a API.

## Trilhos regulados que já existem

São a fase 2 do [roadmap](roadmap.md).

### Tesouro Direto como garantia (TD Garantia)

- Anunciado em 25/11/2024 por B3, Secretaria do Tesouro Nacional e Banco Central:
  serviço que permite ao investidor pessoa física usar títulos do Tesouro Direto como
  garantia. **Confirmado.**
- O Selic passou a permitir o registro de gravames de títulos do Tesouro Direto.
  **Fonte secundária.**
- Quem pode receber a garantia: instituições financeiras participantes da B3 e do
  Selic. **A confirmar.** Só encontrei isso em blog de corretora; a página da B3 fala
  em garantia concedida "às instituições financeiras", sem detalhar a habilitação.

Fontes: [B3, 25/11/2024](https://www.b3.com.br/pt_br/noticias/td-garantia.htm) e
[Anbima](https://www.anbima.com.br/pt_br/noticias/selic-passa-a-permitir-registro-de-gravames-do-tesouro-direto.htm).

### Previdência como garantia

- Lei nº 14.652, de 23/08/2023: permite dar em garantia de operações de crédito o
  direito de resgate de planos de previdência complementar aberta, seguros de pessoas,
  Fapi e títulos de capitalização. **Confirmado.**
- Resolução Conjunta CNSP/CMN nº 12, de 26/09/2024 (publicada em 30/09/2024):
  regulamenta a lei. **Confirmado.**
- O processo nas seguradoras ainda é manual, e a B3 trabalha num registro de garantias
  por API. **A confirmar:** não encontrei fonte pública para essas duas afirmações.

Fontes: [texto da lei na Câmara dos Deputados](https://www2.camara.leg.br/legin/fed/lei/2023/lei-14652-23-agosto-2023-794577-publicacaooriginal-168929-pl.html)
e [notícia da Susep, 01/10/2024](https://www.gov.br/susep/pt-br/central-de-conteudos/noticias/2024/outubro/cnsp-e-cmn-publicam-resolucao-conjunta-sobre-concessao-de-direito-de-resgate-como-garantia-de-operacoes-de-credito).

## Mercado aberto: o que falta

É a fase 3 do roadmap, e a que menos depende da plataforma.

- A Lei nº 12.810/2013, art. 26 (com a redação da Lei nº 13.476/2017), determina que o
  gravame sobre ativo financeiro ou valor mobiliário registrado ou depositado seja
  constituído na registradora ou no depositário central onde o ativo está. **Fonte
  secundária.**
- Na prática isso espalha os gravames por vários sistemas. Usar como garantia um CDB,
  uma ação ou uma cota de fundo guardados em outra instituição exige que esses sistemas
  se falem.
- Drex: segundo a imprensa, o Banco Central descontinuou em novembro de 2025 a
  plataforma em blockchain usada no piloto e redirecionou o projeto para uma solução
  de reconciliação de gravames, com primeira entrega prevista para o segundo semestre
  de 2026. **Fonte secundária.**
- Quando isso fica disponível de forma ampla: **a confirmar.** Encontrei quem fale em
  2027 e quem diga que não há data oficial. Não achei comunicado do Banco Central.

Fontes: [Lei 13.476/2017 na Câmara dos Deputados](https://www2.camara.leg.br/legin/fed/lei/2017/lei-13476-28-agosto-2017-785408-normaatualizada-pl.html),
[HRSA, set/2025](https://hrsa.com.br/artigo/drex-2026-garantias-reconciliacao-de-gravames/),
[Gazeta do Povo](https://www.gazetadopovo.com.br/economia/banco-central-encerra-projeto-do-drex-o-real-digital/)
e [Serasa](https://www.serasa.com.br/blog/o-que-aconteceu-drex/).

## Base jurídica

**Eu não sou advogado.** O que está aqui é a minha leitura de leigo, e a modelagem
jurídica de um produto assim precisa ser validada por quem é. O site do Planalto
bloqueou o acesso a partir do ambiente em que conferi as fontes; por isso os três
dispositivos abaixo foram lidos em repositórios de legislação não oficiais e estão
resumidos, não transcritos. Os links apontam para o texto oficial.

| Dispositivo | O que diz, em resumo | Por que importa aqui | Situação |
|---|---|---|---|
| [Lei 4.728/1965, art. 66-B, § 3º](https://www.planalto.gov.br/ccivil_03/leis/l4728.htm) | Admite alienação fiduciária de coisa fungível e cessão fiduciária de direitos e títulos de crédito. Em caso de inadimplemento, o credor pode vender o bem a terceiros sem leilão nem medida judicial, usa o valor para quitar o crédito e devolve o saldo ao devedor, com demonstrativo. | É o que permite executar a garantia no 7º dia sem ir à Justiça, e o que obriga a devolver a sobra. No código, a execução vende só o necessário e o resto continua do cliente. | Fonte secundária |
| [CDC, art. 104-A, § 1º](https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm) (incluído pela Lei 14.181/2021) | Exclui do processo de repactuação por superendividamento, entre outras, as dívidas de contratos de crédito com garantia real. | Reduz a incerteza de recuperação do crédito. Não elimina o dever de conceder crédito de forma responsável. | Fonte secundária |
| [Lei 12.810/2013, art. 26](https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2013/lei/l12810.htm) | Gravame sobre ativo registrado ou depositado é constituído na registradora ou no depositário central do ativo. | Define onde a garantia precisa ser registrada para valer contra terceiros. É o nó da fase 3. | Fonte secundária |
| [Lei 14.652/2023](https://www2.camara.leg.br/legin/fed/lei/2023/lei-14652-23-agosto-2023-794577-publicacaooriginal-168929-pl.html) e Resolução Conjunta CNSP/CMN nº 12/2024 | Direito de resgate de previdência, seguro de pessoas e capitalização pode ser dado em garantia de crédito. | Abre a previdência como garantia na fase 2. | Confirmado |

Repositório não oficial usado na leitura dos três primeiros:
[LegJur, art. 66-B](https://www.legjur.com/legislacao/art/lei_00047281965-66-B) e
[LegJur, art. 104-A](https://www.legjur.com/legislacao/art/lei_00080781990-104-A).

Outros pontos que um jurídico teria de olhar e que este projeto não resolve:

- **Open Finance.** A premissa é que o consentimento do cliente é dado por parceiro,
  conforme as regras vigentes. Não modelei consentimento no código.
- **Quem é o credor.** A API é oferecida pela plataforma, mas alguém precisa ser a
  instituição que concede o crédito e figura como credor fiduciário. O desenho com FIDC
  comprando os recebíveis muda quem carrega o risco e precisa de estruturação própria.
- **Proteção de dados.** A API só conhece `cliente_ref`, um identificador do parceiro.
  CPF, nome e contato não entram.

## Política de risco de referência

Os LTVs que o projeto usa como ponto de partida são referência de mercado para este
tipo de produto. Na vida real quem define é o time de risco, por parceiro e por ativo.
No código eles ficam em [`app/politica_referencia.toml`](../app/politica_referencia.toml)
e podem ser trocados por parceiro pela API administrativa.

| Ativo | LTV de referência | Racional |
|---|---|---|
| Depósito em conta | 100% | Não oscila |
| Fundo DI, Tesouro Selic | 95% | Oscilação muito baixa |
| CDB | 85% | Marcação a mercado e liquidez dependem do papel |
| Tesouro Prefixado, Tesouro IPCA+ | 80% | Sofrem com a curva de juros |
| Ações | 60% | Volatilidade alta |

A execução a partir do 7º dia de atraso segue a prática que o mercado já divulga. Os
tetos de limite do arquivo são valores ilustrativos de um piloto, não dados de mercado.
