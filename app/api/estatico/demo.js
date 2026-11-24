// Página /demo: um app de parceiro de mentira falando com a API de verdade.
// Sem framework. Todo texto vindo da API entra por textContent, nunca por innerHTML.
// Nada aqui integra com B3, Selic ou instituição alguma: o registro e as contrapartes
// são simulados pela API, e a página diz isso onde aparece cada simulador.

const NOMES = {
  cdb: "CDB",
  lci: "LCI",
  lca: "LCA",
  coe: "COE",
  fundo_di: "Fundo DI",
  tesouro_selic: "Tesouro Selic",
  tesouro_prefixado: "Tesouro Prefixado",
  tesouro_ipca: "Tesouro IPCA+",
  acoes: "Ações",
  fii: "FII",
  etf: "ETF",
  previdencia: "Previdência",
};

// Estado do gravame (o registro simulado), que é o estado da garantia na API.
const ESTADOS = {
  solicitado: "gravame solicitado",
  registrado: "gravame registrado",
  liberado_para_excussao: "liberado para excussão",
  executado: "garantia executada",
  baixado: "gravame baixado",
  recusado: "recusado",
  cancelado: "cancelado",
};

const INFRAESTRUTURAS = {
  b3_balcao: "B3 Balcão",
  b3_central_depositaria: "B3 Central Depositária",
  selic_td_garantia: "Selic (TD Garantia)",
  registradora_ou_b3_escriturador: "registradora ou B3",
  plataforma_res_conjunta_12: "plataforma da Res. Conjunta 12",
};
const INSTRUMENTOS = {
  cessao_fiduciaria: "cessão fiduciária",
  alienacao_fiduciaria: "alienação fiduciária",
  penhor: "penhor",
  direito_de_resgate: "direito de resgate",
};

const ESTADOS_DO_EMPRESTIMO = {
  ativo: "em dia",
  em_atraso: "em atraso",
  quitado: "quitado",
  liquidado_por_execucao: "liquidado pela execução da garantia",
};
const ENCERRADOS = ["quitado", "liquidado_por_execucao"];

// Ativo, produto e contraparte que cada trilho oferece de partida.
const PADRAO_DO_TRILHO = {
  portabilidade_cvm_210: { ativo: "acoes", produto: "emprestimo" },
  transferencia_de_custodia: { ativo: "cdb", produto: "cartao" },
  td_garantia: { ativo: "tesouro_selic", produto: "emprestimo" },
  res_conjunta_12: { ativo: "previdencia", produto: "emprestimo" },
};
const ATIVOS_DE_FORA = [
  "acoes", "fii", "etf", "fundo_di", "cdb", "lci", "lca",
  "tesouro_selic", "tesouro_prefixado", "tesouro_ipca", "previdencia",
];

const MOTIVOS = {
  limite_insuficiente: "limite insuficiente",
  conta_bloqueada: "conta bloqueada",
  fatura_em_atraso: "fatura em atraso",
  teto_do_parceiro_excedido: "teto do parceiro atingido",
};

const DIAS_PARA_EXECUCAO_PADRAO = 7;
const INTERVALO_DOS_WEBHOOKS_EM_MS = 700;
const INTERVALO_DE_RECONSULTA_EM_MS = 1500;
const ANOS_DE_ACUMULACAO = 14;
const CHAVE_DA_CONTA = "lg-demo-conta";
const MENSAGENS = [
  "resultado-garantia", "resultado-compra", "resultado-queda", "resultado-fatura",
  "resultado-fora", "resultado-emprestimo", "resultado-relogio",
];

const moeda = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const hora = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...filhos) => {
  const no = Object.assign(document.createElement(tag), props);
  no.append(...filhos);
  return no;
};

const estado = {
  chave: null,
  hoje: null,
  politica: null,
  matriz: null,
  contaId: null,
  conta: null,
  garantias: [],
  emprestimos: [],
  contrapartes: [],
  demonstrativos: {},
  ultimoWebhook: 0,
  ultimaAtualizacao: 0,
  // Cada `atualizar` ganha um número. Só a mais recente desenha: uma consulta antiga
  // que responde por último não pode apagar da tela o que acabou de acontecer.
  geracao: 0,
  // A última consulta falhou: a próxima volta da consulta periódica tenta de novo.
  desatualizado: false,
  // Para cada mensagem na tela, a "foto" do estado a que ela se refere. Quando o estado
  // muda, a mensagem velha some: um aviso de chamada de margem não pode continuar na
  // tela depois que a garantia foi executada.
  mensagens: {},
};

// ------------------------------------------------------------------ dinheiro
// Só para mostrar na tela. Quem faz conta com dinheiro é a API, em centavos inteiros.
const reais = (texto) => moeda.format(Number(texto));

// "5.000,00" (como a pessoa digita) -> "5000.00" (como a API espera). Sem float.
function paraDecimal(digitado) {
  const limpo = digitado.trim().replace(/^R\$\s*/, "").replace(/\./g, "").replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(limpo)) return null;
  const [inteiro, fracao = ""] = limpo.split(".");
  return `${inteiro}.${fracao.padEnd(2, "0")}`;
}

// ------------------------------------------------------------------- datas
// Datas de negócio chegam como "2026-11-24" (ou datetime ISO). Só o texto é recortado:
// nenhuma conta de calendário é feita aqui, exceto somar dias corridos para o vencimento
// da primeira parcela, em UTC, para não depender do fuso do navegador.
function dataCurta(iso) {
  const [, mes, dia] = iso.slice(0, 10).split("-");
  return `${dia}/${mes}`;
}
function dataCompleta(iso) {
  const [ano, mes, dia] = iso.slice(0, 10).split("-");
  return `${dia}/${mes}/${ano}`;
}
function somarDias(iso, dias) {
  const [a, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(a, m - 1, d + dias)).toISOString().slice(0, 10);
}
function diasEntre(inicio, fim) {
  const ms = (iso) => Date.parse(`${iso.slice(0, 10)}T00:00:00Z`);
  return Math.round((ms(fim) - ms(inicio)) / 86_400_000);
}
const diasUteis = (n) => (n === 1 ? "1 dia útil" : `${n} dias úteis`);

// ----------------------------------------------------------------------- API
async function chamar(metodo, caminho, corpo, { idempotente = false } = {}) {
  const cabecalhos = { Authorization: `Bearer ${estado.chave}` };
  if (corpo !== undefined) cabecalhos["Content-Type"] = "application/json";
  // Toda operação com dinheiro leva uma chave única: se a rede falhar e o navegador
  // repetir, a API reconhece e não executa duas vezes.
  if (idempotente) cabecalhos["Idempotency-Key"] = crypto.randomUUID();
  const resposta = await fetch(`/v1${caminho}`, {
    method: metodo,
    headers: cabecalhos,
    body: corpo === undefined ? undefined : JSON.stringify(corpo),
  });
  let dados = null;
  if (resposta.status !== 204) {
    // Um 502 do proxy chega em HTML: vira erro legível em vez de quebrar o botão.
    dados = await resposta.json().catch(() => ({ detail: `Erro HTTP ${resposta.status}` }));
  }
  return { ok: resposta.ok, status: resposta.status, dados };
}

const PENDENTE = Symbol("ainda sem foto");

function mostrar(id, texto, tipo = "") {
  const alvo = $(id);
  alvo.textContent = texto;
  alvo.className = `resultado ${tipo}`.trim();
  // A foto é tirada no próximo `atualizar`, que é quando o efeito da ação aparece.
  estado.mensagens[id] = texto ? PENDENTE : undefined;
}

function fotoDoEstado() {
  const { conta } = estado;
  if (!conta) return "";
  return JSON.stringify([
    conta.estado,
    conta.motivo_bloqueio,
    conta.chamada_de_margem?.id ?? null,
    conta.fatura_em_aberto?.estado ?? null,
    conta.fatura_em_aberto?.dias_em_atraso ?? null,
    conta.limite.divida_total,
    conta.limite.valor_das_garantias,
    estado.garantias.map((g) => `${g.id}:${g.estado}`),
    estado.emprestimos.map((e) => `${e.id}:${e.estado}:${e.dias_em_atraso}:${e.saldo_devedor}`),
  ]);
}

function conferirMensagens() {
  const foto = fotoDoEstado();
  for (const [id, fotoDaMensagem] of Object.entries(estado.mensagens)) {
    if (fotoDaMensagem === undefined) continue;
    if (fotoDaMensagem === PENDENTE) {
      estado.mensagens[id] = foto;
    } else if (fotoDaMensagem !== foto) {
      const alvo = $(id);
      alvo.textContent = "";
      alvo.className = "resultado";
      estado.mensagens[id] = undefined;
    }
  }
}

// A API explica o erro (`detail`) e, quando sabe, diz o que fazer (`sugestao`).
function erroDe(resposta) {
  const d = resposta.dados;
  const detalhe = d?.detail ?? `Erro HTTP ${resposta.status}`;
  return d?.sugestao ? `${detalhe}. Sugestão: ${d.sugestao}` : detalhe;
}

const nomeDaContraparte = (id) => estado.contrapartes.find((c) => c.id === id)?.nome ?? id;
const nomeDoTrilho = (id) => estado.matriz?.trilhos?.[id]?.nome ?? id;

// Prazo do trilho para o ativo, em dias úteis, como o catálogo regulatório diz.
function prazoDoTrilho(trilho, tipo) {
  const regra = estado.matriz?.trilhos?.[trilho];
  if (!regra) return null;
  const porAtivo = regra.prazos_por_ativo ?? {};
  const dias = tipo in porAtivo ? porAtivo[tipo] : regra.prazo_dias_uteis;
  return dias === undefined ? null : { dias, regulatorio: regra.prazo_e_regulatorio };
}

// --------------------------------------------------------------------- régua
const ativasDoCartao = () =>
  estado.garantias.filter((g) => g.estado === "registrado" && g.produto === "cartao");

function desenharRegua() {
  const limite = estado.conta.limite;
  const aplicado = ativasDoCartao().reduce((soma, g) => soma + Number(g.valor_aplicado), 0);
  const garantia = Number(limite.valor_das_garantias);
  const total = Number(limite.limite_total);
  const divida = Number(limite.divida_total);
  const escala = Math.max(aplicado, garantia, divida, 1);
  const pct = (valor) => `${Math.min((valor / escala) * 100, 100)}%`;

  $("trilho-vazio").hidden = aplicado > 0 || garantia > 0 || divida > 0;
  $("barra-aplicado").style.width = pct(aplicado);
  $("barra-garantia").style.width = pct(garantia);
  $("barra-usado").style.width = pct(Math.min(divida, total));
  $("barra-deficit").style.left = pct(total);
  $("barra-deficit").style.width = pct(Math.max(divida - total, 0));
  $("marco-limite").style.left = pct(total);
  $("marco-limite").hidden = total === 0;
  $("marco-limite").classList.toggle("na-borda", total / escala > 0.8);
  $("marco-texto").textContent = garantia > 0 ? `limite · ${Math.round((total / garantia) * 100)}% da garantia` : "limite";

  $("disponivel").textContent = reais(limite.limite_disponivel);
  $("valor-garantia").textContent = reais(limite.valor_das_garantias);
  $("valor-limite").textContent = reais(limite.limite_total);
  $("valor-usado").textContent = reais(limite.divida_total);
  $("valor-deficit").textContent = reais(limite.deficit);
  $("caixa-deficit").hidden = Number(limite.deficit) === 0;
  $("trilho").setAttribute(
    "aria-label",
    `Garantia de ${reais(limite.valor_das_garantias)}, limite de ${reais(limite.limite_total)}, ` +
      `${reais(limite.divida_total)} usados`,
  );
}

function desenharAviso() {
  const { conta } = estado;
  const aviso = $("aviso");
  let texto = "";
  if (conta.estado === "bloqueada") {
    texto =
      conta.motivo_bloqueio === "chamada_de_margem_vencida"
        ? "Conta bloqueada para novas compras: a chamada de margem venceu sem reforço nem pagamento."
        : "Conta bloqueada: a garantia acabou e ainda há fatura vencida.";
  } else if (conta.fatura_em_aberto?.estado === "em_atraso") {
    const dias = conta.fatura_em_aberto.dias_em_atraso;
    texto = `Fatura em atraso há ${dias} dia${dias > 1 ? "s" : ""}. No ${diasParaExecucao()}º dia a garantia é executada.`;
  } else if (conta.chamada_de_margem) {
    texto =
      `Chamada de margem: a dívida passou do limite em ${reais(conta.limite.deficit)}. ` +
      `Reforce a garantia ou pague até ${dataCurta(conta.chamada_de_margem.prazo_ate)}.`;
  }
  aviso.textContent = texto;
  aviso.hidden = texto === "";
}

// ------------------------------------------------------- garantias e gravame
// Linha do tempo do gravame: solicitado, registrado, (excussão), baixa pedida, baixado.
function etapasDoGravame(garantia) {
  const g = garantia.gravame;
  if (g.estado === "recusado" || g.estado === "cancelado") {
    return [
      { rotulo: "Solicitado", classe: "feito" },
      { rotulo: g.estado === "recusado" ? "Recusado" : "Cancelado", classe: "falhou" },
    ];
  }
  const baixa = g.baixa_prazo
    ? `Baixa pedida (até ${dataCompleta(g.baixa_prazo)}, 2 dias úteis)`
    : "Baixa pedida";
  const passos = [
    { chave: "solicitado", rotulo: "Solicitado" },
    { chave: "registrado", rotulo: "Registrado" },
  ];
  if (g.estado === "liberado_para_excussao" || g.estado === "executado") {
    passos.push({
      chave: g.estado,
      rotulo: g.estado === "executado" ? "Executado" : "Liberado para excussão",
    });
  }
  passos.push({ chave: "baixa", rotulo: baixa }, { chave: "baixado", rotulo: "Baixado" });
  const chaveAtual = g.baixa_prazo && g.estado !== "baixado" ? "baixa" : g.estado;
  const indice = passos.findIndex((p) => p.chave === chaveAtual);
  return passos.map((p, i) => {
    let classe = "";
    if (i < indice) classe = "feito";
    else if (i === indice) classe = p.chave === "registrado" || p.chave === "baixado" ? "feito" : "atual";
    return { rotulo: p.rotulo, classe };
  });
}

function linhaDoTempo(garantia) {
  const lista = el("ol", { className: "linha-do-tempo" });
  lista.setAttribute("aria-label", "Andamento do gravame");
  for (const etapa of etapasDoGravame(garantia)) {
    lista.append(el("li", { className: etapa.classe, textContent: etapa.rotulo }));
  }
  return lista;
}

function textoDoPrazo(garantia) {
  const s = garantia.solicitacao;
  if (!s) return "";
  const prazo = prazoDoTrilho(garantia.gravame.trilho, garantia.tipo_ativo);
  const quando = dataCompleta(s.prazo_ate);
  let texto = prazo
    ? `Prazo do trilho: até ${quando} (${diasUteis(prazo.dias)}${prazo.regulatorio ? ", prazo da norma" : ", ilustrativo do projeto"})`
    : `Prazo do trilho: até ${quando}`;
  if (s.prazo_regulatorio_vencido) {
    texto += ". O prazo regulatório venceu sem resposta; o pedido continua e a obrigação é da contraparte";
  }
  return texto;
}

const ACOES_DO_SANDBOX = [
  ["concluir", "Concluir (simulador)"],
  ["recusar", "Recusar (simulador)"],
  ["nao_responder", "Não responder (simulador)"],
];

function botoesDaContraparte(garantia) {
  const acoes = el("div", { className: "acoes" });
  const nome = nomeDaContraparte(garantia.contraparte_id);
  acoes.setAttribute("aria-label", `Simular a resposta de ${nome}`);
  const trilho = garantia.gravame.trilho;
  for (const [acao, rotulo] of ACOES_DO_SANDBOX) {
    // A entidade operadora não pode recusar pedido que siga as normas (Res. Conjunta 12, art. 18).
    if (acao === "recusar" && trilho === "res_conjunta_12") continue;
    const texto =
      acao === "recusar" && trilho === "portabilidade_cvm_210" ? "Informar impedimento (simulador)" : rotulo;
    const botao = el("button", { type: "button", className: "discreto pequeno", textContent: texto });
    botao.addEventListener("click", () => decidirPelaContraparte(garantia, acao, texto));
    acoes.append(botao);
  }
  return acoes;
}

function cartaoDaGarantia(garantia) {
  const g = garantia.gravame;
  const item = el("li");
  item.dataset.garantia = garantia.id;
  item.dataset.estado = garantia.estado;
  item.dataset.trilho = g.trilho;
  const semValor = Number(garantia.valor_de_mercado) === 0 && garantia.estado === "solicitado";
  item.append(
    el("span", { className: "nome", textContent: NOMES[garantia.tipo_ativo] ?? garantia.tipo_ativo }),
    el("span", {
      className: "valor",
      textContent: reais(semValor ? garantia.valor_aplicado : garantia.valor_de_mercado),
    }),
    el("span", {
      className: `estado ${garantia.estado === "executado" ? "executada" : ""}`,
      textContent: ESTADOS[garantia.estado] ?? garantia.estado,
    }),
    el("span", {
      className: "produto-da-garantia",
      textContent: garantia.produto === "cartao" ? "para o cartão" : "para empréstimo",
    }),
    el("span", {
      className: "origem",
      textContent: garantia.contraparte_id
        ? `de ${nomeDaContraparte(garantia.contraparte_id)}`
        : "custodiado na casa",
    }),
  );
  const tecnico =
    `${nomeDoTrilho(g.trilho)} · ${INFRAESTRUTURAS[g.infraestrutura] ?? g.infraestrutura} (simulada) · ` +
    `${INSTRUMENTOS[g.instrumento] ?? g.instrumento}` +
    (g.protocolo ? ` · protocolo ${g.protocolo}` : "");
  item.append(el("p", { className: "tecnico", textContent: tecnico }));
  const prazo = textoDoPrazo(garantia);
  if (prazo) item.append(el("p", { className: "prazo", textContent: prazo }));
  if (g.motivo) item.append(el("p", { className: "motivo", textContent: `Motivo: ${g.motivo}` }));
  if (garantia.informacoes) {
    const i = garantia.informacoes;
    item.append(
      el("p", {
        className: "informacoes",
        textContent:
          `A operadora informou: disponível ${reais(i.valor_disponivel)}, tributos ${reais(i.tributos_informados)}, ` +
          `líquido ${reais(i.valor_liquido)}, que é o que vale como garantia. ` +
          (garantia.estado === "solicitado" ? "O bloqueio nasce ao contratar o empréstimo (art. 8º)." : ""),
      }),
    );
  }
  for (const alerta of garantia.alertas ?? []) {
    item.append(el("p", { className: "alerta", textContent: alerta }));
  }
  item.append(linhaDoTempo(garantia));
  if (garantia.solicitacao) item.append(botoesDaContraparte(garantia));
  if (garantia.estado === "registrado" && garantia.produto === "cartao" && !garantia.emprestimo_id) {
    const resgatar = el("button", { type: "button", className: "discreto pequeno", textContent: "Resgatar" });
    resgatar.addEventListener("click", () => resgatarGarantia(garantia));
    item.append(resgatar);
  }
  return item;
}

function desenharGarantias() {
  $("garantias-da-casa").replaceChildren(
    ...estado.garantias.filter((g) => g.gravame.trilho === "casa").map(cartaoDaGarantia),
  );
  $("garantias-de-fora").replaceChildren(
    ...estado.garantias.filter((g) => g.gravame.trilho !== "casa").map(cartaoDaGarantia),
  );
}

function diasParaExecucao() {
  return estado.politica?.dias_para_execucao ?? DIAS_PARA_EXECUCAO_PADRAO;
}

function desenharDias(diasEmAtraso) {
  [...$("dias").children].forEach((dia, indice) => {
    dia.classList.toggle("passado", indice < diasEmAtraso);
  });
}

function desenharFatura() {
  const fatura = estado.conta.fatura_em_aberto;
  $("fechar-fatura").disabled = Boolean(fatura) || Number(estado.conta.limite.divida_total) === 0;
  $("passar-dia").disabled = !fatura;
  $("pagar").disabled = Number(estado.conta.limite.divida_total) === 0;
  if (fatura) desenharDias(fatura.dias_em_atraso);
}

// ----------------------------------------------------------------- empréstimo
const garantiasDoEmprestimo = () =>
  estado.garantias.filter(
    (g) =>
      g.produto === "emprestimo" &&
      !g.emprestimo_id &&
      (g.estado === "registrado" ||
        (g.tipo_ativo === "previdencia" && g.estado === "solicitado" && g.informacoes)),
  );

function desenharSeletorDeGarantias() {
  const seletor = $("garantia-do-emprestimo");
  const anterior = seletor.value;
  seletor.replaceChildren();
  for (const g of garantiasDoEmprestimo()) {
    const valor = g.informacoes ? g.informacoes.valor_liquido : g.valor_de_mercado;
    seletor.append(
      el("option", {
        value: g.id,
        textContent: `${NOMES[g.tipo_ativo] ?? g.tipo_ativo} · ${reais(valor)}`,
      }),
    );
  }
  if ([...seletor.options].some((o) => o.value === anterior)) seletor.value = anterior;
  $("contratar").disabled = seletor.options.length === 0;
  $("dica-do-emprestimo").textContent =
    seletor.options.length === 0
      ? "Nenhuma garantia de empréstimo pronta. Traga um ativo de fora e conclua o pedido."
      : "";
}

function ehDePrevidencia(emprestimo) {
  return emprestimo.garantia_ids.some(
    (id) => estado.garantias.find((g) => g.id === id)?.tipo_ativo === "previdencia",
  );
}

// Marcos da régua de atraso: na previdência 45 (aviso), 10 dias antes da liquidação (aviso
// final) e a execução; nos demais, só a execução. Res. Conjunta 12, art. 6º, §2º, IV.
function marcosDaRegua(emprestimo) {
  const execucao = emprestimo.dias_minimos_execucao;
  if (!ehDePrevidencia(emprestimo)) return [{ dia: execucao, rotulo: "execução" }];
  return [
    { dia: 45, rotulo: "1º aviso" },
    { dia: Math.max(execucao - 10, 1), rotulo: "aviso final: 10 dias antes" },
    { dia: execucao, rotulo: "execução" },
  ];
}

function reguaDoAtraso(emprestimo) {
  const marcos = marcosDaRegua(emprestimo);
  const limite = marcos[marcos.length - 1].dia;
  const dias = emprestimo.dias_em_atraso;
  const regua = el("div", { className: "regua-de-atraso" });
  regua.setAttribute("role", "img");
  regua.setAttribute("aria-label", `${dias} dias de atraso; execução a partir do ${limite}º dia`);
  const pista = el("div", { className: "pista" });
  pista.append(el("div", { className: "preenchimento" }));
  pista.firstChild.style.width = `${Math.min((dias / limite) * 100, 100)}%`;
  for (const marco of marcos) {
    const m = el(
      "span",
      { className: `marco-de-atraso ${dias >= marco.dia ? "atingido" : ""}` },
      el("b", { textContent: `${marco.dia}º dia` }),
      el("i", { textContent: marco.rotulo }),
    );
    m.dataset.dia = String(marco.dia);
    m.style.left = `${(marco.dia / limite) * 100}%`;
    pista.append(m);
  }
  regua.append(pista, el("p", { className: "dias-de-atraso", textContent: `${dias} dias de atraso` }));
  return regua;
}

function cronograma(emprestimo) {
  const tabela = el("table", { className: "cronograma" });
  tabela.append(
    el(
      "thead",
      {},
      el("tr", {}, ...["Parcela", "Vencimento", "Valor", "Juros", "Amortização", "Situação"].map((t) =>
        el("th", { scope: "col", textContent: t }),
      )),
    ),
  );
  const corpo = el("tbody");
  for (const p of emprestimo.parcelas) {
    corpo.append(
      el(
        "tr",
        {},
        el("td", { textContent: String(p.numero) }),
        el("td", { textContent: dataCompleta(p.vencimento) }),
        el("td", { textContent: reais(p.valor) }),
        el("td", { textContent: reais(p.juros) }),
        el("td", { textContent: reais(p.amortizacao) }),
        el("td", { textContent: p.paga_em ? "paga" : "em aberto" }),
      ),
    );
  }
  tabela.append(corpo);
  return tabela;
}

function demonstrativoDaExecucao(emprestimo) {
  const d = estado.demonstrativos[emprestimo.id];
  if (!d) return null;
  const campos = [
    ["valor_de_mercado", "Valor de mercado da garantia"],
    ["retencao", "Tributos retidos"],
    ["liquido", "Líquido da venda"],
    ["divida_abatida", "Dívida abatida"],
    ["excedente_devolvido", "Excedente devolvido ao cliente"],
  ];
  const lista = el("dl", { className: "demonstrativo" });
  for (const [campo, rotulo] of campos) {
    const dd = el("dd", { textContent: reais(d[campo]) });
    dd.dataset.campo = campo;
    lista.append(el("div", {}, el("dt", { textContent: rotulo }), dd));
  }
  return el(
    "div",
    { className: "demonstrativo-da-execucao" },
    el("h4", { textContent: "Demonstrativo da execução" }),
    lista,
  );
}

function cartaoDoEmprestimo(emprestimo) {
  const item = el("li");
  item.dataset.emprestimo = emprestimo.id;
  item.dataset.estado = emprestimo.estado;
  item.append(
    el("span", { className: "nome", textContent: `Empréstimo de ${reais(emprestimo.principal)}` }),
    el("span", { className: "valor", textContent: `saldo ${reais(emprestimo.saldo_devedor)}` }),
    el("span", {
      className: `estado ${emprestimo.estado === "ativo" ? "" : "executada"}`,
      textContent: ESTADOS_DO_EMPRESTIMO[emprestimo.estado] ?? emprestimo.estado,
    }),
  );
  if (!ENCERRADOS.includes(emprestimo.estado)) {
    const proxima = emprestimo.parcelas.find((p) => !p.paga_em);
    const acoes = el("div", { className: "acoes" });
    const pagar = el("button", { type: "button", className: "discreto pequeno", textContent: "Pagar parcela" });
    pagar.disabled = !proxima;
    pagar.addEventListener("click", () => pagarEmprestimo(emprestimo, "parcela"));
    const quitar = el("button", { type: "button", className: "discreto pequeno", textContent: "Quitar" });
    quitar.addEventListener("click", () => pagarEmprestimo(emprestimo, "saldo"));
    acoes.append(pagar, quitar);
    item.append(acoes);
  }
  item.append(reguaDoAtraso(emprestimo), cronograma(emprestimo));
  const demonstrativo = demonstrativoDaExecucao(emprestimo);
  if (demonstrativo) item.append(demonstrativo);
  return item;
}

function desenharEmprestimos() {
  $("emprestimos").replaceChildren(...estado.emprestimos.map(cartaoDoEmprestimo));
  desenharSeletorDeGarantias();
}

async function buscarDemonstrativos() {
  for (const e of estado.emprestimos) {
    if (e.estado !== "liquidado_por_execucao" || estado.demonstrativos[e.id]) continue;
    const eventos = await chamar("GET", `/eventos?conta_id=${estado.contaId}&tipo=garantia.executada&limite=20`);
    for (const evento of eventos.dados?.dados ?? []) {
      if (evento.dados.emprestimo_id === e.id && evento.dados.demonstrativo) {
        estado.demonstrativos[e.id] = evento.dados.demonstrativo;
      }
    }
  }
}

async function atualizar() {
  estado.ultimaAtualizacao = Date.now();
  const geracao = ++estado.geracao;
  const [conta, garantias, emprestimos] = await Promise.all([
    chamar("GET", `/contas/${estado.contaId}`),
    chamar("GET", `/contas/${estado.contaId}/garantias?limite=50`),
    chamar("GET", `/contas/${estado.contaId}/emprestimos?limite=50`),
  ]);
  if (geracao !== estado.geracao) return;
  estado.desatualizado = !conta.ok || !garantias.ok || !emprestimos.ok;
  if (estado.desatualizado) return;
  estado.conta = conta.dados;
  estado.garantias = garantias.dados.dados.slice().reverse();
  estado.emprestimos = emprestimos.dados.dados.slice().reverse();
  await buscarDemonstrativos();
  if (geracao !== estado.geracao) return;
  desenharRegua();
  desenharAviso();
  desenharGarantias();
  desenharFatura();
  desenharEmprestimos();
  conferirMensagens();
}

// ---------------------------------------------------------------------- ações
async function darEmGarantia(evento) {
  evento.preventDefault();
  const valor = paraDecimal($("valor-da-garantia").value);
  if (!valor) return mostrar("resultado-garantia", "Digite um valor como 5.000,00.", "negado");
  const tipo = $("ativo-da-casa").value;
  // O cartão exige liquidez diária no CDB e até D+1 no fundo DI.
  const corpo = { tipo_ativo: tipo, valor, produto: "cartao", liquidez_dias: tipo === "cdb" ? 0 : 1 };
  const resposta = await chamar("POST", `/contas/${estado.contaId}/garantias`, corpo, { idempotente: true });
  if (!resposta.ok) return mostrar("resultado-garantia", erroDe(resposta), "negado");
  const { garantia } = resposta.dados;
  mostrar(
    "resultado-garantia",
    `${reais(garantia.valor_aplicado)} em ${NOMES[tipo]} com gravame registrado na B3 (simulador). ` +
      `Viraram ${reais(garantia.limite_gerado)} de limite (${Math.round(Number(garantia.ltv) * 100)}%).`,
    "ok",
  );
  await atualizar();
}

async function comprar(evento) {
  evento.preventDefault();
  const valor = paraDecimal($("valor-da-compra").value);
  if (!valor) return mostrar("resultado-compra", "Digite um valor como 4.000,00.", "negado");
  const descricao = $("descricao-da-compra").value.trim() || "Compra";
  const resposta = await chamar(
    "POST",
    `/contas/${estado.contaId}/compras`,
    { valor, descricao },
    { idempotente: true },
  );
  if (resposta.status === 201) {
    mostrar("resultado-compra", `Compra de ${reais(valor)} em ${descricao} aprovada.`, "ok");
  } else if (resposta.status === 402) {
    const motivo = MOTIVOS[resposta.dados.codigo] ?? resposta.dados.codigo;
    mostrar("resultado-compra", `Compra de ${reais(valor)} negada: ${motivo} (HTTP 402).`, "negado");
  } else {
    mostrar("resultado-compra", erroDe(resposta), "negado");
  }
  await atualizar();
}

async function derrubarAtivo() {
  const alvo = ativasDoCartao()[0];
  if (!alvo) return mostrar("resultado-queda", "Dê um investimento em garantia primeiro.", "negado");
  const resposta = await chamar("POST", `/sandbox/garantias/${alvo.id}/remarcacao`, {
    variacao_percentual: "-10",
  });
  if (!resposta.ok) return mostrar("resultado-queda", erroDe(resposta), "negado");
  const { garantia, limite } = resposta.dados;
  const emMargem = Number(limite.deficit) > 0;
  mostrar(
    "resultado-queda",
    `${NOMES[garantia.tipo_ativo]} caiu de ${reais(alvo.valor_de_mercado)} para ${reais(garantia.valor_de_mercado)}. ` +
      (emMargem ? "A dívida passou do limite: chamada de margem aberta." : "O limite encolheu junto."),
    emMargem ? "negado" : "",
  );
  await atualizar();
}

async function fecharFatura() {
  const resposta = await chamar("POST", `/contas/${estado.contaId}/faturas`, {
    vencimento: estado.hoje,
  });
  if (!resposta.ok) return mostrar("resultado-fatura", erroDe(resposta), "negado");
  desenharDias(0);
  mostrar("resultado-fatura", `Fatura de ${reais(resposta.dados.valor_fechado)} fechada. Vence hoje.`);
  await atualizar();
}

async function passarDia() {
  const resposta = await chamar("POST", `/sandbox/contas/${estado.contaId}/avanco-de-dias`, { dias: 1 });
  if (!resposta.ok) return mostrar("resultado-fatura", erroDe(resposta), "negado");
  const fatura = resposta.dados.fatura_em_aberto;
  if (fatura && fatura.dias_em_atraso < diasParaExecucao()) {
    desenharDias(fatura.dias_em_atraso);
    const dias = fatura.dias_em_atraso;
    mostrar("resultado-fatura", `${dias} dia${dias > 1 ? "s" : ""} de atraso. Novas compras ficam suspensas.`, "negado");
  } else {
    desenharDias(diasParaExecucao());
    const execucoes = await chamar(
      "GET",
      `/eventos?conta_id=${estado.contaId}&tipo=garantia.executada&limite=10`,
    );
    const ultima = (execucoes.dados?.dados ?? []).find((e) => !e.dados.emprestimo_id);
    const d = ultima?.dados?.demonstrativo;
    mostrar(
      "resultado-fatura",
      `${diasParaExecucao()}º dia: a garantia foi executada` +
        (d
          ? `. Vendida por ${reais(d.valor_de_mercado)}; ${reais(d.divida_abatida)} abateram a dívida` +
            `${Number(d.retencao) > 0 ? `, ${reais(d.retencao)} ficaram retidos em tributos` : ""}` +
            ` e ${reais(d.excedente_devolvido)} voltaram ao cliente.`
          : "."),
      "negado",
    );
  }
  await atualizar();
}

async function pagarFatura() {
  const fatura = estado.conta.fatura_em_aberto;
  const valor = fatura ? fatura.saldo_em_aberto : estado.conta.limite.divida_total;
  const resposta = await chamar(
    "POST",
    `/contas/${estado.contaId}/pagamentos`,
    { valor },
    { idempotente: true },
  );
  if (!resposta.ok) return mostrar("resultado-fatura", erroDe(resposta), "negado");
  desenharDias(0);
  mostrar("resultado-fatura", `Pagamento de ${reais(valor)} recebido. A garantia continua lá, rendendo.`, "ok");
  await atualizar();
}

async function resgatarGarantia(garantia) {
  const resposta = await chamar("POST", `/garantias/${garantia.id}/liberacao`);
  if (resposta.ok) {
    const prazo = resposta.dados.garantia.gravame.baixa_prazo;
    mostrar(
      "resultado-garantia",
      `${reais(garantia.valor_de_mercado)} devolvidos ao cliente. O limite caiu na hora; a baixa do gravame foi pedida` +
        (prazo ? ` até ${dataCompleta(prazo)} (2 dias úteis).` : "."),
      "ok",
    );
  } else {
    mostrar("resultado-garantia", erroDe(resposta), "negado");
  }
  await atualizar();
}

// --------------------------------------------------------------- trazer de fora
function trilhoEscolhido() {
  return $("trilho-de-entrada").value;
}

function contrapartesDoTrilho() {
  return estado.contrapartes.filter((c) => c.trilhos.includes(trilhoEscolhido()));
}

function desenharPrazoPrevio() {
  const prazo = prazoDoTrilho(trilhoEscolhido(), $("ativo-de-fora").value);
  const alvo = $("prazo-previo");
  if (!prazo || prazo.dias === null) {
    alvo.textContent = "Este trilho não tem prazo fixo no catálogo.";
    return;
  }
  const origem = prazo.regulatorio
    ? "prazo da norma"
    : "não encontramos prazo regulatório; o número é ilustrativo do projeto";
  alvo.textContent = `Prazo do trilho: ${diasUteis(prazo.dias)} (${origem}).`;
}

function ajustarFormularioDeFora({ redefinir }) {
  const trilho = trilhoEscolhido();
  $("rotulo-plano").hidden = trilho !== "res_conjunta_12";
  const seletor = $("contraparte");
  seletor.replaceChildren(
    ...contrapartesDoTrilho().map((c) => el("option", { value: c.id, textContent: c.nome })),
  );
  if (redefinir) {
    const padrao = PADRAO_DO_TRILHO[trilho];
    $("ativo-de-fora").value = padrao.ativo;
    $("produto-de-fora").value = padrao.produto;
  }
  desenharPrazoPrevio();
}

function preencherAtivosDeFora() {
  $("ativo-de-fora").replaceChildren(
    ...ATIVOS_DE_FORA.map((tipo) => el("option", { value: tipo, textContent: NOMES[tipo] })),
  );
}

function corpoDoPedido(trilho, valor) {
  const tipo = $("ativo-de-fora").value;
  const corpo = {
    tipo_ativo: tipo,
    valor,
    contraparte_id: $("contraparte").value,
    produto: $("produto-de-fora").value,
  };
  if (trilho === "res_conjunta_12") {
    const [ano, mes, dia] = estado.hoje.split("-").map(Number);
    return {
      valor,
      contraparte_id: corpo.contraparte_id,
      produto: corpo.produto,
      plano: $("plano").value,
      fim_da_acumulacao: `${ano + ANOS_DE_ACUMULACAO}-${String(mes).padStart(2, "0")}-${String(Math.min(dia, 28)).padStart(2, "0")}`,
    };
  }
  if (trilho === "td_garantia") return corpo;
  if (tipo === "cdb") corpo.liquidez_dias = 0;
  if (tipo === "fundo_di") corpo.liquidez_dias = 1;
  return corpo;
}

const ROTAS_DE_ENTRADA = {
  portabilidade_cvm_210: "portabilidades",
  transferencia_de_custodia: "transferencias-de-custodia",
  td_garantia: "garantias/tesouro",
  res_conjunta_12: "garantias/previdencia",
};

async function pedirEntrada(evento) {
  evento.preventDefault();
  const valor = paraDecimal($("valor-de-fora").value);
  if (!valor) return mostrar("resultado-fora", "Digite um valor como 10.000,00.", "negado");
  const trilho = trilhoEscolhido();
  const resposta = await chamar(
    "POST",
    `/contas/${estado.contaId}/${ROTAS_DE_ENTRADA[trilho]}`,
    corpoDoPedido(trilho, valor),
    { idempotente: true },
  );
  if (!resposta.ok) return mostrar("resultado-fora", erroDe(resposta), "negado");
  const { garantia } = resposta.dados;
  mostrar(
    "resultado-fora",
    `Pedido de ${reais(garantia.valor_aplicado)} em ${NOMES[garantia.tipo_ativo]} enviado a ` +
      `${nomeDaContraparte(garantia.contraparte_id)}. O limite só nasce quando o gravame for registrado.`,
  );
  await atualizar();
}

const FRASES_DA_CONTRAPARTE = {
  concluir: (g) =>
    g.informacoes
      ? "a operadora informou o valor disponível e os tributos. O bloqueio nasce ao contratar o empréstimo."
      : `pedido concluído. ${ESTADOS[g.estado] ?? g.estado}.`,
  recusar: (g) => `pedido recusado${g.gravame.motivo ? `: ${g.gravame.motivo}` : ""}. Nenhum ativo mudou de lugar.`,
  nao_responder: (g) =>
    g.gravame.estado === "solicitado"
      ? "o prazo venceu sem resposta e o pedido continua: a obrigação é da contraparte."
      : `o prazo venceu sem resposta. ${ESTADOS[g.estado] ?? g.estado}.`,
};

async function decidirPelaContraparte(garantia, acao, rotulo) {
  const resposta = await chamar("POST", `/sandbox/garantias/${garantia.id}/contraparte`, { acao });
  if (!resposta.ok) return mostrar("resultado-fora", erroDe(resposta), "negado");
  const nova = resposta.dados.garantia;
  const alvo = nova.gravame.trilho === "casa" ? "resultado-garantia" : "resultado-fora";
  mostrar(alvo, `${rotulo}: ${FRASES_DA_CONTRAPARTE[acao](nova)}`, acao === "concluir" ? "ok" : "negado");
  await atualizar();
}

// ------------------------------------------------------------------ empréstimo
async function contratarEmprestimo(evento) {
  evento.preventDefault();
  const valor = paraDecimal($("valor-do-emprestimo").value);
  if (!valor) return mostrar("resultado-emprestimo", "Digite um valor como 2.000,00.", "negado");
  const resposta = await chamar(
    "POST",
    `/contas/${estado.contaId}/emprestimos`,
    {
      valor,
      parcelas: Number($("parcelas").value),
      garantia_ids: [$("garantia-do-emprestimo").value],
      // Amanhã: o atraso da demo começa logo, e o relógio de teste faz o resto.
      primeiro_vencimento: somarDias(estado.hoje, 1),
    },
    { idempotente: true },
  );
  if (!resposta.ok) return mostrar("resultado-emprestimo", erroDe(resposta), "negado");
  const e = resposta.dados.emprestimo;
  mostrar(
    "resultado-emprestimo",
    `Empréstimo de ${reais(e.principal)} em ${e.parcelas.length} parcelas contratado. ` +
      `Primeira parcela em ${dataCompleta(e.parcelas[0].vencimento)}.`,
    "ok",
  );
  await atualizar();
}

async function pagarEmprestimo(emprestimo, quanto) {
  const proxima = emprestimo.parcelas.find((p) => !p.paga_em);
  const saldo = emprestimo.saldo_devedor;
  const valor =
    quanto === "saldo" || !proxima || Number(proxima.valor) > Number(saldo) ? saldo : proxima.valor;
  const resposta = await chamar(
    "POST",
    `/emprestimos/${emprestimo.id}/pagamentos`,
    { valor },
    { idempotente: true },
  );
  if (!resposta.ok) return mostrar("resultado-emprestimo", erroDe(resposta), "negado");
  const e = resposta.dados.emprestimo;
  mostrar(
    "resultado-emprestimo",
    e.estado === "quitado"
      ? `Pagamento de ${reais(valor)} recebido: empréstimo quitado. A baixa do gravame foi pedida, com prazo de 2 dias úteis.`
      : `Pagamento de ${reais(valor)} recebido. Saldo devedor de ${reais(e.saldo_devedor)}.`,
    "ok",
  );
  await atualizar();
}

// O relógio anda em dias de atraso. Enquanto a primeira parcela ainda não venceu, os dias até
// o vencimento entram na conta, para que "+45 dias" signifique 45 dias de atraso.
async function avancarRelogio(dias) {
  const aberto = estado.emprestimos.find((e) => !ENCERRADOS.includes(e.estado));
  let total = dias;
  if (aberto && aberto.dias_em_atraso === 0) {
    const proxima = aberto.parcelas.find((p) => !p.paga_em);
    if (proxima) total += Math.max(diasEntre(estado.hoje, proxima.vencimento), 0);
  }
  const resposta = await chamar("POST", `/sandbox/contas/${estado.contaId}/avanco-de-dias`, { dias: total });
  if (!resposta.ok) return mostrar("resultado-relogio", erroDe(resposta), "negado");
  await atualizar();
  const e = estado.emprestimos.find((x) => x.id === aberto?.id);
  mostrar(
    "resultado-relogio",
    e
      ? `+${dias} dias (simulador): ${e.dias_em_atraso} dias de atraso. Empréstimo ${ESTADOS_DO_EMPRESTIMO[e.estado]}.`
      : `+${dias} dias (simulador).`,
    e?.estado === "liquidado_por_execucao" ? "negado" : "",
  );
  await atualizar();
}

// ------------------------------------------------------------------- webhooks
function resumoDoEvento(evento) {
  const d = evento.dados ?? {};
  const ativo = NOMES[d.tipo_ativo] ?? d.tipo_ativo;
  switch (evento.tipo) {
    case "limite.atualizado":
      return `limite ${reais(d.limite_total)}, disponível ${reais(d.limite_disponivel)}`;
    case "compra.aprovada":
      return `${reais(d.valor)} em ${d.descricao}`;
    case "compra.negada":
      return `${reais(d.valor)} em ${d.descricao}: ${MOTIVOS[d.motivo] ?? d.motivo}`;
    case "chamada_de_margem":
      if (d.situacao === "aberta") {
        return `aberta: faltam ${reais(d.deficit)}, prazo até ${dataCurta(d.prazo_ate)}`;
      }
      return d.situacao === "vencida"
        ? "vencida: prazo esgotado, conta bloqueada para novas compras"
        : "atendida: a dívida voltou a caber no limite";
    case "fatura.em_atraso":
      return `${d.dias_em_atraso} dia de atraso; execução a partir de ${dataCurta(d.execucao_a_partir_de)}`;
    case "garantia.executada":
      return d.demonstrativo
        ? `${ativo} vendido por ${reais(d.demonstrativo.valor_de_mercado)}; dívida abatida ${reais(d.demonstrativo.divida_abatida)}`
        : `${ativo} executado`;
    case "fatura.paga":
      return `fatura de ${reais(d.valor_fechado)} quitada`;
    case "garantia.liberada":
      return `${ativo} devolvido ao cliente`;
    case "gravame.solicitado":
      return `${ativo} em ${nomeDoTrilho(d.trilho)}`;
    case "gravame.registrado":
      return `${ativo}: gravame registrado${d.protocolo ? `, protocolo ${d.protocolo}` : ""} (simulador)`;
    case "gravame.recusado":
    case "gravame.cancelado":
      return `${ativo}: ${d.motivo ?? "sem motivo informado"}`;
    case "gravame.liberado_para_excussao":
      return `${ativo}: liberado para excussão`;
    case "gravame.baixa_pedida":
      return `${ativo}: baixa pedida até ${d.baixa_prazo ? dataCompleta(d.baixa_prazo) : "?"} (2 dias úteis)`;
    case "gravame.baixado":
      return `${ativo}: gravame baixado`;
    case "portabilidade.solicitada":
    case "transferencia.solicitada":
      return `${ativo}: pedido a ${nomeDaContraparte(d.contraparte_id)}`;
    case "portabilidade.concluida":
    case "transferencia.concluida":
      return `${ativo}: concluída por ${nomeDaContraparte(d.contraparte_id)}`;
    case "garantia.prazo_regulatorio_vencido":
      return `${ativo}: prazo regulatório vencido sem resposta; o pedido continua`;
    case "previdencia.informacoes_recebidas":
      return "a operadora informou o valor disponível e os tributos";
    case "previdencia.aviso_45_dias":
      return `aviso aos 45 dias de atraso; saldo devedor ${reais(d.saldo_devedor)}`;
    case "previdencia.aviso_10_dias":
      return `aviso final: liquidação a partir de ${d.liquidacao_a_partir_de ? dataCompleta(d.liquidacao_a_partir_de) : "?"}`;
    case "emprestimo.contratado":
      return `empréstimo contratado, ${ativo ?? "garantia"} em garantia`;
    case "emprestimo.parcela_paga":
      return `${reais(d.valor)} pagos; saldo ${reais(d.saldo_devedor)}`;
    case "emprestimo.em_atraso":
      return `${d.dias_em_atraso} dias de atraso; saldo ${reais(d.saldo_devedor)}`;
    case "emprestimo.quitado":
      return "empréstimo quitado";
    default:
      return "";
  }
}

const TOM = {
  "gravame.recusado": "alerta",
  "gravame.cancelado": "alerta",
  "garantia.prazo_regulatorio_vencido": "alerta",
  "previdencia.aviso_45_dias": "alerta",
  "previdencia.aviso_10_dias": "alerta",
  "emprestimo.em_atraso": "alerta",
  "gravame.registrado": "bom",
  "gravame.baixado": "bom",
  "emprestimo.quitado": "bom",
  "emprestimo.contratado": "bom",
  "compra.negada": "alerta",
  chamada_de_margem: "alerta",
  "fatura.em_atraso": "alerta",
  "garantia.executada": "alerta",
  "compra.aprovada": "bom",
  "fatura.paga": "bom",
};

function desenharWebhook(recebido) {
  const item = document.createElement("li");
  const topo = el("div", { className: "evento-topo" });
  topo.append(
    el("span", {
      className: `evento-tipo ${TOM[recebido.evento.tipo] ?? ""}`,
      textContent: recebido.evento.tipo ?? "(sem tipo)",
    }),
    el("span", { className: "evento-hora", textContent: hora.format(new Date(recebido.recebido_em)) }),
    el("span", {
      className: `assinatura ${recebido.assinatura_valida ? "" : "invalida"}`,
      textContent: recebido.assinatura_valida ? "✓ assinatura HMAC confere" : `✗ ${recebido.motivo_da_recusa}`,
    }),
  );
  const resumo = el("p", { className: "evento-resumo", textContent: resumoDoEvento(recebido.evento) });
  const detalhes = document.createElement("details");
  detalhes.append(
    el("summary", { textContent: "ver o JSON entregue" }),
    el("pre", { textContent: JSON.stringify(recebido.evento, null, 2) }),
  );
  item.append(topo, resumo, detalhes);
  $("eventos").prepend(item);
  $("vazio").hidden = true;
}

// O worker muda as coisas sozinho (a contraparte respondeu, a baixa foi feita): enquanto
// algo está esperando, a conta é reconsultada de tempos em tempos.
const algoEsperando = () =>
  estado.garantias.some(
    (g) => g.solicitacao || (g.gravame.baixa_prazo && g.gravame.estado !== "baixado"),
  );

async function buscarWebhooks() {
  if (!estado.contaId) return;
  try {
    const resposta = await fetch(
      `/demo/webhooks?depois_de=${estado.ultimoWebhook}&conta_id=${encodeURIComponent(estado.contaId)}`,
    );
    if (!resposta.ok) return;
    const recebidos = await resposta.json();
    for (const recebido of recebidos) {
      estado.ultimoWebhook = Math.max(estado.ultimoWebhook, recebido.numero);
      desenharWebhook(recebido);
    }
    const faz = Date.now() - estado.ultimaAtualizacao;
    const reconsultar = algoEsperando() || estado.desatualizado;
    if (recebidos.length || (reconsultar && faz > INTERVALO_DE_RECONSULTA_EM_MS)) {
      await atualizar();
    }
  } catch (erro) {
    // A API saiu do ar por um instante: tenta de novo na próxima volta. Fica no console
    // para que um erro de desenho não passe despercebido.
    console.error(erro);
  }
}

// --------------------------------------------------------------------- início
async function abrirConta() {
  const resposta = await chamar("POST", "/contas", { cliente_ref: `demo-${crypto.randomUUID().slice(0, 8)}` });
  if (!resposta.ok) throw new Error(erroDe(resposta));
  estado.contaId = resposta.dados.id;
  estado.demonstrativos = {};
  sessionStorage.setItem(CHAVE_DA_CONTA, estado.contaId);
}

async function recomecar() {
  // Até a conta nova existir, nada na página pode ser clicado: um pedido no meio do
  // caminho iria para a conta antiga e sumiria da tela quando a nova aparecesse.
  document.body.inert = true;
  document.body.dataset.pronta = "false";
  try {
    sessionStorage.removeItem(CHAVE_DA_CONTA);
    $("eventos").replaceChildren();
    $("vazio").hidden = false;
    for (const id of MENSAGENS) mostrar(id, "");
    desenharDias(0);
    await abrirConta();
    $("conta-atual").textContent = estado.contaId;
    await atualizar();
  } finally {
    document.body.inert = false;
    document.body.dataset.pronta = "true";
  }
}

// Os tratadores são ligados antes de qualquer chamada à API: um clique que chegue cedo
// demais não pode virar envio nativo do formulário (que recarregaria a página).
function quandoPronta(acao) {
  return (evento) => {
    evento?.preventDefault?.();
    if (document.body.dataset.pronta === "true") return acao(evento);
    return undefined;
  };
}

function ligarTratadores() {
  $("form-garantia").addEventListener("submit", quandoPronta(darEmGarantia));
  $("form-compra").addEventListener("submit", quandoPronta(comprar));
  $("form-fora").addEventListener("submit", quandoPronta(pedirEntrada));
  $("form-emprestimo").addEventListener("submit", quandoPronta(contratarEmprestimo));
  $("derrubar").addEventListener("click", quandoPronta(derrubarAtivo));
  $("fechar-fatura").addEventListener("click", quandoPronta(fecharFatura));
  $("passar-dia").addEventListener("click", quandoPronta(passarDia));
  $("pagar").addEventListener("click", quandoPronta(pagarFatura));
  $("recomecar").addEventListener("click", quandoPronta(recomecar));
  for (const botao of document.querySelectorAll("button.relogio")) {
    botao.addEventListener("click", quandoPronta(() => avancarRelogio(Number(botao.dataset.dias))));
  }
  $("trilho-de-entrada").addEventListener("change", () => ajustarFormularioDeFora({ redefinir: true }));
  $("ativo-de-fora").addEventListener("change", desenharPrazoPrevio);
}

async function iniciar() {
  ligarTratadores();
  preencherAtivosDeFora();
  const config = await fetch("/demo/config");
  if (!config.ok) {
    $("aviso").hidden = false;
    $("aviso").textContent =
      "A demonstração ainda não foi preparada. Rode `python -m app.seed` e recarregue a página.";
    return;
  }
  const dadosDaConfig = await config.json();
  estado.chave = dadosDaConfig.api_key;
  estado.hoje = dadosDaConfig.hoje;

  const parceiro = await chamar("GET", "/parceiro");
  if (!parceiro.ok) {
    $("aviso").hidden = false;
    $("aviso").textContent = "O parceiro de exemplo não existe. Rode `python -m app.seed` e recarregue.";
    return;
  }
  estado.politica = parceiro.dados.politica;
  const [matriz, contrapartes] = await Promise.all([
    fetch("/v1/regulacao/matriz").then((r) => (r.ok ? r.json() : null)),
    chamar("GET", "/contrapartes"),
  ]);
  estado.matriz = matriz;
  estado.contrapartes = contrapartes.ok ? contrapartes.dados : [];
  ajustarFormularioDeFora({ redefinir: true });
  [...$("dias").children].forEach((dia, indice) => {
    dia.hidden = indice >= diasParaExecucao();
  });

  estado.contaId = sessionStorage.getItem(CHAVE_DA_CONTA);
  if (estado.contaId && !(await chamar("GET", `/contas/${estado.contaId}`)).ok) estado.contaId = null;
  if (!estado.contaId) await abrirConta();
  $("conta-atual").textContent = estado.contaId;

  await atualizar();
  await buscarWebhooks();
  document.body.dataset.pronta = "true";
  setInterval(buscarWebhooks, INTERVALO_DOS_WEBHOOKS_EM_MS);
}

iniciar();
