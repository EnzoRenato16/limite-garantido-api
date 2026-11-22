# Regulação: o que cada ativo aceita e por quê

> O autor não é advogado; a modelagem jurídica precisa ser validada.

Este documento resume a base regulatória do projeto: por qual caminho (trilho) cada tipo
de ativo vira garantia, em que infraestrutura o gravame é registrado, para qual produto
serve e com que prazo. A mesma informação aparece na página `/regulacao` e na rota pública
`GET /v1/regulacao/matriz`; as três saem de `app/regulacao.toml`.

**O projeto simula o registro.** Não há integração com a B3, com o Selic, com a
plataforma da Res. Conjunta 12 nem com nenhuma instituição. As contrapartes da demonstração
são personagens fictícios, marcados como simulador.

Papéis usados no texto: a SCD do grupo concede o crédito, emite o cartão e é a
beneficiária do gravame; a CTVM do grupo custodia e comanda o gravame; a DTVM do grupo
administra os fundos DI; a plataforma autorizada da Res. Conjunta 12 troca as informações
da previdência; a entidade tomadora de BaaS é a fintech parceira, só interface.

## Como ler as categorias

Toda afirmação carrega uma categoria:

- **norma**: vem de norma citada, com o artigo;
- **prática de mercado**: o que o mercado faz ou divulga;
- **interpretação**: leitura do projeto, a validar com o jurídico ou a contabilidade;
- **imprensa**: só noticiado;
- **não verificado**: a fonte lista como ainda sem confirmação oficial.

Quando dizemos que não há algo, o sentido é "não encontramos norma que...", nunca que a
norma não existe. Os LTVs são hipóteses de calibração, não padrão de mercado.

## Matriz regulatória

<!-- matriz:inicio -->
### Matriz por ativo

Cada afirmação traz a categoria entre colchetes: norma, prática de mercado, interpretação, imprensa, não verificado. Prazos em dias úteis, pelo calendário de feriados nacionais do projeto.

#### CDB de banco parceiro

- **O que funciona:**
  - [prática de mercado] Funciona hoje dentro de casa: CDB de banco parceiro custodiado na CTVM do grupo, com gravame registrado na B3 a favor da SCD do grupo.
  - [norma] Não há CDB da casa: a SCD não capta do público. (Res. CMN 5.050/2022, art. 10, I)
- **Trilho:** custódia na casa, transferência de custódia (fora da CVM 210)
  - [prática de mercado] Na casa, gravame no dia. De fora, só com o custodiante de origem ou transferindo a custódia antes, fora da Res. CVM 210.
  - [interpretação] Não encontramos norma que obrigue o custodiante a registrar gravame para credor de outra instituição.
- **Instrumento:** cessão fiduciária, penhor
  - [norma] Cessão fiduciária: no atraso, o credor vende sem leilão nem ordem judicial, abate a dívida e devolve o saldo com demonstrativo. (Lei 4.728/1965, art. 66-B, §§3º e 4º)
  - [norma] Penhor de direitos e títulos: o bem continua do devedor e o credor tem preferência. (Código Civil, arts. 1.451 a 1.460)
  - [norma] Penhor de aplicações financeiras de tomador PJ ou MEI em recuperação judicial vai para conta vinculada; a forma fiduciária fica fora da recuperação. (Lei 11.101/2005, art. 49, §§3º e 5º)
- **Infraestrutura:** B3 Balcão
  - [norma] O CDB é ativo financeiro registrado em registradora (B3, Balcão): a registradora registra o gravame; o custodiante comanda. (Res. CMN 4.593/2017, art. 2º)
  - [norma] O participante que é parte da operação registra o Instrumento de Constituição de Gravame; a contraparte confirma e, se recusar ou não responder, o ativo é liberado. (Regulamento do Balcão B3 (21/09/2026) e Manual de Normas B3 (31/08/2026), Regulamento art. 97, II; Manual arts. 88 e 94)
- **Produto:** Cartão e empréstimo (cartão só com liquidez diária)
  - [prática de mercado] Cartão só com liquidez diária; empréstimo nos demais casos.
- **Prazo:** custódia na casa: no mesmo dia; transferência de custódia (fora da CVM 210): 5 dias úteis (ilustrativo do projeto)
  - [prática de mercado] Gravame no dia; transferência de custódia sem prazo legal encontrado.
- **Risco de execução:**
  - [prática de mercado] Médio: o resgate antecipado depende do emissor ou de mercado secundário; resta o risco do emissor.
  - [norma] Se o emissor quebrar, o FGC paga ao titular registrado, não ao credor. (Regulamento do FGC (Res. CMN 4.222/2013), art. 2º, §4º, I)
  - [interpretação] O limite por emissor do projeto fica dentro do teto do FGC.
- **Norma e artigo:** Res. CMN 5.050/2022, art. 10, I; Lei 4.728/1965, art. 66-B, §§3º e 4º; Código Civil, arts. 1.451 a 1.460; Lei 11.101/2005, art. 49, §§3º e 5º; Res. CMN 4.593/2017, art. 2º; Regulamento do Balcão B3 (21/09/2026) e Manual de Normas B3 (31/08/2026), Regulamento art. 97, II; Manual arts. 88 e 94; Regulamento do FGC (Res. CMN 4.222/2013), art. 2º, §4º, I; Res. BCB 352/2023, art. 81, II, "c"; Lei 11.033/2004, art. 1º

#### LCI

- **O que funciona:**
  - [prática de mercado] Funciona dentro de casa como o CDB de outro banco: custodiada na CTVM do grupo, com gravame registrado na B3.
- **Trilho:** custódia na casa, transferência de custódia (fora da CVM 210)
  - [prática de mercado] De fora, igual ao CDB de outro banco: transferência de custódia antes do gravame, fora da Res. CVM 210.
- **Instrumento:** cessão fiduciária, penhor
  - [norma] Cessão fiduciária: no atraso, o credor vende sem leilão nem ordem judicial, abate a dívida e devolve o saldo com demonstrativo. (Lei 4.728/1965, art. 66-B, §§3º e 4º)
- **Infraestrutura:** B3 Balcão
  - [norma] A LCI é ativo financeiro registrado em registradora (B3, Balcão): a registradora registra o gravame; o custodiante comanda. (Res. CMN 4.593/2017, art. 2º)
- **Produto:** Só empréstimo
  - [interpretação] Só empréstimo, com vencimento casado: a última parcela vence até o vencimento do título.
- **Prazo:** custódia na casa: no mesmo dia; transferência de custódia (fora da CVM 210): 5 dias úteis (ilustrativo do projeto)
  - [prática de mercado] Igual ao CDB: gravame no dia na casa; transferência de custódia sem prazo legal encontrado.
- **Risco de execução:**
  - [não verificado] Alto antes da carência: o emissor não pode recomprar nem resgatar antes do prazo mínimo de 6 meses, ou 36 se atualizada por índice de preços. O artigo não foi conferido. (Res. CMN 4.410/2015 (alterada pela Res. CMN 5.215/2025))
- **Norma e artigo:** Lei 4.728/1965, art. 66-B, §§3º e 4º; Res. CMN 4.593/2017, art. 2º; Res. CMN 4.410/2015 (alterada pela Res. CMN 5.215/2025); Res. BCB 352/2023, art. 81, III, "b"; Lei 11.033/2004, art. 3º

#### LCA

- **O que funciona:**
  - [prática de mercado] Funciona dentro de casa como o CDB de outro banco: custodiada na CTVM do grupo, com gravame registrado na B3.
- **Trilho:** custódia na casa, transferência de custódia (fora da CVM 210)
  - [prática de mercado] De fora, igual ao CDB de outro banco: transferência de custódia antes do gravame, fora da Res. CVM 210.
- **Instrumento:** cessão fiduciária, penhor
  - [norma] Cessão fiduciária: no atraso, o credor vende sem leilão nem ordem judicial, abate a dívida e devolve o saldo com demonstrativo. (Lei 4.728/1965, art. 66-B, §§3º e 4º)
- **Infraestrutura:** B3 Balcão
  - [norma] A LCA é ativo financeiro registrado em registradora (B3, Balcão): a registradora registra o gravame; o custodiante comanda. (Res. CMN 4.593/2017, art. 2º)
- **Produto:** Só empréstimo
  - [interpretação] Só empréstimo, com vencimento casado: a última parcela vence até o vencimento do título.
- **Prazo:** custódia na casa: no mesmo dia; transferência de custódia (fora da CVM 210): 5 dias úteis (ilustrativo do projeto)
  - [prática de mercado] Igual ao CDB: gravame no dia na casa; transferência de custódia sem prazo legal encontrado.
- **Risco de execução:**
  - [não verificado] Alto antes da carência: o emissor não pode recomprar nem resgatar antes do prazo mínimo de 6 meses, ou 12 se atualizada por índice de preços. O artigo não foi conferido. (Res. CMN 5.006/2022 (alterada pela Res. CMN 5.215/2025))
- **Norma e artigo:** Lei 4.728/1965, art. 66-B, §§3º e 4º; Res. CMN 4.593/2017, art. 2º; Res. CMN 5.006/2022 (alterada pela Res. CMN 5.215/2025); Res. BCB 352/2023, art. 81, III, "b"; Lei 11.033/2004, art. 3º

#### COE

Não recomendado como garantia.

- **O que funciona:**
  - [prática de mercado] Possível se emitido e custodiado na casa, mas não recomendado como garantia.
- **Trilho:**
  - [norma] Portável em 2 dias úteis só se ofertado publicamente e custodiado por terceiro; o COE distribuído e custodiado pelo próprio emissor fica fora da portabilidade. (Res. CVM 210/2024, art. 1º, parágrafo único, e art. 16)
- **Instrumento:**
  - [interpretação] Nenhum instrumento modelado: o COE é recusado.
- **Infraestrutura:** B3 Balcão
  - [prática de mercado] B3 (Balcão).
- **Produto:** Só 
  - [prática de mercado] Não recomendado: recusado no cartão e no empréstimo.
- **Prazo:**
  - [norma] 2 dias úteis para portar, quando aplicável. (Res. CVM 210/2024, art. 16)
- **Risco de execução:**
  - [prática de mercado] Alto: baixa liquidez e recompra a critério do emissor.
- **Norma e artigo:** Res. CVM 210/2024, art. 1º, parágrafo único, e art. 16; Res. CVM 210/2024, art. 16

#### fundo DI da DTVM do grupo

- **O que funciona:**
  - [prática de mercado] Cotas de fundos DI da DTVM do grupo, com bloqueio de resgate e gravame sobre as cotas. De fora, portar (mesma classe e subclasse) e travar na casa.
- **Trilho:** custódia na casa, portabilidade (Res. CVM 210)
  - [norma] Portabilidade de cotas de fundos, pedida no custodiante de destino; a troca de classe ou subclasse fica fora. (Res. CVM 210/2024, art. 1º, parágrafo único, e art. 8º)
- **Instrumento:** cessão fiduciária, alienação fiduciária
  - [norma] Alienação ou cessão fiduciária de cotas: no atraso, o credor vende sem leilão nem ordem judicial, abate a dívida e devolve o saldo com demonstrativo. (Lei 4.728/1965, art. 66-B, §§3º e 4º)
- **Infraestrutura:** Registradora ou B3, com comando do escriturador
  - [norma] Registradoras podem constituir gravames, inclusive sobre cotas. (Res. CVM 31/2021, art. 36-A)
  - [norma] Na B3, o bloqueio de cotas exige comando do escriturador, salvo distribuição por conta e ordem. (Manual de Normas B3 (31/08/2026), art. 99, §2º)
- **Produto:** Cartão e empréstimo (cartão só com resgate em D+0 ou D+1)
  - [prática de mercado] Cartão só com fundo DI de liquidez imediata (resgate em D+0 ou D+1); empréstimo nos demais casos.
- **Prazo:** custódia na casa: no mesmo dia; portabilidade (Res. CVM 210): 9 dias úteis (prazo da norma)
  - [norma] Até 9 dias úteis para portar, em etapas de 2, 2 e 3 dias. (Res. CVM 210/2024, art. 16)
- **Risco de execução:**
  - [prática de mercado] Baixo em fundo DI com resgate em D+0 ou D+1; alto com cotização longa.
- **Norma e artigo:** Res. CVM 210/2024, art. 1º, parágrafo único, e art. 8º; Lei 4.728/1965, art. 66-B, §§3º e 4º; Res. CVM 31/2021, art. 36-A; Manual de Normas B3 (31/08/2026), art. 99, §2º; Res. CVM 210/2024, art. 16; Res. BCB 352/2023, art. 81, II, "b"; Res. BCB 352/2023, art. 81, III, "b"; Circular BCB 3.809/2016; Lei 11.033/2004, art. 1º

#### Tesouro Selic

- **O que funciona:**
  - [interpretação] Dentro de casa, só se a CTVM do grupo for agente de custódia do TD, o que não tem confirmação pública. De fora, pelo TD Garantia, se o agente de custódia do cliente operar o serviço.
- **Trilho:** TD Garantia (Selic)
  - [norma] TD Garantia: gravame no Selic instruído pelo agente de custódia do investidor; com agentes diferentes, a contraparte confirma. (Portaria MF 1.748/2024 (Regulamento do TD), itens 100, 105, 108 e 109)
- **Instrumento:** alienação fiduciária
  - [interpretação] Alienação fiduciária do título, com execução por venda ao Tesouro; o regulamento do TD não nomeia o instrumento.
- **Infraestrutura:** Selic (TD Garantia)
  - [norma] Selic, conta de ônus e gravames, comandado pela B3. No TD o gravame é sempre universal, não por título. (Res. BCB 409/2024 (Regulamento do Selic), art. 116, §4º, III)
- **Produto:** Cartão e empréstimo (cartão só se a CTVM do grupo for agente de custódia do TD)
  - [prática de mercado] Cartão e empréstimo; no cartão, só se a CTVM do grupo for agente de custódia do TD. No mercado, um player já usa Tesouro em cartão.
- **Prazo:** TD Garantia (Selic): 3 dias úteis (ilustrativo do projeto)
  - [prática de mercado] O regulamento não fixa prazo; a B3 define.
- **Risco de execução:**
  - [prática de mercado] Baixo: a execução é por venda ao Tesouro, com o dinheiro indo ao agente do garantido (Portaria MF 1.748/2024, itens 116 a 119).
- **Norma e artigo:** Portaria MF 1.748/2024 (Regulamento do TD), itens 100, 105, 108 e 109; Res. BCB 409/2024 (Regulamento do Selic), art. 116, §4º, III; Res. BCB 352/2023, art. 81, II, "b"; Circular BCB 3.809/2016; Lei 11.033/2004, art. 1º

#### Tesouro Prefixado

- **O que funciona:**
  - [interpretação] Dentro de casa, só se a CTVM do grupo for agente de custódia do TD, o que não tem confirmação pública. De fora, pelo TD Garantia, se o agente de custódia do cliente operar o serviço.
- **Trilho:** TD Garantia (Selic)
  - [norma] TD Garantia: gravame no Selic instruído pelo agente de custódia do investidor; com agentes diferentes, a contraparte confirma. (Portaria MF 1.748/2024 (Regulamento do TD), itens 100, 105, 108 e 109)
- **Instrumento:** alienação fiduciária
  - [interpretação] Alienação fiduciária do título, com execução por venda ao Tesouro; o regulamento do TD não nomeia o instrumento.
- **Infraestrutura:** Selic (TD Garantia)
  - [norma] Selic, conta de ônus e gravames, comandado pela B3. No TD o gravame é sempre universal, não por título. (Res. BCB 409/2024 (Regulamento do Selic), art. 116, §4º, III)
- **Produto:** Cartão e empréstimo (cartão só se a CTVM do grupo for agente de custódia do TD)
  - [prática de mercado] Cartão e empréstimo; no cartão, só se a CTVM do grupo for agente de custódia do TD.
- **Prazo:** TD Garantia (Selic): 3 dias úteis (ilustrativo do projeto)
  - [prática de mercado] O regulamento não fixa prazo; a B3 define.
- **Risco de execução:**
  - [prática de mercado] Baixo na execução, por venda ao Tesouro; o prefixado longo oscila com a marcação a mercado.
- **Norma e artigo:** Portaria MF 1.748/2024 (Regulamento do TD), itens 100, 105, 108 e 109; Res. BCB 409/2024 (Regulamento do Selic), art. 116, §4º, III; Res. BCB 352/2023, art. 81, II, "b"; Lei 11.033/2004, art. 1º

#### Tesouro IPCA+

- **O que funciona:**
  - [interpretação] Dentro de casa, só se a CTVM do grupo for agente de custódia do TD, o que não tem confirmação pública. De fora, pelo TD Garantia, se o agente de custódia do cliente operar o serviço.
- **Trilho:** TD Garantia (Selic)
  - [norma] TD Garantia: gravame no Selic instruído pelo agente de custódia do investidor; com agentes diferentes, a contraparte confirma. (Portaria MF 1.748/2024 (Regulamento do TD), itens 100, 105, 108 e 109)
- **Instrumento:** alienação fiduciária
  - [interpretação] Alienação fiduciária do título, com execução por venda ao Tesouro; o regulamento do TD não nomeia o instrumento.
- **Infraestrutura:** Selic (TD Garantia)
  - [norma] Selic, conta de ônus e gravames, comandado pela B3. No TD o gravame é sempre universal, não por título. (Res. BCB 409/2024 (Regulamento do Selic), art. 116, §4º, III)
- **Produto:** Cartão e empréstimo (cartão só se a CTVM do grupo for agente de custódia do TD)
  - [prática de mercado] Cartão e empréstimo; no cartão, só se a CTVM do grupo for agente de custódia do TD.
- **Prazo:** TD Garantia (Selic): 3 dias úteis (ilustrativo do projeto)
  - [prática de mercado] O regulamento não fixa prazo; a B3 define.
- **Risco de execução:**
  - [prática de mercado] Baixo na execução, por venda ao Tesouro; o IPCA+ longo oscila com a marcação a mercado.
- **Norma e artigo:** Portaria MF 1.748/2024 (Regulamento do TD), itens 100, 105, 108 e 109; Res. BCB 409/2024 (Regulamento do Selic), art. 116, §4º, III; Res. BCB 352/2023, art. 81, II, "b"; Lei 11.033/2004, art. 1º

#### ações

- **O que funciona:**
  - [prática de mercado] Dentro de casa: gravame na Central Depositária da B3 via custodiante. De fora: portar para a CTVM do grupo e travar.
- **Trilho:** custódia na casa, portabilidade (Res. CVM 210)
  - [norma] Portabilidade em 2 dias úteis, pedida no custodiante de destino. (Res. CVM 210/2024, arts. 8º e 16)
- **Instrumento:** alienação fiduciária, penhor
  - [norma] Alienação fiduciária e penhor de ações. (Lei 6.404/1976, arts. 39 e 40)
- **Infraestrutura:** B3 Central Depositária
  - [norma] Gravames seguem as regras do depositário central; a movimentação depende de instrução do custodiante. (Res. CVM 31/2021, arts. 35 e 36)
- **Produto:** Só empréstimo
  - [interpretação] Só empréstimo, com desconto de 50% a 70% sobre o valor, em linha com usar no cartão só colateral que a casa controla.
- **Prazo:** custódia na casa: no mesmo dia; portabilidade (Res. CVM 210): 2 dias úteis (prazo da norma)
  - [norma] 2 dias úteis para portar. (Res. CVM 210/2024, art. 16)
- **Risco de execução:**
  - [prática de mercado] Alto: volatilidade e liquidação em D+2; o cliente apura o IR sobre o ganho.
- **Norma e artigo:** Res. CVM 210/2024, arts. 8º e 16; Lei 6.404/1976, arts. 39 e 40; Res. CVM 31/2021, arts. 35 e 36; Res. CVM 210/2024, art. 16; Res. BCB 352/2023, art. 81, II, "b", e III, "b"; Circular BCB 3.809/2016; Lei 11.033/2004, art. 2º, II e §1º, e art. 3º, I

#### FII

- **O que funciona:**
  - [prática de mercado] Dentro de casa: gravame na Central Depositária da B3 via custodiante. De fora: portar para a CTVM do grupo e travar.
- **Trilho:** custódia na casa, portabilidade (Res. CVM 210)
  - [norma] Portabilidade em 2 dias úteis, pedida no custodiante de destino. (Res. CVM 210/2024, arts. 8º e 16)
- **Instrumento:** alienação fiduciária, penhor
  - [norma] Alienação fiduciária de cotas: no atraso, o credor vende sem leilão nem ordem judicial, abate a dívida e devolve o saldo com demonstrativo. (Lei 4.728/1965, art. 66-B, §§3º e 4º)
- **Infraestrutura:** B3 Central Depositária
  - [norma] Gravames seguem as regras do depositário central; a movimentação depende de instrução do custodiante. (Res. CVM 31/2021, arts. 35 e 36)
- **Produto:** Só empréstimo
  - [interpretação] Só empréstimo, com desconto de 50% a 70% sobre o valor.
- **Prazo:** custódia na casa: no mesmo dia; portabilidade (Res. CVM 210): 2 dias úteis (prazo da norma)
  - [norma] 2 dias úteis para portar. (Res. CVM 210/2024, art. 16)
- **Risco de execução:**
  - [prática de mercado] Alto: volatilidade e liquidação em D+2; o cliente apura o IR sobre o ganho.
- **Norma e artigo:** Res. CVM 210/2024, arts. 8º e 16; Lei 4.728/1965, art. 66-B, §§3º e 4º; Res. CVM 31/2021, arts. 35 e 36; Res. CVM 210/2024, art. 16; Res. BCB 352/2023, art. 81, II, "b", e III, "b"; Lei 11.033/2004, art. 2º, §1º

#### ETF

- **O que funciona:**
  - [prática de mercado] Dentro de casa: gravame na Central Depositária da B3 via custodiante. De fora: portar para a CTVM do grupo e travar.
- **Trilho:** custódia na casa, portabilidade (Res. CVM 210)
  - [norma] Portabilidade em 2 dias úteis, pedida no custodiante de destino. (Res. CVM 210/2024, arts. 8º e 16)
- **Instrumento:** alienação fiduciária, penhor
  - [norma] Alienação fiduciária de cotas: no atraso, o credor vende sem leilão nem ordem judicial, abate a dívida e devolve o saldo com demonstrativo. (Lei 4.728/1965, art. 66-B, §§3º e 4º)
- **Infraestrutura:** B3 Central Depositária
  - [norma] Gravames seguem as regras do depositário central; a movimentação depende de instrução do custodiante. (Res. CVM 31/2021, arts. 35 e 36)
- **Produto:** Só empréstimo
  - [interpretação] Só empréstimo, com desconto de 50% a 70% sobre o valor.
- **Prazo:** custódia na casa: no mesmo dia; portabilidade (Res. CVM 210): 2 dias úteis (prazo da norma)
  - [norma] 2 dias úteis para portar. (Res. CVM 210/2024, art. 16)
- **Risco de execução:**
  - [prática de mercado] Alto: volatilidade e liquidação em D+2; o cliente apura o IR sobre o ganho.
- **Norma e artigo:** Res. CVM 210/2024, arts. 8º e 16; Lei 4.728/1965, art. 66-B, §§3º e 4º; Res. CVM 31/2021, arts. 35 e 36; Res. CVM 210/2024, art. 16; Res. BCB 352/2023, art. 81, II, "b", e III, "b"; Lei 11.033/2004, art. 2º, §1º

#### previdência (PGBL, VGBL) e capitalização

- **O que funciona:**
  - [norma] De fora, trilho obrigatório e sem recusa: a entidade operadora não pode recusar pedido que siga as normas. (Res. Conjunta 12/2024, art. 18)
  - [norma] Vale para PGBL e VGBL de contribuição variável e para capitalização tradicional; o FAPI fica fora. (Res. Conjunta 12/2024, art. 1º, §1º)
- **Trilho:** direito de resgate (Res. Conjunta 12)
  - [norma] Direito de resgate em garantia de crédito de instituição financeira, por plataforma de infraestrutura autorizada. (Res. Conjunta 12/2024, arts. 1º, §2º, VII, e 10)
- **Instrumento:** direito de resgate
  - [norma] Após 90 dias de atraso, a instituição financeira pede o resgate; a entidade operadora resgata o valor bloqueado e paga líquido de tributos e carregamento. (Res. Conjunta 12/2024, arts. 12 e 13)
- **Infraestrutura:** Plataforma da Res. Conjunta 12 (simulada)
  - [norma] Plataforma de infraestrutura autorizada para a troca de informações. (Res. Conjunta 12/2024, art. 10)
  - [imprensa] Uma plataforma privada opera a troca de informações.
  - [não verificado] O RC Garantias da B3 foi anunciado; a operação não tem confirmação oficial.
- **Produto:** Só empréstimo
  - [norma] Só empréstimo ou financiamento com dívida previamente estabelecida; ficam fora os limites rotativos de conta e de cartão. (Res. Conjunta 12/2024, art. 1º, §2º, VII)
- **Prazo:** direito de resgate (Res. Conjunta 12): 2 dias úteis (prazo da norma)
  - [norma] Informações em até 2 dias úteis; bloqueio na formalização. (Res. Conjunta 12/2024, art. 4º, §1º, e art. 8º)
  - [norma] O prazo do crédito não pode passar do fim do período de acumulação do plano. (Res. Conjunta 12/2024, art. 6º, §3º)
- **Risco de execução:**
  - [norma] Execução só após 90 dias de inadimplemento, salvo pedido do cliente. (Res. Conjunta 12/2024, art. 12)
  - [norma] Aviso obrigatório ao cliente aos 45 dias de atraso e 10 dias antes da liquidação. (Res. Conjunta 12/2024, art. 6º, §2º, IV)
  - [prática de mercado] O valor do plano oscila e há IR no resgate.
- **Norma e artigo:** Res. Conjunta 12/2024, art. 18; Res. Conjunta 12/2024, art. 1º, §1º; Res. Conjunta 12/2024, arts. 1º, §2º, VII, e 10; Res. Conjunta 12/2024, arts. 12 e 13; Res. Conjunta 12/2024, art. 10; Res. Conjunta 12/2024, art. 1º, §2º, VII; Res. Conjunta 12/2024, art. 4º, §1º, e art. 8º; Res. Conjunta 12/2024, art. 6º, §3º; Res. Conjunta 12/2024, art. 12; Res. Conjunta 12/2024, art. 6º, §2º, IV; Res. Conjunta 12/2024, art. 13

### Trilhos de entrada

#### custódia na casa

- **Rota:** `POST /v1/contas/{conta_id}/garantias`
- **Prazo:** prazo por ativo ou sem prazo; não pode recusar
  - [prática de mercado] Ativo custodiado na CTVM do grupo, ou cotas de fundo DI da DTVM do grupo, com gravame registrado na B3 a favor da SCD do grupo, no mesmo dia.
  - [norma] O gravame sobre ativo registrado ou depositado só tem eficácia perante terceiros se constituído na registradora ou no depositário central; contrato sozinho não basta. (Lei 12.810/2013 (redação da Lei 13.476/2017), art. 26)
  - [interpretação] O limite só é liberado depois da confirmação do registro do gravame.

#### portabilidade (Res. CVM 210)

- **Rota:** `POST /v1/contas/{conta_id}/portabilidades`
- **Prazo:** prazo por ativo ou sem prazo; pode recusar
- **Prazos por ativo:** acoes: 2 dias úteis, fii: 2 dias úteis, etf: 2 dias úteis, fundo_di: 9 dias úteis
  - [norma] Portabilidade em 2 dias úteis para valores mobiliários em depósito centralizado (ações, FIIs, ETFs) e em até 9 dias úteis para cotas de fundos. (Res. CVM 210/2024, art. 16)
  - [norma] A portabilidade pode ser pedida no custodiante de destino. (Res. CVM 210/2024, art. 8º)
  - [norma] Ativo dado em garantia é impedimento à portabilidade; o art. 19, §1º traz exemplos de impedimento, não uma lista fechada. (Res. CVM 210/2024, art. 19, §1º, IV, "b")
  - [interpretação] Pelo entendimento corrente, CDB, LCI, LCA e Tesouro ficam fora da Res. CVM 210, porque não são valores mobiliários. (Lei 6.385/1976, art. 2º, §1º)
  - [interpretação] Sem resgate nem alienação, a portabilidade não gera fato gerador de IR; isso não vem da CVM.

#### transferência de custódia (fora da CVM 210)

- **Rota:** `POST /v1/contas/{conta_id}/transferencias-de-custodia`
- **Prazo:** 5 dias úteis, não regulatório; pode recusar
  - [prática de mercado] CDB, LCI e LCA de outro banco vêm para a CTVM do grupo por pedido de transferência de custódia; não encontramos prazo regulatório para ela.
  - [prática de mercado] Não encontramos prazo regulatório para a transferência de custódia; a B3 define.
  - [interpretação] O número de 5 dias úteis é ilustrativo do projeto e configurável.
  - [norma] O CDB é título nominativo, transferível por endosso ou por lançamento no sistema. (Lei 13.986/2020, arts. 34 e 35)
  - [prática de mercado] O CDB do próprio emissor, emitido e custodiado por ele, costuma não ser portável.
  - [interpretação] Transferir a custódia sem trocar o titular não é alienação, liquidação, resgate, cessão nem repactuação: não há retenção de IR na transferência.

#### TD Garantia (Selic)

- **Rota:** `POST /v1/contas/{conta_id}/garantias/tesouro`
- **Prazo:** 3 dias úteis, não regulatório; pode recusar
  - [norma] O gravame é instruído pelo agente de custódia do investidor nos sistemas da B3 e registrado no Selic. (Portaria MF 1.748/2024 (Regulamento do TD), itens 108 e 109)
  - [norma] Quando os agentes de custódia do garantidor e do garantido são diferentes, a contraparte confirma. (Portaria MF 1.748/2024 (Regulamento do TD), itens 100 e 105)
  - [interpretação] A recusa acontece quando o agente de custódia do cliente não opera o serviço ou não confirma.
  - [prática de mercado] Não encontramos prazo regulatório para o gravame do TD Garantia; a B3 define.
  - [interpretação] O número de 3 dias úteis é ilustrativo do projeto e configurável.
  - [norma] Execução: o agente do garantido instrui, os títulos são vendidos ao Tesouro e o dinheiro vai ao agente do garantido. (Portaria MF 1.748/2024 (Regulamento do TD), itens 116 a 119)
  - [norma] Juros e amortizações dos títulos gravados vão ao agente de custódia do garantido. (Portaria MF 1.748/2024 (Regulamento do TD), item 127)

#### direito de resgate (Res. Conjunta 12)

- **Rota:** `POST /v1/contas/{conta_id}/garantias/previdencia`
- **Prazo:** 2 dias úteis, regulatório; não pode recusar
  - [norma] A entidade operadora presta as informações em até 2 dias úteis. (Res. Conjunta 12/2024, art. 4º, §1º)
  - [norma] A entidade operadora não pode recusar pedido que siga as normas. (Res. Conjunta 12/2024, art. 18)
  - [norma] O bloqueio é simultâneo à formalização da operação de crédito. (Res. Conjunta 12/2024, art. 8º)
  - [norma] O valor bloqueado não pode ser resgatado, portado nem transferido. (Res. Conjunta 12/2024, art. 9º)
  - [norma] A troca de informações passa por plataforma de infraestrutura autorizada, exigida desde 30/09/2025. (Res. Conjunta 12/2024, art. 10)

### Regras gerais

- [não verificado] Após a quitação, a baixa do gravame deve ser pedida em até 2 dias úteis. A regra é da previdência; o documento recomenda adotá-la para todos os ativos, sem citar o artigo.
- [norma] O FGC garante até R$ 250 mil por titular e conglomerado. (Regulamento do FGC (Res. CMN 4.222/2013), art. 2º, §2º)
- [norma] O beneficiário da garantia do FGC é o titular do crédito, não o credor da operação garantida. (Regulamento do FGC (Res. CMN 4.222/2013), art. 2º, §4º, I)
- [norma] Pisos de provisão para inadimplidos, contados a partir do inadimplemento (atraso acima de 90 dias): no 1º mês, C2 30%, C3 45% e C5 50%; no 13º mês, C2 70,8%, C3 89,4% e C5 90,8%. (Res. CMN 4.966/2021 e Res. BCB 352/2023, art. 76, §2º, I (inadimplido: atraso acima de 90 dias); art. 81 e Anexo I)
- [norma] Com mais de uma garantia, vale a carteira de menor provisão. (Res. BCB 352/2023, art. 81, §1º)
- [norma] Crédito sem garantia, como o rotativo, vai para a C5. (Res. BCB 352/2023, art. 81, V, "a")

Teto do FGC: R$ 250000.00 por titular e conglomerado. Baixa do gravame: pedida em até 2 dias úteis depois da quitação, da liberação ou da execução.
<!-- matriz:fim -->

## Fontes

Citadas pelo nome; este documento não traz links.

- Leis: Lei 12.810/2013 (arts. 26 e 26-A, na redação da Lei 13.476/2017); Lei 13.986/2020
  (arts. 30 a 40); Lei 6.385/1976; Lei 4.728/1965 (art. 66-B); Lei 6.404/1976; Lei
  11.101/2005; Lei 11.033/2004.
- CMN, BCB e CNSP: Res. BCB 409/2024 (Regulamento do Selic); Res. BCB 352/2023 e Res. CMN
  4.966/2021 (provisão); Circular BCB 3.809/2016; Regulamento do FGC (Res. CMN 4.222/2013);
  Res. Conjunta 12/2024 (direito de resgate da previdência).
- CVM: Res. CVM 31/2021; Res. CVM 210/2024 (portabilidade).
- Ministério da Fazenda e Tesouro: Portaria MF 1.748, de 08/11/2024 (Regulamento do
  Tesouro Direto e TD Garantia); lançamento do TD Garantia pelo Tesouro Nacional
  (25/11/2024).
- Infraestruturas: B3, Regulamento do Balcão; B3, Manual de Normas dos Subsistemas; B3,
  Manual de Registro de Contrato de Garantia.
- Imprensa: plataforma privada da previdência, segundo o CQCS com dados do Valor.

A data de publicação da Portaria MF 1.748 no DOU, a operação do RC Garantias da B3, a
retomada do Drex, o IR de FIIs e ETFs e a base de IR de PGBL e VGBL aparecem como não
verificados.

## Referências de mercado

Empresas reais aparecem só como referência, com a fonte e a categoria. Não há vínculo com
nenhuma delas e o código do projeto não usa nome nem marca de terceiros. O desenho de cada
produto está em `docs/mercado.md`.

| Quem | O que a fonte diz | Fonte | Categoria |
|---|---|---|---|
| Nubank (Nu Limite Garantido) | Caixinhas (RDB) e Tesouro Selic, IPCA+ e Prefixado; após mais de 7 dias de atraso usa a garantia | Termos do produto (vigência 04/12/2025) e blog | prática de mercado |
| Inter (CDB Mais Limite) | CDB emitido pelo próprio banco, com liquidez diária e FGC; 1:1 | Blog do Inter | prática de mercado |
| Itaú (Cofrinho "Mais limite no Cartão") | R$ 1 investido vira R$ 1 de limite | Página do Itaú sobre limites do cartão | prática de mercado |
| XP (cartão de crédito) | Ativos do próprio grupo, inclusive PGBL e VGBL; alienação ou cessão fiduciária | Termos do cartão | prática de mercado |
| C6 (CDB C6 Limite Garantido) | CDB do próprio banco, 1:1 até R$ 200 mil | Página do produto | prática de mercado |

Todos os players pesquisados aceitam só ativos custodiados no próprio grupo. Não
encontramos BaaS que ofereça publicamente garantia em investimentos por API.

## Vocabulário

<!-- vocabulario:inicio -->
| Evite | Use |
|---|---|
| guardado, guardar | custodiado, custodiar, custodiante |
| bloqueio no app | gravame registrado na B3 ou no Selic (conta gravame, conta de ônus e gravames) |
| corretora do cliente (no Tesouro) | agente de custódia |
| seguradora (na Res. Conjunta 12) | entidade operadora (seguradora, EAPC ou sociedade de capitalização) |
| B3 registra | a registradora ou o depositário central registra; o custodiante comanda |
| parceiro correspondente | entidade tomadora de BaaS |
| portabilidade de CDB pela CVM | transferência de custódia de CDB (fora da CVM 210) |
| garantia elimina o risco | garantia reduz a perda; restam risco do emissor, de preço e de liquidez |
<!-- vocabulario:fim -->
