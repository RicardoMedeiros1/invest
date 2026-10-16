"use strict";
// Painel da carteira: a lógica (funções puras, testadas com o Node) vem primeiro; o desenho da página, depois.
// As regras de escolha e de compra são as mesmas de scanner_dividendos_b3.py e carteira_simples.py.

const VERSAO_PAGINA = 3;            // precisa ser igual a VERSAO_DADOS em painel_pagina.py
const FUSO_BRASILIA_MS = -3 * 3600 * 1000;   // Brasília não tem horário de verão desde 2019
const ABERTURA_MIN = 10 * 60;       // pregão da B3: 10h às 18h, só para o "aberto/fechado" (feriados vêm dos dados)
const FECHAMENTO_MIN = 18 * 60;

// ---------- formatação (igual a brl() e pct() do Python) ----------

function fmtNum(x, casas) {
  const s = Math.abs(x).toFixed(casas);
  const partes = s.split(".");
  const inteiro = partes[0].replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  const negativo = x < 0 && Number(s) !== 0;
  return (negativo ? "-" : "") + inteiro + (partes[1] ? "," + partes[1] : "");
}
function fmtBRL(x) { return "R$ " + fmtNum(x, 2); }
function fmtPct(x, sinal) {
  if (x === null || x === undefined || Number.isNaN(x)) return "n/d";
  const r = Math.round(x * 10) / 10;
  return (sinal && r > 0 ? "+" : "") + fmtNum(r, 1) + "%";
}
function fmtData(iso) { const [a, m, d] = iso.split("-"); return d + "/" + m + "/" + a; }
function pad2(n) { return String(n).padStart(2, "0"); }

// ---------- mercado aberto ou fechado ----------

function brasilia(ms) { return new Date(ms + FUSO_BRASILIA_MS); }   // use só os métodos getUTC*

function estadoMercado(agoraMs, dataPrecos) {
  const d = brasilia(agoraMs);
  const hoje = d.getUTCFullYear() + "-" + pad2(d.getUTCMonth() + 1) + "-" + pad2(d.getUTCDate());
  const dia = d.getUTCDay();
  const min = d.getUTCHours() * 60 + d.getUTCMinutes();
  const naJanela = dia >= 1 && dia <= 5 && min >= ABERTURA_MIN && min < FECHAMENTO_MIN;
  if (naJanela && dataPrecos === hoje) return { estado: "aberto", hoje };
  if (naJanela) return { estado: "sem-preco-de-hoje", hoje };   // feriado, ou a primeira atualização do dia ainda não saiu
  return { estado: "fechado", hoje };
}

function horaBrasilia(ms) { const d = brasilia(ms); return pad2(d.getUTCHours()) + ":" + pad2(d.getUTCMinutes()); }

// ---------- escolha dos ativos e plano de compra (iguais ao Python) ----------

function escolherCandidatos(ranking, topN, nFiis) {
  const tickers = ranking.map((r) => r.t);
  if (nFiis === null || nFiis === undefined) return tickers.slice(0, topN);
  const fiis = ranking.filter((r) => r.classe === "FII").map((r) => r.t);
  const acoes = ranking.filter((r) => r.classe !== "FII").map((r) => r.t);
  let escolhidos = acoes.slice(0, topN - nFiis).concat(fiis.slice(0, nFiis));
  const sobra = tickers.filter((t) => !escolhidos.includes(t));
  escolhidos = escolhidos.concat(sobra.slice(0, Math.max(topN - escolhidos.length, 0)));
  return escolhidos.sort((a, b) => tickers.indexOf(a) - tickers.indexOf(b));
}

function planoDeCompras(precos, ordem, valor, meses, investidoInicial) {
  if (!ordem.length) return [];
  const investido = {};
  ordem.forEach((t) => { investido[t] = (investidoInicial && investidoInicial[t]) || 0; });   // a carteira virtual já tem posições
  let caixa = 0;
  const plano = [];
  for (let mes = 1; mes <= meses; mes++) {
    caixa += valor;
    const compras = {};
    let gasto = 0;
    for (;;) {
      let alvo = ordem[0];
      for (const t of ordem) if (investido[t] < investido[alvo]) alvo = t;   // empate: o primeiro da ordem do ranking
      if (precos[alvo] > caixa + 1e-9) break;
      caixa -= precos[alvo];
      investido[alvo] += precos[alvo];
      gasto += precos[alvo];
      compras[alvo] = (compras[alvo] || 0) + 1;
    }
    plano.push({ mes, compras, gasto, sobra: caixa });
  }
  return plano;
}

function estimativaDividendos(dys, total) {
  const v = dys.filter((d) => d !== null && d !== undefined && !Number.isNaN(d));
  return v.length ? (v.reduce((a, b) => a + b, 0) / v.length) / 100 * total : 0;
}

function cotasQueCabem(valor, preco) { return preco > 0 ? Math.floor(valor / preco + 1e-9) : 0; }

// ---------- listas ----------

const ORDENS = {
  nota: (a) => (a.pos === null ? Infinity : a.pos),   // menor posição primeiro
  dia: (a) => (a.dia === null ? Infinity : -a.dia),
  mes: (a) => (a.mes === null ? Infinity : -a.mes),
  ano: (a) => (a.ano === null ? Infinity : -a.ano),
  dy: (a) => (a.dy === null ? Infinity : -a.dy),
  caida: (a) => (a.topo === null ? Infinity : a.topo),           // apostas: as mais abaixo do topo de 12 meses primeiro
  vol: (a) => (a.oscilacao === null ? Infinity : -a.oscilacao),
  liq: (a) => (a.liquidez === null ? Infinity : -a.liquidez),
};
const ORDENS_RANKING = ["nota", "dia", "mes", "ano", "dy"];
const ORDENS_APOSTAS = ["ano", "mes", "caida", "vol", "liq"];

function ordenarAtivos(lista, ordem) {
  const chave = ORDENS[ordem] || ORDENS.nota;
  return lista.map((a, i) => ({ a, i })).sort((x, y) => (chave(x.a) - chave(y.a)) || (x.i - y.i)).map((x) => x.a);
}

function filtrarAtivos(lista, { classe = "todos", busca = "", reprovados = false } = {}) {
  const q = busca.trim().toUpperCase();
  return lista.filter((a) => (reprovados || a.ok) && (classe === "todos" || a.classe === classe) && (!q || a.t.includes(q)));
}

function filtrarApostas(lista, tipo) {
  return lista.filter((a) => tipo === "todos" || (tipo === "ação" ? a.classe === "ação" : a.classe !== "ação"));
}

function maisMenos(ativos, chave, n) {
  const v = ativos.filter((a) => a.ok && a[chave] !== null && a[chave] !== undefined);
  const sobem = v.slice().sort((a, b) => b[chave] - a[chave]).slice(0, n);
  const caem = v.filter((a) => a[chave] < 0).sort((a, b) => a[chave] - b[chave]).slice(0, n);
  return { sobem, caem };
}

function caminhoSerie(serie, largura, altura, margem) {
  const m = margem === undefined ? 2 : margem;
  const v = serie.filter((x) => x !== null && x !== undefined && !Number.isNaN(x));
  if (v.length < 2) return "";
  const min = Math.min(...v);
  const max = Math.max(...v);
  const faixa = max - min || 1;
  return v.map((y, i) => {
    const px = m + (largura - 2 * m) * (i / (v.length - 1));
    const py = altura - m - (altura - 2 * m) * ((y - min) / faixa);
    return (i ? "L" : "M") + px.toFixed(1) + " " + py.toFixed(1);
  }).join("");
}

function idadeMin(agoraMs, geradoEm) { return Math.max(0, Math.round((agoraMs - Date.parse(geradoEm)) / 60000)); }

function atrasado(estado, idade, intervaloMin) { return estado === "aberto" && idade > Math.max(45, 3 * intervaloMin); }

// ---------- carteira virtual: as contas (funções puras) ----------

const VERSAO_CARTEIRA = 1;
const MAX_ORDENS = 2000;
const MAX_HISTORICO = 1000;
const LIMITE_VALOR = 1e9;
const RE_DATA = /^\d{4}-\d{2}-\d{2}$/;
const RE_TICKER = /^[A-Z0-9]{4,8}$/;

function carteiraNova() { return { versao: VERSAO_CARTEIRA, ordens: [], historico: [], proximo_id: 1 }; }

// Unidades que a pessoa tinha na véspera de `data`: quem compra no dia do ex-dividendo não recebe.
function unidadesAntes(ordens, t, data) {
  let q = 0;
  for (const o of ordens) {
    if (o.t === t && o.data < data) q += o.tipo === "compra" ? o.qtd : o.tipo === "venda" ? -o.qtd : 0;
  }
  return q;
}

// dividendos: { ticker: [[AAAA-MM-DD, valor por unidade], ...] }. Entram no caixa na data ex-dividendo.
function dividendosDaCarteira(ordens, dividendos) {
  const itens = [];
  let total = 0;
  const tickers = new Set(ordens.filter((o) => o.t).map((o) => o.t));
  for (const t of tickers) {
    for (const [data, v] of (dividendos[t] || [])) {
      const q = unidadesAntes(ordens, t, data);
      if (q > 0) { itens.push({ t, data, qtd: q, por_unidade: v, valor: q * v }); total += q * v; }
    }
  }
  itens.sort((a, b) => (a.data < b.data ? -1 : a.data > b.data ? 1 : 0));
  return { itens, total };
}

function calcularCarteira(ordens, precos, dividendos) {
  const pos = {};
  let caixa = 0, aportado = 0, realizado = 0;
  for (const o of ordens) {
    if (o.tipo === "aporte") { caixa += o.valor; aportado += o.valor; continue; }
    const p = pos[o.t] || (pos[o.t] = { t: o.t, qtd: 0, custo: 0 });
    if (o.tipo === "compra") {
      caixa -= o.qtd * o.preco; p.qtd += o.qtd; p.custo += o.qtd * o.preco;
    } else {
      const medio = p.qtd ? p.custo / p.qtd : 0;
      realizado += (o.preco - medio) * o.qtd; caixa += o.qtd * o.preco;
      p.custo -= medio * o.qtd; p.qtd -= o.qtd;
      if (p.qtd <= 0) { p.qtd = 0; p.custo = 0; }
    }
  }
  const div = dividendosDaCarteira(ordens, dividendos || {});
  caixa += div.total;
  const posicoes = Object.values(pos).filter((p) => p.qtd > 0).map((p) => {
    const atual = precos[p.t] === undefined ? null : precos[p.t];
    const valor = atual === null ? p.custo : p.qtd * atual;
    return { t: p.t, qtd: p.qtd, preco_medio: p.custo / p.qtd, preco_atual: atual, custo: p.custo, valor,
      resultado: valor - p.custo, resultado_pct: p.custo > 0 ? (valor - p.custo) / p.custo * 100 : 0, sem_preco: atual === null };
  }).sort((a, b) => b.valor - a.valor || (a.t < b.t ? -1 : 1));
  const valorPosicoes = posicoes.reduce((acc, p) => acc + p.valor, 0);
  const total = caixa + valorPosicoes;
  return { caixa, aportado, realizado, dividendos: div.total, dividendosItens: div.itens, posicoes, valorPosicoes, total,
    resultado: total - aportado, resultado_pct: aportado > 0 ? (total - aportado) / aportado * 100 : null };
}

// Devolve o texto do problema, ou null se a ordem pode ser registrada.
function validarOrdem(ordens, o, dividendos) {
  if (o.tipo === "aporte") {
    return Number.isFinite(o.valor) && o.valor > 0 && o.valor <= LIMITE_VALOR ? null : "Informe um valor de aporte maior que zero.";
  }
  if (o.tipo !== "compra" && o.tipo !== "venda") return "Tipo de ordem inválido.";
  if (typeof o.t !== "string" || !RE_TICKER.test(o.t)) return "Escolha um ativo da lista.";
  if (!Number.isInteger(o.qtd) || o.qtd < 1 || o.qtd > 1e7) return "A quantidade precisa ser um número inteiro de 1 em diante (não existe meia ação).";
  if (!Number.isFinite(o.preco) || o.preco <= 0 || o.preco >= 1e7) return "Informe um preço maior que zero.";
  const c = calcularCarteira(ordens, {}, dividendos);
  if (o.tipo === "compra") {
    const custo = o.qtd * o.preco;
    return custo <= c.caixa + 1e-6 ? null : "Saldo insuficiente: a compra custa " + fmtBRL(custo) + " e o caixa tem " + fmtBRL(Math.max(c.caixa, 0)) + ". Faça um aporte antes.";
  }
  const p = c.posicoes.find((x) => x.t === o.t);
  const tem = p ? p.qtd : 0;
  return o.qtd <= tem ? null : "Você tem " + tem + " unidade(s) de " + o.t + " e tentou vender " + o.qtd + ".";
}

// Registra uma ordem (sem alterar a carteira recebida). Devolve { carteira } ou { erro }.
function registrarOrdem(carteira, o, data, dividendos) {
  const erro = validarOrdem(carteira.ordens, o, dividendos);
  if (erro) return { erro };
  if (carteira.ordens.length >= MAX_ORDENS) return { erro: "Limite de " + MAX_ORDENS + " ordens atingido. Exporte e comece outra carteira." };
  const nova = o.tipo === "aporte" ? { id: carteira.proximo_id, data, tipo: "aporte", valor: o.valor }
    : { id: carteira.proximo_id, data, tipo: o.tipo, t: o.t, qtd: o.qtd, preco: o.preco };
  return { carteira: Object.assign({}, carteira, { ordens: carteira.ordens.concat([nova]), proximo_id: carteira.proximo_id + 1 }) };
}

function desfazerUltima(carteira) {
  if (!carteira.ordens.length) return carteira;
  return Object.assign({}, carteira, { ordens: carteira.ordens.slice(0, -1) });
}

// Um ponto por dia de preços (o último valor do dia vence). Devolve a mesma lista se nada mudou.
function registrarSnapshot(historico, d, total, aportado) {
  if (!RE_DATA.test(d) || !Number.isFinite(total) || !Number.isFinite(aportado)) return historico;
  const t = Math.round(total * 100) / 100, a = Math.round(aportado * 100) / 100;
  const ultimo = historico[historico.length - 1];
  if (ultimo && ultimo.d === d && ultimo.total === t && ultimo.aportado === a) return historico;
  const base = ultimo && ultimo.d === d ? historico.slice(0, -1) : historico;
  return base.concat([{ d, total: t, aportado: a }]).slice(-MAX_HISTORICO);
}

// Quantas unidades de cada ativo do plano a carteira compraria agora com o caixa que tem.
function comprasPeloPlano(ranking, config, precos, caixa, custoPorAtivo) {
  const ordem = escolherCandidatos(ranking, config.ativos, config.fiis);
  const plano = planoDeCompras(precos, ordem, Math.max(caixa, 0), 1, custoPorAtivo);
  return plano.length ? plano[0].compras : {};
}

function aporteFeitoNoMes(carteira, dataPrecos) {
  return carteira.ordens.some((o) => o.tipo === "aporte" && o.data.slice(0, 7) === dataPrecos.slice(0, 7));
}

function exportarCarteira(carteira, exportadoEm) {
  return JSON.stringify({ versao: VERSAO_CARTEIRA, exportado_em: exportadoEm, ordens: carteira.ordens, historico: carteira.historico }, null, 2);
}

// Lê o texto de um arquivo (ou do navegador) e só aceita o que for coerente; reconstrói cada campo, nunca copia o objeto de fora.
function importarCarteira(texto, dividendos) {
  let bruto;
  try { bruto = JSON.parse(texto); } catch (e) { return { erro: "O arquivo não é um JSON válido." }; }
  if (!bruto || typeof bruto !== "object" || bruto.versao !== VERSAO_CARTEIRA || !Array.isArray(bruto.ordens)) {
    return { erro: "Este arquivo não é de uma carteira virtual deste painel." };
  }
  if (bruto.ordens.length > MAX_ORDENS) return { erro: "O arquivo tem ordens demais (o limite é " + MAX_ORDENS + ")." };
  const ordens = [];
  let anterior = "";
  for (const o of bruto.ordens) {
    if (!o || typeof o !== "object" || typeof o.data !== "string" || !RE_DATA.test(o.data) || o.data < anterior) return { erro: "Há ordens com data inválida ou fora de ordem." };
    anterior = o.data;
    const id = ordens.length + 1;
    if (o.tipo === "aporte") {
      if (!Number.isFinite(o.valor) || o.valor <= 0 || o.valor > LIMITE_VALOR) return { erro: "Há um aporte com valor inválido." };
      ordens.push({ id, data: o.data, tipo: "aporte", valor: o.valor });
    } else if (o.tipo === "compra" || o.tipo === "venda") {
      if (typeof o.t !== "string" || !RE_TICKER.test(o.t) || !Number.isInteger(o.qtd) || o.qtd < 1 || o.qtd > 1e7
        || !Number.isFinite(o.preco) || o.preco <= 0 || o.preco >= 1e7) return { erro: "Há uma compra ou venda com dados inválidos." };
      ordens.push({ id, data: o.data, tipo: o.tipo, t: o.t, qtd: o.qtd, preco: o.preco });
    } else {
      return { erro: "Há uma ordem de tipo desconhecido." };
    }
  }
  const unidades = {};
  for (const o of ordens) {
    if (o.tipo === "compra") unidades[o.t] = (unidades[o.t] || 0) + o.qtd;
    if (o.tipo === "venda") {
      if ((unidades[o.t] || 0) < o.qtd) return { erro: "O arquivo vende mais " + o.t + " do que a carteira tinha." };
      unidades[o.t] -= o.qtd;
    }
  }
  if (calcularCarteira(ordens, {}, dividendos || {}).caixa < -0.01) return { erro: "As contas do arquivo não fecham: o caixa ficaria negativo." };
  const historico = [];
  for (const h of Array.isArray(bruto.historico) ? bruto.historico.slice(-MAX_HISTORICO) : []) {
    if (h && typeof h.d === "string" && RE_DATA.test(h.d) && Number.isFinite(h.total) && Number.isFinite(h.aportado)) {
      historico.push({ d: h.d, total: h.total, aportado: h.aportado });
    }
  }
  return { carteira: { versao: VERSAO_CARTEIRA, ordens, historico, proximo_id: ordens.length + 1 } };
}

// Duas linhas na mesma escala (para comparar o valor da carteira com o que foi aportado).
function caminhosComuns(series, largura, altura, margem) {
  const m = margem === undefined ? 4 : margem;
  const todos = [].concat(...series).filter((x) => Number.isFinite(x));
  if (!todos.length) return series.map(() => "");
  const min = Math.min(...todos), max = Math.max(...todos), faixa = max - min || 1;
  return series.map((serie) => (serie.length < 2 ? "" : serie.map((y, i) => {
    const px = m + (largura - 2 * m) * (i / (serie.length - 1));
    const py = altura - m - (altura - 2 * m) * ((y - min) / faixa);
    return (i ? "L" : "M") + px.toFixed(1) + " " + py.toFixed(1);
  }).join("")));
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { fmtNum, fmtBRL, fmtPct, fmtData, estadoMercado, horaBrasilia, escolherCandidatos, planoDeCompras,
    estimativaDividendos, cotasQueCabem, ordenarAtivos, filtrarAtivos, filtrarApostas, maisMenos, caminhoSerie, idadeMin, atrasado, VERSAO_PAGINA,
    carteiraNova, unidadesAntes, dividendosDaCarteira, calcularCarteira, validarOrdem, registrarOrdem, desfazerUltima, registrarSnapshot,
    comprasPeloPlano, aporteFeitoNoMes, exportarCarteira, importarCarteira, caminhosComuns };
}

// ---------- a página ----------

if (typeof document !== "undefined") {
  const NS_SVG = "http://www.w3.org/2000/svg";
  const CHAVE_PREFS = "carteira:preferencias";
  const PADRAO_UI = { classe: "todos", ordem: "nota", periodo: "dia", tipo: "todos", ordemApostas: "ano", reserva: 10 };
  let dados = null;
  let ui = Object.assign({}, PADRAO_UI, { busca: "", reprovados: false });
  let config = null;
  let temporizador = null;
  let buscando = false;

  const $ = (id) => document.getElementById(id);

  function el(tag, attrs, ...filhos) {
    const e = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") e.className = v;
        else if (k === "style") e.style.cssText = v;
        else e.setAttribute(k, v === true ? "" : v);
      }
    }
    for (const f of filhos.flat()) {
      if (f === null || f === undefined || f === false) continue;
      e.append(f.nodeType ? f : document.createTextNode(String(f)));
    }
    return e;
  }
  function limpar(no) { while (no.firstChild) no.removeChild(no.firstChild); return no; }
  function linkSeguro(url) { return /^https?:\/\//i.test(url) ? url : null; }
  function caminhoRelativo(url) { return !/^([a-z][a-z0-9+.-]*:|\/\/)/i.test(url); }

  function guardar(chave, valor) { try { localStorage.setItem(chave, JSON.stringify(valor)); } catch (e) { /* sem armazenamento: tudo bem */ } }
  function ler(chave) { try { return JSON.parse(localStorage.getItem(chave)); } catch (e) { return null; } }

  // ----- peças de desenho -----

  function variacao(v) {
    if (v === null || v === undefined) return el("span", { class: "neutro" }, "n/d");
    const r = Math.round(v * 10) / 10;
    const classe = r > 0 ? "sobe" : r < 0 ? "desce" : "neutro";
    return el("span", { class: classe + " num" }, (r > 0 ? "▲ " : r < 0 ? "▼ " : "") + fmtPct(v, true));
  }

  function sparkline(serie, classe) {
    const svg = document.createElementNS(NS_SVG, "svg");
    svg.setAttribute("viewBox", "0 0 92 28");
    svg.setAttribute("class", "mini");
    svg.setAttribute("role", "img");
    const v = serie.filter((x) => x !== null);
    const sobe = v.length > 1 && v[v.length - 1] >= v[0];
    svg.setAttribute("aria-label", v.length > 1 ? "Preço nos últimos meses: " + (sobe ? "em alta" : "em queda") : "Sem histórico");
    const p = document.createElementNS(NS_SVG, "path");
    p.setAttribute("d", caminhoSerie(serie, 92, 28));
    p.setAttribute("class", classe || (sobe ? "sobe" : "desce"));
    svg.append(p);
    return svg;
  }

  function rotuloDia(est) { return est === "aberto" ? "Hoje" : "Último pregão"; }

  // ----- topo -----

  function estadoAgora() { return estadoMercado(Date.now(), dados.data_precos); }

  function desenharTopo() {
    const est = estadoAgora();
    const pilula = $("status-mercado");
    pilula.className = "pilula" + (est.estado === "aberto" ? " aberto" : "");
    pilula.textContent = est.estado === "aberto" ? "Mercado aberto" : est.estado === "fechado" ? "Mercado fechado" : "Sem preços de hoje";
    const idade = idadeMin(Date.now(), dados.gerado_em);
    const velho = atrasado(est.estado, idade, dados.intervalo_min);
    const quando = idade < 1 ? "agora" : idade < 60 ? "há " + idade + " min" : "às " + horaBrasilia(Date.parse(dados.gerado_em));
    const txt = $("atualizacao");
    txt.className = "atualizacao" + (velho ? " atraso" : "");
    txt.textContent = velho
      ? "Atenção: preços de " + horaBrasilia(Date.parse(dados.gerado_em)) + " (atualização atrasada)"
      : "Preços de " + fmtData(dados.data_precos) + " · atualizado " + quando;
    $("btn-atualizar").hidden = !dados.dados_url;
    document.querySelectorAll("[data-periodo=dia]").forEach((b) => { b.textContent = rotuloDia(est.estado); });
  }

  // ----- mercado -----

  function cartao(rotulo, valor, detalhe, extra) {
    return el("div", { class: "cartao" }, el("div", { class: "rotulo" }, rotulo), el("div", { class: "valor num" }, valor),
      detalhe ? el("div", { class: "detalhe" }, detalhe) : null, extra || null);
  }

  function desenharMercado() {
    const est = estadoAgora().estado;
    const dia = rotuloDia(est).toLowerCase();
    const cartoes = limpar($("cartoes"));
    for (const i of dados.indices) {
      const valor = i.unidade === "R$" ? fmtBRL(i.valor) : fmtNum(i.valor, 0) + " pts";
      cartoes.append(cartao(i.nome, valor, [variacao(i.dia_pct), " ", el("small", null, dia)], sparkline(i.serie)));
    }
    const ok = dados.ativos.filter((a) => a.ok && a.dia !== null);
    if (ok.length) {
      const altas = ok.filter((a) => a.dia > 0).length;
      const barra = el("div", { class: "proporcao", role: "img", "aria-label": altas + " sobem e " + (ok.length - altas) + " não sobem" },
        el("i", { style: "width:" + (altas / ok.length * 100).toFixed(1) + "%" }));
      cartoes.append(cartao("Aprovados em alta (" + dia + ")", altas + " de " + ok.length, el("small", null, "os outros ficaram iguais ou caíram"), barra));
      const { sobem, caem } = maisMenos(dados.ativos, "dia", 1);
      const linhas = [];
      if (sobem[0]) linhas.push(el("div", null, el("b", null, sobem[0].t), " ", variacao(sobem[0].dia)));
      if (caem[0]) linhas.push(el("div", null, el("b", null, caem[0].t), " ", variacao(caem[0].dia)));
      cartoes.append(cartao("Maior alta e maior queda", "", null, el("div", { class: "detalhe" }, linhas)));
    }
    const mapa = limpar($("mapa"));
    const aprovados = dados.ativos.filter((a) => a.ok).sort((a, b) => a.pos - b.pos);
    for (const a of aprovados) {
      const v = a.dia;
      const sinal = v === null ? null : v > 0 ? "--up" : v < 0 ? "--down" : null;
      const forca = v === null ? 0 : Math.min(Math.abs(v) / 3, 1);
      const estilo = sinal ? "background:color-mix(in srgb, var(" + sinal + ") " + Math.round(12 + 43 * forca) + "%, var(--surf))" : null;
      mapa.append(el("div", { class: "telha", style: estilo, title: a.t + ": " + fmtBRL(a.preco) }, el("b", null, a.t), variacao(v)));
    }
    $("mapa-titulo").textContent = aprovados.length
      ? "Cada quadrado é um ativo aprovado nos filtros; a cor mostra a variação (" + dia + ")." : "Nenhum ativo passou nos filtros.";
  }

  // ----- ranking -----

  function desenharRanking() {
    const lista = ordenarAtivos(filtrarAtivos(dados.ativos, ui), ui.ordem);
    const t = limpar($("tabela-ranking"));
    t.append(el("thead", null, el("tr", null,
      el("th", null, "Ativo"), el("th", { class: "n" }, "Preço"), el("th", { class: "n" }, rotuloDia(estadoAgora().estado)), el("th", { class: "n" }, "No mês"),
      el("th", { class: "n" }, "12 meses"), el("th", null, "Dividendos em 12 meses"), el("th", null, "Preço (3 meses)"),
      el("th", { class: "n" }, fmtBRL(config.valor) + " compram"))));
    const corpo = el("tbody");
    for (const a of lista) {
      const cotas = cotasQueCabem(config.valor, a.preco);
      const cabe = cotas === 0 ? "não cabe (1 custa " + fmtBRL(a.preco) + ")" : cotas + " (" + fmtBRL(cotas * a.preco) + ")";
      const largura = a.dy === null ? 0 : Math.min(a.dy / 12 * 100, 100);
      corpo.append(el("tr", { class: a.ok ? null : "reprovado" },
        el("td", { class: "col-ativo" }, el("b", null, a.t), el("span", { class: "selo" }, a.classe),
          el("small", null, a.ok ? "  " + a.pos + "º" : "  não passou nos filtros")),
        el("td", { class: "n", "data-label": "Preço" }, fmtBRL(a.preco)),
        el("td", { class: "n", "data-label": rotuloDia(estadoAgora().estado) }, variacao(a.dia)),
        el("td", { class: "n", "data-label": "No mês" }, variacao(a.mes)),
        el("td", { class: "n", "data-label": "12 meses" }, variacao(a.ano)),
        el("td", { class: "col-barra", "data-label": "Dividendos em 12 meses" },
          el("span", { class: "barra" }, el("i", { style: "width:" + largura.toFixed(0) + "%" })),
          el("small", null, fmtPct(a.dy) + " do preço · " + fmtBRL(a.div12) + " por unidade")),
        el("td", { class: "sem-rotulo" }, sparkline(a.serie)),
        el("td", { class: "n", "data-label": fmtBRL(config.valor) + " compram" }, cabe)));
    }
    t.append(corpo);
    $("aviso-vazio").hidden = lista.length > 0;
  }

  // ----- quem mais subiu e caiu -----

  function blocoMovimento(titulo, lista, chave, maximo) {
    const caixa = el("div", { class: "painel" }, el("h3", null, titulo));
    if (!lista.length) { caixa.append(el("p", { class: "nota" }, "Nenhum nesta situação.")); return caixa; }
    const t = el("table", { class: "lista" });
    for (const a of lista) {
      const v = a[chave];
      const w = Math.min(Math.abs(v) / maximo * 100, 100).toFixed(0);
      t.append(el("tr", null, el("td", null, el("b", null, a.t)),
        el("td", null, el("span", { class: "vb" },
          el("span", { class: "l" }, v < 0 ? el("i", { style: "width:" + w + "%" }) : null),
          el("span", { class: "r" }, v > 0 ? el("i", { style: "width:" + w + "%" }) : null))),
        el("td", { class: "n" }, variacao(v))));
    }
    caixa.append(t);
    return caixa;
  }

  function desenharMovimento() {
    const { sobem, caem } = maisMenos(dados.ativos, ui.periodo, 5);
    const maximo = Math.max(0.01, ...sobem.concat(caem).map((a) => Math.abs(a[ui.periodo])));
    limpar($("movimento-listas")).append(blocoMovimento("Mais subiram", sobem, ui.periodo, maximo), blocoMovimento("Mais caíram", caem, ui.periodo, maximo));
  }

  // ----- plano de compra -----

  function desenharPlano() {
    const saida = limpar($("plano-saida"));
    const ranking = dados.ativos.filter((a) => a.ok && a.preco > 0).sort((a, b) => a.pos - b.pos);
    const ordem = escolherCandidatos(ranking, config.ativos, config.fiis);
    if (!ordem.length) { saida.append(el("p", { class: "nota" }, "Nenhum ativo passou nos filtros hoje, então não há o que comprar.")); return; }
    const precos = {};
    ranking.forEach((a) => { precos[a.t] = a.preco; });
    const plano = planoDeCompras(precos, ordem, config.valor, config.meses);
    const texto = (c) => Object.entries(c).map(([t, n]) => n + " " + t).join(" + ");
    if (ordem.length < config.ativos) {
      saida.append(el("p", { class: "nota" }, "Só " + ordem.length + " ativos passaram nos filtros, então o plano usa " + ordem.length + "."));
    }
    saida.append(el("div", { class: "chips" }, ordem.map((t) => el("span", { class: "etiqueta" }, t))));
    const mes1 = plano[0];
    if (mes1 && Object.keys(mes1.compras).length) {
      saida.append(el("div", { class: "acao" }, "Neste mês, compre: ", el("b", null, texto(mes1.compras)),
        el("div", { class: "nota" }, "Gasto " + fmtBRL(mes1.gasto) + " · sobra " + fmtBRL(mes1.sobra) + " no caixa. Confira o preço no aplicativo antes de comprar.")));
      const fatias = el("div", { class: "fatias", role: "img", "aria-label": "Divisão do dinheiro do mês 1" });
      const legenda = el("ul", { class: "legenda" });
      Object.entries(mes1.compras).forEach(([t, n], i) => {
        const parte = n * precos[t];
        fatias.append(el("i", { class: "fatia-" + ((i % 8) + 1), style: "flex:" + parte.toFixed(2) }));
        legenda.append(el("li", null, el("i", { class: "fatia-" + ((i % 8) + 1) }), t + " " + (parte / mes1.gasto * 100).toFixed(0) + "%"));
      });
      saida.append(fatias, legenda);
    } else {
      saida.append(el("div", { class: "acao" }, "Neste mês nenhum ativo cabe no caixa: ", el("b", null, "guarde o dinheiro"), " para o mês seguinte."));
    }
    const linhas = plano.map((m) => el("tr", null, el("td", { class: "col-ativo" }, "Mês " + m.mes),
      el("td", { class: "largo", "data-label": "Comprar" }, Object.keys(m.compras).length ? texto(m.compras) : "nenhuma compra (guarda o caixa)"),
      el("td", { class: "n", "data-label": "Gasto" }, fmtBRL(m.gasto)), el("td", { class: "n", "data-label": "Sobra" }, fmtBRL(m.sobra))));
    saida.append(el("div", { class: "rolagem" }, el("table", { class: "tabela empilha" },
      el("thead", null, el("tr", null, el("th", null, "Quando"), el("th", null, "Comprar"), el("th", { class: "n" }, "Gasto"), el("th", { class: "n" }, "Sobra"))),
      el("tbody", null, linhas))));
    const dys = ordem.map((t) => ranking.find((a) => a.t === t).dy);
    const total = config.valor * 12;
    const ano = estimativaDividendos(dys, total);
    saida.append(el("p", { class: "nota" }, "Usa os preços de agora nos " + config.meses + " meses. O gasto de um mês pode passar de " + fmtBRL(config.valor)
      + " porque inclui a sobra dos meses anteriores."));
    saida.append(el("p", { class: "nota" }, el("b", null, "Quanto pode render: "), "se você investir " + fmtBRL(total) + " em 12 meses nesses ativos e eles pagarem o mesmo dividendo do último ano, são cerca de "
      + fmtBRL(ano) + " por ano (" + fmtBRL(ano / 12) + " por mês). É o que aconteceu no último ano, não uma promessa: o preço também sobe e desce."));
  }

  // ----- apostas -----

  function desenharApostas() {
    const todas = dados.apostas || [];
    const reservaRS = config.valor * ui.reserva / 100;
    const resumo = $("apostas-reserva");
    resumo.textContent = ui.reserva > 0
      ? "Com " + ui.reserva + "% de " + fmtBRL(config.valor) + " por mês, são " + fmtBRL(reservaRS) + " para apostas: só para calcular quantas unidades cabem, não é recomendação. O resto do aporte segue o plano de compra."
      : "Reservando 0%, nenhum dinheiro do aporte vai para apostas.";
    const t = limpar($("tabela-apostas"));
    const vazio = $("apostas-vazio");
    const lista = ordenarAtivos(filtrarApostas(todas, ui.tipo), ui.ordemApostas);
    vazio.hidden = lista.length > 0;
    vazio.textContent = todas.length ? "Nenhuma aposta nesse tipo." : "Não consegui baixar as apostas desta vez. Elas voltam na próxima atualização.";
    if (!lista.length) return;
    const limite = dados.liquidez_baixa || 1000000;
    t.append(el("thead", null, el("tr", null,
      el("th", null, "Ativo"), el("th", { class: "n" }, "Preço"), el("th", { class: "n" }, rotuloDia(estadoAgora().estado)), el("th", { class: "n" }, "No mês"),
      el("th", { class: "n" }, "12 meses"), el("th", null, "Oscilação ao ano"), el("th", { class: "n" }, "Pior queda em 12 meses"),
      el("th", null, "Preço (12 meses)"), el("th", { class: "n" }, fmtBRL(reservaRS) + " compram"))));
    const corpo = el("tbody");
    for (const a of lista) {
      const cotas = reservaRS > 0 ? cotasQueCabem(reservaRS, a.preco) : 0;
      const cabe = reservaRS <= 0 ? "n/d" : cotas === 0 ? "não cabe" : cotas + " (" + fmtBRL(cotas * a.preco) + ")";
      const poucaLiquidez = a.liquidez !== null && a.liquidez < limite;
      corpo.append(el("tr", null,
        el("td", { class: "col-ativo" }, el("span", null, el("b", null, a.t), el("span", { class: "selo" }, a.classe),
          poucaLiquidez ? el("span", { class: "selo aviso" }, "pouco negociada") : null, el("span", { class: "setor" }, a.setor))),
        el("td", { class: "n", "data-label": "Preço" }, fmtBRL(a.preco)),
        el("td", { class: "n", "data-label": rotuloDia(estadoAgora().estado) }, variacao(a.dia)),
        el("td", { class: "n", "data-label": "No mês" }, variacao(a.mes)),
        el("td", { class: "n", "data-label": "12 meses" }, variacao(a.ano)),
        el("td", { class: "col-barra", "data-label": "Oscilação ao ano" },
          el("span", { class: "barra risco" }, el("i", { style: "width:" + (a.oscilacao === null ? 0 : Math.min(a.oscilacao, 100)).toFixed(0) + "%" })),
          el("small", null, a.oscilacao === null ? "histórico curto" : fmtPct(a.oscilacao))),
        el("td", { class: "n largo", "data-label": "Pior queda em 12 meses" }, el("div", null, a.queda12 === null ? "n/d" : variacao(a.queda12),
          a.topo === null ? null : el("small", { class: "bloco" }, a.topo > -0.05 ? "no topo do ano" : "hoje " + fmtPct(-a.topo) + " abaixo do topo"))),
        el("td", { class: "sem-rotulo" }, sparkline(a.serie)),
        el("td", { class: "n", "data-label": fmtBRL(reservaRS) + " compram" }, cabe)));
    }
    t.append(corpo);
  }

  // ----- carteira virtual (a simulação fica só neste navegador) -----

  const CHAVE_CARTEIRA = "carteira:virtual";
  let carteira = null;
  let virtual = { montada: null, msg: null, topo: null, baixo: null };

  function mapaPrecos() {
    const m = {};
    for (const a of dados.ativos.concat(dados.apostas || [])) m[a.t] = a.preco;
    return m;
  }
  function mapaDividendos() {
    const m = {};
    for (const a of dados.ativos.concat(dados.apostas || [])) if (a.div && a.div.length) m[a.t] = a.div;
    return m;
  }
  function lerCarteiraGuardada() {
    try {
      const texto = localStorage.getItem(CHAVE_CARTEIRA);
      return texto ? (importarCarteira(texto, mapaDividendos()).carteira || null) : null;
    } catch (e) { return null; }
  }
  function guardarCarteira() {
    try {
      if (carteira) localStorage.setItem(CHAVE_CARTEIRA, JSON.stringify(carteira)); else localStorage.removeItem(CHAVE_CARTEIRA);
      return true;
    } catch (e) { return false; }
  }
  function mensagem(texto, erro) {
    if (!virtual.msg) return;
    virtual.msg.textContent = texto;
    virtual.msg.className = "msg " + (erro ? "erro" : "ok");
  }
  function atualizarSnapshot() {
    if (!carteira || !carteira.ordens.length) return;
    const c = calcularCarteira(carteira.ordens, mapaPrecos(), mapaDividendos());
    const h = registrarSnapshot(carteira.historico, dados.data_precos, c.total, c.aportado);
    if (h !== carteira.historico) { carteira = Object.assign({}, carteira, { historico: h }); guardarCarteira(); }
  }
  function registrar(o) {
    const r = registrarOrdem(carteira, o, dados.data_precos, mapaDividendos());
    if (r.erro) { mensagem(r.erro, true); return false; }
    carteira = r.carteira;
    atualizarSnapshot();
    if (!guardarCarteira()) mensagem("Registrado, mas o navegador não deixou guardar: use Exportar para não perder.", true);
    desenharVirtualNumeros();
    return true;
  }
  const descricaoOrdem = (o) => o.tipo === "aporte" ? "Aporte de " + fmtBRL(o.valor)
    : (o.tipo === "compra" ? "Compra de " : "Venda de ") + o.qtd + " " + o.t + " a " + fmtBRL(o.preco);

  function campoNumero(rotulo, atributos) {
    const entrada = el("input", Object.assign({ type: "number", inputmode: "decimal" }, atributos));
    return { campo: el("label", { class: "campo" }, rotulo, entrada), entrada };
  }

  function formularioCriar() {
    const valor = campoNumero("Aporte inicial (R$)", { min: "1", step: "10" });
    valor.entrada.value = config.valor;
    const criar = el("button", { type: "button", class: "botao principal" }, "Criar carteira virtual");
    criar.addEventListener("click", () => {
      const v = numero(valor.entrada, 1, LIMITE_VALOR, config.valor);
      const r = registrarOrdem(carteiraNova(), { tipo: "aporte", valor: v }, dados.data_precos, mapaDividendos());
      carteira = r.carteira;
      atualizarSnapshot(); guardarCarteira(); desenharVirtual();
      mensagem("Carteira criada com " + fmtBRL(v) + " de caixa. Agora compre pelo plano ou escolha os ativos.", false);
    });
    return el("div", { class: "painel" }, el("h3", null, "Comece uma carteira virtual"),
      el("p", { class: "nota" }, "Você começa só com caixa. Depois compra ativos pelo preço do painel e acompanha o resultado."),
      el("div", { class: "linha-form" }, valor.campo, criar, botaoImportar()));
  }

  function botaoImportar() {
    const arquivo = el("input", { type: "file", accept: ".json,application/json", hidden: true });
    arquivo.addEventListener("change", async () => {
      const f = arquivo.files && arquivo.files[0];
      arquivo.value = "";
      if (!f) return;
      const r = importarCarteira(await f.text(), mapaDividendos());
      if (r.erro) { mensagem(r.erro, true); return; }
      if (carteira && !window.confirm("Trocar a carteira virtual atual pela do arquivo? A atual será apagada deste navegador.")) return;
      carteira = r.carteira; atualizarSnapshot(); guardarCarteira(); desenharVirtual();
      mensagem("Carteira importada: " + carteira.ordens.length + " ordens.", false);
    });
    const botao = el("button", { type: "button", class: "botao" }, "Importar de um arquivo");
    botao.addEventListener("click", () => arquivo.click());
    return el("span", null, botao, arquivo);
  }

  function formularioAcoes() {
    const precos = mapaPrecos();
    const tickers = Object.keys(precos).sort();
    // aportar
    const ap = campoNumero("Aportar (R$)", { min: "1", step: "10" });
    ap.entrada.value = config.valor;
    const bAportar = el("button", { type: "button", class: "botao" }, "Aportar");
    bAportar.addEventListener("click", () => {
      const v = numero(ap.entrada, 0, LIMITE_VALOR, 0);
      if (registrar({ tipo: "aporte", valor: v })) mensagem("Aporte de " + fmtBRL(v) + " registrado.", false);
    });
    const dica = el("p", { class: "nota" });
    // comprar pelo plano
    const previa = el("div");
    const bPlano = el("button", { type: "button", class: "botao" }, "Ver compra pelo plano");
    bPlano.addEventListener("click", () => {
      limpar(previa);
      const c = calcularCarteira(carteira.ordens, precos, mapaDividendos());
      const ranking = dados.ativos.filter((a) => a.ok && a.preco > 0).sort((a, b) => a.pos - b.pos);
      const custo = {};
      c.posicoes.forEach((p) => { custo[p.t] = p.custo; });
      const compras = comprasPeloPlano(ranking, config, precos, c.caixa, custo);
      const lista = Object.entries(compras);
      if (!lista.length) {
        previa.append(el("div", { class: "previa" }, "Com " + fmtBRL(Math.max(c.caixa, 0)) + " em caixa ainda não cabe nenhuma unidade dos ativos do plano. Guarde para o próximo aporte."));
        return;
      }
      const gasto = lista.reduce((acc, [t, n]) => acc + n * precos[t], 0);
      const confirmar = el("button", { type: "button", class: "botao principal" }, "Confirmar compra");
      const cancelar = el("button", { type: "button", class: "botao" }, "Cancelar");
      cancelar.addEventListener("click", () => limpar(previa));
      confirmar.addEventListener("click", () => {
        let feito = carteira;
        for (const [t, n] of lista) {
          const r = registrarOrdem(feito, { tipo: "compra", t, qtd: n, preco: precos[t] }, dados.data_precos, mapaDividendos());
          if (r.erro) { mensagem(r.erro, true); return; }
          feito = r.carteira;
        }
        carteira = feito; atualizarSnapshot(); guardarCarteira(); limpar(previa); desenharVirtualNumeros();
        mensagem("Compra pelo plano registrada: " + lista.map(([t, n]) => n + " " + t).join(" + ") + ".", false);
      });
      previa.append(el("div", { class: "previa" }, el("div", null, "Vai comprar: ", el("b", null, lista.map(([t, n]) => n + " " + t).join(" + "))),
        el("div", { class: "nota" }, "Gasto " + fmtBRL(gasto) + "; sobram " + fmtBRL(c.caixa - gasto) + ". O plano reforça o ativo com menos dinheiro aplicado, contando o que você já tem."),
        el("div", { class: "linha-form" }, confirmar, cancelar)));
    });
    // ordem manual
    const tipo = el("select", null, el("option", { value: "compra" }, "Comprar"), el("option", { value: "venda" }, "Vender"));
    const ativo = el("input", { type: "text", list: "virtual-ativos", placeholder: "ex.: PETR4", autocomplete: "off", maxlength: "8" });
    const lista = el("datalist", { id: "virtual-ativos" }, tickers.map((t) => el("option", { value: t }, fmtBRL(precos[t]))));
    const qtd = campoNumero("Quantidade", { min: "1", step: "1", inputmode: "numeric" });
    const preco = campoNumero("Preço (R$)", { min: "0.01", step: "0.01" });
    const estimativa = el("p", { class: "nota" });
    let precoAuto = true;
    const atualizar = () => {
      const t = ativo.value.trim().toUpperCase();
      if (precoAuto && precos[t] !== undefined) preco.entrada.value = precos[t];
      const q = Number(qtd.entrada.value), pr = Number(preco.entrada.value);
      estimativa.textContent = t && Number.isFinite(q) && Number.isFinite(pr) && q > 0 && pr > 0 ? "Total: " + fmtBRL(q * pr) : "";
    };
    ativo.addEventListener("input", () => { precoAuto = true; atualizar(); });
    preco.entrada.addEventListener("input", () => { precoAuto = false; atualizar(); });
    qtd.entrada.addEventListener("input", atualizar);
    const bRegistrar = el("button", { type: "button", class: "botao principal" }, "Registrar");
    bRegistrar.addEventListener("click", () => {
      const o = { tipo: tipo.value, t: ativo.value.trim().toUpperCase(), qtd: Number(qtd.entrada.value), preco: Number(preco.entrada.value) };
      if (registrar(o)) { mensagem(descricaoOrdem(o) + " registrada.", false); qtd.entrada.value = ""; estimativa.textContent = ""; }
    });
    // manutenção
    const bDesfazer = el("button", { type: "button", class: "botao" }, "Desfazer a última ordem");
    bDesfazer.addEventListener("click", () => {
      if (!carteira.ordens.length) { mensagem("Não há ordem para desfazer.", true); return; }
      const ultima = carteira.ordens[carteira.ordens.length - 1];
      carteira = desfazerUltima(carteira); guardarCarteira(); desenharVirtualNumeros();
      mensagem("Desfeito: " + descricaoOrdem(ultima) + ".", false);
    });
    const bExportar = el("button", { type: "button", class: "botao" }, "Exportar (guardar uma cópia)");
    bExportar.addEventListener("click", () => {
      const blob = new Blob([exportarCarteira(carteira, new Date().toISOString())], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = el("a", { href: url, download: "carteira-virtual-" + dados.data_precos + ".json" });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      mensagem("Arquivo gerado. Guarde-o: é a única cópia fora deste navegador.", false);
    });
    const bApagar = el("button", { type: "button", class: "botao perigo" }, "Apagar a carteira");
    bApagar.addEventListener("click", () => {
      if (!window.confirm("Apagar a carteira virtual deste navegador? Isso não pode ser desfeito (use Exportar antes, se quiser guardar).")) return;
      carteira = null; guardarCarteira(); desenharVirtual();
      mensagem("Carteira apagada.", false);
    });
    virtual.dica = dica;
    return el("div", null,
      el("div", { class: "virtual-acoes" },
        el("div", { class: "painel" }, el("h3", null, "Aporte"), el("div", { class: "linha-form" }, ap.campo, bAportar), dica),
        el("div", { class: "painel" }, el("h3", null, "Compra pelo plano"),
          el("p", { class: "nota" }, "Usa o caixa da carteira e os ativos do plano (" + config.ativos + " ativos, " + config.fiis + " FIIs; mude lá em cima)."), bPlano, previa),
        el("div", { class: "painel" }, el("h3", null, "Ordem manual"),
          el("div", { class: "linha-form" }, el("label", { class: "campo" }, "Tipo", tipo), el("label", { class: "campo" }, "Ativo", ativo, lista), qtd.campo, preco.campo, bRegistrar), estimativa)),
      el("div", { class: "manutencao" }, bDesfazer, bExportar, botaoImportar(), bApagar));
  }

  function graficoVirtual(historico) {
    if (historico.length < 2) return el("p", { class: "nota" }, "O gráfico aparece quando a carteira tiver pelo menos dois dias de preços.");
    const W = 600, H = 170;
    const [cTotal, cAportado] = caminhosComuns([historico.map((h) => h.total), historico.map((h) => h.aportado)], W, H, 6);
    const todos = historico.map((h) => h.total).concat(historico.map((h) => h.aportado));
    const svg = document.createElementNS(NS_SVG, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("class", "grafico");
    svg.setAttribute("role", "img");
    const ultimo = historico[historico.length - 1];
    svg.setAttribute("aria-label", "Valor da carteira virtual: de " + fmtBRL(historico[0].total) + " em " + fmtData(historico[0].d) + " para " + fmtBRL(ultimo.total) + " em " + fmtData(ultimo.d) + "; total aportado " + fmtBRL(ultimo.aportado));
    const caminho = (d, classe) => { const p = document.createElementNS(NS_SVG, "path"); p.setAttribute("d", d); p.setAttribute("class", classe); return p; };
    svg.append(caminho(cAportado, "aportado"), caminho(cTotal, "total"));
    return el("div", null,
      el("p", { class: "nota" }, "De " + fmtData(historico[0].d) + " a " + fmtData(ultimo.d) + ": mínimo " + fmtBRL(Math.min(...todos)) + ", máximo " + fmtBRL(Math.max(...todos)) + "."),
      svg,
      el("p", { class: "nota" }, el("span", { class: "chip-linha" }), "valor total da carteira   ", el("span", { class: "chip-linha tracejada" }), "total aportado"));
  }

  function desenharVirtualNumeros() {
    if (!carteira || !virtual.topo) return;
    const c = calcularCarteira(carteira.ordens, mapaPrecos(), mapaDividendos());
    limpar(virtual.topo).append(el("div", { class: "cartoes" },
      cartao("Valor total", fmtBRL(c.total), el("small", null, "caixa + ativos")),
      cartao("Total aportado", fmtBRL(c.aportado), null),
      cartao("Resultado", fmtBRL(c.resultado), c.resultado_pct === null ? null : variacao(c.resultado_pct)),
      cartao("Caixa", fmtBRL(c.caixa), el("small", null, c.caixa > 0.005 ? "ainda não aplicado" : "sem caixa")),
      cartao("Dividendos recebidos", fmtBRL(c.dividendos), el("small", null, "simulados, já no caixa"))));
    if (virtual.dica) {
      virtual.dica.textContent = aporteFeitoNoMes(carteira, dados.data_precos) ? "Você já fez o aporte deste mês." : "Você ainda não fez o aporte deste mês.";
    }
    const baixo = limpar(virtual.baixo);
    if (!c.posicoes.length) {
      baixo.append(el("p", { class: "nota" }, "Ainda não há ativos na carteira. Use \"Compra pelo plano\" ou \"Ordem manual\"."));
    } else {
      baixo.append(el("h3", null, "Seus ativos"), el("div", { class: "painel rolagem" }, el("table", { class: "tabela empilha", id: "tabela-posicoes" },
        el("thead", null, el("tr", null, el("th", null, "Ativo"), el("th", { class: "n" }, "Quantidade"), el("th", { class: "n" }, "Preço médio"),
          el("th", { class: "n" }, "Preço agora"), el("th", { class: "n" }, "Valor"), el("th", { class: "n" }, "Resultado"))),
        el("tbody", null, c.posicoes.map((p) => el("tr", null,
          el("td", { class: "col-ativo" }, p.t),
          el("td", { class: "n", "data-label": "Quantidade" }, String(p.qtd)),
          el("td", { class: "n", "data-label": "Preço médio" }, fmtBRL(p.preco_medio)),
          el("td", { class: "n", "data-label": "Preço agora" }, p.sem_preco ? "sem preço" : fmtBRL(p.preco_atual)),
          el("td", { class: "n", "data-label": "Valor" }, fmtBRL(p.valor)),
          el("td", { class: "n largo", "data-label": "Resultado" }, el("div", null, fmtBRL(p.resultado), " ", variacao(p.resultado_pct)))))))));
    }
    baixo.append(el("h3", { style: "margin-top:18px" }, "Evolução"), graficoVirtual(carteira.historico));
    const mov = carteira.ordens.map((o) => ({ data: o.data, texto: descricaoOrdem(o), valor: o.tipo === "aporte" ? o.valor : o.tipo === "compra" ? -o.qtd * o.preco : o.qtd * o.preco }))
      .concat(c.dividendosItens.map((d) => ({ data: d.data, texto: "Dividendos de " + d.t + ": " + d.qtd + " x " + fmtBRL(d.por_unidade), valor: d.valor })));
    mov.sort((a, b) => (a.data < b.data ? 1 : a.data > b.data ? -1 : 0));
    baixo.append(el("h3", { style: "margin-top:18px" }, "Movimentações (as mais recentes primeiro)"), el("div", { class: "painel rolagem" }, el("table", { class: "tabela empilha", id: "tabela-movimentos" },
      el("thead", null, el("tr", null, el("th", null, "Data"), el("th", null, "O que aconteceu"), el("th", { class: "n" }, "Caixa"))),
      el("tbody", null, mov.slice(0, 30).map((m) => el("tr", null, el("td", { class: "col-ativo" }, fmtData(m.data)),
        el("td", { class: "largo", "data-label": "O que aconteceu" }, m.texto),
        el("td", { class: "n", "data-label": "Caixa" }, (m.valor >= 0 ? "+" : "-") + fmtBRL(Math.abs(m.valor)))))))));
  }

  function desenharVirtual() {
    const raiz = limpar($("virtual-saida"));
    raiz.append(el("div", { class: "aviso-risco", role: "note" }, el("b", null, "Simulação com dinheiro de mentira. "),
      "Ela fica só neste navegador (nada vai para o servidor) e some se você limpar os dados do navegador: use Exportar para guardar uma cópia. ",
      "Os preços são os do painel (com atraso de alguns minutos); não há corretagem, taxas da B3 nem imposto, e toda ordem sai pelo preço mostrado, o que na vida real nem sempre acontece. ",
      "Dividendos entram no caixa na data ex (quando o ativo passa a negociar sem direito ao pagamento), para quem tinha o ativo no dia anterior. Não é recomendação."));
    virtual.msg = el("p", { class: "msg", role: "status", "aria-live": "polite" });
    virtual.topo = null; virtual.baixo = null; virtual.dica = null;
    virtual.montada = !!carteira;
    if (!carteira) { raiz.append(virtual.msg, formularioCriar()); return; }
    virtual.topo = el("div"); virtual.baixo = el("div");
    raiz.append(virtual.msg, virtual.topo, formularioAcoes(), virtual.baixo);
    desenharVirtualNumeros();
  }

  // Redesenha só os números quando a seção já está montada (assim não apaga o que a pessoa está digitando).
  function desenharVirtualSeguro() {
    if (virtual.montada === !!carteira && (virtual.montada ? virtual.topo : true)) desenharVirtualNumeros(); else desenharVirtual();
  }

  // ----- passado, corretoras, rodapé -----

  function desenharPassado() {
    const anos = dados.passado.anos.map((a) => el("tr", null, el("td", { class: "col-ativo" }, String(a.ano)),
      el("td", { class: "n", "data-label": "Meses" }, String(a.meses)),
      el("td", { class: "largo", "data-label": "As que mais apareceram" }, el("div", { class: "chips" }, a.top.map(([t, n]) => el("span", { class: "etiqueta" }, t + " (" + n + ")"))))));
    const meses = dados.passado.meses.map((m) => el("tr", null, el("td", { class: "col-ativo" }, m.mes),
      el("td", { class: "largo", "data-label": "Escolhidas" }, el("div", { class: "chips" }, m.escolhidas.map((t) => el("span", { class: "etiqueta" }, t))))));
    limpar($("passado-saida")).append(
      el("h3", null, "Por ano"), el("table", { class: "tabela empilha" },
        el("thead", null, el("tr", null, el("th", null, "Ano"), el("th", { class: "n" }, "Meses"), el("th", null, "As que mais apareceram"))), el("tbody", null, anos)),
      el("h3", { style: "margin-top:18px" }, "Últimos 12 meses (no fim de cada mês)"),
      el("table", { class: "tabela empilha" }, el("thead", null, el("tr", null, el("th", null, "Mês"), el("th", null, "Escolhidas"))), el("tbody", null, meses)));
  }

  function desenharCorretoras() {
    const c = dados.corretoras;
    $("cor-sub").textContent = "Pesquisado em " + c.pesquisado_em + " em fontes públicas. Tarifas mudam: confirme na tabela oficial antes de abrir conta.";
    const t = limpar($("tabela-corretoras"));
    t.append(el("thead", null, el("tr", null, el("th", null, "Corretora"), el("th", null, "Corretagem"), el("th", null, "Custódia"), el("th", null, "Atenção"))));
    t.append(el("tbody", null, c.lista.map((x) => {
      const url = linkSeguro(x.fonte);
      return el("tr", null, el("td", { class: "col-ativo" }, el("b", null, x.nome)), el("td", { class: "largo", "data-label": "Corretagem" }, x.corretagem),
        el("td", { class: "largo", "data-label": "Custódia" }, x.custodia),
        el("td", { class: "largo", "data-label": "Atenção" }, x.atencao + " ", url ? el("a", { href: url, rel: "noopener", target: "_blank" }, "fonte") : null));
    })));
    $("cor-aviso").textContent = c.aviso;
  }

  function desenharRodape() {
    $("aviso-final").textContent = dados.aviso_final;
    const partes = ["Preços do Yahoo Finance, que podem ter atraso de alguns minutos em relação à bolsa."];
    if (dados.dados_url) partes.push("A página busca preços novos sozinha a cada minuto enquanto estiver aberta; o servidor os atualiza a cada " + dados.intervalo_min + " minutos durante o pregão.");
    const rodape = limpar($("rodape-fontes"));
    rodape.append(partes.join(" "));
    if (dados.planilha_url && caminhoRelativo(dados.planilha_url)) rodape.append(" ", el("a", { href: dados.planilha_url }, "Baixar a planilha (Excel)"), ".");
  }

  function desenharTudo() {
    desenharTopo(); desenharMercado(); desenharRanking(); desenharMovimento(); desenharPlano(); desenharApostas(); atualizarSnapshot(); desenharVirtualSeguro(); desenharPassado(); desenharCorretoras(); desenharRodape();
  }

  // ----- atualização automática -----

  async function buscar(manual) {
    if (!dados.dados_url || buscando) return;
    buscando = true;
    try {
      const resp = await fetch(dados.dados_url + "?m=" + Math.floor(Date.now() / 60000), { cache: "no-store" });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const novo = await resp.json();
      if (novo.versao !== VERSAO_PAGINA) { location.reload(); return; }
      if (Date.parse(novo.gerado_em) > Date.parse(dados.gerado_em)) {
        dados = novo;
        desenharTudo();
      } else if (manual) {
        $("atualizacao").textContent = "Já está com os preços mais recentes (" + horaBrasilia(Date.parse(dados.gerado_em)) + ").";
        setTimeout(desenharTopo, 4000);
      }
    } catch (e) {
      if (manual) $("atualizacao").textContent = "Não consegui buscar agora. Tente de novo em instantes.";
    } finally {
      buscando = false;
    }
  }

  function agendar() {
    if (temporizador) clearInterval(temporizador);
    temporizador = setInterval(() => {
      if (document.hidden) return;
      desenharTopo();      // o relógio anda mesmo sem dado novo: "há X min", mercado aberto/fechado, "Hoje" ou "Último pregão"
      desenharMercado();
      desenharRanking();
      desenharApostas();
      desenharVirtualNumeros();
      buscar(false);
    }, 60000);
  }

  // ----- controles -----

  function numero(campo, minimo, maximo, padrao) {
    const bruto = String(campo.value).trim().replace(",", ".");
    const v = bruto === "" ? NaN : Number(bruto);        // campo vazio (enquanto a pessoa digita) mantém o valor de antes
    if (!Number.isFinite(v)) return padrao;
    return Math.min(Math.max(v, minimo), maximo);
  }

  function lerConfig() {
    const base = config || dados.config;
    const ativos = Math.round(numero($("cfg-ativos"), 1, 10, base.ativos));
    config = {
      valor: numero($("cfg-valor"), 1, 1e7, base.valor),
      ativos,
      fiis: Math.min(Math.round(numero($("cfg-fiis"), 0, 10, base.fiis)), ativos),
      meses: Math.round(numero($("cfg-meses"), 1, 12, base.meses)),
    };
  }

  function escreverConfig() {
    $("cfg-valor").value = config.valor; $("cfg-ativos").value = config.ativos; $("cfg-fiis").value = config.fiis; $("cfg-meses").value = config.meses;
  }

  function marcar(selecao, atributo, valor) {
    document.querySelectorAll(selecao).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset[atributo] === valor)));
  }

  function ligarEventos() {
    document.querySelectorAll("[data-classe]").forEach((b) => b.addEventListener("click", () => {
      ui.classe = b.dataset.classe; marcar("[data-classe]", "classe", ui.classe); salvar(); desenharRanking();
    }));
    document.querySelectorAll("[data-periodo]").forEach((b) => b.addEventListener("click", () => {
      ui.periodo = b.dataset.periodo; marcar("[data-periodo]", "periodo", ui.periodo); salvar(); desenharMovimento();
    }));
    $("ordem").addEventListener("change", (e) => { ui.ordem = e.target.value; salvar(); desenharRanking(); });
    $("busca").addEventListener("input", (e) => { ui.busca = e.target.value; desenharRanking(); });
    $("reprovados").addEventListener("change", (e) => { ui.reprovados = e.target.checked; desenharRanking(); });
    $("config").addEventListener("input", () => { lerConfig(); salvar(); desenharRanking(); desenharPlano(); desenharApostas(); });
    document.querySelectorAll("[data-tipo]").forEach((b) => b.addEventListener("click", () => {
      ui.tipo = b.dataset.tipo; marcar("[data-tipo]", "tipo", ui.tipo); salvar(); desenharApostas();
    }));
    $("ordem-apostas").addEventListener("change", (e) => { ui.ordemApostas = e.target.value; salvar(); desenharApostas(); });
    $("cfg-reserva").addEventListener("input", () => { ui.reserva = numero($("cfg-reserva"), 0, 100, ui.reserva); salvar(); desenharApostas(); });
    $("cfg-reserva").addEventListener("change", () => { $("cfg-reserva").value = ui.reserva; });
    $("config").addEventListener("change", () => { lerConfig(); escreverConfig(); });   // ao sair do campo, mostra o valor que de fato vale
    $("config").addEventListener("submit", (e) => e.preventDefault());
    $("cfg-padrao").addEventListener("click", () => { config = Object.assign({}, dados.config); escreverConfig(); salvar(); desenharRanking(); desenharPlano(); desenharApostas(); });
    $("btn-atualizar").addEventListener("click", () => buscar(true));
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { desenharTopo(); desenharMercado(); desenharRanking(); desenharApostas(); desenharVirtualNumeros(); buscar(false); } });
  }

  function salvar() {
    guardar(CHAVE_PREFS, { config, classe: ui.classe, ordem: ui.ordem, periodo: ui.periodo, tipo: ui.tipo, ordemApostas: ui.ordemApostas, reserva: ui.reserva });
  }

  function carregarPreferencias() {
    config = Object.assign({}, dados.config);
    const p = ler(CHAVE_PREFS);
    if (p && typeof p === "object") {
      if (p.config && Number.isFinite(p.config.valor)) config = Object.assign(config, p.config);
      if (["todos", "ação", "FII"].includes(p.classe)) ui.classe = p.classe;
      if (ORDENS_RANKING.includes(p.ordem)) ui.ordem = p.ordem;
      if (["dia", "mes", "ano"].includes(p.periodo)) ui.periodo = p.periodo;
      if (["todos", "ação", "fundo"].includes(p.tipo)) ui.tipo = p.tipo;
      if (ORDENS_APOSTAS.includes(p.ordemApostas)) ui.ordemApostas = p.ordemApostas;
      if (Number.isFinite(p.reserva) && p.reserva >= 0 && p.reserva <= 100) ui.reserva = p.reserva;
    }
    escreverConfig();
    lerConfig();
    $("ordem").value = ui.ordem;
    $("ordem-apostas").value = ui.ordemApostas;
    $("cfg-reserva").value = ui.reserva;
    marcar("[data-tipo]", "tipo", ui.tipo);
    marcar("[data-classe]", "classe", ui.classe);
    marcar("[data-periodo]", "periodo", ui.periodo);
  }

  function iniciar() {
    try {
      dados = JSON.parse($("dados-iniciais").textContent);
    } catch (e) {
      document.querySelector("main").prepend(el("p", { class: "nota" }, "Não consegui ler os dados deste painel."));
      return;
    }
    carregarPreferencias();
    carteira = lerCarteiraGuardada();
    window.addEventListener("storage", (e) => { if (e.key === CHAVE_CARTEIRA) { carteira = lerCarteiraGuardada(); desenharVirtual(); } });
    ligarEventos();
    desenharTudo();
    agendar();
    buscar(false);   // o HTML pode estar em cache por alguns minutos: já busca os dados mais novos ao abrir
  }

  iniciar();
}
