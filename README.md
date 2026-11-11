# Limite Garantido as a Service (v0.1)

Prova de conceito de uma API de **limite de crédito garantido por investimento**: o
cliente aplica, o investimento fica bloqueado como garantia e vira limite no cartão,
calculado por um LTV que depende do tipo de ativo.

> Projeto pessoal de estudo. Não tem vínculo com nenhuma instituição.

## O que esta versão faz

- Bloqueia uma garantia e calcula o limite (`valor de mercado x LTV`, arredondado para baixo).
- Remarca o valor de mercado e avisa quando a dívida passa do limite (chamada de margem).
- Libera a garantia, desde que ela não esteja cobrindo dívida.
- Autoriza compras (HTTP 402 quando falta limite), recebe pagamentos e registra atraso.
- Executa a garantia a partir do 7º dia de atraso, começando pelos ativos mais líquidos.
  A sobra é re-bloqueada.
- `Idempotency-Key` em garantias e compras, e log de eventos em `/v1/eventos`.

Tudo em memória: reiniciou, perdeu.

## Rodando

```bash
pip install -r requirements.txt
pytest
python demo.py
uvicorn app.main:app --reload   # docs em http://localhost:8000/docs
```
