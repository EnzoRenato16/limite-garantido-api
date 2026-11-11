# Post para o LinkedIn

Texto para publicar junto com o vídeo de 2 minutos (ou com a imagem
`docs/img/demo-2-chamada-de-margem.png`).

---

Uma pergunta que não saía da minha cabeça: por que o meu investimento só vira limite no
cartão dentro do banco onde ele está?

Vários bancos e fintechs já fazem isso com o dinheiro guardado neles. Você guarda, o
valor fica reservado e vira limite. Funciona, e muita gente usa. Só que quem não é banco
não consegue oferecer a mesma coisa para os próprios clientes.

Montei, como projeto pessoal, um protótipo de como isso poderia virar um serviço: uma
API que uma plataforma de Banking as a Service ofereceria aos parceiros. O parceiro
chama, o dinheiro do cliente vai para um investimento que continua rendendo, fica
bloqueado como garantia e o limite aparece no cartão.

O que mais me ensinou não foi o caminho feliz, foi o resto.

O que acontece quando o investimento cai e a dívida fica maior que o limite. O que
acontece quando a fatura atrasa, e como vender só o necessário da garantia. Como impedir
que duas compras ao mesmo tempo passem juntas do limite: o teste que cobre isso achou
dois bugs que não apareciam lendo o código. E como ter certeza de que o dinheiro fecha:
não existe campo de saldo, tudo sai de um livro contábil de partidas dobradas.

Tem uma página de demonstração onde dá para guardar, comprar, derrubar o ativo e atrasar
a fatura, vendo os avisos chegarem do outro lado. O vídeo mostra em dois minutos.

Um aviso importante: é um projeto de estudo, feito só com informação pública e sem
vínculo com nenhuma instituição. Não sou advogado, e a parte jurídica precisaria ser
validada por quem é.

O código e a documentação estão aqui: [link do repositório]

Se você trabalha com crédito ou com infraestrutura financeira, eu gostaria muito de
saber o que você criticaria.

#fintech #credito #engenhariadesoftware

---

**Notas para mim**

- Trocar `[link do repositório]` pelo endereço do GitHub.
- O projeto foi construído com o apoio do Claude Code, numa sessão autônoma a partir do
  meu briefing (está registrado em `docs/DECISOES.md`). Decidir se e como mencionar isso
  no post. Uma frase honesta resolve, por exemplo: "Usei o Claude Code como par de
  programação; as decisões que ele tomou sozinho estão documentadas no repositório."
- Antes de publicar, ler o código e rodar a demo: quem comentar vai fazer perguntas
  sobre o ledger, a concorrência e a execução da garantia.
- Para um tom mais curto, cortar o parágrafo do meio e deixar só a frase das duas
  compras simultâneas, que é a que mais gera conversa.
- Não marcar empresas. O projeto não é de nenhuma delas.
