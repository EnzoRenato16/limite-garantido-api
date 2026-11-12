# Roteiro do vídeo (até 3 min)

Gravação de tela das páginas `/demo`, `/risco` e `/negocio`, falada em primeira pessoa. No `/demo`, a
coluna da esquerda é o app de um parceiro fictício; a da direita mostra os webhooks
chegando.

**Antes de gravar**

- `docker compose up --build` (ou `./scripts/dev.sh`) e abrir
  `http://localhost:8000/demo` numa aba, `http://localhost:8000/risco` em outra e
  `http://localhost:8000/negocio` numa terceira.
- Janela do navegador em 1440 × 900, zoom em 100%. A régua fica presa no topo, então dá
  para rolar a página sem perder os números.
- No `/demo`, clicar em **Começar de novo** para sair de uma conta zerada.
- Falar devagar. Os tempos abaixo somam 2 min 50 s.

---

### 0:00 a 0:15 · O problema

**Na tela:** o `/demo` parado, limite em R$ 0,00.

> Hoje, se você tem dinheiro investido e quer limite no cartão, isso só funciona no
> banco onde o investimento está. Eu montei um protótipo de como isso poderia virar um
> serviço que uma plataforma oferece para quem não é banco: uma API.

### 0:15 a 0:35 · Fase 1, dentro de casa (20 segundos)

**Na tela:** **Guardar como garantia** (CDB, R$ 5.000,00), depois **Comprar**
(R$ 4.000,00).

> O cliente guarda cinco mil num CDB. O dinheiro continua rendendo, fica bloqueado como
> garantia e vira quatro mil duzentos e cinquenta de limite na hora. Compra de quatro
> mil, aprovada. Do lado direito, o parceiro recebe cada evento, assinado.

### 0:35 a 1:00 · Fase 2: investimento em outra instituição

**Na tela:** **Começar de novo**. Em "Onde está o investimento", escolher **Investimento
em outra instituição** e **Custodiante Lento (simulador)**; Tesouro Selic, R$ 10.000,00;
**Guardar como garantia**. Esperar a linha do tempo chegar em "Custodiante analisando".

> Agora o investimento está em outro lugar. Aqui o bloqueio não é meu: eu peço a quem
> guarda o ativo, e ele responde quando quiser. Neste projeto esse custodiante é um
> simulador, não tem integração com ninguém de verdade. O pedido saiu, ele está
> analisando, e repara no limite: continua zero. Ele só nasce quando o bloqueio for
> confirmado.

### 1:00 a 1:20 · A recusa

**Na tela:** clicar em **Custodiante recusa**. Apontar a linha do tempo e o webhook
`gravame.recusado` na direita.

> E se ele recusar? Por exemplo, porque o título já está dado em garantia em outro
> lugar. A garantia termina recusada, nenhum centavo entra no livro contábil, e o
> parceiro é avisado com o motivo. Se o custodiante simplesmente não responder, o pedido
> expira no prazo, com o mesmo cuidado.

### 1:20 a 1:35 · A confirmação

**Na tela:** guardar de novo (mesmo custodiante) e clicar em **Custodiante confirma**.

> Quando ele confirma, aí sim: nove mil e quinhentos de limite. A confirmação chega
> assinada, e se ela vier duas vezes, ou pelo aviso e pela consulta ao mesmo tempo, é
> aplicada uma vez só.

### 1:35 a 2:10 · O crash da bolsa no painel de risco

**Na tela:** trocar para a aba do `/risco`. Clicar em **Crash da bolsa (ações -30%)**.
Apontar o "9 → 43" e a régua de Ações com o tracejado. Depois clicar em **Custodiante
fora do ar** e apontar a perda adicional da garantia externa.

> Do lado de quem cuida do risco: uma carteira sintética de mil clientes, com parte das
> garantias em custodiantes de fora. Crash da bolsa, ações caindo trinta por cento. Os
> clientes em chamada de margem vão de nove para quarenta e três, mas a perda fica em
> zero, porque a margem de segurança das ações absorve a queda. Agora, se um
> custodiante sai do ar, a perda aparece, e ela vem inteira da garantia que estava fora.
> Os números são de uma carteira inventada, mas a pergunta é a que um time de risco faria.

### 2:10 a 2:35 · O modelo econômico (25 segundos)

**Na tela:** abrir a aba do `/negocio`, no conjunto **Base**. Apontar a margem da
plataforma e a do parceiro por cliente por mês e o ponto de equilíbrio. Clicar em
**Conservador** e mostrar a margem negativa e o "não há ponto de equilíbrio".

> E o negócio fecha? Esta página mostra, por cliente por mês, quanto a plataforma e o
> parceiro ganham, e quantos clientes a plataforma precisa para pagar o custo fixo. Os
> números são ilustrativos: premissas minhas, editáveis, sem dado de empresa nenhuma.
> No conjunto base, a margem é de uns quatro reais por cliente e o equilíbrio fica logo
> abaixo da carteira. No conservador, a margem é negativa e não existe equilíbrio: com
> premissa ruim, a conta não fecha, e eu preferi deixar isso à vista.

### 2:35 a 2:50 · Fechamento

**Na tela:** voltar ao `/demo` e abrir um **ver o JSON entregue**.

> Por baixo tem um livro contábil de partidas dobradas, proteção contra operações
> simultâneas e testes para tudo isso. É um projeto pessoal de estudo, e a parte de
> outra instituição é simulada. Se você trabalha com crédito ou infraestrutura
> financeira, eu adoraria ouvir onde isso quebra.

---

**Se alguém perguntar na apresentação ao vivo**, respostas curtas:

- *"Isso fala com o Tesouro ou com a B3?"* Não. A fase 2 é simulada. Está documentado
  onde um trilho real entraria e o que as fontes públicas dizem sobre ele.
- *"E se o mesmo aviso do custodiante chegar duas vezes?"* Cada resposta tem um id, e o
  banco não aceita o mesmo id duas vezes. Tem teste com dez avisos iguais ao mesmo tempo.
- *"E se duas compras chegarem juntas?"* A conta é travada durante a decisão. Tem um
  teste que dispara quarenta compras ao mesmo tempo e exige que passem exatamente as
  que cabem.
- *"Isso é legal?"* A base é a alienação fiduciária de ativos financeiros, mas eu não
  sou advogado e isso está escrito em toda a documentação.
