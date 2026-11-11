# Plano de implementação da versão 1.0

> Plano escrito antes de codar, no formato de tarefas pequenas com teste primeiro (TDD).
> As decisões tomadas pelo caminho estão em [DECISOES.md](DECISOES.md).

**Objetivo.** Levar a prova de conceito 0.1 (em memória) a uma API de padrão de mercado:
domínio puro, ledger de partidas dobradas, Postgres, multi-tenant, idempotência persistida,
webhooks com outbox, rotinas de risco, observabilidade, demo e documentação.

**Arquitetura.** Quatro camadas dentro do pacote `app/`:

| Camada | Pasta | Pode importar | Responsabilidade |
|---|---|---|---|
| Domínio | `app/dominio/` | só a biblioteca padrão | Dinheiro, política de risco, garantia (máquina de estados), conta (agregado), ledger, stress |
| Aplicação | `app/aplicacao/` | domínio | Casos de uso e portas (repositórios, relógio, custódia, preços) |
| Infraestrutura | `app/infra/` | domínio, aplicação | SQLAlchemy, Alembic, outbox e worker de webhooks, rotinas, provedor de preços fake, logs, métricas |
| API | `app/api/` | todas | FastAPI: rotas, autenticação, RFC 7807, idempotência, paginação, rate limit, `/demo` |

**Stack.** Python 3.12, FastAPI, SQLAlchemy 2 (síncrono) + psycopg 3, Alembic, Pydantic 2,
httpx2, prometheus-client, Rich. Testes com pytest, Hypothesis e Playwright. Ruff, mypy, pre-commit, uv.

## Restrições globais

- Dinheiro é `Dinheiro` (inteiro em centavos) no domínio e `BIGINT` no banco. Nunca `float`.
- Arredondamento sempre a favor da segurança: limite e valor de garantia para baixo.
- Saldo (dívida, valor de garantia) é sempre derivado do ledger. Não existe coluna de saldo.
- Política de risco (LTV, tetos, prazos) vem de configuração por parceiro, nunca do domínio.
- Código, nomes e docs em português. Nenhuma marca real no código.
- Os 6 testes da 0.1 continuam existindo, adaptados à nova interface.

## Onde um revisor deve olhar com lupa

1. Concorrência na autorização de compra (duas compras simultâneas no mesmo limite).
2. Lançamento desbalanceado no ledger (soma de débitos diferente da de créditos).
3. Idempotência com a mesma chave e payload diferente, e com duas requisições simultâneas.
4. Vazamento entre parceiros (um parceiro lendo ou alterando conta de outro).
5. Execução vendendo mais garantia do que a dívida em atraso.

## Tarefas

### 1. Fundação do projeto
- [ ] `pyproject.toml` com uv, Ruff, mypy (estrito no domínio) e pytest.
- [ ] Estrutura de pastas das quatro camadas.

### 2. Domínio (TDD, sem banco e sem FastAPI)
- [ ] `dinheiro.py`: `Dinheiro` em centavos, soma, subtração, `aplicar_razao` com arredondamento para baixo. Testes de propriedade (ida e volta, piso).
- [ ] `politica.py`: `TipoAtivo`, `PoliticaRisco` validada (LTV entre 0 e 1, prazos positivos, ordem de execução).
- [ ] `ledger.py`: `Lancamento` e `Partida`; um lançamento só existe se débitos = créditos.
- [ ] `garantia.py`: máquina de estados `pendente → bloqueada → liberada | executada | parcialmente_executada`.
- [ ] `conta.py`: agregado com bloqueio, remarcação, liberação, compra, fechamento de fatura, pagamento, virada do dia (atraso, execução, prazo da chamada de margem).
- [ ] `stress.py`: choque por tipo de ativo sobre uma carteira.
- [ ] Teste com máquina de estados do Hypothesis: limite nunca negativo, ledger sempre fecha, execução nunca passa da dívida.

### 3. Persistência
- [ ] Modelos SQLAlchemy e migração Alembic inicial.
- [ ] Repositório da conta: carrega com lock de linha e saldos agregados do ledger; grava lançamentos, eventos (outbox) e versão.
- [ ] Fixtures de teste para SQLite e, quando `DATABASE_URL_TESTE` aponta para Postgres, para Postgres.

### 4. Aplicação e API
- [ ] Autenticação por API key (hash), isolamento por parceiro e API administrativa.
- [ ] Erros RFC 7807 com catálogo de códigos em `/v1/erros`.
- [ ] Idempotência persistida (expiração, conflito de payload, corrida).
- [ ] Paginação por cursor, rate limit por parceiro.
- [ ] Rotas de contas, garantias, compras, faturas, pagamentos, eventos, extrato do ledger.
- [ ] Testes de integração, incluindo os 6 da 0.1 adaptados.

### 5. Webhooks
- [ ] Evento gravado na mesma transação da operação (outbox).
- [ ] Worker: assinatura HMAC-SHA256 com timestamp, backoff exponencial, dead letter, reenvio manual.
- [ ] Validação da URL de destino contra SSRF.

### 6. Risco
- [ ] Provedor de preços fake e determinístico atrás de uma interface.
- [ ] Rotina de marcação a mercado e rotina de virada do dia.
- [ ] Chamada de margem com prazo e bloqueio da conta.
- [ ] Simulador de stress por endpoint.
- [ ] Teto de limite por cliente e por parceiro.

### 7. Observabilidade e qualidade
- [ ] Logs JSON com `request_id` e `parceiro_id`; `/health` e `/metrics`.
- [ ] Teste de concorrência contra Postgres.
- [ ] Cobertura do domínio acima de 90%, Ruff, mypy, pre-commit, GitHub Actions.

### 8. Demonstração e entrega
- [ ] `demo.py` com Rich e página `/demo`.
- [ ] Fluxo do `/demo` testado com Playwright, screenshots em `docs/img/`.
- [ ] Dockerfile, `docker compose` (API, Postgres, worker, seed).
- [ ] Documentação: README, arquitetura, mercado, roadmap, riscos, decisões, roteiro do vídeo, post.
- [ ] Revisão final (crédito, API, segurança) e correções.
