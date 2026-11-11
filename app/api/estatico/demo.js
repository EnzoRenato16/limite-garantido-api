// Página /demo: um app de parceiro de mentira falando com a API de verdade.
// Sem framework. Todo texto vindo da API entra por textContent, nunca por innerHTML.

const NOMES = {
  deposito: "Conta remunerada",
  fundo_di: "Fundo DI",
  tesouro_selic: "Tesouro Selic",
  cdb: "CDB",
  tesouro_prefixado: "Tesouro Prefixado",
  tesouro_ipca: "Tesouro IPCA+",
  acoes: "Ações",
};

const ESTADOS = {
  pendente: "aguardando bloqueio",
  bloqueada: "bloqueado como garantia",
  parcialmente_executada: "parte vendida para pagar a fatura",
  executada: "vendido para pagar a fatura",
  liberada: "devolvido ao cliente",
};

const MOTIVOS = {
  limite_insuficiente: "limite insuficiente",
  conta_bloqueada: "conta bloqueada",
  fatura_em_atraso: "fatura em atraso",
  teto_do_parceiro_excedido: "teto do parceiro atingido",
};

const DIAS_PARA_EXECUCAO_PADRAO = 7;
const INTERVALO_DOS_WEBHOOKS_EM_MS = 700;
const GUARDADO = "lg-demo-conta";

const moeda = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const hora = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const $ = (id) => document.getElementById(id);

const estado = {
  chave: null,
  hoje: null,
  politica: null,
  contaId: null,
  conta: null,
  garantias: [],
  ultimoWebhook: 0,
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

function dataCurta(iso) {
  const [, mes, dia] = iso.split("-");
  return `${dia}/${mes}`;
}

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
  const dados = resposta.status === 204 ? null : await resposta.json();
  return { ok: resposta.ok, status: resposta.status, dados };
}

function mostrar(id, texto, tipo = "") {
  const alvo = $(id);
  alvo.textContent = texto;
  alvo.className = `resultado ${tipo}`.trim();
}

const erroDe = (resposta) => resposta.dados?.detail ?? `Erro HTTP ${resposta.status}`;

// --------------------------------------------------------------------- régua
function desenharRegua() {
  const limite = estado.conta.limite;
  const ativas = estado.garantias.filter((g) => ["bloqueada", "parcialmente_executada"].includes(g.estado));
  const aplicado = ativas.reduce((soma, g) => soma + Number(g.valor_aplicado), 0);
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

function desenharGarantias() {
  const lista = $("garantias");
  lista.replaceChildren();
  for (const garantia of estado.garantias) {
    const item = document.createElement("li");
    const nome = Object.assign(document.createElement("span"), {
      className: "nome",
      textContent: NOMES[garantia.tipo_ativo] ?? garantia.tipo_ativo,
    });
    const valor = Object.assign(document.createElement("span"), {
      className: "valor",
      textContent: reais(garantia.valor_de_mercado),
    });
    const situacao = Object.assign(document.createElement("span"), {
      className: `estado ${garantia.estado.includes("executada") ? "executada" : ""}`,
      textContent: ESTADOS[garantia.estado] ?? garantia.estado,
    });
    item.append(nome, valor, situacao);
    if (["bloqueada", "parcialmente_executada"].includes(garantia.estado)) {
      const resgatar = Object.assign(document.createElement("button"), {
        type: "button",
        className: "discreto pequeno",
        textContent: "Resgatar",
      });
      resgatar.addEventListener("click", () => resgatarGarantia(garantia));
      item.append(resgatar);
    }
    lista.append(item);
  }
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

async function atualizar() {
  const [conta, garantias] = await Promise.all([
    chamar("GET", `/contas/${estado.contaId}`),
    chamar("GET", `/contas/${estado.contaId}/garantias?limite=50`),
  ]);
  if (!conta.ok) return;
  estado.conta = conta.dados;
  estado.garantias = garantias.ok ? garantias.dados.dados.slice().reverse() : [];
  desenharRegua();
  desenharAviso();
  desenharGarantias();
  desenharFatura();
}

// ---------------------------------------------------------------------- ações
async function guardarGarantia(evento) {
  evento.preventDefault();
  const valor = paraDecimal($("valor-da-garantia").value);
  if (!valor) return mostrar("resultado-garantia", "Digite um valor como 5.000,00.", "negado");
  const tipo = $("tipo-ativo").value;
  const resposta = await chamar(
    "POST",
    `/contas/${estado.contaId}/garantias`,
    { tipo_ativo: tipo, valor },
    { idempotente: true },
  );
  if (!resposta.ok) return mostrar("resultado-garantia", erroDe(resposta), "negado");
  const { garantia } = resposta.dados;
  mostrar(
    "resultado-garantia",
    `${reais(garantia.valor_aplicado)} em ${NOMES[tipo]} bloqueados. ` +
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
  const alvo = estado.garantias.find((g) => ["bloqueada", "parcialmente_executada"].includes(g.estado));
  if (!alvo) return mostrar("resultado-queda", "Guarde um investimento primeiro.", "negado");
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
    const vendido = (execucoes.dados?.dados ?? []).reduce(
      (soma, e) => soma + Number(e.dados.valor_executado),
      0,
    );
    mostrar(
      "resultado-fatura",
      fatura
        ? `${diasParaExecucao()}º dia: toda a garantia foi vendida (${reais(vendido)}) e ainda sobrou dívida. Conta bloqueada.`
        : `${diasParaExecucao()}º dia: a garantia foi executada. Foram vendidos só os ${reais(vendido)} da fatura; o resto continua bloqueado e rendendo.`,
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
    mostrar("resultado-garantia", `${reais(garantia.valor_de_mercado)} devolvidos ao cliente.`, "ok");
  } else {
    mostrar("resultado-garantia", erroDe(resposta), "negado");
  }
  await atualizar();
}

// ------------------------------------------------------------------- webhooks
function resumoDoEvento(evento) {
  const d = evento.dados ?? {};
  switch (evento.tipo) {
    case "garantia.bloqueada":
      return `${NOMES[d.tipo_ativo] ?? d.tipo_ativo} de ${reais(d.valor_de_mercado)} bloqueado; gerou ${reais(d.limite_gerado)} de limite`;
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
      return `vendidos ${reais(d.valor_executado)}; sobram ${reais(d.valor_remanescente)} bloqueados`;
    case "fatura.paga":
      return `fatura de ${reais(d.valor_fechado)} quitada`;
    case "garantia.liberada":
      return `${reais(d.valor_liberado)} devolvidos ao cliente`;
    default:
      return "";
  }
}

const TOM = {
  "compra.negada": "alerta",
  chamada_de_margem: "alerta",
  "fatura.em_atraso": "alerta",
  "garantia.executada": "alerta",
  "compra.aprovada": "bom",
  "garantia.bloqueada": "bom",
  "fatura.paga": "bom",
};

function desenharWebhook(recebido) {
  const item = document.createElement("li");
  const topo = Object.assign(document.createElement("div"), { className: "evento-topo" });
  topo.append(
    Object.assign(document.createElement("span"), {
      className: `evento-tipo ${TOM[recebido.evento.tipo] ?? ""}`,
      textContent: recebido.evento.tipo ?? "(sem tipo)",
    }),
    Object.assign(document.createElement("span"), {
      className: "evento-hora",
      textContent: hora.format(new Date(recebido.recebido_em)),
    }),
    Object.assign(document.createElement("span"), {
      className: `assinatura ${recebido.assinatura_valida ? "" : "invalida"}`,
      textContent: recebido.assinatura_valida
        ? "✓ assinatura HMAC confere"
        : `✗ ${recebido.motivo_da_recusa}`,
    }),
  );
  const resumo = Object.assign(document.createElement("p"), {
    className: "evento-resumo",
    textContent: resumoDoEvento(recebido.evento),
  });
  const detalhes = document.createElement("details");
  detalhes.append(
    Object.assign(document.createElement("summary"), { textContent: "ver o JSON entregue" }),
    Object.assign(document.createElement("pre"), {
      textContent: JSON.stringify(recebido.evento, null, 2),
    }),
  );
  item.append(topo, resumo, detalhes);
  $("eventos").prepend(item);
  $("vazio").hidden = true;
}

async function buscarWebhooks() {
  if (!estado.contaId) return;
  try {
    const resposta = await fetch(
      `/demo/webhooks?depois_de=${estado.ultimoWebhook}&conta_id=${encodeURIComponent(estado.contaId)}`,
    );
    if (!resposta.ok) return;
    for (const recebido of await resposta.json()) {
      estado.ultimoWebhook = Math.max(estado.ultimoWebhook, recebido.numero);
      desenharWebhook(recebido);
    }
  } catch {
    // A API saiu do ar por um instante: tenta de novo na próxima volta.
  }
}

// --------------------------------------------------------------------- início
async function abrirConta() {
  const resposta = await chamar("POST", "/contas", { cliente_ref: `demo-${crypto.randomUUID().slice(0, 8)}` });
  if (!resposta.ok) throw new Error(erroDe(resposta));
  estado.contaId = resposta.dados.id;
  sessionStorage.setItem(GUARDADO, estado.contaId);
}

async function recomecar() {
  sessionStorage.removeItem(GUARDADO);
  $("eventos").replaceChildren();
  $("vazio").hidden = false;
  for (const id of ["resultado-garantia", "resultado-compra", "resultado-queda", "resultado-fatura"]) {
    mostrar(id, "");
  }
  desenharDias(0);
  await abrirConta();
  $("conta-atual").textContent = estado.contaId;
  await atualizar();
}

function preencherAtivos() {
  const seletor = $("tipo-ativo");
  seletor.replaceChildren();
  for (const [tipo, ltv] of Object.entries(estado.politica.ltv_por_ativo)) {
    const opcao = Object.assign(document.createElement("option"), {
      value: tipo,
      textContent: `${NOMES[tipo] ?? tipo} · vira ${Math.round(Number(ltv) * 100)}% de limite`,
    });
    seletor.append(opcao);
  }
  seletor.value = "cdb" in estado.politica.ltv_por_ativo ? "cdb" : seletor.options[0].value;
}

async function iniciar() {
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
  preencherAtivos();
  [...$("dias").children].forEach((dia, indice) => {
    dia.hidden = indice >= diasParaExecucao();
  });

  estado.contaId = sessionStorage.getItem(GUARDADO);
  if (estado.contaId && !(await chamar("GET", `/contas/${estado.contaId}`)).ok) estado.contaId = null;
  if (!estado.contaId) await abrirConta();
  $("conta-atual").textContent = estado.contaId;

  $("form-garantia").addEventListener("submit", guardarGarantia);
  $("form-compra").addEventListener("submit", comprar);
  $("derrubar").addEventListener("click", derrubarAtivo);
  $("fechar-fatura").addEventListener("click", fecharFatura);
  $("passar-dia").addEventListener("click", passarDia);
  $("pagar").addEventListener("click", pagarFatura);
  $("recomecar").addEventListener("click", recomecar);

  await atualizar();
  await buscarWebhooks();
  setInterval(buscarWebhooks, INTERVALO_DOS_WEBHOOKS_EM_MS);
}

iniciar();
