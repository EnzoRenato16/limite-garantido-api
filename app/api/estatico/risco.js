// Página /risco: painel de risco sobre uma carteira sintética.
// Sem framework. Texto da API entra por textContent; o SVG é montado com createElementNS.
// Os valores em dinheiro chegam da API como texto ("4250.00") e só são formatados aqui;
// os números de largura das barras são proporções para desenhar, não contas de dinheiro.

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
  casa: "Na casa (custodiado pela CTVM do grupo)",
  agente_td_a: "Agente de custódia A (simulado)",
  agente_td_b: "Agente de custódia B (simulado)",
  entidade_operadora_simulada: "Entidade operadora (simulada)",
  conglomerado_a: "Conglomerado A (fictício)",
  conglomerado_b: "Conglomerado B (fictício)",
  conglomerado_c: "Conglomerado C (fictício)",
  conglomerado_d: "Conglomerado D (fictício)",
  c1: "C1",
  c2: "C2",
  c3: "C3",
  c4: "C4",
  c5: "C5",
};
const ATIVOS_DO_PAINEL = [
  "cdb", "lci", "lca", "tesouro_selic", "tesouro_prefixado", "tesouro_ipca", "acoes", "fii", "etf",
];
const AGENTE_B = "agente_td_b";
const CONGLOMERADO_B = "conglomerado_b";
const SVG = "http://www.w3.org/2000/svg";

const moeda = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const inteiro = new Intl.NumberFormat("pt-BR");
const $ = (id) => document.getElementById(id);
const reais = (texto) => moeda.format(Number(texto));

const estado = { chave: null, cenarios: [], cenarioAtual: null, ultimo: null };

async function chamar(metodo, caminho, corpo) {
  const resposta = await fetch(`/v1${caminho}`, {
    method: metodo,
    headers: { Authorization: `Bearer ${estado.chave}`, "Content-Type": "application/json" },
    body: corpo === undefined ? undefined : JSON.stringify(corpo),
  });
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) {
    const detalhe = dados.detail ?? `Erro HTTP ${resposta.status}`;
    const campos = (dados.erros || []).map((e) => e.campo).filter(Boolean).join(", ");
    throw new Error(campos ? `${detalhe} (${campos})` : detalhe);
  }
  return dados;
}

// ------------------------------------------------------------------ texto <-> número
// Choque digitado em % ("-30", "-2,5") vira fração com 4 casas ("-0.3000"), em texto.
function percentualParaFracao(digitado) {
  const limpo = String(digitado).trim().replace(",", ".");
  if (!/^-?\d+(\.\d{1,2})?$/.test(limpo)) return null;
  const negativo = limpo.startsWith("-");
  const [int, frac = ""] = limpo.replace("-", "").split(".");
  const centesimos = Number(int) * 100 + Number(frac.padEnd(2, "0"));
  const casas = String(centesimos).padStart(5, "0"); // 10000 centésimos = 1
  const texto = `${casas.slice(0, -4)}.${casas.slice(-4)}`;
  return negativo && centesimos !== 0 ? `-${texto}` : texto;
}

function fracaoParaPercentual(texto) {
  const valor = Number(texto) * 100;
  return String(Math.round(valor * 100) / 100);
}

// --------------------------------------------------------------------- controles
function montarControles() {
  const grade = $("choques");
  for (const ativo of ATIVOS_DO_PAINEL) {
    const rotulo = document.createElement("label");
    rotulo.textContent = NOMES[ativo];
    const campo = document.createElement("input");
    campo.type = "number";
    campo.min = "-100";
    campo.max = "100";
    campo.step = "0.5";
    campo.id = `choque-${ativo}`;
    campo.dataset.ativo = ativo;
    rotulo.append(campo);
    grade.append(rotulo);
  }
  const botoes = $("cenarios");
  for (const cenario of estado.cenarios) {
    const botao = document.createElement("button");
    botao.type = "button";
    botao.id = `cenario-${cenario.id}`;
    botao.textContent = cenario.nome;
    botao.setAttribute("aria-pressed", "false");
    botao.addEventListener("click", () => escolherCenario(cenario));
    botoes.append(botao);
  }
}

function preencherControles(cenario) {
  for (const ativo of ATIVOS_DO_PAINEL) {
    const choque = cenario.choques[ativo];
    $(`choque-${ativo}`).value = choque ? fracaoParaPercentual(choque) : "0";
  }
  $("taxa").value = fracaoParaPercentual(cenario.taxa_sem_reforco);
  $("dias-extra").value = String(cenario.dias_extra_de_execucao);
  $("agente-b").checked = Object.hasOwn(cenario.atraso_por_onde_esta, AGENTE_B);
  $("quebra-b").checked = cenario.emissores_em_quebra.includes(CONGLOMERADO_B);
}

function lerControles() {
  const choques = {};
  for (const ativo of ATIVOS_DO_PAINEL) {
    const fracao = percentualParaFracao($(`choque-${ativo}`).value || "0");
    if (fracao === null) throw new Error(`Choque de ${NOMES[ativo]} inválido: use até duas casas`);
    if (Number(fracao) !== 0) choques[ativo] = fracao;
  }
  const taxa = percentualParaFracao($("taxa").value || "0");
  if (taxa === null) throw new Error("Taxa de clientes que não reforçam inválida");
  return {
    choques,
    taxa_sem_reforco: taxa,
    dias_extra_de_execucao: Number($("dias-extra").value || "0"),
    atraso_por_onde_esta: $("agente-b").checked ? { [AGENTE_B]: 14 } : {},
    emissores_em_quebra: $("quebra-b").checked ? [CONGLOMERADO_B] : [],
  };
}

async function escolherCenario(cenario) {
  estado.cenarioAtual = cenario.id;
  for (const botao of $("cenarios").querySelectorAll("button")) {
    botao.setAttribute("aria-pressed", String(botao.id === `cenario-${cenario.id}`));
  }
  $("descricao-cenario").textContent = cenario.descricao;
  preencherControles(cenario);
  await simular({ cenario: cenario.id });
}

async function simularComControles() {
  try {
    const corpo = lerControles();
    estado.cenarioAtual = null;
    for (const botao of $("cenarios").querySelectorAll("button")) botao.setAttribute("aria-pressed", "false");
    $("descricao-cenario").textContent = "Premissas ajustadas à mão.";
    await simular(corpo);
  } catch (erro) {
    $("erro").textContent = erro.message;
  }
}

async function simular(corpo) {
  $("erro").textContent = "";
  document.body.setAttribute("aria-busy", "true");
  try {
    const resultado = await chamar("POST", "/risco/simulacoes-de-carteira", corpo);
    mostrar(resultado);
  } catch (erro) {
    $("erro").textContent = erro.message;
  } finally {
    document.body.removeAttribute("aria-busy");
  }
}

// ---------------------------------------------------------------------- resultado
function numero(id, texto, formatado) {
  const alvo = $(id);
  alvo.dataset.valor = texto;
  alvo.textContent = formatado;
}

function mostrar(r) {
  estado.ultimo = r;
  $("aviso-sintetico").textContent = r.aviso_sintetico;
  $("tamanho").textContent = inteiro.format(r.carteira.tamanho);
  $("semente").textContent = String(r.carteira.semente);
  numero("chamada-antes", String(r.antes.contas_em_chamada), inteiro.format(r.antes.contas_em_chamada));
  numero("chamada-depois", String(r.depois.contas_em_chamada), inteiro.format(r.depois.contas_em_chamada));
  $("chamada-depois").classList.toggle("piorou", r.depois.contas_em_chamada > r.antes.contas_em_chamada);
  numero("perda-total", r.perda.perda_esperada_total, reais(r.perda.perda_esperada_total));
  $("perda-total").classList.toggle("piorou", Number(r.perda.perda_esperada_total) > 0);
  numero("exposicao", r.perda.exposicao, reais(r.perda.exposicao));
  numero("limite-antes", r.antes.limite_total, reais(r.antes.limite_total));
  numero("limite-depois", r.depois.limite_total, reais(r.depois.limite_total));
  numero("utilizacao-depois", r.depois.utilizacao_percentual, `${r.depois.utilizacao_percentual.replace(".", ",")}%`);
  numero("perda-imediata", r.perda.perda_com_execucao_imediata, reais(r.perda.perda_com_execucao_imediata));
  numero("perda-externa", r.perda.perda_adicional_externa, reais(r.perda.perda_adicional_externa));

  const total = Number(r.perda.perda_esperada_total);
  const exposicao = Number(r.perda.exposicao) || 1;
  // A barra é a exposição inteira; as duas parcelas ocupam a parte dela que é perda.
  $("parte-imediata").style.width = `${(Number(r.perda.perda_com_execucao_imediata) / exposicao) * 100}%`;
  $("parte-externa").style.width = `${(Number(r.perda.perda_adicional_externa) / exposicao) * 100}%`;
  $("barra-perda").setAttribute(
    "aria-label",
    total > 0 ? `Perda de ${reais(total)} sobre exposição de ${reais(exposicao)}` : "Sem perda esperada",
  );

  desenharReguas($("grafico-ativos"), r.concentracao_por_ativo, (f) => NOMES[f.chave] || f.chave, () => "");
  desenharReguas(
    $("grafico-onde-esta"),
    r.concentracao_por_onde_esta,
    (f) => NOMES[f.chave] || f.chave,
    (f) => (f.chave === "casa" ? "interna" : "externa"),
  );
  mostrarRegulatorio(r.regulatorio);
  mostrarPremissas(r.premissas);
}

// ------------------------------------------------------------ painel regulatório
function preencherTabela(id, linhas) {
  const corpo = $(id).querySelector("tbody");
  corpo.replaceChildren();
  for (const celulas of linhas) {
    const tr = document.createElement("tr");
    for (const celula of celulas) {
      const td = document.createElement("td");
      td.textContent = celula.texto ?? celula;
      if (celula.alerta) td.className = "alerta";
      tr.append(td);
    }
    corpo.append(tr);
  }
}

const percentualDe = (texto) => `${fracaoParaPercentual(texto).replace(".", ",")}%`;

function mostrarRegulatorio(reg) {
  preencherTabela(
    "tabela-haircut",
    reg.haircut_por_classe_e_prazo.map((h) => [
      NOMES[h.tipo_ativo] || h.tipo_ativo,
      h.ate_anos === null ? "qualquer prazo" : `até ${h.ate_anos} ano(s)`,
      h.haircut_minimo === h.haircut_maximo
        ? percentualDe(h.haircut_minimo)
        : `${percentualDe(h.haircut_minimo)} a ${percentualDe(h.haircut_maximo)}`,
    ]),
  );
  $("teto-fgc").textContent = reais(reg.teto_do_fgc);
  preencherTabela(
    "tabela-fgc",
    reg.concentracao_por_conglomerado.map((c) => [
      NOMES[c.conglomerado] || c.conglomerado,
      reais(c.valor),
      inteiro.format(c.clientes),
      { texto: inteiro.format(c.clientes_acima_do_teto), alerta: c.clientes_acima_do_teto > 0 },
      { texto: reais(c.excesso_total), alerta: Number(c.excesso_total) > 0 },
    ]),
  );
  preencherTabela(
    "tabela-provisao",
    reg.carteiras_de_provisao.map((p) => [
      NOMES[p.carteira] || p.carteira,
      inteiro.format(p.contas),
      reais(p.saldo),
      reais(p.provisao_primeiro_mes),
      reais(p.provisao_decimo_terceiro_mes),
    ]),
  );
  $("marcacao").textContent = `${reg.marcacao_a_mercado} ${reg.avisos_de_risco}`;
}

function elemento(nome, atributos = {}, texto) {
  const no = document.createElementNS(SVG, nome);
  for (const [chave, valor] of Object.entries(atributos)) no.setAttribute(chave, String(valor));
  if (texto !== undefined) no.textContent = texto;
  return no;
}

// Uma régua por fatia: a pista é o valor antes; o preenchimento, o valor depois; o
// contorno tracejado mostra até onde ia antes do choque. Nome em cima, valores embaixo.
function desenharReguas(svg, fatias, nomear, classe) {
  // Desenha na largura real do elemento: o texto fica do mesmo tamanho no celular.
  const largura = Math.max(280, Math.round(svg.getBoundingClientRect().width) || 640);
  const alturaDaLinha = 64;
  const maior = Math.max(...fatias.map((f) => Number(f.valor_antes)), 1);
  svg.replaceChildren();
  svg.setAttribute("viewBox", `0 0 ${largura} ${fatias.length * alturaDaLinha}`);
  fatias.forEach((fatia, i) => {
    const y = i * alturaDaLinha;
    const antes = (Number(fatia.valor_antes) / maior) * largura;
    const depois = (Number(fatia.valor_depois) / maior) * largura;
    const grupo = elemento("g", { "data-chave": fatia.chave });
    grupo.append(
      elemento("text", { x: 0, y: y + 14, class: "nome" }, nomear(fatia)),
      elemento(
        "text",
        { x: largura, y: y + 14, "text-anchor": "end", class: "valor" },
        `${fatia.percentual_antes.replace(".", ",")}% da garantia, ${inteiro.format(fatia.contas)} clientes`,
      ),
      elemento("rect", { x: 0, y: y + 21, width: largura, height: 16, rx: 3, class: "pista" }),
      elemento("rect", { x: 0, y: y + 21, width: Math.max(depois, 0), height: 16, rx: 3, class: `depois ${classe(fatia)}` }),
      elemento("rect", { x: 0, y: y + 21, width: Math.max(antes, 1), height: 16, rx: 3, class: "antes" }),
      elemento("text", { x: 0, y: y + 52, class: "valor" }, `${reais(fatia.valor_antes)} antes, ${reais(fatia.valor_depois)} depois`),
    );
    const queda = Number(fatia.valor_antes) - Number(fatia.valor_depois);
    if (queda > 0) {
      grupo.append(elemento("text", { x: largura, y: y + 52, "text-anchor": "end", class: "queda" }, `−${reais(queda.toFixed(2))}`));
    }
    svg.append(grupo);
  });
}

function mostrarPremissas(p) {
  const lista = $("premissas");
  lista.replaceChildren();
  const linhas = [
    ["Choques", Object.keys(p.choques).length
      ? Object.entries(p.choques).map(([a, v]) => `${NOMES[a] || a} ${fracaoParaPercentual(v).replace(".", ",")}%`).join("; ")
      : "nenhum"],
    ["Não reforçam", `${fracaoParaPercentual(p.taxa_sem_reforco).replace(".", ",")}% dos descobertos`],
    ["Atraso por agente", Object.keys(p.atraso_por_onde_esta).length
      ? Object.entries(p.atraso_por_onde_esta).map(([a, d]) => `${NOMES[a] || a}: ${d} dias corridos`).join("; ")
      : "nenhum agente"],
    ["Emissor em quebra", p.emissores_em_quebra.length
      ? p.emissores_em_quebra.map((e) => NOMES[e] || e).join(", ")
      : "nenhum emissor"],
    ["Venda fora da casa", `${p.dias_extra_de_execucao} dia(s) a mais que a de dentro`],
    ["Desconto por dia", Object.entries(p.desconto_diario_por_ativo)
      .filter(([, v]) => Number(v) > 0)
      .map(([a, v]) => `${NOMES[a] || a} ${fracaoParaPercentual(v).replace(".", ",")}%`).join("; ")],
  ];
  for (const [nome, valor] of linhas) {
    const dt = document.createElement("dt");
    dt.textContent = nome;
    const dd = document.createElement("dd");
    dd.textContent = valor;
    lista.append(dt, dd);
  }
}

// ------------------------------------------------------------------------ início
async function iniciar() {
  // Ligado antes de tudo: um envio cedo demais não pode recarregar a página.
  $("controles").addEventListener("submit", (evento) => {
    evento.preventDefault();
    if (document.body.dataset.pronta === "true") simularComControles(evento);
  });
  const config = await fetch("/demo/config");
  if (!config.ok) {
    $("erro").textContent = "Demonstração sem parceiro de exemplo. Rode python -m app.seed.";
    return;
  }
  estado.chave = (await config.json()).api_key;
  estado.cenarios = await chamar("GET", "/risco/cenarios");
  montarControles();
  await escolherCenario(estado.cenarios[0]);
  document.body.dataset.pronta = "true";
}

let redesenho = null;
window.addEventListener("resize", () => {
  clearTimeout(redesenho);
  redesenho = setTimeout(() => estado.ultimo && mostrar(estado.ultimo), 150);
});

iniciar();
