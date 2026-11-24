# Referências de mercado

Limite garantido por investimento já é um produto provado no varejo brasileiro. O que
este projeto discute é outra coisa: todo mundo que faz, faz com o ativo do próprio grupo.
Todos os players pesquisados aceitam só ativos custodiados no próprio grupo. "De fora"
só aparece em previdência (Res. Conjunta 12), no TD Garantia e numa empresa de crédito
que diz aceitar investimentos de qualquer banco ou corretora sem detalhar o mecanismo.
Não encontramos BaaS que ofereça publicamente garantia em investimentos por API.

Este documento reúne os casos e a base legal que sustentam essa leitura, com a fonte e a
categoria de cada afirmação.

> Projeto pessoal de estudo, feito só com informação pública. As empresas aparecem aqui
> como referência de mercado; não há vínculo com nenhuma delas, e nada no código usa
> nome ou marca de terceiros. Nenhum dado de cliente real foi usado.

## Como ler

Cada afirmação traz uma categoria:

- **norma**: vem de norma citada, com o artigo;
- **prática de mercado**: o que a empresa ou a infraestrutura divulga em página, termo ou
  contrato próprios;
- **interpretação**: leitura do projeto, a validar com o jurídico;
- **imprensa**: só noticiado;
- **não verificado**: a fonte lista como ainda sem confirmação oficial.

Quando dizemos que algo não existe, o sentido é "não encontramos norma ou fonte que...",
nunca que não existe. Condições comerciais (rendimento, teto, valor mínimo) mudam com
frequência; o que importa para a tese é o desenho do produto, não o número do mês. O
levantamento é de nov/2026 (revisado em 22/11/2026).

## Quem já faz, e com o quê

| Quem | O que vira limite ou garantia | Categoria |
|---|---|---|
| Nubank (Nu Limite Garantido) | Caixinhas (RDB emitido pela Nu Financeira, segundo o Nubank) e Tesouro Direto contratado pelo Nubank | prática de mercado |
| Inter (CDB Mais Limite) | CDB do Inter e Poupança Mais Limite, 1:1 | prática de mercado |
| Itaú (Cofrinho "Mais limite no Cartão") | Cofrinho ou Poupança, R$ 1 = R$ 1 de limite | prática de mercado |
| XP (cartão de crédito) | Ativos do grupo XP, inclusive PGBL e VGBL | prática de mercado |
| C6 (CDB C6 Limite Garantido) | CDB C6, 1:1 até R$ 200 mil | prática de mercado |
| PicPay (cartão com saldo em garantia) | Saldo da conta remunerada ou de um cofrinho | imprensa |
| PagBank (Cartão com Limite Garantido) | Qualquer CDB PagBank, 1:1 | prática de mercado |
| Banco do Brasil (Limite com Investimentos) | Fundos BB elegíveis, uma porcentagem do valor aplicado | prática de mercado |
| Santander (crédito com garantia, não cartão) | Previdência, CDB, LCA, LCI, capitalização e fundos do Santander | prática de mercado |
| Nubank (empréstimo com garantia) | Investimentos custodiados em empresas do grupo | prática de mercado |
| MetLife (previdência) | PGBL e VGBL da MetLife em garantia de crédito de qualquer instituição financeira | prática de mercado |
| Nobli (crédito com garantia) | Diz aceitar CDB, LCI, LCA, ações, FIIs, ETFs, BDRs e debêntures de qualquer banco ou corretora, sem detalhar o mecanismo | prática de mercado (trechos do próprio site exibidos na busca) |
| Mercado Pago | Não encontramos fonte oficial de cartão com garantia em investimento | não verificado |

### Inter: CDB Mais Limite de Crédito

- CDB emitido pelo Inter, com liquidez diária e FGC, a partir de R$ 100; também a
  Poupança Mais Limite; 1:1 no CDB e na poupança. Termos de execução não encontrados.
  **Prática de mercado** (blog do Inter).
- Lançado em novembro de 2020. Em 21/05/2021, cerca de seis meses depois, o banco
  publicou que a modalidade tinha passado de R$ 1 bilhão investido, com 388 mil clientes
  e cerca de R$ 205 milhões em uso como limite. **Prática de mercado.**

Fonte: [blog do Inter, 21/05/2021](https://blog.inter.co/cdb-mais-limite-de-credito-do-inter-supera-a-marca-de-1-bilhao/).

### Nubank: Nu Limite Garantido e empréstimo com garantia

- Anunciado em 24/04/2023: o dinheiro aplicado numa Caixinha aumenta o limite do cartão,
  a partir de R$ 1. As Caixinhas são RDB emitido pela Nu Financeira, segundo páginas do
  Nubank. **Prática de mercado.**
- Em 03/10/2024 passou a aceitar Tesouro Direto contratado pelo Nubank (Selic, IPCA+ e
  Prefixado). **Prática de mercado.**
- Segundo os termos (vigência 04/12/2025) e o blog: o limite pode não ser igual ao valor
  atual; no Tesouro, ele cai se o preço cair; depois de mais de 7 dias de atraso usa a
  garantia, incluindo IOF e IR; os juros correm até a quitação; com pendência, o ativo
  não pode ser transferido. **Prática de mercado.**
- O teto de R$ 150 mil é do limite total do cartão, somando o pré-aprovado e o
  garantido. **Prática de mercado.**
- Empréstimo com garantia: investimentos custodiados em empresas do grupo, por alienação
  ou cessão fiduciária; o empréstimo impede a transferência de custódia até a quitação.
  **Prática de mercado** (contrato).

Fontes: [comunicado de 24/04/2023](https://nu.com/pt/sala-de-imprensa/consumidores/nubank-lanca-recurso-para-aumentar-limite-do-cartao-de-credito-a-partir-de-investimentos-colateralizados),
[comunicado de 03/10/2024](https://nu.com/pt/sala-de-imprensa/consumidores/nubank-evolui-recurso-para-aumentar-limite-do-cartao-com-investimentos-em-tesouro-direto),
[blog do Nubank](https://blog.nubank.com.br/nu-limite-garantido/), página do produto e
termos do cartão e do empréstimo.

### Itaú: Cofrinho "Mais limite no Cartão"

- Cofrinho ou Poupança; R$ 1 investido = R$ 1 de limite. **Prática de mercado** (página
  de limites do cartão do Itaú). A imprensa noticiou a novidade em 14/01/2026
  (**imprensa**).

Fontes: [página de limites do Itaú](https://www.itau.com.br/cartoes/limites) e
[Olhar Digital, 14/01/2026](https://olhardigital.com.br/2026/01/14/pro/igual-ao-nubank-itau-libera-aumento-de-limite-no-cartao-sem-analise-de-credito-pelo-app/).

### XP: cartão de crédito

- Ativos do grupo XP, inclusive PGBL e VGBL; limites Flexível (cláusula 2.2), Fixo (2.3)
  e Extra (2.3.6); garantia por alienação ou cessão fiduciária (6.1), bloqueada conforme
  o uso (6.1.1); execução por compensação ou excussão e mandato (6.1.9 e 6.1.10).
  **Prática de mercado** (termos do cartão XP).

### C6: CDB C6 Limite Garantido

- CDB C6, 103% do CDI na página atual, FGC; mínimo de R$ 100; 1:1 até R$ 200 mil. Se o
  cliente pagar menos que o mínimo, o CDB quita a dívida, com resgate em até 7 dias
  corridos do vencimento. **Prática de mercado** (página do produto). Releases de 2020 e
  2023 falavam em 3 a 5 dias: vale a página atual.

Fonte: [blog do C6](https://c6bank.com.br/blog/o-que-e-limite-garantido).

### PicPay

- Cartão com saldo em garantia: saldo da conta remunerada ou de um cofrinho; todo o saldo
  vira limite, e o rendimento também. Execução não encontrada. **Imprensa** (Seu Dinheiro,
  2023; Money Times, 25/07/2023).

Fonte: [Money Times, 25/07/2023](https://www.moneytimes.com.br/picpay-une-liberacao-de-credito-e-rendimento-acima-da-poupanca-em-um-so-novo-servico-confira/).

### PagBank, Banco do Brasil e Santander

- **PagBank** (Cartão com Limite Garantido): qualquer CDB PagBank, 1:1, a partir de
  R$ 300 para pedir o cartão; se o CDB vence ou é resgatado com compras em aberto, a
  parte comprometida vai para o "Valor Reservado", que segue como garantia. **Prática de
  mercado** (blog do PagBank).
- **Banco do Brasil** (Limite com Investimentos): fundos BB elegíveis, de risco muito
  baixo e liquidez imediata; uma porcentagem do valor aplicado; o valor vinculado é
  garantia do pagamento das faturas nos vencimentos. **Prática de mercado** (página e
  contrato do cartão).
- **Santander** (crédito com garantia, não cartão): previdência, CDB, LCA, LCI,
  capitalização e fundos no Santander; no app, previdência, capitalização e CDB, LCI e
  LCA com carência legal menor que 2 meses. O blog diz que, "em muitos casos", as
  instituições emprestam de 50% a 100% do valor aplicado: é uma generalização do blog,
  não um número do Santander. **Prática de mercado** (blog do Santander).

### MetLife e Nobli

- **MetLife**: PGBL e VGBL da MetLife em garantia de crédito de qualquer instituição
  financeira; a instituição envia documentos e a certidão do BCB que confirma a
  autorização para operar crédito, e a MetLife analisa em 5 dias úteis; regras da Res.
  Conjunta 12. **Prática de mercado** (página da MetLife).
- **Nobli**: diz aceitar CDB, LCI, LCA, ações, FIIs, ETFs, BDRs e debêntures, de qualquer
  banco ou corretora; segundo o FAQ da Nobli, atua como correspondente de uma SCD. O site
  bloqueia a leitura direta e não detalha o mecanismo. **Prática de mercado** (trechos do
  próprio site exibidos na busca, sem leitura da página).

### BaaS e infraestrutura

- **Celcoin**: crédito com e sem garantia, licenças próprias de IP e SCD, white label
  para crédito; não encontramos produto específico de garantia em investimento.
  **Prática de mercado.**
- **Dock, Swap, Zoop, BMP e Pismo**: não encontramos menção em buscas gerais; não
  pesquisamos cada um a fundo. **Não verificado.**
- **Núclea** (plataforma da Res. Conjunta 12): registro de contratos, gestão de garantias,
  cláusulas de liberação e monitoramento diário; segundo a imprensa, em operação desde o
  início de nov/2025, só para previdência e capitalização. **Imprensa** (CQCS, com dados
  do Valor).
- **B3 (RC Garantias)**: o mesmo trilho da Res. Conjunta 12, por API ou pela plataforma;
  anunciado em 11/11/2025 para o início de 2026, só previdência, capitalização e vida.
  **Não verificado** que já opera.
- **Asaas e Dock** (mercado): a Asaas informou em maio/2026 que está adequando a operação
  à Res. Conjunta 16, e a Dock diz já ter adequado a sua (nov/2026). **Prática de
  mercado** (comunicados, não planos).

### Fora do Brasil

- **Índia, ações em conta demat:** pledge na depositária NSDL ou CDSL; o investidor
  autentica com OTP. No serviço de LAS da CDSL, quem tem conta em qualquer participante
  pode tomar crédito de banco ou NBFC participante sem que o credor seja participante da
  depositária; o pedido ainda passa pela aprovação do participante do devedor.
  **Prática de mercado** (CDSL, NSDL, Mirae Asset, Finsire).
- **Índia, fundos mútuos:** lien marking no agente de transferência (CAMS ou KFintech),
  com confirmação por OTP. **Prática de mercado.**
- **EUA:** control agreement tripartite, UCC 8-106. **Norma** estrangeira (FDIC).
- **Reino Unido:** lombard lending; em geral os ativos ficam na custódia do banco, e
  alguns bancos, como o C. Hoare & Co., tomam garantia sobre carteira em gestora de
  terceiro mediante acordo. **Prática de mercado.**

### Manifestações e números públicos

- **Zetta**, na consulta pública da CVM que gerou a Res. 210 (SDM 02/23), propôs
  portabilidade coordenada do crédito com a garantia. **Prática de mercado** (resposta à
  consulta).
- **ANBIMA**, na mesma consulta, sugeriu prever a recusa da portabilidade, com diligências
  do custodiante de destino em até 2 dias úteis para superar impedimentos.
- **Banco Central**, em mar/2026, disse que a portabilidade de investimentos via Open
  Finance está "no radar", em conversa com a CVM. **Imprensa.**
- Febraban, ABBC e ABFintechs: não encontramos manifestação específica.
- **Ministério da Fazenda**, nota de 26/09/2024: mais de R$ 1 trilhão em recursos de
  previdência e capitalização que poderiam servir de garantia. Nota oficial do ministério, que não é norma.
- **Caso recente:** a liquidação do Banco Master, com pagamentos do FGC a partir de
  jan/2026, ilustra o risco do emissor. **Imprensa.**

### O padrão

Em todos os casos o ativo que vira garantia está custodiado no mesmo grupo que concede o
crédito. Faz sentido: é ele que enxerga o ativo, consegue registrar o gravame e consegue
executá-lo. Quem não é banco nem custodiante fica de fora.

A ideia deste projeto é a plataforma de Banking as a Service oferecer isso uma vez, para
todos os parceiros: o ativo fica custodiado no grupo da plataforma, o gravame é
registrado e o limite é liberado, e o parceiro só chama a API. Para ativos de fora, só
os trilhos abaixo, e não para qualquer ativo.

## Trilhos regulados que já existem

São a fase 2 do [roadmap](roadmap.md).

### Tesouro Direto como garantia (TD Garantia)

- Anunciado em 25/11/2024 por B3, Secretaria do Tesouro Nacional e Banco Central:
  serviço que permite ao investidor pessoa física usar títulos do Tesouro Direto como
  garantia. **Prática de mercado** (nota do Tesouro e da B3). Portaria MF 1.748, de
  08/11/2024; a data de publicação no DOU só aparece em agregador: **não verificado.**
- O Selic passou a registrar gravames de títulos do Tesouro Direto. **Prática de
  mercado** (ANBIMA).
- Quem pode receber a garantia e como a instituição se habilita: **não verificado.** Só
  encontramos isso em blog de corretora; a página da B3 fala em garantia concedida "às
  instituições financeiras", sem detalhar a habilitação.

Fontes: [B3, 25/11/2024](https://www.b3.com.br/pt_br/noticias/td-garantia.htm) e
[Anbima](https://www.anbima.com.br/pt_br/noticias/selic-passa-a-permitir-registro-de-gravames-do-tesouro-direto.htm).

### Previdência como garantia

- Lei nº 14.652, de 23/08/2023: permite dar em garantia de operações de crédito o
  direito de resgate de planos de previdência complementar aberta, seguros de pessoas,
  Fapi e títulos de capitalização. **Norma.**
- Resolução Conjunta CNSP/CMN nº 12/2024 (publicação de 01/10/2024): regulamenta a lei,
  só para empréstimo ou financiamento. A entidade operadora informa em 2 dias úteis e não
  recusa o pedido que siga a norma (art. 4º, §1º, e art. 18); a execução só vem após 90
  dias de atraso (art. 12); a execução é o resgate pedido pela instituição financeira e
  pago por ela, líquido de tributos e carregamento (art. 13). **Norma.**
- O RC Garantias da B3 e a plataforma privada da previdência: a operação do RC Garantias
  é **não verificado**; a plataforma, segundo a imprensa, opera desde nov/2025
  (**imprensa**). Não encontramos número de volume com fonte.
- A previdência não aparece no art. 81 da Res. BCB 352; concedida como direito de
  resgate, tende a cair na carteira C3 (45% de piso no 1º mês). É leitura do autor:
  **interpretação, não verificado.**

Fontes: [texto da lei na Câmara dos Deputados](https://www2.camara.leg.br/legin/fed/lei/2023/lei-14652-23-agosto-2023-794577-publicacaooriginal-168929-pl.html)
e [notícia da Susep, 01/10/2024](https://www.gov.br/susep/pt-br/central-de-conteudos/noticias/2024/outubro/cnsp-e-cmn-publicam-resolucao-conjunta-sobre-concessao-de-direito-de-resgate-como-garantia-de-operacoes-de-credito).

### Portabilidade da CVM 210 e transferência de custódia

- Ações, FIIs, ETFs e cotas de fundos podem mudar de instituição em 2, 5 ou até 9 dias
  úteis (Res. CVM 210/2024, art. 16). O art. 19, §1º traz exemplos de impedimento, não
  uma lista fechada. **Norma.**
- CDB, LCI, LCA e Tesouro ficam fora da CVM 210 pelo entendimento corrente (Lei 6.385,
  art. 2º, §1º). **Interpretação.** Para eles, o caminho é a transferência de custódia.
- Pelo entendimento corrente, portar a custódia não gera IR. **Interpretação**; ressalvas
  em FIIs, ETFs, IOF na venda em bolsa, base de PGBL e VGBL e portabilidade entre planos:
  **não verificado**; confirmar com advogado e contador.

## Mercado aberto: o que falta

É a fase 3 do roadmap, e a que menos depende da plataforma.

- A Lei nº 12.810/2013, art. 26 (com a redação da Lei nº 13.476/2017), determina que o
  gravame sobre ativo financeiro ou valor mobiliário registrado ou depositado seja
  constituído na registradora ou no depositário central onde o ativo está. **Norma.**
- Na prática isso espalha os gravames por vários sistemas. Usar como garantia um CDB,
  uma ação ou uma cota de fundo custodiados em outra instituição exige que esses
  sistemas se falem. Não encontramos norma que obrigue o custodiante a registrar gravame
  para credor de outra instituição. **Interpretação.**
- **Portabilidade de garantia:** existe para recebíveis de cartão (Res. CMN 4.734/2019 e
  Res. BCB 264/2022). Para investimentos, ainda não. **Norma** para recebíveis.
- **Open Finance.** Permite ver a carteira em outras instituições, com consentimento; a
  trava acontece na B3 ou no Selic. O repasse de dados à fintech exige contrato de
  parceria (Res. Conjunta 1/2020, arts. 36 a 38-A, na redação da Res. Conjunta 21/2026).
  O início dos dados de investimentos ficou "setembro de 2023" (o dia, 28 ou 29, é
  **não verificado**). **Norma.**
- **Drex.** Segundo a imprensa, o Banco Central descontinuou em novembro de 2025 a
  plataforma em blockchain usada no piloto e redirecionou o projeto para uma solução de
  reconciliação de gravames. A plataforma de testes foi desligada; não encontramos norma
  nem data oficial, e a retomada é **não verificado.** **Imprensa.** Nada neste projeto
  depende do Drex.

Fontes: [Lei 13.476/2017 na Câmara dos Deputados](https://www2.camara.leg.br/legin/fed/lei/2017/lei-13476-28-agosto-2017-785408-normaatualizada-pl.html),
[HRSA, set/2025](https://hrsa.com.br/artigo/drex-2026-garantias-reconciliacao-de-gravames/),
[Gazeta do Povo](https://www.gazetadopovo.com.br/economia/banco-central-encerra-projeto-do-drex-o-real-digital/)
e [Serasa](https://www.serasa.com.br/blog/o-que-aconteceu-drex/).

## Base jurídica

**Eu não sou advogado.** O que está aqui é a minha leitura de leigo, e a modelagem
jurídica de um produto assim precisa ser validada por quem é. O site do Planalto
bloqueou o acesso a partir do ambiente em que conferi as fontes; por isso os
dispositivos abaixo foram lidos em repositórios de legislação não oficiais e estão
resumidos, não transcritos. Os links apontam para o texto oficial.

| Dispositivo | O que diz, em resumo | Por que importa aqui | Categoria |
|---|---|---|---|
| [Lei 4.728/1965, art. 66-B, § 3º](https://www.planalto.gov.br/ccivil_03/leis/l4728.htm) | Admite alienação fiduciária de coisa fungível e cessão fiduciária de direitos e títulos de crédito. Em caso de inadimplemento, o credor pode vender o bem a terceiros sem leilão nem medida judicial, usa o valor para quitar o crédito e devolve o saldo ao devedor, com demonstrativo. | É o que permite executar a garantia sem ir à Justiça, e o que obriga a devolver a sobra. No código, a execução vende o ativo por inteiro, o líquido abate a dívida e o excedente volta ao cliente com demonstrativo. | norma (lida em repositório não oficial) |
| [CDC, art. 104-A, § 1º](https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm) (incluído pela Lei 14.181/2021) | Exclui do processo de repactuação por superendividamento, entre outras, as dívidas de contratos de crédito com garantia real. | Reduz a incerteza de recuperação do crédito; se a cessão fiduciária conta como garantia real para esse artigo é ponto a confirmar com advogado. Não dispensa o dever de conceder crédito de forma responsável. | norma (lida em repositório não oficial) |
| [Lei 12.810/2013, art. 26](https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2013/lei/l12810.htm) | Gravame sobre ativo registrado ou depositado é constituído na registradora ou no depositário central do ativo. | Define onde a garantia precisa ser registrada para valer contra terceiros. É o nó da fase 3. | norma (lida em repositório não oficial) |
| [Lei 14.652/2023](https://www2.camara.leg.br/legin/fed/lei/2023/lei-14652-23-agosto-2023-794577-publicacaooriginal-168929-pl.html) e Resolução Conjunta CNSP/CMN nº 12/2024 | Direito de resgate de previdência, seguro de pessoas e capitalização pode ser dado em garantia de crédito. | Abre a previdência como garantia, só para empréstimo. | norma |

Repositório não oficial usado na leitura dos dispositivos acima, quando o Planalto bloqueou:
[LegJur, art. 66-B](https://www.legjur.com/legislacao/art/lei_00047281965-66-B) e
[LegJur, art. 104-A](https://www.legjur.com/legislacao/art/lei_00080781990-104-A).

Outros pontos que um jurídico teria de olhar e que este projeto não resolve:

- **Open Finance.** O consentimento do cliente é dado por parceiro, conforme as regras
  vigentes (Res. Conjunta 1/2020, na redação da Res. Conjunta 21/2026). Não modelamos o
  consentimento no código.
- **Quem é o credor.** A API é oferecida pela plataforma, mas a instituição que concede o
  crédito (uma SCD do grupo) figura como credora e beneficiária do gravame. A fintech
  parceira é entidade tomadora de BaaS e não pode atuar em nome da prestadora como
  correspondente (Res. Conjunta 16/2025, art. 6º, I). Se o crédito for cedido a um FIDC, a
  garantia acompanha o crédito (Código Civil, art. 287), mas o registro precisaria trocar
  de credor; com agente de garantia, não. **Interpretação.**
- **Proteção de dados.** A API só conhece `cliente_ref`, um identificador do parceiro.
  CPF, nome e contato não entram.
- **Se alguém quebrar.** Custodiante: no Selic as contas vão para outro participante em
  10 dias úteis, e ativo e gravame ficam na infraestrutura, fora do patrimônio do
  custodiante; a quebra atrasa, mas não apaga o gravame (**interpretação**). Emissor do
  CDB, LCI ou LCA: a garantia vira crédito contra a massa (Lei 6.024, arts. 6º e 18), e o
  FGC paga o titular registrado, até R$ 250 mil por titular e conglomerado
  (**norma**). Credor: a dívida continua e o liquidante comanda a baixa. Tomador PJ ou MEI
  em recuperação judicial: Lei 11.101, art. 49, §§3º e 5º.

## Política de risco de referência

Os LTVs que o projeto usa como ponto de partida são **hipóteses de calibração**, não
padrão de mercado nem número de norma. Na vida real quem define é o time de risco, por
parceiro e por ativo, dentro das faixas do catálogo. No código eles ficam em
[`app/politica_referencia.toml`](../app/politica_referencia.toml) e podem ser trocados
por parceiro pela API administrativa, que valida contra a faixa.

| Ativo | LTV de partida | Racional |
|---|---|---|
| CDB | 85% (faixa 85 a 95%) | Marcação a mercado e liquidez dependem do papel |
| Fundo DI | 85% (faixa 85 a 95%) | Oscilação muito baixa |
| Tesouro Selic | 90% (faixa 90 a 95%) | Oscilação muito baixa |
| Tesouro Prefixado, Tesouro IPCA+ | 85%, caindo com o prazo | Sofrem com a curva de juros |
| Ações, ETFs, FIIs (só empréstimo) | 50% (faixa 50 a 70%) | Volatilidade alta |
| LCI e LCA (só empréstimo) | 80% (faixa 80 a 90%) | Carência e liquidez |
| Previdência (só empréstimo) | 60% (faixa 60 a 80%) | Prazo até o fim da acumulação |

Haircut no capital: título público 0,5%, 2% ou 4%; título de instituição financeira sem
subordinação e de emissor na categoria A, de 2% a 20%, conforme o prazo; ações incluídas
em índices relevantes, 20% (Circular 3.809/2016). Piso de provisão no 1º mês após o
inadimplemento: C2 30%, C3 45%, C5 50% (Res. BCB 352/2023, Anexo I). **Norma**; a escolha
da carteira de cada ativo é interpretação do projeto.

A execução a partir do 7º dia de atraso do cartão segue a prática que o mercado já
divulga (**prática de mercado**). Os tetos de limite do arquivo são valores ilustrativos
de um piloto, não dados de mercado.

## Correções que a revisão de 22/11/2026 trouxe

Pontos que mudaram o que este documento afirma, com a categoria de cada um:

- Previdência só para empréstimo ou financiamento, e a execução é o resgate pedido pela
  instituição e pago líquido de tributos e carregamento (Res. Conjunta 12, art. 13).
  **Norma.**
- Na B3, a confirmação da contraparte é obrigatória (art. 88); o art. 94 libera o ativo
  quando ela recusa ou não responde. **Prática de mercado** (Regulamento e Manual de
  Normas da B3).
- Em cotas de fundos, o registro é na registradora (Res. CVM 31, art. 36-A) ou na B3, com
  comando do escriturador. **Norma.**
- CDB, LCI, LCA e Tesouro fora da CVM 210 é entendimento, não texto de norma
  (**interpretação**).
- Players: C6 em 103% do CDI e resgate em até 7 dias corridos (página atual); Inter 1:1
  também na poupança; PagBank sem a menção a FGC; Santander aceita previdência e
  capitalização no app; MetLife exige a certidão do BCB; Nubank: as Caixinhas são RDB da
  Nu Financeira, segundo o próprio Nubank; Celcoin com white label para crédito; o número
  de volume da plataforma da previdência saiu por falta de fonte; o FAQ da Nobli a
  apresenta como correspondente de uma SCD.
- Estimativa de mais de R$ 1 trilhão: nota do Ministério da Fazenda (26/09/2024), no lugar
  de imprensa. Entrou a nota do Tesouro sobre o TD Garantia. O Drex passou a ser atribuído
  à imprensa por inteiro.
- Provisão: os meses contam a partir do inadimplemento (atraso acima de 90 dias).
- Lei 14.690: o teto é sobre juros e encargos do rotativo e do parcelado, não sobre
  "receita do atraso" (art. 28). **Norma.**
- Tributos: o fato gerador do IR na renda fixa segue o Mafon; ressalvas em FIIs, ETFs,
  IOF na venda em bolsa e base de PGBL e VGBL são **não verificado.**
