# Post para o LinkedIn

Texto para publicar junto com o vídeo de 3 minutos (ou com a imagem
`docs/img/trilhos-1-portabilidade-solicitada.png`).

---

Uma pergunta que não saía da minha cabeça: por que o meu investimento só vira limite no
cartão dentro do banco onde ele está?

Bancos e fintechs grandes já fazem isso com investimentos custodiados no próprio grupo.
Você aplica, o valor fica reservado e vira limite. Funciona, e muita gente usa. Só que quem
não é banco não consegue oferecer a mesma coisa para os próprios clientes.

Montei, como projeto pessoal, um protótipo de como isso poderia virar um serviço: uma API
que uma plataforma de Banking as a Service ofereceria às fintechs parceiras. O parceiro
chama, o investimento continua rendendo e fica custodiado, o gravame é registrado e o
limite aparece no cartão. Quem concede o crédito e emite o cartão é a instituição
autorizada; a fintech distribui, como tomadora de BaaS.

Duas coisas que eu quero deixar claras, porque aprendi lendo as normas:

1. O projeto **simula** o registro de gravame na B3 e no Selic. Não integra com nenhum
   dos dois, e as contrapartes da demonstração são personagens fictícios.
2. Investimento de outra instituição não vale para qualquer ativo. Existem trilhos
   regulados: previdência (Res. Conjunta 12/2024), Tesouro pelo TD Garantia e a
   portabilidade da Res. CVM 210 para ações, FIIs, ETFs e fundos. CDB, LCI e LCA entram por
   transferência de custódia, e para eles não encontramos norma que obrigue o custodiante
   a cooperar.

O Open Finance permite ver a carteira em outras instituições, com consentimento, mas a
trava acontece na B3 ou no Selic. O Drex chegou a prever reconciliação de gravames, mas a
plataforma de testes foi desligada, e não há norma nem data: nada aqui depende dele.

O que mais me ensinou foi o resto do caminho feliz: o que acontece quando o investimento
cai, quando a fatura atrasa, quando a execução precisa vender o ativo por inteiro, reter
o tributo e devolver o excedente. A garantia reduz a perda, mas restam o risco do emissor,
de preço, de liquidez e de execução. E como impedir que duas compras ao mesmo tempo
passem do limite: o teste que cobre isso achou dois bugs que não apareciam lendo o código.

Os números do modelo econômico são ilustrativos, a carteira do painel de risco tem mil
clientes sintéticos e nenhum dado de cliente real foi usado. É um projeto de estudo, feito
só com informação pública. Não sou advogado, e a parte jurídica precisaria ser validada
por quem é. O nome "Limite Garantido" já é usado por outras instituições: aqui é
descrição, não marca.

O código e a documentação estão aqui: [link do repositório]

Se você trabalha com crédito ou com infraestrutura financeira, eu gostaria muito de
saber o que você criticaria.

#fintech #credito #engenhariadesoftware

---

**Notas para mim**

- Trocar `[link do repositório]` pelo endereço do GitHub.
- O projeto foi construído com o apoio do Claude Code a partir do meu briefing (está
  registrado em `docs/DECISOES.md`). Uma frase honesta resolve, por exemplo: "Usei o
  Claude Code como par de programação; as decisões que ele tomou sozinho estão
  documentadas no repositório."
- Não dizer que alguma empresa vai lançar isso nem que estou construindo com ela; não
  citar conversa privada nem associar o projeto a empregador.
- Não afirmar "qualquer banco" ou "integra com a B3 e o Selic". Dizer "simula o
  registro".
- Antes de publicar, ler o código e rodar a demo: quem comentar vai perguntar sobre o
  ledger, a concorrência e a execução da garantia.
- Para um tom mais curto, cortar os dois itens numerados e deixar só a frase das duas
  compras simultâneas.
- Não marcar empresas.
