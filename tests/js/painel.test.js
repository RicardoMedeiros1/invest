// Testes da lógica do painel (painel_web/painel.js) com o Node: node --test tests/js/painel.test.js
// A paridade com o Python é conferida por tests/test_js.py, que passa os casos pela variável CASOS_PARIDADE.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const p = require(path.join(__dirname, "..", "..", "painel_web", "painel.js"));

const em = (iso) => Date.parse(iso);

test("formatação em português", () => {
  assert.equal(p.fmtBRL(1234.5), "R$ 1.234,50");
  assert.equal(p.fmtBRL(0), "R$ 0,00");
  assert.equal(p.fmtBRL(-5), "R$ -5,00");
  assert.equal(p.fmtBRL(1234567.891), "R$ 1.234.567,89");
  assert.equal(p.fmtPct(7.46), "7,5%");
  assert.equal(p.fmtPct(2, true), "+2,0%");
  assert.equal(p.fmtPct(-2.04, true), "-2,0%");
  assert.equal(p.fmtPct(-0.04, true), "0,0%");   // arredonda para zero: sem sinal
  assert.equal(p.fmtPct(null), "n/d");
  assert.equal(p.fmtPct(NaN), "n/d");
  assert.equal(p.fmtData("2026-10-15"), "15/10/2026");
});

test("mercado aberto só em dia útil, das 10h às 18h de Brasília, com preços de hoje", () => {
  const e = (iso, data) => p.estadoMercado(em(iso), data).estado;
  assert.equal(e("2026-10-15T14:00:00-03:00", "2026-10-15"), "aberto");               // quinta
  assert.equal(e("2026-10-15T10:00:00-03:00", "2026-10-15"), "aberto");
  assert.equal(e("2026-10-15T17:59:00-03:00", "2026-10-15"), "aberto");
  assert.equal(e("2026-10-15T18:00:00-03:00", "2026-10-15"), "fechado");
  assert.equal(e("2026-10-15T09:59:00-03:00", "2026-10-14"), "fechado");
  assert.equal(e("2026-10-17T14:00:00-03:00", "2026-10-16"), "fechado");              // sábado
  assert.equal(e("2026-10-18T14:00:00-03:00", "2026-10-16"), "fechado");              // domingo
  assert.equal(e("2026-10-15T14:00:00-03:00", "2026-10-14"), "sem-preco-de-hoje");    // feriado ou primeira atualização ainda não saiu
});

test("o horário de Brasília não depende do fuso do computador nem da data em UTC", () => {
  // 01h UTC de sexta ainda é quinta à noite em Brasília
  assert.equal(p.estadoMercado(em("2026-10-16T01:00:00Z"), "2026-10-15").hoje, "2026-10-15");
  assert.equal(p.estadoMercado(em("2026-10-15T13:00:00Z"), "2026-10-15").estado, "aberto");   // 10h em Brasília
  assert.equal(p.estadoMercado(em("2026-10-15T12:59:00Z"), "2026-10-15").estado, "fechado");
  assert.equal(p.horaBrasilia(em("2026-10-15T17:35:00Z")), "14:35");
});

test("aviso de atualização atrasada só com o mercado aberto e depois de 3 intervalos (mínimo 45 min)", () => {
  assert.equal(p.idadeMin(em("2026-10-15T14:30:00-03:00"), "2026-10-15T14:00:00-03:00"), 30);
  assert.equal(p.idadeMin(em("2026-10-15T13:00:00-03:00"), "2026-10-15T14:00:00-03:00"), 0);   // relógio adiantado: nunca negativo
  assert.equal(p.atrasado("aberto", 45, 15), false);
  assert.equal(p.atrasado("aberto", 46, 15), true);
  assert.equal(p.atrasado("aberto", 46, 30), false);        // intervalo de 30: limite de 90
  assert.equal(p.atrasado("aberto", 91, 30), true);
  assert.equal(p.atrasado("aberto", 20, 5), false);         // intervalo curto: continua valendo o mínimo de 45 min
  assert.equal(p.atrasado("aberto", 46, 5), true);
  assert.equal(p.atrasado("fechado", 600, 15), false);
});

test("quantas unidades cabem no valor", () => {
  assert.equal(p.cotasQueCabem(200, 49.12), 4);
  assert.equal(p.cotasQueCabem(200, 250), 0);
  assert.equal(p.cotasQueCabem(200, 0), 0);
  assert.equal(p.cotasQueCabem(25.15, 5.03), 5);            // 5 x 5,03 = 25,15 exato: a divisão crua dá 4,999... e tiraria uma unidade
  assert.equal(p.cotasQueCabem(25.14, 5.03), 4);            // mas quem não completa a unidade continua sem ela
});

const ranking = [["A", "ação"], ["B", "FII"], ["C", "ação"], ["D", "ação"], ["E", "FII"], ["F", "ação"]].map(([t, classe]) => ({ t, classe }));

test("escolha de ativos com cota de FIIs", () => {
  assert.deepEqual(p.escolherCandidatos(ranking, 3, 0), ["A", "C", "D"]);
  assert.deepEqual(p.escolherCandidatos(ranking, 3, 1), ["A", "B", "C"]);
  assert.deepEqual(p.escolherCandidatos(ranking, 4, 2), ["A", "B", "C", "E"]);
  assert.deepEqual(p.escolherCandidatos(ranking, 3, null), ["A", "B", "C"]);
  assert.deepEqual(p.escolherCandidatos(ranking.slice(0, 3), 5, 2), ["A", "B", "C"]);     // vaga que ninguém preenche fica vazia
  assert.deepEqual(p.escolherCandidatos([{ t: "A", classe: "ação" }, { t: "B", classe: "ação" }], 2, 1), ["A", "B"]);   // sem FII: a vaga vai para o próximo
});

test("plano de compra: unidades inteiras, ativo com menos dinheiro primeiro, caixa guardado", () => {
  const precos = { PETR4: 49.12, CXSE3: 19.88, ABEV3: 15.36, WEGE3: 49.46, ISAE4: 27.59 };
  const plano = p.planoDeCompras(precos, Object.keys(precos), 200, 2);
  assert.deepEqual(plano[0].compras, { PETR4: 1, CXSE3: 2, ABEV3: 2, WEGE3: 1, ISAE4: 1 });
  assert.ok(Math.abs(plano[0].gasto - 196.65) < 1e-9 && Math.abs(plano[0].sobra - 3.35) < 1e-9);
  const caro = p.planoDeCompras({ HGLG11: 150, X: 10 }, ["HGLG11", "X"], 100, 2);
  assert.deepEqual(caro[0].compras, {});                                                  // o FII ainda não cabe: guarda tudo
  assert.deepEqual(caro[1].compras, { HGLG11: 1, X: 5 });                                 // 200 = FII de 150 + 5 de R$ 10
  assert.deepEqual(p.planoDeCompras({}, [], 200, 3), []);
});

test("estimativa de dividendos ignora ativos sem dado", () => {
  assert.ok(Math.abs(p.estimativaDividendos([6, 8], 2400) - 168) < 1e-9);
  assert.equal(p.estimativaDividendos([null], 2400), 0);
  assert.equal(p.estimativaDividendos([], 2400), 0);
});

const ativos = [
  { t: "AAA3", classe: "ação", ok: true, pos: 2, dia: 1.0, mes: -1, ano: 10, dy: 6 },
  { t: "BBB11", classe: "FII", ok: true, pos: 1, dia: -2.0, mes: 3, ano: null, dy: 8 },
  { t: "CCC4", classe: "ação", ok: true, pos: 3, dia: null, mes: 2, ano: 5, dy: null },
  { t: "DDD3", classe: "ação", ok: false, pos: null, dia: 9, mes: 9, ano: 9, dy: 1 },
  { t: "EEE3", classe: "ação", ok: true, pos: 4, dia: 1.0, mes: 0, ano: 7, dy: 4 },
];

test("filtros e ordem da tabela", () => {
  assert.deepEqual(p.filtrarAtivos(ativos).map((a) => a.t), ["AAA3", "BBB11", "CCC4", "EEE3"]);                // reprovado fica de fora
  assert.deepEqual(p.filtrarAtivos(ativos, { reprovados: true }).map((a) => a.t).includes("DDD3"), true);
  assert.deepEqual(p.filtrarAtivos(ativos, { classe: "FII" }).map((a) => a.t), ["BBB11"]);
  assert.deepEqual(p.filtrarAtivos(ativos, { busca: " ccc" }).map((a) => a.t), ["CCC4"]);
  assert.deepEqual(p.ordenarAtivos(ativos, "nota").map((a) => a.t), ["BBB11", "AAA3", "CCC4", "EEE3", "DDD3"]);   // sem posição por último
  assert.deepEqual(p.ordenarAtivos(ativos, "dia").map((a) => a.t), ["DDD3", "AAA3", "EEE3", "BBB11", "CCC4"]);   // empate AAA3/EEE3 segue a ordem original; sem dado por último
  assert.deepEqual(p.ordenarAtivos(ativos, "dy").map((a) => a.t), ["BBB11", "AAA3", "EEE3", "DDD3", "CCC4"]);
  assert.deepEqual(ativos.map((a) => a.t), ["AAA3", "BBB11", "CCC4", "DDD3", "EEE3"]);                            // não mexe na lista original
});

test("quem mais subiu e caiu só entre os aprovados e só quedas reais em 'caíram'", () => {
  const { sobem, caem } = p.maisMenos(ativos, "dia", 5);
  assert.deepEqual(sobem.map((a) => a.t), ["AAA3", "EEE3", "BBB11"]);       // CCC4 sem dado e DDD3 reprovado ficam de fora
  assert.deepEqual(caem.map((a) => a.t), ["BBB11"]);
  assert.deepEqual(p.maisMenos(ativos, "mes", 1).caem.map((a) => a.t), ["AAA3"]);
  assert.deepEqual(p.maisMenos(ativos, "dia", 1).sobem.map((a) => a.t), ["AAA3"]);
});

test("gráfico de linha", () => {
  assert.equal(p.caminhoSerie([1], 92, 28), "");
  assert.equal(p.caminhoSerie([], 92, 28), "");
  assert.equal(p.caminhoSerie([null, null], 92, 28), "");
  const d = p.caminhoSerie([1, 2, 3, 4], 92, 28);
  assert.equal(d.match(/L/g).length, 3);
  assert.ok(d.startsWith("M2.0 26.0") && d.endsWith("L90.0 2.0"));          // preço mais baixo embaixo, mais alto em cima
  assert.ok(p.caminhoSerie([5, 5, 5], 92, 28).startsWith("M"));            // série parada não divide por zero
  assert.ok(!/NaN|Infinity/.test(p.caminhoSerie([5, 5, 5], 92, 28)));
});

test("paridade com o Python", { skip: !process.env.CASOS_PARIDADE && "sem CASOS_PARIDADE" }, () => {
  const casos = JSON.parse(fs.readFileSync(process.env.CASOS_PARIDADE, "utf-8"));
  assert.ok(casos.escolha.length > 50 && casos.plano.length > 50);
  for (const c of casos.escolha) {
    assert.deepEqual(p.escolherCandidatos(c.ranking, c.top_n, c.n_fiis), c.esperado, JSON.stringify(c));
  }
  for (const c of casos.plano) {
    const got = p.planoDeCompras(c.precos, c.ordem, c.valor, c.meses);
    assert.equal(got.length, c.esperado.length);
    got.forEach((m, i) => {
      assert.deepEqual(m.compras, c.esperado[i].compras, JSON.stringify(c));
      assert.ok(Math.abs(m.gasto - c.esperado[i].gasto) < 1e-6 && Math.abs(m.sobra - c.esperado[i].sobra) < 1e-6, JSON.stringify(c));
    });
  }
  for (const c of casos.cotas) assert.equal(p.cotasQueCabem(c.valor, c.preco), c.esperado, JSON.stringify(c));
  for (const c of casos.brl) assert.equal(p.fmtBRL(c.x), c.esperado);
  for (const c of casos.pct) assert.equal(p.fmtPct(c.x, c.sinal), c.esperado);
});

const apostas = [
  { t: "AAA3", classe: "ação", ano: 80, mes: -5, topo: -40, oscilacao: 60, liquidez: 5e6 },
  { t: "BBB11", classe: "ETF", ano: 20, mes: 10, topo: -2, oscilacao: 25, liquidez: null },
  { t: "CCC3", classe: "ação", ano: null, mes: 3, topo: null, oscilacao: null, liquidez: 2e5 },
  { t: "DDD11", classe: "FII", ano: 5, mes: 1, topo: -10, oscilacao: 8, liquidez: 9e6 },
];

test("apostas: filtro por tipo (ETFs e FIIs contam como fundos) e ordem com o que falta por último", () => {
  assert.deepEqual(p.filtrarApostas(apostas, "todos").length, 4);
  assert.deepEqual(p.filtrarApostas(apostas, "ação").map((a) => a.t), ["AAA3", "CCC3"]);
  assert.deepEqual(p.filtrarApostas(apostas, "fundo").map((a) => a.t), ["BBB11", "DDD11"]);
  const ord = (o) => p.ordenarAtivos(apostas, o).map((a) => a.t);
  assert.deepEqual(ord("ano"), ["AAA3", "BBB11", "DDD11", "CCC3"]);       // sem dado por último
  assert.deepEqual(ord("mes"), ["BBB11", "CCC3", "DDD11", "AAA3"]);
  assert.deepEqual(ord("caida"), ["AAA3", "DDD11", "BBB11", "CCC3"]);     // as mais abaixo do topo primeiro
  assert.deepEqual(ord("vol"), ["AAA3", "BBB11", "DDD11", "CCC3"]);
  assert.deepEqual(ord("liq"), ["DDD11", "AAA3", "CCC3", "BBB11"]);
});

// ---------- carteira virtual ----------

const comOrdens = (...itens) => itens.reduce((c, [o, data, div]) => {
  const r = p.registrarOrdem(c, o, data, div || {});
  assert.equal(r.erro, undefined, r.erro);
  return r.carteira;
}, p.carteiraNova());

test("carteira virtual: aporte, compra, preço médio, caixa, total e resultado", () => {
  const c = comOrdens(
    [{ tipo: "aporte", valor: 2000 }, "2026-10-01"],
    [{ tipo: "compra", t: "PETR4", qtd: 10, preco: 50 }, "2026-10-01"],
    [{ tipo: "compra", t: "PETR4", qtd: 10, preco: 60 }, "2026-10-02"],
    [{ tipo: "compra", t: "KNRI11", qtd: 1, preco: 150 }, "2026-10-02"]);
  const r = p.calcularCarteira(c.ordens, { PETR4: 70, KNRI11: 140 }, {});
  assert.ok(Math.abs(r.caixa - (2000 - 500 - 600 - 150)) < 1e-9);                            // 750
  assert.ok(Math.abs(r.valorPosicoes - (1400 + 140)) < 1e-9);
  assert.ok(Math.abs(r.total - (750 + 1540)) < 1e-9);
  assert.ok(Math.abs(r.resultado - 290) < 1e-9 && Math.abs(r.resultado_pct - 14.5) < 1e-9);  // 2290 contra 2000 aportados
  assert.deepEqual(r.posicoes.map((x) => x.t), ["PETR4", "KNRI11"]);                          // maior valor primeiro
  const petr = r.posicoes.find((x) => x.t === "PETR4");
  assert.equal(petr.qtd, 20);
  assert.ok(Math.abs(petr.preco_medio - 55) < 1e-9);                                           // (500 + 600) / 20
  assert.ok(Math.abs(petr.resultado - 300) < 1e-9 && Math.abs(petr.resultado_pct - 300 / 1100 * 100) < 1e-9);
});

test("carteira virtual: venda realiza o resultado pelo preço médio e zera a posição", () => {
  let c = comOrdens(
    [{ tipo: "aporte", valor: 2000 }, "2026-10-01"],
    [{ tipo: "compra", t: "VALE3", qtd: 10, preco: 50 }, "2026-10-01"],
    [{ tipo: "compra", t: "VALE3", qtd: 10, preco: 70 }, "2026-10-02"],                       // médio 60
    [{ tipo: "venda", t: "VALE3", qtd: 5, preco: 80 }, "2026-10-03"]);
  let r = p.calcularCarteira(c.ordens, { VALE3: 80 }, {});
  assert.ok(Math.abs(r.realizado - 100) < 1e-9);                                              // (80 - 60) x 5
  assert.equal(r.posicoes[0].qtd, 15);
  assert.ok(Math.abs(r.posicoes[0].preco_medio - 60) < 1e-9);                                 // vender não muda o preço médio
  const fim = p.registrarOrdem(c, { tipo: "venda", t: "VALE3", qtd: 15, preco: 90 }, "2026-10-04", {});
  assert.equal(fim.erro, undefined);
  r = p.calcularCarteira(fim.carteira.ordens, { VALE3: 90 }, {});
  assert.equal(r.posicoes.length, 0);
  assert.ok(Math.abs(r.total - (2000 - 500 - 700 + 5 * 80 + 15 * 90)) < 1e-9);               // 2550
  assert.ok(Math.abs(r.realizado - (100 + 15 * 30)) < 1e-9 && Math.abs(r.resultado - 550) < 1e-9);
});

test("carteira virtual: recusa o que não dá para fazer", () => {
  const c = comOrdens([{ tipo: "aporte", valor: 100 }, "2026-10-01"], [{ tipo: "compra", t: "PETR4", qtd: 1, preco: 40 }, "2026-10-01"]);
  const erro = (o) => p.validarOrdem(c.ordens, o, {});
  assert.match(erro({ tipo: "compra", t: "PETR4", qtd: 2, preco: 40 }), /Saldo insuficiente: a compra custa R\$ 80,00 e o caixa tem R\$ 60,00/);
  assert.equal(erro({ tipo: "compra", t: "PETR4", qtd: 1, preco: 60 }), null);                // gasta o caixa todo, e é permitido
  assert.match(erro({ tipo: "compra", t: "PETR4", qtd: 1, preco: 60.01 }), /Saldo insuficiente/);
  assert.match(erro({ tipo: "compra", t: "PETR4", qtd: 1.5, preco: 10 }), /número inteiro/);
  assert.match(erro({ tipo: "compra", t: "PETR4", qtd: 0, preco: 10 }), /número inteiro/);
  assert.match(erro({ tipo: "compra", t: "PETR4", qtd: -1, preco: 10 }), /número inteiro/);
  assert.match(erro({ tipo: "compra", t: "PETR4", qtd: 1, preco: 0 }), /preço maior que zero/);
  assert.match(erro({ tipo: "compra", t: "PETR4", qtd: 1, preco: NaN }), /preço maior que zero/);
  assert.match(erro({ tipo: "compra", t: "", qtd: 1, preco: 1 }), /Escolha um ativo/);
  assert.match(erro({ tipo: "compra", t: "<img src=x>", qtd: 1, preco: 1 }), /Escolha um ativo/);
  assert.match(erro({ tipo: "venda", t: "PETR4", qtd: 2, preco: 40 }), /Você tem 1 unidade\(s\) de PETR4 e tentou vender 2/);
  assert.match(erro({ tipo: "venda", t: "VALE3", qtd: 1, preco: 40 }), /Você tem 0 unidade/);
  assert.equal(erro({ tipo: "venda", t: "PETR4", qtd: 1, preco: 45 }), null);
  assert.match(erro({ tipo: "aporte", valor: 0 }), /maior que zero/);
  assert.match(erro({ tipo: "aporte", valor: -5 }), /maior que zero/);
  assert.match(erro({ tipo: "aporte", valor: Infinity }), /maior que zero/);
  assert.match(erro({ tipo: "transferir" }), /Tipo de ordem inválido/);
  assert.equal(p.registrarOrdem(c, { tipo: "compra", t: "PETR4", qtd: 5, preco: 40 }, "2026-10-02", {}).erro !== undefined, true);
  assert.equal(c.ordens.length, 2);                                                           // a carteira original não muda
});

test("carteira virtual: desfazer tira só a última ordem e não mexe na original", () => {
  const c = comOrdens([{ tipo: "aporte", valor: 100 }, "2026-10-01"], [{ tipo: "compra", t: "PETR4", qtd: 1, preco: 40 }, "2026-10-01"]);
  const d = p.desfazerUltima(c);
  assert.equal(d.ordens.length, 1); assert.equal(c.ordens.length, 2);
  assert.equal(p.desfazerUltima(p.carteiraNova()).ordens.length, 0);
});

test("dividendos entram pela data ex: quem tinha na véspera recebe, quem comprou no dia não", () => {
  const div = { PETR4: [["2026-10-10", 1], ["2026-10-20", 2]], VALE3: [["2026-10-05", 5]] };
  const c = comOrdens(
    [{ tipo: "aporte", valor: 1000 }, "2026-10-01"],
    [{ tipo: "compra", t: "PETR4", qtd: 10, preco: 10 }, "2026-10-09"],                       // tinha na véspera de 10/10: recebe 10 x 1
    [{ tipo: "compra", t: "VALE3", qtd: 4, preco: 10 }, "2026-10-05"]);                        // comprou NO dia ex: não recebe
  assert.equal(p.unidadesAntes(c.ordens, "PETR4", "2026-10-10"), 10);
  assert.equal(p.unidadesAntes(c.ordens, "PETR4", "2026-10-09"), 0);
  assert.equal(p.unidadesAntes(c.ordens, "VALE3", "2026-10-05"), 0);
  const d = p.dividendosDaCarteira(c.ordens, div);
  assert.deepEqual(d.itens.map((x) => [x.t, x.data, x.qtd, x.valor]), [["PETR4", "2026-10-10", 10, 10], ["PETR4", "2026-10-20", 10, 20]]);
  assert.equal(d.total, 30);
  const r = p.calcularCarteira(c.ordens, { PETR4: 10, VALE3: 10 }, div);
  assert.ok(Math.abs(r.caixa - (1000 - 100 - 40 + 30)) < 1e-9 && r.dividendos === 30);
  assert.ok(Math.abs(r.resultado - 30) < 1e-9);                                                // preços iguais: o resultado são os dividendos
});

test("dividendos: quem vendeu antes da data ex não recebe; com dividendos o caixa passa a comprar", () => {
  const div = { PETR4: [["2026-10-10", 1]] };
  const vendeu = comOrdens(
    [{ tipo: "aporte", valor: 100 }, "2026-10-01"],
    [{ tipo: "compra", t: "PETR4", qtd: 10, preco: 10 }, "2026-10-02"],
    [{ tipo: "venda", t: "PETR4", qtd: 10, preco: 10 }, "2026-10-09"]);
  assert.equal(p.dividendosDaCarteira(vendeu.ordens, div).total, 0);
  const segurou = comOrdens([{ tipo: "aporte", valor: 100 }, "2026-10-01"], [{ tipo: "compra", t: "PETR4", qtd: 10, preco: 10 }, "2026-10-02"]);
  const compra = { tipo: "compra", t: "VALE3", qtd: 1, preco: 5 };
  assert.match(p.validarOrdem(segurou.ordens, compra, {}), /Saldo insuficiente/);              // sem dividendos o caixa é 0
  assert.equal(p.validarOrdem(segurou.ordens, compra, div), null);                             // com R$ 10 de dividendos, cabe
});

test("posição sem preço no painel usa o custo em vez de sumir ou virar NaN", () => {
  const c = comOrdens([{ tipo: "aporte", valor: 100 }, "2026-10-01"], [{ tipo: "compra", t: "EMBJ3", qtd: 2, preco: 20 }, "2026-10-01"]);
  const r = p.calcularCarteira(c.ordens, {}, {});
  assert.equal(r.posicoes[0].sem_preco, true);
  assert.ok(Math.abs(r.posicoes[0].valor - 40) < 1e-9 && Number.isFinite(r.total) && Math.abs(r.resultado) < 1e-9);
});

test("plano com a carteira virtual: continua equilibrando o que já foi aplicado", () => {
  const precos = { A: 10, B: 10, C: 10 };
  const sem = p.planoDeCompras(precos, ["A", "B", "C"], 30, 1);
  assert.deepEqual(sem[0].compras, { A: 1, B: 1, C: 1 });
  const com = p.planoDeCompras(precos, ["A", "B", "C"], 30, 1, { A: 100, B: 0, C: 15 });     // A já tem muito: o dinheiro vai para B e C
  assert.equal(com[0].compras.A, undefined);
  assert.deepEqual(com[0].compras, { B: 2, C: 1 });
  const ranking = ["A", "B", "C"].map((t) => ({ t, classe: "ação" }));
  assert.deepEqual(p.comprasPeloPlano(ranking, { ativos: 3, fiis: 0 }, precos, 25, {}), { A: 1, B: 1 });
  assert.deepEqual(p.comprasPeloPlano(ranking, { ativos: 3, fiis: 0 }, precos, -5, {}), {});   // caixa negativo não compra nada
});

test("um ponto por dia no histórico; o último valor do dia vence; sem mudança devolve a mesma lista", () => {
  let h = [];
  h = p.registrarSnapshot(h, "2026-10-15", 1000.004, 1000);
  assert.deepEqual(h, [{ d: "2026-10-15", total: 1000, aportado: 1000 }]);
  const mesma = p.registrarSnapshot(h, "2026-10-15", 1000.004, 1000);
  assert.equal(mesma, h);
  h = p.registrarSnapshot(h, "2026-10-15", 1010, 1000);
  assert.equal(h.length, 1); assert.equal(h[0].total, 1010);
  h = p.registrarSnapshot(h, "2026-10-16", 990, 1000);
  assert.deepEqual(h.map((x) => x.d), ["2026-10-15", "2026-10-16"]);
  assert.equal(p.registrarSnapshot(h, "ontem", 1, 1), h);                                      // data inválida é ignorada
  assert.equal(p.registrarSnapshot(h, "2026-10-17", NaN, 1), h);
  let longo = [];
  for (let i = 0; i < 1100; i++) longo = p.registrarSnapshot(longo, new Date(Date.UTC(2020, 0, 1 + i)).toISOString().slice(0, 10), i, i);
  assert.equal(longo.length, 1000);
});

test("aporte do mês: só conta aporte no mesmo mês dos preços", () => {
  const c = comOrdens([{ tipo: "aporte", valor: 200 }, "2026-09-30"]);
  assert.equal(p.aporteFeitoNoMes(c, "2026-10-15"), false);
  assert.equal(p.aporteFeitoNoMes(c, "2026-09-02"), true);
  assert.equal(p.aporteFeitoNoMes(p.carteiraNova(), "2026-10-15"), false);
});

test("exportar e importar devolvem a mesma carteira", () => {
  const div = { PETR4: [["2026-10-10", 1]] };
  let c = comOrdens([{ tipo: "aporte", valor: 500 }, "2026-10-01"], [{ tipo: "compra", t: "PETR4", qtd: 5, preco: 40 }, "2026-10-02"],
    [{ tipo: "venda", t: "PETR4", qtd: 2, preco: 45 }, "2026-10-12"]);
  c = Object.assign({}, c, { historico: p.registrarSnapshot([], "2026-10-12", 520, 500) });
  const texto = p.exportarCarteira(c, "2026-10-12T12:00:00Z");
  const r = p.importarCarteira(texto, div);
  assert.equal(r.erro, undefined);
  assert.deepEqual(r.carteira, c);
  assert.deepEqual(p.calcularCarteira(r.carteira.ordens, { PETR4: 41 }, div), p.calcularCarteira(c.ordens, { PETR4: 41 }, div));
});

test("importar recusa arquivo estranho, ordem inválida e contas que não fecham", () => {
  const ok = { versao: 1, ordens: [{ data: "2026-10-01", tipo: "aporte", valor: 100 }, { data: "2026-10-01", tipo: "compra", t: "PETR4", qtd: 2, preco: 40 }] };
  const tenta = (o) => p.importarCarteira(JSON.stringify(o), {});
  assert.equal(tenta(ok).erro, undefined);
  assert.match(p.importarCarteira("isto não é json", {}).erro, /JSON válido/);
  assert.match(p.importarCarteira("null", {}).erro, /não é de uma carteira virtual/);
  assert.match(p.importarCarteira("[]", {}).erro, /não é de uma carteira virtual/);
  assert.match(tenta({ versao: 2, ordens: [] }).erro, /não é de uma carteira virtual/);
  assert.match(tenta({ versao: 1, ordens: "x" }).erro, /não é de uma carteira virtual/);
  assert.match(tenta({ versao: 1, ordens: [null] }).erro, /data inválida/);
  assert.match(tenta({ versao: 1, ordens: [{ data: "01/10/2026", tipo: "aporte", valor: 1 }] }).erro, /data inválida/);
  assert.match(tenta({ versao: 1, ordens: [{ data: "2026-10-02", tipo: "aporte", valor: 1 }, { data: "2026-10-01", tipo: "aporte", valor: 1 }] }).erro, /fora de ordem/);
  assert.match(tenta({ versao: 1, ordens: [{ data: "2026-10-01", tipo: "aporte", valor: -1 }] }).erro, /aporte com valor inválido/);
  assert.match(tenta({ versao: 1, ordens: [{ data: "2026-10-01", tipo: "aporte", valor: "100" }] }).erro, /aporte com valor inválido/);
  assert.match(tenta({ versao: 1, ordens: [{ data: "2026-10-01", tipo: "compra", t: "petr4", qtd: 1, preco: 1 }] }).erro, /dados inválidos/);
  assert.match(tenta({ versao: 1, ordens: [{ data: "2026-10-01", tipo: "compra", t: "PETR4", qtd: 1.5, preco: 1 }] }).erro, /dados inválidos/);
  assert.match(tenta({ versao: 1, ordens: [{ data: "2026-10-01", tipo: "compra", t: "PETR4", qtd: 1, preco: 0 }] }).erro, /dados inválidos/);
  assert.match(tenta({ versao: 1, ordens: [{ data: "2026-10-01", tipo: "doar", valor: 1 }] }).erro, /tipo desconhecido/);
  assert.match(tenta({ versao: 1, ordens: [{ data: "2026-10-01", tipo: "aporte", valor: 10 }, { data: "2026-10-01", tipo: "venda", t: "PETR4", qtd: 1, preco: 1 }] }).erro, /vende mais PETR4/);
  assert.match(tenta({ versao: 1, ordens: [{ data: "2026-10-01", tipo: "aporte", valor: 10 }, { data: "2026-10-01", tipo: "compra", t: "PETR4", qtd: 1, preco: 40 }] }).erro, /caixa ficaria negativo/);
  const grande = { versao: 1, ordens: Array.from({ length: 2001 }, () => ({ data: "2026-10-01", tipo: "aporte", valor: 1 })) };
  assert.match(tenta(grande).erro, /ordens demais/);
});

test("importar reconstrói os campos: o que vem de fora além do esperado não entra", () => {
  const r = p.importarCarteira(JSON.stringify({
    versao: 1, extra: "x", __proto__: { admin: true },
    ordens: [{ id: 99, data: "2026-10-01", tipo: "aporte", valor: 100, html: "<script>1</script>" }],
    historico: [{ d: "2026-10-01", total: 100, aportado: 100, x: 1 }, { d: "lixo", total: 1, aportado: 1 }, { d: "2026-10-02", total: "a", aportado: 1 }, null],
  }), {});
  assert.deepEqual(r.carteira, { versao: 1, ordens: [{ id: 1, data: "2026-10-01", tipo: "aporte", valor: 100 }], historico: [{ d: "2026-10-01", total: 100, aportado: 100 }], proximo_id: 2 });
  assert.equal(({}).admin, undefined);
});

test("gráfico da carteira: as duas linhas na mesma escala", () => {
  const [a, b] = p.caminhosComuns([[100, 150, 120], [100, 100, 100]], 100, 50, 0);
  assert.ok(a.startsWith("M0.0 50.0") && a.includes("L50.0 0.0"));                           // 100 embaixo, 150 em cima
  assert.ok(b.startsWith("M0.0 50.0") && b.endsWith("L100.0 50.0"));                         // a aportada fica reta na base
  assert.deepEqual(p.caminhosComuns([[1], []], 100, 50), ["", ""]);
  assert.deepEqual(p.caminhosComuns([[], []], 100, 50), ["", ""]);
  assert.ok(!/NaN|Infinity/.test(p.caminhosComuns([[5, 5], [5, 5]], 100, 50)[0]));
});
