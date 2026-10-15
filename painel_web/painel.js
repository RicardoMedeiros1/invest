"use strict";
// Painel da carteira: a lógica (funções puras, testadas com o Node) vem primeiro; o desenho da página, depois.
// As regras de escolha e de compra são as mesmas de scanner_dividendos_b3.py e carteira_simples.py.

const VERSAO_PAGINA = 1;            // precisa ser igual a VERSAO_DADOS em painel_pagina.py
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

function planoDeCompras(precos, ordem, valor, meses) {
  if (!ordem.length) return [];
  const investido = {};
  ordem.forEach((t) => { investido[t] = 0; });
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
};

function ordenarAtivos(lista, ordem) {
  const chave = ORDENS[ordem] || ORDENS.nota;
  return lista.map((a, i) => ({ a, i })).sort((x, y) => (chave(x.a) - chave(y.a)) || (x.i - y.i)).map((x) => x.a);
}

function filtrarAtivos(lista, { classe = "todos", busca = "", reprovados = false } = {}) {
  const q = busca.trim().toUpperCase();
  return lista.filter((a) => (reprovados || a.ok) && (classe === "todos" || a.classe === classe) && (!q || a.t.includes(q)));
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

if (typeof module !== "undefined" && module.exports) {
  module.exports = { fmtNum, fmtBRL, fmtPct, fmtData, estadoMercado, horaBrasilia, escolherCandidatos, planoDeCompras,
    estimativaDividendos, cotasQueCabem, ordenarAtivos, filtrarAtivos, maisMenos, caminhoSerie, idadeMin, atrasado, VERSAO_PAGINA };
}

// ---------- a página ----------

if (typeof document !== "undefined") {
  const NS_SVG = "http://www.w3.org/2000/svg";
  const CHAVE_PREFS = "carteira:preferencias";
  const PADRAO_UI = { classe: "todos", ordem: "nota", periodo: "dia" };
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
    desenharTopo(); desenharMercado(); desenharRanking(); desenharMovimento(); desenharPlano(); desenharPassado(); desenharCorretoras(); desenharRodape();
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
    $("config").addEventListener("input", () => { lerConfig(); salvar(); desenharRanking(); desenharPlano(); });
    $("config").addEventListener("change", () => { lerConfig(); escreverConfig(); });   // ao sair do campo, mostra o valor que de fato vale
    $("config").addEventListener("submit", (e) => e.preventDefault());
    $("cfg-padrao").addEventListener("click", () => { config = Object.assign({}, dados.config); escreverConfig(); salvar(); desenharRanking(); desenharPlano(); });
    $("btn-atualizar").addEventListener("click", () => buscar(true));
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { desenharTopo(); desenharMercado(); desenharRanking(); buscar(false); } });
  }

  function salvar() { guardar(CHAVE_PREFS, { config, classe: ui.classe, ordem: ui.ordem, periodo: ui.periodo }); }

  function carregarPreferencias() {
    config = Object.assign({}, dados.config);
    const p = ler(CHAVE_PREFS);
    if (p && typeof p === "object") {
      if (p.config && Number.isFinite(p.config.valor)) config = Object.assign(config, p.config);
      if (["todos", "ação", "FII"].includes(p.classe)) ui.classe = p.classe;
      if (p.ordem in ORDENS) ui.ordem = p.ordem;
      if (["dia", "mes", "ano"].includes(p.periodo)) ui.periodo = p.periodo;
    }
    escreverConfig();
    lerConfig();
    $("ordem").value = ui.ordem;
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
    ligarEventos();
    desenharTudo();
    agendar();
    buscar(false);   // o HTML pode estar em cache por alguns minutos: já busca os dados mais novos ao abrir
  }

  iniciar();
}
