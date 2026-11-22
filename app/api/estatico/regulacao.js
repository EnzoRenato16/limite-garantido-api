// Página /regulacao: a matriz regulatória, desenhada a partir de GET /v1/regulacao/matriz.
// Sem framework. Todo texto da API entra por textContent, nunca por innerHTML.
// A lógica das células é a mesma de scripts/gerar_regulacao_md.py (o teste confere as duas).

const CATEGORIAS = {
  norma: "norma",
  pratica_de_mercado: "prática de mercado",
  interpretacao: "interpretação",
  imprensa: "imprensa",
  nao_verificado: "não verificado",
};
const SENTIDO_DOS_SELOS = {
  norma: "Vem de norma citada, com o artigo.",
  pratica_de_mercado: "O que o mercado faz ou divulga.",
  interpretacao: "Leitura do projeto, a validar com o jurídico ou a contabilidade.",
  imprensa: "Só noticiado.",
  nao_verificado: "A fonte lista como ainda sem confirmação oficial.",
};
const COLUNAS = [
  ["o_que_funciona", "O que funciona"],
  ["trilho", "Trilho"],
  ["instrumento", "Instrumento"],
  ["infraestrutura", "Infraestrutura"],
  ["produto", "Produto"],
  ["prazo", "Prazo"],
  ["risco_de_execucao", "Risco de execução"],
  ["norma", "Norma e artigo"],
];
const INFRAESTRUTURAS = {
  b3_balcao: "B3 Balcão",
  b3_central_depositaria: "B3 Central Depositária",
  selic_td_garantia: "Selic (TD Garantia)",
  registradora_ou_b3_escriturador: "Registradora ou B3, com comando do escriturador",
  plataforma_res_conjunta_12: "Plataforma da Res. Conjunta 12 (simulada)",
};
const INSTRUMENTOS = {
  cessao_fiduciaria: "cessão fiduciária",
  alienacao_fiduciaria: "alienação fiduciária",
  penhor: "penhor",
  direito_de_resgate: "direito de resgate",
};
const PRODUTOS = { cartao: "cartão", emprestimo: "empréstimo" };
const CONDICOES = {
  liquidez_diaria: "cartão só com liquidez diária",
  resgate_ate_d1: "cartão só com resgate em D+0 ou D+1",
  ctvm_agente_do_td: "cartão só se a CTVM do grupo for agente de custódia do TD",
};

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...filhos) => {
  const no = Object.assign(document.createElement(tag), props);
  no.append(...filhos);
  return no;
};

const dias = (n) => (n === 1 ? "1 dia útil" : `${n} dias úteis`);

function valorDoPrazo(matriz, tipo, trilho) {
  const regra = matriz.trilhos[trilho];
  if (trilho === "casa") return `${regra.nome}: no mesmo dia`;
  const porAtivo = regra.prazos_por_ativo ?? {};
  const n = tipo in porAtivo ? porAtivo[tipo] : regra.prazo_dias_uteis;
  if (n === null || n === undefined) return `${regra.nome}: sem prazo fixo`;
  const origem = regra.prazo_e_regulatorio ? "prazo da norma" : "ilustrativo do projeto";
  return `${regra.nome}: ${dias(n)} (${origem})`;
}

function celulasDoAtivo(matriz, tipo) {
  const ativo = matriz.ativos[tipo];
  const todas = [...ativo.afirmacoes, ...(ativo.retencao?.afirmacoes ?? [])];
  const celulas = {};
  for (const [coluna] of COLUNAS.slice(0, -1)) {
    celulas[coluna] = {
      valor: null,
      itens: ativo.afirmacoes.filter((a) => a.tema === coluna),
    };
  }
  celulas.trilho.valor = ativo.trilhos.map((t) => matriz.trilhos[t].nome).join(", ");
  celulas.instrumento.valor = ativo.instrumentos.map((i) => INSTRUMENTOS[i]).join(", ");
  celulas.infraestrutura.valor = INFRAESTRUTURAS[ativo.infraestrutura];
  let produto = ativo.produtos.map((p) => PRODUTOS[p]).join(" e ");
  if (ativo.condicao_no_cartao && ativo.produtos.includes("cartao")) {
    produto += ` (${CONDICOES[ativo.condicao_no_cartao]})`;
  } else if (!ativo.produtos.includes("cartao")) {
    produto = `só ${produto}`;
  }
  celulas.produto.valor = produto[0].toUpperCase() + produto.slice(1);
  celulas.prazo.valor = ativo.trilhos.map((t) => valorDoPrazo(matriz, tipo, t)).join("; ");
  const citadas = [];
  for (const a of todas) {
    if (!a.norma) continue;
    const texto = a.norma + (a.artigo ? `, ${a.artigo}` : "");
    if (!citadas.includes(texto)) citadas.push(texto);
  }
  celulas.norma = { valor: null, itens: citadas.map((texto) => ({ texto, categoria: null })) };
  return celulas;
}

function selo(categoria) {
  return el("span", { className: `selo selo-${categoria}`, textContent: CATEGORIAS[categoria] });
}

function itemDeAfirmacao(a) {
  const item = el("li", { className: "afirmacao" });
  item.dataset.categoria = a.categoria ?? "";
  item.append(selo(a.categoria), " ", el("span", { className: "texto", textContent: a.texto }));
  if (a.norma) {
    item.append(" ", el("span", { className: "fonte", textContent: `(${a.norma}${a.artigo ? `, ${a.artigo}` : ""})` }));
  }
  return item;
}

function desenharCelula(coluna, celula) {
  const td = el("td");
  td.dataset.coluna = coluna;
  if (celula.valor) td.append(el("p", { className: "valor", textContent: celula.valor }));
  const lista = el("ul", { className: "afirmacoes" });
  for (const item of celula.itens) {
    if (coluna === "norma") {
      lista.append(el("li", { className: "norma-citada", textContent: item.texto }));
    } else {
      lista.append(itemDeAfirmacao(item));
    }
  }
  if (lista.children.length) td.append(lista);
  return td;
}

function desenharMatriz(matriz) {
  const cabecalho = el("tr");
  cabecalho.append(el("th", { scope: "col", textContent: "Ativo" }));
  for (const [, titulo] of COLUNAS) cabecalho.append(el("th", { scope: "col", textContent: titulo }));
  $("cabecalho-matriz").replaceChildren(cabecalho);

  const corpo = $("corpo-matriz");
  corpo.replaceChildren();
  for (const [tipo, ativo] of Object.entries(matriz.ativos)) {
    const linha = el("tr");
    linha.dataset.ativo = tipo;
    const nome = el("th", { scope: "row" }, el("span", { textContent: ativo.nome }));
    if (!ativo.recomendado) {
      nome.append(el("span", { className: "nao-recomendado", textContent: "não recomendado como garantia" }));
    }
    linha.append(nome);
    const celulas = celulasDoAtivo(matriz, tipo);
    for (const [coluna] of COLUNAS) linha.append(desenharCelula(coluna, celulas[coluna]));
    corpo.append(linha);
  }
}

function desenharTrilhos(matriz) {
  const corpo = $("corpo-trilhos");
  corpo.replaceChildren();
  for (const [id, trilho] of Object.entries(matriz.trilhos)) {
    const linha = el("tr");
    linha.dataset.trilho = id;
    const n = trilho.prazo_dias_uteis;
    const origem = trilho.prazo_e_regulatorio ? "regulatório" : "não regulatório";
    const prazo = n === null ? "por ativo ou sem prazo" : `${dias(n)}, ${origem}`;
    const recusa = trilho.pode_recusar ? "pode recusar" : "não pode recusar";
    const lista = el("ul", { className: "afirmacoes" });
    for (const a of trilho.afirmacoes) lista.append(itemDeAfirmacao(a));
    linha.append(
      el("th", { scope: "row", textContent: trilho.nome }),
      el("td", {}, el("code", { textContent: trilho.rota })),
      el("td", { textContent: `${prazo}; ${recusa}` }),
      el("td", {}, lista),
    );
    corpo.append(linha);
  }
}

function desenharGerais(matriz) {
  const lista = $("gerais");
  lista.replaceChildren(...matriz.afirmacoes.map(itemDeAfirmacao));
  $("numeros-gerais").textContent =
    `Teto do FGC: R$ ${matriz.teto_do_fgc} por titular e conglomerado. ` +
    `Baixa do gravame: pedida em até ${dias(matriz.prazo_da_baixa_dias_uteis)} depois da quitação, ` +
    "da liberação ou da execução.";
}

function desenharLegenda() {
  $("legenda-selos").replaceChildren(
    ...Object.keys(CATEGORIAS).map((c) =>
      el("li", {}, selo(c), " ", el("span", { textContent: SENTIDO_DOS_SELOS[c] })),
    ),
  );
}

async function iniciar() {
  desenharLegenda();
  try {
    const resposta = await fetch("/v1/regulacao/matriz");
    if (!resposta.ok) throw new Error(`Erro HTTP ${resposta.status}`);
    const matriz = await resposta.json();
    desenharMatriz(matriz);
    desenharTrilhos(matriz);
    desenharGerais(matriz);
    document.body.dataset.pronta = "true";
  } catch (erro) {
    $("erro").hidden = false;
    $("erro").textContent = `Não consegui carregar a matriz: ${erro.message}`;
  }
}

iniciar();
