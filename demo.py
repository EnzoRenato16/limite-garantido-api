"""A história de um cliente, do começo ao fim, no terminal.

    uv run python demo.py

Roda a API de verdade dentro deste processo, contra um SQLite temporário: não precisa de
Docker, de servidor no ar nem de configuração. Cada passo é uma chamada HTTP real; os
webhooks passam pela outbox, são assinados pelo worker e conferidos por um receptor.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
from fastapi.testclient import TestClient
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.api.fabrica import criar_app
from app.config import Ambiente, Configuracao
from app.infra.banco.modelos import Base
from app.infra.banco.sessao import criar_engine
from app.infra.relogio import RelogioFixo
from app.infra.webhooks.assinatura import AssinaturaInvalida, verificar_assinatura
from app.infra.webhooks.entregador import Entregador

ADMIN_TOKEN = "token-ficticio-so-para-esta-demonstracao-0000"
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
LARGURA_DA_REGUA = 44

tela = Console(highlight=False)


def reais(valor: str) -> str:
    inteiro, centavos = f"{Decimal(valor):.2f}".split(".")
    return f"R$ {int(inteiro):,}".replace(",", ".") + f",{centavos}"


class Parceiro:
    """O lado do parceiro: chama a API com a sua chave e recebe os webhooks."""

    def __init__(self, api: TestClient, chave: str) -> None:
        self.api = api
        self.cabecalhos = {"Authorization": f"Bearer {chave}"}
        self.segredo = ""
        self.webhooks: list[dict[str, Any]] = []

    def get(self, caminho: str, **parametros: Any) -> httpx2.Response:
        return self.api.get(f"/v1{caminho}", headers=self.cabecalhos, params=parametros)

    def post(self, caminho: str, corpo: Any = None, chave: str | None = None) -> httpx2.Response:
        cabecalhos = {**self.cabecalhos, "Idempotency-Key": chave or str(uuid.uuid4())}
        resposta = self.api.post(f"/v1{caminho}", json=corpo, headers=cabecalhos)
        tela.print(f"   [dim]POST /v1{caminho} → HTTP {resposta.status_code}[/dim]")
        return resposta

    def receber_webhook(self, requisicao: httpx2.Request, relogio: RelogioFixo) -> httpx2.Response:
        """O que o servidor do parceiro faz: confere a assinatura antes de confiar."""
        try:
            verificar_assinatura(
                self.segredo,
                requisicao.content,
                requisicao.headers.get("X-LG-Assinatura"),
                agora=int(relogio.agora().timestamp()),
            )
        except AssinaturaInvalida:
            return httpx2.Response(401)
        self.webhooks.append(json.loads(requisicao.content))
        return httpx2.Response(204)


def capitulo(numero: int, titulo: str, texto: str) -> None:
    tela.print()
    tela.rule(f"[bold]{numero}. {titulo}[/bold]", align="left", style="cyan")
    tela.print(f"   {texto}")


def regua(limite: dict[str, str], aplicado: str) -> None:
    """Desenha garantia, limite e uso na mesma escala."""
    escala = max(Decimal(aplicado), Decimal(limite["valor_das_garantias"]), Decimal(1))

    def barra(valor: str, cor: str) -> Text:
        cheios = round(Decimal(valor) / escala * LARGURA_DA_REGUA)
        cheios = min(max(cheios, 0), LARGURA_DA_REGUA)
        return Text("█" * cheios, style=cor) + Text(
            "░" * (LARGURA_DA_REGUA - cheios), style="grey35"
        )

    linhas = [
        ("garantia", limite["valor_das_garantias"], "steel_blue1"),
        ("limite", limite["limite_total"], "green3"),
        ("usado", limite["divida_total"], "orange1"),
    ]
    for nome, valor, cor in linhas:
        tela.print(
            Text(f"   {nome:<9}") + barra(valor, cor) + Text(f"  {reais(valor)}", style="bold")
        )
    resumo = f"   disponível para comprar: [bold]{reais(limite['limite_disponivel'])}[/bold]"
    if Decimal(limite["deficit"]) > 0:
        resumo += f"   [bold red]dívida acima do limite: {reais(limite['deficit'])}[/bold red]"
    tela.print(resumo)


def contar_a_historia(
    api: TestClient, entregador: Entregador, parceiro: Parceiro, relogio: RelogioFixo
) -> None:
    conta = parceiro.post("/contas", {"cliente_ref": "cliente-da-demo"}).json()["id"]

    capitulo(
        1,
        "O cliente guarda R$ 5.000 em CDB",
        "O investimento continua rendendo e fica bloqueado como garantia.",
    )
    garantia = parceiro.post(
        f"/contas/{conta}/garantias", {"tipo_ativo": "cdb", "valor": "5000.00"}
    ).json()
    tela.print(
        f"   LTV do CDB na política deste parceiro: {Decimal(garantia['garantia']['ltv']):.0%}. "
        f"Limite liberado: [bold green]{reais(garantia['limite']['limite_total'])}[/bold green]"
    )
    regua(garantia["limite"], "5000.00")

    capitulo(2, "Compra de R$ 4.000 no cartão", "Cabe no limite: aprovada.")
    compra = parceiro.post(
        f"/contas/{conta}/compras", {"valor": "4000.00", "descricao": "Mercado Exemplo"}
    )
    regua(compra.json()["limite"], "5000.00")

    capitulo(3, "Compra de R$ 500", "Só restam R$ 250 de limite: negada, com HTTP 402.")
    chave = str(uuid.uuid4())
    negada = parceiro.post(
        f"/contas/{conta}/compras", {"valor": "500.00", "descricao": "Posto Exemplo"}, chave
    )
    tela.print(
        f"   código de negócio: [bold red]{negada.json()['codigo']}[/bold red] · {negada.json()['detail']}"
    )
    repetida = parceiro.post(
        f"/contas/{conta}/compras", {"valor": "500.00", "descricao": "Posto Exemplo"}, chave
    )
    tela.print(
        "   Repetindo com a mesma Idempotency-Key, a API devolve a decisão original "
        f"(Idempotent-Replayed: {repetida.headers['Idempotent-Replayed']})."
    )

    capitulo(
        4,
        "O CDB cai 10% na marcação a mercado",
        "A garantia encolhe, o limite encolhe junto e a dívida passa dele.",
    )
    queda = parceiro.post(
        f"/sandbox/garantias/{garantia['garantia']['id']}/remarcacao",
        {"variacao_percentual": "-10"},
    ).json()
    regua(queda["limite"], "5000.00")
    chamada = parceiro.get(f"/contas/{conta}").json()["chamada_de_margem"]
    tela.print(
        f"   [bold yellow]Chamada de margem aberta:[/bold yellow] faltam {reais(chamada['deficit_inicial'])}. "
        f"O cliente tem até {chamada['prazo_ate']} para reforçar a garantia ou pagar."
    )

    capitulo(
        5,
        "A fatura fecha e não é paga",
        "Os dias passam. Ninguém reforça a garantia, ninguém paga.",
    )
    parceiro.post(f"/contas/{conta}/faturas", {"vencimento": relogio.hoje().isoformat()})
    tela.print("   [dim]A cada dia o relógio anda e a rotina diária da plataforma roda.[/dim]")
    for dia in range(1, 8):
        relogio.avancar(days=1)
        api.post("/v1/admin/rotinas/virada-do-dia", headers=ADMIN)
        # Entrega os webhooks do dia antes de o relógio andar de novo.
        while entregador.processar_lote().total:
            pass
        estado = parceiro.get(f"/contas/{conta}").json()
        if dia == 1:
            observacao = "fatura em atraso: novas compras suspensas"
        elif (
            estado["estado"] == "bloqueada"
            and estado["motivo_bloqueio"] == "chamada_de_margem_vencida"
        ):
            observacao = "[yellow]prazo da chamada de margem esgotado: conta bloqueada[/yellow]"
        elif estado["fatura_em_aberto"] is None:
            observacao = "[bold red]7º dia de atraso: a garantia é executada[/bold red]"
        else:
            observacao = "[dim]nada muda[/dim]"
        tela.print(f"   {relogio.hoje():%d/%m}  dia {dia}  {observacao}")
    final = parceiro.get(f"/contas/{conta}").json()
    executada = parceiro.get("/eventos", conta_id=conta, tipo="garantia.executada").json()["dados"][
        0
    ]["dados"]
    tela.print(
        f"   Vendidos só os [bold]{reais(executada['valor_executado'])}[/bold] da fatura. "
        f"Os {reais(executada['valor_remanescente'])} que sobraram continuam bloqueados e rendendo."
    )
    regua(final["limite"], "5000.00")

    capitulo(
        6,
        "O que o parceiro recebeu",
        "Cada evento foi gravado na transação da operação e entregue pelo worker.",
    )
    while entregador.processar_lote().total:
        pass
    tabela = Table(box=None, padding=(0, 2), header_style="bold")
    tabela.add_column("webhook")
    tabela.add_column("o que diz")
    tabela.add_column("assinatura HMAC")
    for evento in parceiro.webhooks:
        tabela.add_row(evento["tipo"], _resumo(evento), "[green]confere[/green]")
    tela.print(tabela)

    capitulo(
        7,
        "O ledger por trás",
        "Partidas dobradas: em cada lançamento, débitos e créditos somam o mesmo.",
    )
    extrato = parceiro.get(f"/contas/{conta}/lancamentos", limite=50).json()["dados"]
    tabela = Table(box=None, padding=(0, 2), header_style="bold")
    tabela.add_column("lançamento")
    tabela.add_column("débito")
    tabela.add_column("crédito")
    tabela.add_column("valor", justify="right")
    for lancamento in reversed(extrato):
        debitos = [p for p in lancamento["partidas"] if p["sentido"] == "D"]
        creditos = [p for p in lancamento["partidas"] if p["sentido"] == "C"]
        for indice, (debito, credito) in enumerate(zip(debitos, creditos, strict=True)):
            tabela.add_row(
                lancamento["tipo"] if indice == 0 else "",
                debito["conta_contabil"],
                credito["conta_contabil"],
                reais(debito["valor"]),
            )
    tela.print(tabela)
    verificacao = api.get("/v1/admin/ledger/verificacao", headers=ADMIN).json()
    tela.print(
        f"   Conferência do ledger inteiro: débitos {reais(verificacao['total_de_debitos'])}, "
        f"créditos {reais(verificacao['total_de_creditos'])} → "
        + (
            "[bold green]fecha[/bold green]"
            if verificacao["fecha"]
            else "[bold red]NÃO fecha[/bold red]"
        )
    )


def simular_stress(parceiro: Parceiro) -> None:
    capitulo(
        8,
        "E se o mercado virar amanhã?",
        "Simulador de stress numa carteira de exemplo: ações −30%, prefixado −10%.",
    )
    carteira = [
        ("acoes", "10000.00", "5500.00"),
        ("tesouro_prefixado", "10000.00", "7500.00"),
        ("cdb", "8000.00", "2000.00"),
    ]
    for indice, (tipo, valor, compra) in enumerate(carteira):
        conta = parceiro.api.post(
            "/v1/contas", json={"cliente_ref": f"carteira-{indice}"}, headers=parceiro.cabecalhos
        ).json()["id"]
        for caminho, corpo in (
            (f"/v1/contas/{conta}/garantias", {"tipo_ativo": tipo, "valor": valor}),
            (f"/v1/contas/{conta}/compras", {"valor": compra, "descricao": "Compras"}),
        ):
            parceiro.api.post(
                caminho,
                json=corpo,
                headers={**parceiro.cabecalhos, "Idempotency-Key": str(uuid.uuid4())},
            )
    resultado = parceiro.post(
        "/risco/simulacoes-de-stress", {"choques": {"acoes": "-0.30", "tesouro_prefixado": "-0.10"}}
    ).json()
    tabela = Table(box=None, padding=(0, 2), show_header=False)
    tabela.add_row("contas analisadas", str(resultado["contas_analisadas"]))
    tabela.add_row(
        "entrariam em chamada de margem", f"[bold]{resultado['novas_chamadas_de_margem']}[/bold]"
    )
    tabela.add_row(
        "garantias antes → depois",
        f"{reais(resultado['valor_das_garantias_antes'])} → {reais(resultado['valor_das_garantias_depois'])}",
    )
    tabela.add_row(
        "déficit de margem a cobrar", f"[bold]{reais(resultado['deficit_de_margem_total'])}[/bold]"
    )
    tabela.add_row(
        "dívida sem cobertura nem vendendo tudo", reais(resultado["exposicao_descoberta_total"])
    )
    tela.print(tabela)


def _resumo(evento: dict[str, Any]) -> str:
    dados = evento["dados"]
    match evento["tipo"]:
        case "garantia.bloqueada":
            return f"{reais(dados['valor_de_mercado'])} em {dados['tipo_ativo']} → {reais(dados['limite_gerado'])} de limite"
        case "limite.atualizado":
            return f"limite {reais(dados['limite_total'])}, disponível {reais(dados['limite_disponivel'])}"
        case "compra.aprovada":
            return f"{reais(dados['valor'])} em {dados['descricao']}"
        case "compra.negada":
            return f"{reais(dados['valor'])} em {dados['descricao']}: {dados['motivo']}"
        case "chamada_de_margem":
            return f"{dados['situacao']}: déficit {reais(dados['deficit'])}, prazo até {dados['prazo_ate']}"
        case "fatura.em_atraso":
            return f"{dados['dias_em_atraso']} dia de atraso; execução a partir de {dados['execucao_a_partir_de']}"
        case "garantia.executada":
            return f"vendidos {reais(dados['valor_executado'])}; sobram {reais(dados['valor_remanescente'])}"
        case _:
            return ""


def main() -> None:
    tela.print(
        Panel.fit(
            "[bold]Limite Garantido as a Service[/bold]\n"
            "O investimento do cliente vira limite no cartão, por API, para quem não é banco.\n"
            "[dim]Projeto pessoal de estudo. Dados inventados, sem vínculo com nenhuma instituição.[/dim]",
            border_style="cyan",
        )
    )
    with tempfile.TemporaryDirectory() as pasta:
        url = f"sqlite:///{Path(pasta) / 'demo.db'}"
        config = Configuracao(
            _env_file=None,  # type: ignore[call-arg]
            ambiente=Ambiente.TESTE,
            database_url=url,
            admin_token=ADMIN_TOKEN,  # type: ignore[arg-type]
            sandbox_habilitado=True,
            webhook_permitir_rede_interna=True,
            log_nivel="CRITICAL",
        )
        engine = criar_engine(url)
        Base.metadata.create_all(engine)
        relogio = RelogioFixo(datetime(2026, 3, 2, 13, 0, tzinfo=UTC))
        app = criar_app(config, engine=engine, relogio=relogio)

        with TestClient(app) as api:
            tela.print()
            tela.rule(
                "[bold]0. A plataforma cadastra o parceiro[/bold]", align="left", style="cyan"
            )
            criado = api.post(
                "/v1/admin/parceiros", json={"nome": "Fintech Exemplo"}, headers=ADMIN
            ).json()
            parceiro = Parceiro(api, criado["api_key"])
            prefixo = "_".join(criado["api_key"].split("_")[:2])
            tela.print(
                f"   Parceiro [bold]{criado['parceiro']['nome']}[/bold] criado. API key {prefixo}_•••• "
                "(mostrada uma vez; no banco fica só o hash)."
            )
            parceiro.segredo = api.put(
                "/v1/parceiro/webhook",
                json={"url": "https://fintech.exemplo/webhooks"},
                headers=parceiro.cabecalhos,
            ).json()["segredo"]
            tela.print(
                "   Webhook configurado em https://fintech.exemplo/webhooks, com segredo de assinatura."
            )

            entregador = Entregador(
                app.state.recursos.fabrica_de_sessoes,
                config,
                relogio,
                cliente_http=httpx2.Client(
                    transport=httpx2.MockTransport(lambda r: parceiro.receber_webhook(r, relogio))
                ),
            )
            contar_a_historia(api, entregador, parceiro, relogio)
            simular_stress(parceiro)
        engine.dispose()

    tela.print()
    tela.print(
        Panel.fit(
            "Para ver isto num navegador, com os webhooks chegando ao vivo:\n"
            "[bold]./scripts/dev.sh[/bold]  e abra  [bold]http://localhost:8000/demo[/bold]",
            border_style="cyan",
        )
    )


if __name__ == "__main__":
    main()
