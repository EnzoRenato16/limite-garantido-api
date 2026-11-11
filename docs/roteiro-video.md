# Roteiro do vídeo (2 minutos)

Gravação de tela da página `/demo`, com a minha voz por cima. A coluna da esquerda é o
app de um parceiro fictício; a da direita mostra os webhooks chegando.

**Antes de gravar**

- `docker compose up --build` (ou `./scripts/dev.sh`) e abrir `http://localhost:8000/demo`.
- Janela do navegador em 1440 × 900, zoom em 100%. A régua fica presa no topo, então dá
  para rolar a página sem perder os números.
- Clicar em **Começar de novo** para sair de uma conta zerada.
- Falar devagar. Os tempos abaixo somam dois minutos com folga para respirar.

---

### 0:00 a 0:15 · O problema

**Na tela:** a página parada, limite em R$ 0,00.

> Oi, eu sou o Enzo. Hoje, se você tem dinheiro investido e quer limite no cartão, isso
> só funciona no banco onde o investimento está. Quem não é banco não consegue oferecer.
> Eu montei um protótipo de como isso poderia virar um serviço: uma API.

### 0:15 a 0:35 · Guardar e ganhar limite

**Na tela:** clicar em **Guardar como garantia** (CDB, R$ 5.000,00).

> Este é o app de um parceiro qualquer, uma fintech ou um varejista. O cliente guarda
> cinco mil reais num CDB. O dinheiro continua rendendo, só que fica bloqueado como
> garantia, e na mesma hora vira quatro mil duzentos e cinquenta de limite. Oitenta e
> cinco por cento, que é a margem de segurança desse tipo de ativo.

**Apontar para a direita:**

> Aqui do lado é o que o parceiro recebe: um aviso para cada coisa que acontece,
> assinado, para ele ter certeza de que veio da plataforma.

### 0:35 a 0:55 · Comprar

**Na tela:** clicar em **Comprar** (R$ 4.000,00). Trocar o valor para `500,00` e clicar
de novo.

> Compra de quatro mil: aprovada, e a barra amarela mostra o limite sendo usado. Agora
> uma de quinhentos. Só sobraram duzentos e cinquenta, então ela é negada. E repara que
> a negativa também fica registrada do lado de lá.

### 0:55 a 1:20 · O investimento cai

**Na tela:** clicar em **Derrubar o ativo em 10%**.

> Agora o que nenhum demo de crédito costuma mostrar: e se o investimento perder valor?
> Caiu dez por cento. A garantia encolheu, o limite encolheu junto, e a dívida passou
> dele. Esse pedaço vermelho é o que ficou descoberto. O sistema abre uma chamada de
> margem: o cliente tem um prazo para reforçar a garantia ou pagar.

### 1:20 a 1:45 · A fatura atrasa

**Na tela:** clicar em **Fechar a fatura**, depois em **Passar 1 dia** sete vezes, sem
pressa nos dois últimos.

> E se ele não pagar? Fecho a fatura e faço os dias passarem. Primeiro dia de atraso,
> as compras são suspensas. No sexto, o prazo da chamada de margem acaba e a conta é
> bloqueada. No sétimo dia a garantia é executada: o sistema vende só o valor da fatura,
> quatro mil, e os quinhentos que sobraram continuam sendo do cliente, rendendo.

### 1:45 a 2:00 · Fechamento

**Na tela:** rolar a coluna da direita e abrir um **ver o JSON entregue**.

> Por baixo tem um livro contábil de partidas dobradas, proteção contra duas compras ao
> mesmo tempo e testes para tudo isso. É um projeto pessoal de estudo, feito com dados
> públicos. O código e a documentação estão no meu GitHub. Se você trabalha com crédito
> ou com infraestrutura financeira, eu adoraria ouvir onde isso quebra.

---

**Se sobrar tempo**, um plano B de 15 segundos para o meio do vídeo: clicar em **Pagar a
fatura** antes do atraso e mostrar que, pagando, nada é vendido e a garantia pode ser
resgatada.

**Se alguém perguntar na apresentação ao vivo**, três respostas curtas:

- *"E se duas compras chegarem juntas?"* A conta é travada durante a decisão. Tem um
  teste que dispara quarenta compras ao mesmo tempo e exige que passem exatamente as
  que cabem.
- *"De onde vem o saldo?"* De lugar nenhum: não existe campo de saldo. Tudo é soma do
  livro contábil, e o banco recusa lançamento que não fecha.
- *"Isso é legal?"* A base é a alienação fiduciária de ativos financeiros, mas eu não
  sou advogado e está escrito isso em todo lugar da documentação.
