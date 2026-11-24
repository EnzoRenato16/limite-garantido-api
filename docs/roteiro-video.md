# Roteiro do vídeo (até 3 min)

Gravação de tela, em primeira pessoa, começando pelo Docker e passando pelos trilhos
simulados da `/demo`, pela `/regulacao`, pelo `/risco` e pelo `/negocio`. Na `/demo`, a
coluna da esquerda é o app de um parceiro fictício; a da direita mostra os webhooks
chegando.

**Antes de gravar**

- Terminal aberto na pasta do projeto, com o Docker pronto. Abas: `/demo`, `/regulacao`,
  `/risco` e `/negocio`, em janela de 1440 × 900, zoom em 100%.
- Falar devagar. Os tempos abaixo somam 2 min 55 s.

---

### 0:00 a 0:20 · O problema e o Docker

**Na tela:** o terminal com `docker compose up --build` já rodando, depois a `/demo`
parada, limite em R$ 0,00.

> Meu investimento só vira limite no cartão no banco onde ele está custodiado. Montei um
> protótipo de como isso poderia virar uma API para quem não é banco. Sobe com um
> comando, Docker compose, com Postgres, API e worker. Tudo aqui é simulado: não integra
> com a B3, com o Selic nem com ninguém de verdade.

### 0:20 a 0:45 · Dentro de casa

**Na tela:** garantia de CDB, R$ 5.000,00, depois uma compra de R$ 4.000,00.

> O ativo fica custodiado na casa, o gravame é registrado e só então nasce o limite:
> quatro mil duzentos e cinquenta. Compra aprovada. Do lado direito, o parceiro recebe cada
> evento, assinado.

### 0:45 a 1:20 · Os trilhos de fora

**Na tela:** **Começar de novo**. Em "Trazer de fora", escolher **Portabilidade** de ações,
R$ 10.000,00, **Pedir a entrada**. Apontar o prazo em dias úteis e a linha do tempo
(solicitado, registrado, baixa pedida, baixado). Clicar em **Informar impedimento
(simulador)**, depois pedir de novo e **Concluir (simulador)**.

> Investimento de outra instituição não vale para qualquer ativo. Cada tipo tem o seu
> trilho: portabilidade da CVM 210 para ações, transferência de custódia para CDB, TD
> Garantia para o Tesouro e a Resolução Conjunta 12 para a previdência. Aqui as ações
> vêm por portabilidade. O limite continua zero até o gravame ser registrado. Se houver
> impedimento, nada entra no livro contábil. Se concluir, o gravame nasce, e o limite
> também.

### 1:20 a 1:45 · Previdência e empréstimo

**Na tela:** previdência com a régua: avisos de 45 e de 10 dias e a execução só depois
de 90 dias de atraso.

> Na previdência, a norma exige avisos ao cliente e só permite a execução depois de
> noventa dias de atraso. A régua está no código. A execução vende por inteiro, retém o
> tributo e devolve o excedente com demonstrativo.

### 1:45 a 2:05 · A regulação

**Na tela:** aba `/regulacao`, apontar os selos de categoria e uma linha da matriz.

> Cada afirmação regulatória tem norma e artigo, ou uma categoria: prática de mercado,
> interpretação, imprensa ou não verificado. A matriz sai de um único arquivo, e a página,
> a API e o documento leem dele.

### 2:05 a 2:30 · O painel de risco

**Na tela:** aba `/risco`. Clicar em **Quebra de emissor** e apontar a perda e a
concentração contra o teto do FGC. O aviso de carteira sintética deve aparecer na tela.

> Do lado de quem cuida do risco: uma carteira sintética de mil clientes, sem dado real.
> Se o emissor quebra, o FGC paga o titular, não o credor, e a perda aparece. A garantia
> reduz a perda, não a elimina.

### 2:30 a 2:55 · O modelo econômico e o fechamento

**Na tela:** aba `/negocio`, conjunto **Base**: margem da plataforma, do parceiro e ponto
de equilíbrio. Depois **Conservador**, com margem negativa.

> E o negócio fecha? Os números são ilustrativos, premissas minhas, sem dado de empresa.
> No base, fecha por pouco, e no conservador, não. Por baixo há um livro contábil de
> partidas dobradas e testes para tudo isso. É um projeto de estudo, e eu não sou
> advogado. Eu adoraria ouvir onde isso quebra.

---

**Se alguém perguntar na apresentação ao vivo**, respostas curtas:

- *"Isso fala com o Tesouro, a B3 ou o Selic?"* Não. O registro é simulado. Está
  documentado onde um trilho real entraria e o que as fontes públicas dizem.
- *"Funciona com qualquer ativo de outra instituição?"* Não. Só nos trilhos da matriz; para
  CDB, LCI e LCA, não encontramos norma que obrigue o custodiante a cooperar.
- *"E o Drex?"* Não há norma nem data; nada depende dele.
- *"E se o mesmo aviso da contraparte chegar duas vezes?"* Cada resposta tem um id, e o
  banco não aceita o mesmo id duas vezes.
- *"E se duas compras chegarem juntas?"* A conta é travada durante a decisão. Um teste
  dispara quarenta compras ao mesmo tempo.
- *"Isso é legal?"* Há norma por trás de cada trilho, mas eu não sou advogado e isso
  está escrito em toda a documentação.
