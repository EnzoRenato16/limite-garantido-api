// Página /negocio: modelo econômico por cliente (plataforma e parceiro).
// Sem framework. Texto da API entra por textContent; o SVG é montado com createElementNS.
// O que vai para a API sai dos controles como texto decimal, sem passar por float. Os
// números que a API devolve ("4.13") só são formatados aqui; as larguras das barras são
// proporções para desenhar, não contas de dinheiro.

const SVG = "http://www.w3.org/2000/svg";
const CAMINHO = "/negocio/simulacoes";
const CONJUNTO_INICIAL = "base";
const BARRAS_NO_TORNADO = 10;
// Premissas que os controles mexem, na ordem em que aparecem na coluna "Como ler".
const DOS_CONTROLES = [
  "gasto_mensal",
  "saldo_em_garantia",
  "fracao_externa",
  "cdi_ao_ano",
  "taxa_de_intercambio",
  "fee_por_garantia_ativa",
  "custo_fixo_mensal",
];
const PARTES = {
  cliente: "Cliente",
  lojista: "Lojista",
  parceiro: "Parceiro",
  plataforma: "Plataforma",
  fidc: "FIDC",
  custodiante: "Custodiante",
  fornecedores: "Fornecedores",
  financiadores: "Financiadores",
  governo: "Governo",
  inadimplencia: "Inadimplência",
};

const moeda = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const inteiro = new Intl.NumberFormat("pt-BR");
const $ = (id) => document.getElementById(id);
// Sinal de menos tipográfico (U+2212): a linha não quebra entre ele e o "R$".
const reais = (texto) => moeda.format(Number(texto)).replace(/^-/, "\u2212");
const negativo = (texto) => String(texto).trim().startsWith("-");

const estado = {
  chave: null,
  catalogo: null,
  conjuntoAtual: CONJUNTO_INICIAL,
  ultimoCorpo: null,
  ultimo: null,
  pedidos: 0,
};

function mensagemDeErro(dados, status) {
  const detalhe = dados.detail ?? `Erro HTTP ${status}`;
  const campos = (dados.erros || []).map((e) => e.campo).filter(Boolean).join(", ");
  return campos ? `${detalhe} (${campos})` : detalhe;
}

async function chamar(metodo, caminho, corpo, aceitar = "application/json") {
  return fetch(`/v1${caminho}`, {
    method: metodo,
    headers: {
      Authorization: `Bearer ${estado.chave}`,
      "Content-Type": "application/json",
      Accept: aceitar,
    },
    body: corpo === undefined ? undefined : JSON.stringify(corpo),
  });
}

async function chamarJson(metodo, caminho, corpo) {
  const resposta = await chamar(metodo, caminho, corpo);
  const dados = await resposta.json().catch(() => ({}));
  if (!resposta.ok) throw new Error(mensagemDeErro(dados, resposta.status));
  return dados;
}

// ------------------------------------------------------------------ texto <-> número
// Tudo em inteiros (BigInt) sobre os dígitos digitados: nada de float no que vai à API.
function comCasas(unidades, casas) {
  const sinal = unidades < 0n ? "-" : "";
  const digitos = (unidades < 0n ? -unidades : unidades).toString().padStart(casas + 1, "0");
  return `${sinal}${digitos.slice(0, -casas)}.${digitos.slice(-casas)}`;
}

// "13,65" (% digitado) -> "0.1365"; "42,5" -> "0.4250". Até quatro casas no percentual.
function percentualParaFracao(digitado) {
  const limpo = String(digitado).trim().replace(/\s*%$/, "").replace(",", ".");
  if (!/^\d+(\.\d{1,4})?$/.test(limpo)) return null;
  const [int, frac = ""] = limpo.split(".");
  const casas = Math.max(2, frac.length);
  return comCasas(BigInt(int + frac.padEnd(casas, "0")), casas + 2);
}

// "0.4250" -> "0.5750": o complemento a 1, com as mesmas casas.
function complementar(fracao) {
  const [int, frac] = fracao.split(".");
  const um = 10n ** BigInt(frac.length);
  return comCasas(um - BigInt(int + frac), frac.length);
}

// Reais como a pessoa digita -> "1500.00" (como a API espera). Com vírgula, a vírgula é
// o decimal e os pontos só podem separar milhar, em grupos de 3: "1.500,00", "3,25".
// Sem vírgula, pontos em grupos de 3 são milhar ("1.500", "12.345.678") e um único ponto
// com 1 ou 2 casas é decimal ("3.25", "1.5"). Qualquer outra forma é recusada (null).
function paraDecimal(digitado) {
  const limpo = String(digitado).trim().replace(/^R\$\s*/, "");
  let inteiro;
  let centavos = "";
  if (limpo.includes(",")) {
    const partes = /^(\d{1,3}(?:\.\d{3})+|\d+),(\d{1,2})$/.exec(limpo);
    if (!partes) return null;
    [, inteiro, centavos] = partes;
  } else if (/^\d{1,3}(\.\d{3})+$/.test(limpo)) {
    inteiro = limpo;
  } else {
    const partes = /^(\d+)(?:\.(\d{1,2}))?$/.exec(limpo);
    if (!partes) return null;
    [, inteiro, centavos = ""] = partes;
  }
  return `${BigInt(inteiro.replace(/\./g, ""))}.${centavos.padEnd(2, "0")}`;
}

// "1094.93" -> 109493n: centavos em inteiro, para comparar sem float.
function centavos(texto) {
  const [int, frac = ""] = String(texto).replace("-", "").split(".");
  const valor = BigInt(int + frac.padEnd(2, "0").slice(0, 2));
  return String(texto).trim().startsWith("-") ? -valor : valor;
}

// "1500" -> "1.500,00", para preencher o campo como a pessoa digitaria.
function dinheiroParaCampo(texto) {
  const [int, frac = ""] = String(texto).split(".");
  const agrupado = String(BigInt(int)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${agrupado},${frac.padEnd(2, "0")}`;
}

// "0.1365" -> "13,65"; "0.30" -> "30"; "0.012" -> "1,2".
function fracaoParaCampo(texto) {
  const [int, frac = ""] = String(texto).split(".");
  const casas = frac.padEnd(2, "0");
  const parteInteira = String(BigInt(int + casas.slice(0, 2)));
  const resto = casas.slice(2).replace(/0+$/, "");
  return resto ? `${parteInteira},${resto}` : parteInteira;
}

// Só para exibir: percentual com vírgula.
function percentual(texto, casas = 2) {
  const valor = Number(texto) * 100;
  return `${valor.toLocaleString("pt-BR", { maximumFractionDigits: casas })}%`;
}

function numeroLegivel(texto) {
  return Number(texto).toLocaleString("pt-BR", { maximumFractionDigits: 2 });
}

// Valor de uma premissa na unidade dela, para os rótulos do tornado e das origens.
function valorDaPremissa(texto, unidade) {
  if (unidade.startsWith("R$")) return reais(texto);
  if (unidade.startsWith("fração")) return percentual(texto);
  if (unidade === "1/ano") return `${percentual(texto)} a.a.`;
  if (unidade === "1/mês") return `${percentual(texto)} a.m.`;
  const um = Number(texto) === 1;
  const plurais = {
    dia: um ? "dia" : "dias",
    mês: um ? "mês" : "meses",
    "garantia/cliente": um ? "garantia por cliente" : "garantias por cliente",
  };
  return `${numeroLegivel(texto)} ${plurais[unidade] ?? unidade}`;
}

// --------------------------------------------------------------------- controles
function montarControles() {
  const botoes = $("conjuntos");
  for (const conjunto of estado.catalogo.conjuntos) {
    const botao = document.createElement("button");
    botao.type = "button";
    botao.id = `conjunto-${conjunto.id}`;
    botao.textContent = conjunto.nome;
    botao.setAttribute("aria-pressed", "false");
    botao.addEventListener("click", () => escolherConjunto(conjunto));
    botoes.append(botao);
  }
  const seletor = $("cenario-risco");
  for (const cenario of estado.catalogo.cenarios_de_risco) {
    const opcao = document.createElement("option");
    opcao.value = cenario.id;
    opcao.textContent = cenario.nome;
    seletor.append(opcao);
  }
}

function preencherControles(conjunto) {
  const v = conjunto.valores;
  $("gasto").value = dinheiroParaCampo(v.gasto_mensal);
  $("saldo").value = dinheiroParaCampo(v.saldo_em_garantia);
  $("externa").value = fracaoParaCampo(v.fracao_externa);
  $("cdi").value = fracaoParaCampo(v.cdi_ao_ano);
  $("intercambio").value = fracaoParaCampo(v.taxa_de_intercambio);
  $("fee-api").value = dinheiroParaCampo(v.fee_por_garantia_ativa);
  $("cenario-risco").value = conjunto.cenario_de_risco;
}

const DICA_DE_REAIS = "use vírgula para os centavos e ponto só para o milhar, como 1.500,00";
const DICA_DE_PERCENTUAL = "em %, com vírgula e até 4 casas, como 13,65";

function lerControles() {
  const ler = (id, converter, nome, dica) => {
    const valor = converter($(id).value);
    if (valor === null) {
      throw new Error(`${nome}: valor inválido (${$(id).value || "vazio"}); ${dica}`);
    }
    return valor;
  };
  const reaisDe = (id, nome) => ler(id, paraDecimal, nome, DICA_DE_REAIS);
  const fracaoDe = (id, nome) => ler(id, percentualParaFracao, nome, DICA_DE_PERCENTUAL);
  const externa = fracaoDe("externa", "Garantia em outra instituição");
  return {
    conjunto: estado.conjuntoAtual,
    premissas: {
      gasto_mensal: reaisDe("gasto", "Gasto no cartão"),
      saldo_em_garantia: reaisDe("saldo", "Saldo em garantia"),
      fracao_externa: externa,
      fracao_interna: complementar(externa),
      cdi_ao_ano: fracaoDe("cdi", "CDI"),
      taxa_de_intercambio: fracaoDe("intercambio", "Intercâmbio"),
      fee_por_garantia_ativa: reaisDe("fee-api", "Fee da API"),
    },
    cenario_de_risco: $("cenario-risco").value,
  };
}

function marcarConjunto(id) {
  for (const botao of $("conjuntos").querySelectorAll("button")) {
    botao.setAttribute("aria-pressed", String(botao.id === `conjunto-${id}`));
  }
}

async function escolherConjunto(conjunto) {
  estado.conjuntoAtual = conjunto.id;
  marcarConjunto(conjunto.id);
  $("descricao-conjunto").textContent = conjunto.descricao;
  preencherControles(conjunto);
  await simular({ conjunto: conjunto.id });
}

async function simularComControles() {
  let corpo;
  try {
    corpo = lerControles();
  } catch (erro) {
    $("erro").textContent = erro.message;
    return;
  }
  marcarConjunto(null);
  const nome = estado.catalogo.conjuntos.find((c) => c.id === estado.conjuntoAtual)?.nome ?? "";
  $("descricao-conjunto").textContent =
    `Premissas do conjunto ${nome}, com os controles ajustados à mão.`;
  await simular(corpo);
}

async function simular(corpo) {
  const numero = ++estado.pedidos;
  $("erro").textContent = "";
  document.body.setAttribute("aria-busy", "true");
  try {
    const resultado = await chamarJson("POST", CAMINHO, corpo);
    if (numero !== estado.pedidos) return; // chegou depois de um pedido mais novo
    estado.ultimoCorpo = corpo;
    mostrar(resultado);
  } catch (erro) {
    if (numero === estado.pedidos) $("erro").textContent = erro.message;
  } finally {
    if (numero === estado.pedidos) document.body.removeAttribute("aria-busy");
  }
}

// O mesmo corpo da última simulação que deu certo, agora pedindo CSV.
async function exportar() {
  if (!estado.ultimoCorpo) return;
  $("erro").textContent = "";
  try {
    const resposta = await chamar("POST", CAMINHO, estado.ultimoCorpo, "text/csv");
    if (!resposta.ok) {
      const dados = await resposta.json().catch(() => ({}));
      throw new Error(mensagemDeErro(dados, resposta.status));
    }
    const arquivo = await resposta.blob();
    const endereco = URL.createObjectURL(arquivo);
    const link = document.createElement("a");
    link.href = endereco;
    link.download = "negocio.csv";
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(endereco), 1000);
  } catch (erro) {
    $("erro").textContent = erro.message;
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
  const mes = r.por_cliente_mes;
  const valores = r.premissas.valores;
  const fixo = valores.custo_fixo_mensal;

  numero("margem-plataforma", mes.plataforma.margem, reais(mes.plataforma.margem));
  $("margem-plataforma").classList.toggle("negativo", negativo(mes.plataforma.margem));
  $("detalhe-plataforma").textContent =
    `receita de ${reais(mes.plataforma.receita)} menos ${reais(mes.plataforma.custo)} de custos`;

  numero("margem-parceiro", mes.parceiro.margem, reais(mes.parceiro.margem));
  $("margem-parceiro").classList.toggle("negativo", negativo(mes.parceiro.margem));
  $("detalhe-parceiro").textContent =
    `${reais(r.carteira.parceiro_por_parceiro_mes)} por mês para um parceiro com ` +
    `${inteiro.format(Number(valores.clientes_por_parceiro))} clientes`;

  const equilibrio = r.ponto_de_equilibrio;
  if (equilibrio.clientes === null) {
    numero("equilibrio", "", "não há ponto de equilíbrio");
    $("detalhe-equilibrio").textContent = equilibrio.motivo ?? "";
  } else {
    numero("equilibrio", String(equilibrio.clientes), `${inteiro.format(equilibrio.clientes)} clientes`);
    $("detalhe-equilibrio").textContent =
      `pagam ${reais(fixo)} de custo fixo por mês; a carteira simulada tem ` +
      `${inteiro.format(r.carteira.clientes_ativos)}`;
  }
  $("equilibrio").classList.toggle("sem-equilibrio", equilibrio.clientes === null);

  const carteira = r.carteira;
  numero("resultado-carteira", carteira.plataforma_mes, reais(carteira.plataforma_mes));
  $("resultado-carteira").classList.toggle("negativo", negativo(carteira.plataforma_mes));
  $("detalhe-carteira").textContent =
    `${inteiro.format(carteira.clientes_ativos)} clientes vezes a margem, menos ` +
    `${reais(fixo)} de custo fixo; ${reais(carteira.plataforma_ano)} no ano`;

  // Ao lado de cada campo em %, o que a API usou: uma fração digitada no lugar do
  // percentual ("0,1365" para 13,65%) aparece aqui como 0,1365%.
  $("usado-externa").textContent = `usado: ${fracaoParaCampo(valores.fracao_externa)}% das garantias`;
  $("usado-cdi").textContent = `usado: ${fracaoParaCampo(valores.cdi_ao_ano)}% ao ano`;
  $("usado-intercambio").textContent = `usado: ${fracaoParaCampo(valores.taxa_de_intercambio)}% do gasto`;

  desenharGraficos();
  mostrarContas(r);
  mostrarLinhas(r.linhas);
  mostrarOrigens(valores);
}

function desenharGraficos() {
  const r = estado.ultimo;
  if (!r) return;
  desenharCascata($("grafico-cascata"), r.linhas, r.por_cliente_mes.plataforma);
  desenharFontes($("grafico-fontes"), r.linhas);
  desenharTornado($("grafico-tornado"), r.tornado.slice(0, BARRAS_NO_TORNADO));
}

function mostrarContas(r) {
  const p = r.perda;
  const linhaDaPerda = r.linhas.find((l) => l.id === "perda_esperada");
  $("texto-perda").textContent =
    `Cenário ${p.cenario.nome}: ${percentual(p.taxa_base_ao_ano)} ao ano de perda base, mais ` +
    `${numeroLegivel(p.frequencia_ao_ano)} evento por ano × ${percentual(p.condicional_mista, 4)} ` +
    `de perda por evento, dá ${percentual(p.taxa_ao_ano, 4)} ao ano; dividido por 12 (perda ` +
    `não se compõe como juro), ${percentual(p.taxa_ao_mes, 4)} ao mês. A perda por evento ` +
    `mistura a da garantia interna ` +
    `(${percentual(p.condicional_interna, 4)}) e a da externa (${percentual(p.condicional_externa, 4)}). ` +
    `Sobre o saldo financiado de ${reais(p.saldo_financiado)}, são ` +
    `${linhaDaPerda ? reais(linhaDaPerda.valor) : "R$ 0,00"} por cliente por mês.` +
    (r.rotativo_no_teto ? " O rotativo bateu no teto legal de 100% do valor original." : "");

  // Cada valor de empate vem da API com o motivo de quando não há valor.
  const ie = r.interna_x_externa;
  const fee = ie.fee_de_gravame_externo_para_empatar;
  const gasto = ie.gasto_para_empatar;
  const partes = [
    `Com toda a garantia na plataforma, ela ganha ${reais(ie.margem_interna)} por cliente por ` +
      `mês; com toda a garantia em outra instituição, ${reais(ie.margem_externa)}.`,
  ];
  if (centavos(ie.custo_a_mais_da_externa) > 0n) {
    partes.push(`A externa custa ${reais(ie.custo_a_mais_da_externa)} a mais.`);
    partes.push(
      fee === null ? ie.motivo_fee : `Empata com um fee de gravame externo de ${reais(fee)} por operação.`,
    );
    if (gasto === null) {
      partes.push(ie.motivo_gasto);
    } else {
      const lado = centavos(gasto) < centavos(r.premissas.valores.gasto_mensal) ? "menor" : "maior";
      partes.push(
        `${fee === null ? "Empata" : "Também empata"} com um gasto de ${reais(gasto)} por mês, ` +
          `${lado} que o atual, que ${ie.gasto_cabe_no_limite ? "cabe" : "não cabe"} no limite ` +
          "que a garantia gera.",
      );
    }
  } else {
    partes.push(ie.motivo_fee);
  }
  partes.push(
    `O parceiro ganha ${reais(ie.parceiro_interna)} com a interna e ` +
      `${reais(ie.parceiro_externa)} com a externa.`,
  );
  $("texto-interna-externa").textContent = partes.join(" ");
}

function celula(texto, classe) {
  const td = document.createElement("td");
  td.textContent = texto;
  if (classe) td.className = classe;
  return td;
}

function mostrarLinhas(linhas) {
  const corpo = $("quem-paga-quem").querySelector("tbody");
  corpo.replaceChildren();
  for (const linha of linhas) {
    const tr = document.createElement("tr");
    tr.dataset.linha = linha.id;
    if (linha.destino === "plataforma") tr.className = "receita";
    else if (linha.origem === "plataforma") tr.className = "custo";
    const valor = celula(reais(linha.valor), "valor");
    valor.dataset.valor = linha.valor;
    tr.append(
      celula(linha.nome, "nome"),
      celula(`${PARTES[linha.origem] ?? linha.origem} → ${PARTES[linha.destino] ?? linha.destino}`, "partes"),
      valor,
      celula(linha.formula, "formula"),
    );
    corpo.append(tr);
  }
}

function mostrarOrigens(valores) {
  const lista = $("origens");
  lista.replaceChildren();
  for (const nome of DOS_CONTROLES) {
    const esquema = estado.catalogo.premissas.find((p) => p.nome === nome);
    if (!esquema) continue;
    const dt = document.createElement("dt");
    dt.textContent = `${esquema.nome_exibicao}: ${valorDaPremissa(valores[nome], esquema.unidade)}`;
    const dd = document.createElement("dd");
    dd.textContent = esquema.fonte ? `${esquema.origem} Fonte: ${esquema.fonte}.` : esquema.origem;
    lista.append(dt, dd);
  }
}

// ----------------------------------------------------------------------- gráficos
function elemento(nome, atributos = {}, texto) {
  const no = document.createElementNS(SVG, nome);
  for (const [chave, valor] of Object.entries(atributos)) no.setAttribute(chave, String(valor));
  if (texto !== undefined) no.textContent = texto;
  return no;
}

// Desenha na largura real do elemento: o texto fica do mesmo tamanho no celular.
function larguraDe(svg) {
  return Math.max(280, Math.round(svg.getBoundingClientRect().width) || 640);
}

function escala(minimo, maximo, largura) {
  const amplitude = maximo - minimo || 1;
  return (valor) => ((valor - minimo) / amplitude) * largura;
}

function retangulo(x1, x2, y, altura, classe) {
  const x = Math.min(x1, x2);
  return elemento("rect", { x, y, width: Math.max(Math.abs(x2 - x1), 1), height: altura, rx: 2, class: classe });
}

// Cascata horizontal: uma linha por degrau, para caber o nome inteiro no celular. A receita
// sai do zero, cada custo desce a partir de onde o anterior parou, e a margem volta ao zero.
function desenharCascata(svg, linhas, visao) {
  const largura = larguraDe(svg);
  const alturaDaLinha = 46;
  const receita = Number(visao.receita);
  const margem = Number(visao.margem);
  const custos = linhas
    .filter((l) => l.origem === "plataforma")
    .sort((a, b) => Number(b.valor) - Number(a.valor));
  const degraus = [{ tipo: "receita", nome: "Receita da plataforma", texto: visao.receita, de: 0, ate: receita }];
  let atual = receita;
  for (const custo of custos) {
    degraus.push({ tipo: "custo", nome: custo.nome, texto: custo.valor, de: atual, ate: atual - Number(custo.valor), id: custo.id });
    atual -= Number(custo.valor);
  }
  degraus.push({ tipo: "margem", nome: "Margem da plataforma", texto: visao.margem, de: 0, ate: margem });

  const x = escala(Math.min(0, margem), Math.max(receita, 0), largura);
  svg.replaceChildren();
  svg.setAttribute("viewBox", `0 0 ${largura} ${degraus.length * alturaDaLinha}`);
  // O zero só aparece quando a margem fica negativa; senão ele é a própria borda.
  if (margem < 0) {
    svg.append(elemento("line", { x1: x(0), x2: x(0), y1: 16, y2: degraus.length * alturaDaLinha, class: "zero" }));
  }
  degraus.forEach((degrau, i) => {
    const y = i * alturaDaLinha;
    const sinal = degrau.tipo === "custo" ? "−" : "";
    const classeDaBarra =
      degrau.tipo === "margem" ? (margem < 0 ? "negativo" : "positivo") : degrau.tipo;
    const grupo = elemento("g", { class: `degrau ${degrau.tipo}`, "data-valor": degrau.texto });
    if (degrau.id) grupo.setAttribute("data-linha", degrau.id);
    grupo.append(
      elemento("text", { x: 0, y: y + 14, class: degrau.tipo === "custo" ? "valor" : "nome" }, degrau.nome),
      elemento("text", { x: largura, y: y + 14, "text-anchor": "end", class: `quantia ${classeDaBarra}` }, `${sinal}${reais(degrau.texto)}`),
      retangulo(x(degrau.de), x(degrau.ate), y + 21, 16, classeDaBarra),
    );
    if (i < degraus.length - 1) {
      // Liga o fim deste degrau ao começo do próximo (o último custo para onde a margem acaba).
      const xFim = x(degrau.ate);
      grupo.append(elemento("line", { x1: xFim, x2: xFim, y1: y + 37, y2: y + alturaDaLinha + 21, class: "conector" }));
    }
    svg.append(grupo);
  });
}

// Receita da plataforma por fonte: as linhas que chegam à plataforma, da maior à menor.
function desenharFontes(svg, linhas) {
  const largura = larguraDe(svg);
  const alturaDaLinha = 46;
  const receitas = linhas
    .filter((l) => l.destino === "plataforma")
    .sort((a, b) => Number(b.valor) - Number(a.valor));
  const total = receitas.reduce((soma, l) => soma + Number(l.valor), 0) || 1;
  const maior = Math.max(...receitas.map((l) => Number(l.valor)), 0.01);
  svg.replaceChildren();
  svg.setAttribute("viewBox", `0 0 ${largura} ${Math.max(receitas.length, 1) * alturaDaLinha}`);
  receitas.forEach((linha, i) => {
    const y = i * alturaDaLinha;
    const proporcao = (Number(linha.valor) / total) * 100;
    const parte = proporcao > 0 && proporcao < 1 ? "menos de 1%" : `${Math.round(proporcao)}%`;
    const grupo = elemento("g", { class: "barra", "data-linha": linha.id, "data-valor": linha.valor });
    grupo.append(
      elemento("text", { x: 0, y: y + 14, class: "nome" }, linha.nome),
      elemento("text", { x: largura, y: y + 14, "text-anchor": "end", class: "valor" }, `${reais(linha.valor)} (${parte})`),
      elemento("rect", { x: 0, y: y + 21, width: largura, height: 14, rx: 2, class: "pista" }),
      elemento("rect", { x: 0, y: y + 21, width: (Number(linha.valor) / maior) * largura, height: 14, rx: 2, class: "receita" }),
    );
    svg.append(grupo);
  });
}

// Tornado: uma barra por premissa, do menor ao maior valor da margem entre os extremos e a
// base. A cor diz qual extremo da premissa leva a cada ponta; o traço vertical é a base.
// Camadas: primeiro as pistas, depois as linhas de zero e de base, por fim as barras e os
// textos (com contorno da cor do papel, para as linhas não riscarem as letras).
function desenharTornado(svg, barras) {
  const largura = larguraDe(svg);
  const topo = 24;
  const alturaDaLinha = 62;
  const linhaExtra = 16; // quando os dois rótulos não cabem lado a lado
  const base = barras.length ? Number(barras[0].margem_base) : 0;
  const pontos = barras.flatMap((b) => [Number(b.minimo), Number(b.maximo)]);
  const x = escala(Math.min(0, base, ...pontos), Math.max(0, base, ...pontos), largura);
  svg.replaceChildren();
  const pistas = elemento("g", { class: "pistas" });
  const linhas = elemento("g", { class: "referencias" });
  svg.append(pistas, linhas);

  let y = topo;
  for (const barra of barras) {
    const baixo = Number(barra.margem_no_baixo);
    const alto = Number(barra.margem_no_alto);
    pistas.append(elemento("rect", { x: 0, y: y + 21, width: largura, height: 14, rx: 2, class: "pista" }));
    const grupo = elemento("g", { class: "barra", "data-premissa": barra.premissa, "data-valor": barra.amplitude });
    const nome = barra.cortado_pela_coerencia ? `${barra.nome_exibicao} (faixa cortada)` : barra.nome_exibicao;
    const segmentos = [
      [Math.abs(baixo - base), retangulo(x(base), x(baixo), y + 21, 14, "baixo")],
      [Math.abs(alto - base), retangulo(x(base), x(alto), y + 21, 14, "alto")],
    ].sort((a, b) => b[0] - a[0]); // o menor por cima, quando os dois caem do mesmo lado
    // O rótulo da ponta de margem menor fica à esquerda, como a barra.
    const rotulos = [
      ["baixo", `${valorDaPremissa(barra.valor_baixo, barra.unidade)}: ${reais(barra.margem_no_baixo)}`, baixo],
      ["alto", `${valorDaPremissa(barra.valor_alto, barra.unidade)}: ${reais(barra.margem_no_alto)}`, alto],
    ].sort((a, b) => a[2] - b[2]);
    const esquerdo = elemento("text", { x: 0, y: y + 51, class: `valor ${rotulos[0][0]}` }, rotulos[0][1]);
    const direito = elemento("text", { x: largura, y: y + 51, "text-anchor": "end", class: `valor ${rotulos[1][0]}` }, rotulos[1][1]);
    grupo.append(elemento("text", { x: 0, y: y + 14, class: "nome" }, nome), ...segmentos.map(([, no]) => no), esquerdo, direito);
    svg.append(grupo);
    let altura = alturaDaLinha;
    if (esquerdo.getComputedTextLength() + direito.getComputedTextLength() + 16 > largura) {
      direito.setAttribute("y", String(y + 51 + linhaExtra));
      altura += linhaExtra;
    }
    y += altura;
  }
  const fundo = Math.max(y, topo + alturaDaLinha);
  svg.setAttribute("viewBox", `0 0 ${largura} ${fundo}`);

  if (Math.abs(x(0) - x(base)) > 1) {
    linhas.append(elemento("line", { x1: x(0), x2: x(0), y1: topo - 6, y2: fundo, class: "zero" }));
    if (Math.abs(x(0) - x(base)) > 90) {
      const lado = x(0) < largura * 0.15 ? "start" : x(0) > largura * 0.85 ? "end" : "middle";
      svg.append(elemento("text", { x: x(0), y: 12, "text-anchor": lado, class: "valor" }, "margem zero"));
    }
  }
  linhas.append(elemento("line", { x1: x(base), x2: x(base), y1: topo - 6, y2: fundo, class: "base" }));
  const ancora = x(base) < largura * 0.25 ? "start" : x(base) > largura * 0.75 ? "end" : "middle";
  svg.append(elemento("text", { x: x(base), y: 12, "text-anchor": ancora, class: "rotulo-base" }, `base ${reais(base)}`));

  const cortadas = barras.filter((b) => b.cortado_pela_coerencia).map((b) => b.nome_exibicao);
  const nota = $("nota-tornado");
  nota.hidden = cortadas.length === 0;
  nota.textContent = cortadas.length
    ? `Faixa cortada pela coerência (${cortadas.join(", ")}): o gasto não passa do saldo × LTV ` +
      "e o saldo não fica abaixo do gasto ÷ LTV, então o extremo foi trazido para dentro."
    : "";
}

// ------------------------------------------------------------------------ início
async function iniciar() {
  // Ligado antes de tudo: um envio cedo demais não pode recarregar a página.
  $("controles").addEventListener("submit", (evento) => {
    evento.preventDefault();
    if (document.body.dataset.pronta === "true") simularComControles();
  });
  $("exportar").addEventListener("click", () => {
    if (document.body.dataset.pronta === "true") exportar();
  });
  const config = await fetch("/demo/config");
  if (!config.ok) {
    $("erro").textContent = "Demonstração sem parceiro de exemplo. Rode python -m app.seed.";
    return;
  }
  estado.chave = (await config.json()).api_key;
  try {
    estado.catalogo = await chamarJson("GET", "/negocio/premissas");
  } catch (erro) {
    $("erro").textContent = erro.message;
    return;
  }
  montarControles();
  const inicial =
    estado.catalogo.conjuntos.find((c) => c.id === CONJUNTO_INICIAL) ?? estado.catalogo.conjuntos[0];
  await escolherConjunto(inicial);
  document.body.dataset.pronta = "true";
}

let redesenho = null;
window.addEventListener("resize", () => {
  clearTimeout(redesenho);
  redesenho = setTimeout(desenharGraficos, 150);
});

iniciar();
