/*
 * Painel de leitura do camu-crm — JS puro, sem bundler, sem CDN.
 *
 * Regras que este arquivo não pode quebrar (CLAUDE.md / plano dos changes
 * `painel-leitura` e `painel-tempo-real`):
 *   - textContent sempre, innerHTML nunca, para qualquer texto que venha de
 *     conversa/mensagem/nome (evita XSS a partir de conteúdo de cliente).
 *   - token em localStorage, nunca em querystring — inclusive no stream:
 *     `EventSource` não aceita header customizado, então o cliente de
 *     tempo real é `fetch()` + `ReadableStream` com um parser SSE manual
 *     (ver "Tempo real" abaixo), não `EventSource`.
 *   - botão "Atualizar" manual continua existindo — o stream é reforço, não
 *     substituição; se a conexão SSE cair, a tela não fica presa no estado
 *     velho sem jeito de atualizar.
 *   - a fila é a tela inicial ("#/"), o kanban é aba secundária (§6).
 */

const CHAVE_TOKEN = "camu_painel_token";
const CHAVE_OPERADOR = "camu_painel_operador";
// Change `dropdown-operador`: lista fixa dos dois operadores do projeto (ver
// openspec/project.md — "dele e do Felipe"). Um dropdown evita o erro
// `por é obrigatório` que o texto livre deixava passar em branco; se um
// terceiro operador entrar, é só adicionar aqui.
const OPERADORES = ["Marcos", "Felipe"];

/** Monta um <select> de operador, com o valor salvo pré-selecionado quando
 *  bate com a lista (senão fica no placeholder, forçando escolha explícita). */
function criarSeletorOperador() {
  const sel = el("select", {});
  sel.appendChild(el("option", { value: "", texto: "Quem está operando" }));
  OPERADORES.forEach((nome) => {
    sel.appendChild(el("option", { value: nome, texto: nome }));
  });
  const salvo = obterOperador();
  if (OPERADORES.includes(salvo)) sel.value = salvo;
  return sel;
}
// Change `contatos-de-teste-isolados`: "Modo teste" é o toggle binário do
// topo do painel — ligado mostra só contato de teste, desligado (padrão) só
// os reais, nunca os dois juntos na mesma tela (mesmo padrão de persistência
// de CHAVE_TOKEN/CHAVE_OPERADOR acima).
const CHAVE_MODO_TESTE = "camu_painel_modo_teste";

// Change `painel-preserva-estado-em-refresh`: `renderizarRota` reconstrói
// `conteudo` do zero (`textContent = ""`) a cada render — estas variáveis de
// módulo, fora de qualquer função, são o que sobrevive a isso.
//   - `estadoFiltrosConversas`/`estadoFiltrosProspeccao`: valor atual dos
//     filtros de cada aba, lido ao montar os `<select>`/campos e escrito de
//     volta a cada `change` — sem isso, todo re-render (inclusive o
//     disparado pelo SSE por uma mensagem em OUTRA conversa) resetava os
//     filtros para o padrão.
//   - `haEdicaoEmAndamento`: true enquanto há formulário com conteúdo digitado
//     ou uma escrita (`chamarApiEscrever`) em voo — suprime o refresh
//     automático do SSE (ver `processarBlocoSse`) para não apagar trabalho
//     em risco. O botão "Atualizar" manual ignora esta flag de propósito.
//   - `atualizacaoPendente`: true quando o SSE anunciou mudança mas o
//     refresh foi suprimido por `haEdicaoEmAndamento` — vira o aviso não
//     destrutivo ao lado do botão "Atualizar".
let estadoFiltrosConversas = { estagio: "", temperatura: "", bola: "", ordenar: "horas_esperando" };
let estadoFiltrosProspeccao = {
  zona: "",
  bairro: "",
  notaMinima: "",
  tier: "",
  naoConvertidas: false,
  ordenar: "",
};
let haEdicaoEmAndamento = false;
let atualizacaoPendente = false;

// Pedido do usuário na revisão visual (2026-08-31): botão "Importar
// conversa" de uma linha de Prospecção já enviada leva pra aba de importação
// de WhatsApp com telefone/nome pré-preenchidos, em vez do operador ter que
// digitar de novo o que a linha de prospecção já tinha. Estado de módulo
// simples (mesmo padrão de `refreshSuaveAtual`) — só existe entre o clique
// no botão e o próximo render de `renderizarImportarConversaWhatsapp`, que
// consome e zera.
let prefilhoImportacaoWhatsapp = null;

function obterToken() {
  try {
    return localStorage.getItem(CHAVE_TOKEN) || "";
  } catch (e) {
    return "";
  }
}

function salvarToken(valor) {
  try {
    localStorage.setItem(CHAVE_TOKEN, valor);
  } catch (e) {
    /* localStorage indisponível: segue sem persistir, só nesta sessão. */
  }
}

function obterOperador() {
  try {
    return localStorage.getItem(CHAVE_OPERADOR) || "";
  } catch (e) {
    return "";
  }
}

function salvarOperador(valor) {
  try {
    localStorage.setItem(CHAVE_OPERADOR, valor);
  } catch (e) {
    /* idem obterToken/salvarToken. */
  }
}

function modoTesteAtivo() {
  try {
    return localStorage.getItem(CHAVE_MODO_TESTE) === "1";
  } catch (e) {
    return false;
  }
}

function salvarModoTeste(ativo) {
  try {
    localStorage.setItem(CHAVE_MODO_TESTE, ativo ? "1" : "0");
  } catch (e) {
    /* idem obterToken/salvarToken. */
  }
}

/**
 * Acrescenta `apenas_teste=1` na URL quando "Modo teste" está ligado (change
 * `contatos-de-teste-isolados`) — chamado de dentro de `chamarApi`, nunca
 * espalhado tela por tela, para nenhuma rota de leitura escapar do
 * requirement "Modo teste nunca mistura as duas visões na mesma tela".
 * Rotas que não declaram este parâmetro (`/conversas/{id}`, `/stream`,
 * `/eval/*`) simplesmente ignoram a query extra — FastAPI não reclama de
 * parâmetro desconhecido.
 */
function comModoTeste(caminho) {
  if (!modoTesteAtivo()) return caminho;
  const separador = caminho.includes("?") ? "&" : "?";
  return `${caminho}${separador}apenas_teste=1`;
}

async function chamarApi(caminho) {
  const resposta = await fetch(`/api${comModoTeste(caminho)}`, {
    headers: { "X-Camu-Token": obterToken() },
  });
  const corpo = await resposta.json();
  if (!resposta.ok) {
    const erro = new Error(corpo.erro || `erro HTTP ${resposta.status}`);
    erro.regra = corpo.regra;
    throw erro;
  }
  return corpo;
}

/**
 * Escreve uma ação humana (marco, funil, correção — change `acoes-no-painel`;
 * também usada por `/eval/rotulos` — change `ground-truth-no-painel`, que
 * precisa de `PUT`/`DELETE` além de `POST`). Mesmo formato de erro de
 * `chamarApi`: `erro.regra` carrega a seção citada pelo servidor (ex.:
 * "§3"/"§7") quando a ação é recusada com 422.
 */
async function chamarApiEscrever(caminho, corpo, metodo) {
  const resposta = await fetch(`/api${caminho}`, {
    method: metodo || "POST",
    headers: {
      "X-Camu-Token": obterToken(),
      "Content-Type": "application/json",
    },
    body: corpo === undefined ? undefined : JSON.stringify(corpo),
  });
  const dados = await resposta.json();
  if (!resposta.ok) {
    const erro = new Error(dados.erro || `erro HTTP ${resposta.status}`);
    erro.regra = dados.regra;
    throw erro;
  }
  return dados;
}

function el(tag, props, filhos) {
  const elemento = document.createElement(tag);
  if (props) {
    for (const [chave, valor] of Object.entries(props)) {
      if (chave === "class") elemento.className = valor;
      else if (chave === "texto") elemento.textContent = valor;
      else elemento.setAttribute(chave, valor);
    }
  }
  (filhos || []).forEach((filho) => {
    if (filho) elemento.appendChild(filho);
  });
  return elemento;
}

// Rótulo fixo acima de um campo de `.filtros` — os `<select>` da tela já
// se auto-rotulam (primeira opção "estágio: todos" continua visível depois
// de escolher), mas um `<input>` de texto/número só tinha `placeholder`, que
// some assim que o operador digita. Achado na revisão visual (2026-08-31):
// sem rótulo fixo, "4.5" sozinho no meio da linha de filtros não diz se é
// nota mínima ou outra coisa qualquer.
function campoComRotulo(rotulo, input) {
  return el("label", { class: "filtro-campo" }, [el("span", { texto: rotulo }), input]);
}

function formatarHoras(horas) {
  if (horas === null || horas === undefined) return "—";
  if (horas < 1) return `${Math.round(horas * 60)}min`;
  if (horas < 48) return `${horas.toFixed(0)}h`;
  return `${(horas / 24).toFixed(0)}d`;
}

function tagTemperatura(temperatura) {
  return el("span", { class: `tag ${temperatura}`, texto: temperatura });
}

// Change `marco-manual-visivel-na-aba-conversas`: indicador de conversa
// fechada por marco manual (ganho/perdido) — diferente de `.tag.encerrado`,
// que já existe para o estágio terminal automático (SX/PX). Devolve `null`
// para conversa aberta, para o chamador decidir se renderiza a tag.
function tagResultado(resultado) {
  if (!resultado) return null;
  const rotulo = resultado === "ganho" ? "✓ ganho (manual)" : "✕ perdido (manual)";
  return el("span", { class: `tag ${resultado}`, texto: rotulo });
}

// -- Telas -----------------------------------------------------------------

async function renderizarFila(container) {
  const dados = await chamarApi("/fila");
  container.appendChild(el("h2", { texto: `Fila de hoje (${dados.itens.length})` }));
  // Change `painel-mensagens-recentes-e-acoes-seguras`: `total` é a
  // contagem real de conversas abertas, mesmo que o carregamento interno
  // (`_carregar_candidatos`) tenha cortado antes de montar a fila — avisa o
  // operador que existe mais conversa aberta do que a fila processou.
  if (typeof dados.total === "number" && dados.total > dados.itens.length) {
    container.appendChild(
      el("p", {
        class: "aviso",
        texto: `${dados.total} conversa(s) aberta(s) no total — corte de carregamento pode não ter processado todas`,
      })
    );
  }
  if (dados.itens.length === 0) {
    container.appendChild(el("p", { class: "aviso", texto: "Fila vazia — nada a fazer hoje." }));
    return;
  }
  dados.itens.forEach((item) => {
    const linha = el("div", { class: "fila-item" }, [
      el("span", { class: "prioridade", texto: String(item.prioridade) }),
      el("span", { class: "nome", texto: `${item.nome} — ${item.estagio_label} (${item.estagio})` }),
      tagTemperatura(item.temperatura),
      el("span", { class: "acao", texto: `${item.acao} — ${formatarHoras(item.horas_esperando)}` }),
    ]);
    linha.style.cursor = "pointer";
    linha.addEventListener("click", () => {
      window.location.hash = `#/conversas/${item.conversa_id}`;
    });
    container.appendChild(linha);
  });
}

// Coluna de marco -> marco a gravar (§3: as únicas 5 marcadas à mão).
// SX/PX (terminal) usam o mesmo marco "perdido" — §3 diz que ele vale nos
// dois funis; S6 usa "ganho" pela mesma razão.
const ESTAGIO_PARA_MARCO = {
  S6: "ganho",
  SX: "perdido",
  P5: "consignacao_assinada",
  P6: "primeira_reposicao",
  PX: "perdido",
};

// Origem do drag em curso (id do card + funil do kanban de onde ele saiu).
// Variável de módulo, não `dataTransfer.getData`: alguns navegadores só
// deixam ler o payload no `drop`, não no `dragover`, e é no `dragover` que
// a coluna precisa saber se pinta o alvo como válido ou recusado.
let origemArraste = null;

function mostrarErroKanban(container, mensagem) {
  const existente = container.querySelector(".kanban-erro");
  if (existente) existente.remove();
  container.insertBefore(el("div", { class: "kanban-erro", texto: mensagem }), container.firstChild);
}

/** O que um drop nesta coluna faria: mudar funil, marcar marco, ou nada. */
function planoDoDrop(coluna, funilDoBoard) {
  if (!origemArraste) return { valido: false };
  if (origemArraste.funil !== funilDoBoard) {
    // Card veio do outro kanban: qualquer coluna aqui serve de alvo — o
    // servidor recalcula o estágio a partir dos fatos já gravados, a coluna
    // de destino é só onde o operador soltou o card.
    return { valido: true, tipo: "funil" };
  }
  if (coluna.aceita_drop) {
    return { valido: true, tipo: "marco", marco: ESTAGIO_PARA_MARCO[coluna.estagio] };
  }
  return { valido: false };
}

async function renderizarKanban(container) {
  const dados = await chamarApi("/kanban");
  // Change `painel-mensagens-recentes-e-acoes-seguras`: `total` é a
  // contagem real de conversas abertas — o carregamento interno corta em
  // `LIMITE_CONVERSAS_PADRAO`, e o operador precisa saber quando isso
  // aconteceu (soma dos cards nas colunas < total real).
  const totalNosCards = dados.kanbans.reduce(
    (soma, k) => soma + k.colunas.reduce((s, c) => s + c.cards.length, 0),
    0
  );
  if (typeof dados.total === "number" && dados.total > totalNosCards) {
    container.appendChild(
      el("p", {
        class: "aviso",
        texto: `${dados.total} conversa(s) aberta(s) no total — só ${totalNosCards} exibida(s) (corte de carregamento)`,
      })
    );
  }
  dados.kanbans.forEach((kanban) => {
    container.appendChild(el("h2", { texto: `Kanban — ${kanban.funil.toUpperCase()}` }));
    const board = el("div", { class: "kanban" });
    kanban.colunas.forEach((coluna) => {
      const classes = "coluna" + (coluna.derivada ? " derivada" : "");
      const tituloLinha = el("div", { class: "titulo" }, [
        el("span", { texto: coluna.label }),
        el("span", { texto: String(coluna.cards.length) }),
      ]);
      const filhos = [tituloLinha];
      if (coluna.motivo_recusa) {
        filhos.push(el("div", { class: "motivo-recusa", texto: coluna.motivo_recusa }));
      }
      coluna.cards.forEach((card) => {
        const cardEl = el("div", { class: "card", draggable: "true" }, [
          el("span", { class: "nome", texto: card.nome }),
          el("span", { class: "sinal", texto: card.sinal }),
        ]);
        cardEl.addEventListener("click", () => {
          window.location.hash = `#/conversas/${card.id}`;
        });
        cardEl.addEventListener("dragstart", (evento) => {
          origemArraste = { id: card.id, funil: kanban.funil };
          cardEl.classList.add("arrastando");
          evento.dataTransfer.effectAllowed = "move";
          // Payload no `dataTransfer` também, por completude — o drop lê
          // `origemArraste`, que sobrevive mesmo nos navegadores que só
          // liberam `getData` no próprio evento de drop.
          evento.dataTransfer.setData("text/plain", String(card.id));
        });
        cardEl.addEventListener("dragend", () => {
          cardEl.classList.remove("arrastando");
          origemArraste = null;
        });
        filhos.push(cardEl);
      });

      const colunaEl = el("div", { class: classes }, filhos);
      colunaEl.addEventListener("dragover", (evento) => {
        const plano = planoDoDrop(coluna, kanban.funil);
        if (!plano.valido && !origemArraste) return; // nada sendo arrastado
        evento.preventDefault(); // permite o drop mesmo quando será recusado
        colunaEl.classList.toggle("alvo-valido", plano.valido);
        colunaEl.classList.toggle("alvo-invalido", !plano.valido);
      });
      colunaEl.addEventListener("dragleave", () => {
        colunaEl.classList.remove("alvo-valido", "alvo-invalido");
      });
      colunaEl.addEventListener("drop", async (evento) => {
        evento.preventDefault();
        colunaEl.classList.remove("alvo-valido", "alvo-invalido");
        const origem = origemArraste;
        origemArraste = null;
        if (!origem) return;
        const plano = planoDoDrop(coluna, kanban.funil);
        if (!plano.valido) {
          mostrarErroKanban(
            container,
            coluna.motivo_recusa || "§3: esta coluna não aceita marcação manual"
          );
          return;
        }
        const por = obterOperador();
        try {
          if (plano.tipo === "marco") {
            await chamarApiEscrever(`/conversas/${origem.id}/marcos`, {
              marco: plano.marco,
              por,
            });
          } else {
            await chamarApiEscrever(`/conversas/${origem.id}/funil`, {
              funil: kanban.funil,
              por,
            });
          }
          await renderizarRotaSegura(); // recarrega o kanban com o estado novo
        } catch (erro) {
          mostrarErroKanban(
            container,
            `${erro.message}${erro.regra ? ` (${erro.regra})` : ""}`
          );
        }
      });
      board.appendChild(colunaEl);
    });
    container.appendChild(board);
  });
}

function montarFiltros(recarregar) {
  const wrap = el("div", { class: "filtros" });

  const selEstagio = el("select", { id: "filtro-estagio" });
  selEstagio.appendChild(el("option", { value: "", texto: "estágio: todos" }));
  const selTemperatura = el("select", { id: "filtro-temperatura" });
  selTemperatura.appendChild(el("option", { value: "", texto: "temperatura: todas" }));
  ["quente", "morno", "esfriando", "frio", "encerrado"].forEach((t) => {
    selTemperatura.appendChild(el("option", { value: t, texto: t }));
  });
  const selBola = el("select", { id: "filtro-bola" });
  selBola.appendChild(el("option", { value: "", texto: "bola: todas" }));
  selBola.appendChild(el("option", { value: "camu", texto: "com a Camu" }));
  selBola.appendChild(el("option", { value: "cliente", texto: "com o cliente" }));

  const selOrdenar = el("select", { id: "ordenar" });
  [
    ["horas_esperando", "espera"],
    ["nome", "nome"],
    ["estagio", "estágio"],
    ["temperatura", "temperatura"],
  ].forEach(([valor, rotulo]) => {
    selOrdenar.appendChild(el("option", { value: valor, texto: rotulo }));
  });

  // Change `painel-preserva-estado-em-refresh`: inicializa a partir do
  // estado de módulo (sobrevive ao re-render), não sempre no padrão — e
  // grava de volta a cada `change`, antes de recarregar a lista.
  selEstagio.value = estadoFiltrosConversas.estagio;
  selTemperatura.value = estadoFiltrosConversas.temperatura;
  selBola.value = estadoFiltrosConversas.bola;
  selOrdenar.value = estadoFiltrosConversas.ordenar;

  selEstagio.addEventListener("change", () => { estadoFiltrosConversas.estagio = selEstagio.value; });
  selTemperatura.addEventListener("change", () => { estadoFiltrosConversas.temperatura = selTemperatura.value; });
  selBola.addEventListener("change", () => { estadoFiltrosConversas.bola = selBola.value; });
  selOrdenar.addEventListener("change", () => { estadoFiltrosConversas.ordenar = selOrdenar.value; });

  [selEstagio, selTemperatura, selBola, selOrdenar].forEach((s) => {
    s.addEventListener("change", recarregar);
  });

  wrap.appendChild(selEstagio);
  wrap.appendChild(selTemperatura);
  wrap.appendChild(selBola);
  wrap.appendChild(selOrdenar);
  return wrap;
}

async function renderizarConversas(container) {
  container.appendChild(el("h2", { texto: "Conversas" }));
  const lista = el("div", { id: "lista-conversas" });

  const carregar = async () => {
    lista.textContent = "";
    const params = new URLSearchParams();
    const estagio = document.getElementById("filtro-estagio").value;
    const temperatura = document.getElementById("filtro-temperatura").value;
    const bola = document.getElementById("filtro-bola").value;
    const ordenar = document.getElementById("ordenar").value;
    if (estagio) params.set("estagio", estagio);
    if (temperatura) params.set("temperatura", temperatura);
    if (bola) params.set("bola", bola);
    params.set("ordenar", ordenar);
    const dados = await chamarApi(`/conversas?${params.toString()}`);
    lista.appendChild(el("p", { class: "aviso", texto: `${dados.total} conversa(s)` }));
    dados.conversas.forEach((card) => {
      const filhos = [
        el("span", { class: "nome", texto: `${card.nome} — ${card.estagio_label}` }),
        tagTemperatura(card.temperatura),
      ];
      const tagFechada = tagResultado(card.resultado);
      if (tagFechada) filhos.push(tagFechada);
      filhos.push(el("span", { class: "acao", texto: formatarHoras(card.horas_esperando) }));
      const linha = el("div", { class: "fila-item" }, filhos);
      linha.style.cursor = "pointer";
      linha.addEventListener("click", () => {
        window.location.hash = `#/conversas/${card.id}`;
      });
      lista.appendChild(linha);
    });
  };

  container.appendChild(montarFiltros(carregar));
  container.appendChild(lista);
  await carregar();
}

function linhaFato(fato) {
  return el("div", { class: "linha" }, [
    el("strong", { texto: fato.chave }),
    document.createTextNode(" — "),
    el("span", { class: "evidencia", texto: fato.evidencia || "(sem evidência localizada)" }),
  ]);
}

function linhaEvento(evento) {
  const filhos = [
    el("span", { texto: `${evento.de || "início"} → ${evento.para}` }),
    document.createTextNode(` — ${evento.motivo || ""} (${evento.origem})`),
  ];
  if (evento.aviso) {
    filhos.push(el("div", { class: "aviso-backfill", texto: evento.aviso }));
  }
  return el("div", { class: "linha" }, filhos);
}

async function renderizarDetalhe(container, id) {
  const detalhe = await chamarApi(`/conversas/${id}`);
  if (detalhe.erro) {
    container.appendChild(el("p", { class: "aviso", texto: detalhe.erro }));
    return;
  }
  const card = detalhe.card;
  container.appendChild(el("h2", { texto: `#${card.id} ${card.nome}` }));
  const filhosResumo = [
    document.createTextNode(`${card.estagio_label} (${card.estagio}) — `),
    tagTemperatura(card.temperatura),
  ];
  const tagFechada = tagResultado(card.resultado);
  if (tagFechada) filhosResumo.push(document.createTextNode(" "), tagFechada);
  filhosResumo.push(document.createTextNode(` — sinal: ${card.sinal}`));
  const resumo = el("p", { class: "aviso" }, filhosResumo);
  container.appendChild(resumo);

  // Change `extracao-em-lote-por-janela`: extração manual, incondicional —
  // ignora o gatilho híbrido do webhook (contagem/espera de mensagens
  // pendentes). Útil quando o operador quer o estágio atualizado agora,
  // sem esperar a próxima rodada de `camucrm extrair`.
  const botaoExtrair = el("button", { class: "secundario", texto: "Extrair agora" });
  botaoExtrair.addEventListener("click", async () => {
    botaoExtrair.disabled = true;
    const rotuloOriginal = botaoExtrair.textContent;
    botaoExtrair.textContent = "Extraindo (chama o LLM)…";
    try {
      await chamarApiEscrever(`/conversas/${id}/extrair`);
      await renderizarRotaSegura();
    } catch (erro) {
      container.appendChild(el("p", { class: "aviso", texto: `Erro: ${erro.message}` }));
      botaoExtrair.disabled = false;
      botaoExtrair.textContent = rotuloOriginal;
    }
  });
  container.appendChild(botaoExtrair);

  if (detalhe.contato) {
    const contato = detalhe.contato;
    container.appendChild(
      el("p", {
        class: "aviso",
        texto: `Contato: ${contato.nome || "(sem nome)"} — ${contato.tipo.toUpperCase()} — ` +
          (contato.tem_telefone ? "tem telefone cadastrado" : "sem telefone cadastrado") +
          (contato.e_teste ? " — TESTE" : ""),
      })
    );

    // Change `contatos-de-teste-isolados`: botão dedicado, sem misturar com
    // a correção genérica de campo — marcar/desmarcar teste é uma flag
    // operacional, não uma correção de classificação de negócio (§7).
    const botaoTeste = el("button", {
      class: "secundario",
      texto: contato.e_teste ? "Desmarcar contato de teste" : "Marcar contato de teste",
    });
    botaoTeste.addEventListener("click", async () => {
      try {
        await chamarApiEscrever(`/conversas/${id}/teste`, {
          e_teste: !contato.e_teste,
          por: obterOperador(),
        });
        await renderizarRotaSegura();
      } catch (erro) {
        container.appendChild(
          el("p", { class: "aviso", texto: `Erro: ${erro.message}` })
        );
      }
    });
    container.appendChild(botaoTeste);
  }

  // Change `estagio-reabertura-manual-e-relogio`: botão só aparece quando
  // há um `recusa_explicita=true` gravado E ele ainda não foi desconsiderado
  // — evita oferecer a ação de novo depois de já ter sido feita. O fato em
  // si nunca é apagado (design.md); só a interpretação da regra de estágio
  // muda, registrada em `correcoes` com `por` obrigatório.
  const temRecusa = detalhe.fatos.some((f) => f.chave === "recusa_explicita");
  const jaDesconsiderada = detalhe.correcoes.some(
    (c) => c.campo === "recusa_explicita" && c.depois === "desconsiderado"
  );
  if (temRecusa && !jaDesconsiderada) {
    const botaoDesconsiderarRecusa = el("button", {
      class: "secundario",
      texto: "Desconsiderar recusa explícita (falso positivo)",
    });
    botaoDesconsiderarRecusa.addEventListener("click", async () => {
      // Change `painel-preserva-estado-em-refresh`: cobre a janela entre o
      // clique e a resposta chegar — um refresh do SSE no meio não pode
      // apagar este container antes da escrita terminar.
      haEdicaoEmAndamento = true;
      try {
        await chamarApiEscrever(`/conversas/${id}/desconsiderar-recusa`, {
          por: obterOperador(),
        });
        await renderizarRotaSegura();
      } catch (erro) {
        container.appendChild(
          el("p", { class: "aviso", texto: `Erro: ${erro.message}` })
        );
      } finally {
        haEdicaoEmAndamento = false;
      }
    });
    container.appendChild(botaoDesconsiderarRecusa);
  }

  const secaoFatos = el("div", { class: "secao" }, [el("h3", { texto: "Fatos (com evidência)" })]);
  if (detalhe.fatos.length === 0) {
    secaoFatos.appendChild(el("p", { class: "aviso", texto: "nenhum fato extraído ainda" }));
  }
  detalhe.fatos.forEach((f) => secaoFatos.appendChild(linhaFato(f)));
  container.appendChild(secaoFatos);

  const secaoEventos = el("div", { class: "secao" }, [el("h3", { texto: "Timeline de estágio" })]);
  detalhe.eventos.forEach((e) => secaoEventos.appendChild(linhaEvento(e)));
  container.appendChild(secaoEventos);

  const secaoObjecoes = el("div", { class: "secao" }, [el("h3", { texto: "Objeções (§4)" })]);
  if (detalhe.objecoes.length === 0) {
    secaoObjecoes.appendChild(el("p", { class: "aviso", texto: "nenhuma objeção registrada" }));
  }
  detalhe.objecoes.forEach((o) => {
    secaoObjecoes.appendChild(
      el("div", { class: "linha" }, [
        el("strong", { texto: o.categoria }),
        document.createTextNode(` — ${o.trecho || ""}`),
      ])
    );
  });
  container.appendChild(secaoObjecoes);

  // Change `resumo-conversa`: correções (5) vêm ANTES de follow-ups (6) —
  // "a ordem da tela é ela que ancora o humano em evidência" (design.md) —
  // e as duas vêm antes do bloco de resumo (7), abaixo.
  const secaoCorrecoes = el("div", { class: "secao" }, [el("h3", { texto: "Correções (§7)" })]);
  detalhe.correcoes.forEach((c) => {
    secaoCorrecoes.appendChild(
      el("div", {
        class: "linha",
        texto: `${c.campo}: "${c.antes || ""}" → "${c.depois || ""}" (${c.por || "?"})`,
      })
    );
  });
  container.appendChild(secaoCorrecoes);

  const secaoFollowups = el("div", { class: "secao" }, [el("h3", { texto: "Follow-ups (§6)" })]);
  detalhe.followups.forEach((f) => {
    secaoFollowups.appendChild(
      el("div", { class: "linha", texto: `#${f.numero}: ${f.texto || "(sem texto salvo)"}` })
    );
  });
  container.appendChild(secaoFollowups);

  const secaoMarcos = el("div", { class: "secao" }, [el("h3", { texto: "Marcos manuais (§3)" })]);
  detalhe.marcos.forEach((m) => {
    secaoMarcos.appendChild(
      el("div", { class: "linha", texto: `${m.marco} — por ${m.por || "?"}` })
    );
  });
  container.appendChild(secaoMarcos);

  // Bloco 7 — só depois dos 6 blocos determinísticos acima. Visualmente
  // distinto (classe `resumo-llm`): é a única prosa gerada por modelo na
  // tela, e a tela precisa continuar inteiramente útil sem ela.
  await renderizarResumo(container, id);

  await renderizarRascunhos(container, id);
  await renderizarMensagens(container, id);

  // Change `ground-truth-no-painel`: atalho para rotular esta conversa —
  // abre o formulário de `#/groundtruth` com a transcrição real já
  // pré-carregada (somente leitura), campos de julgamento em branco.
  const secaoGroundTruth = el("div", { class: "secao" });
  const botaoGroundTruth = el("button", {
    class: "secundario",
    texto: "Usar para ground truth (§7)",
  });
  botaoGroundTruth.addEventListener("click", () => {
    window.location.hash = `#/groundtruth/novo/${id}`;
  });
  secaoGroundTruth.appendChild(botaoGroundTruth);
  container.appendChild(secaoGroundTruth);
}

/**
 * Change `resumo-conversa`: terceira superfície de LLM (§1/CLAUDE.md,
 * `camucrm/summaries.py`). `GET` só lê o cache — nunca gera; o botão
 * Gerar/Regerar é a única coisa que chama `POST` (gasta cota, grava linha).
 * Sem LLM configurado (ou cache vazio) a seção mostra "resumo não gerado" —
 * os blocos 1-6 acima já são inteiramente úteis sozinhos.
 */
async function renderizarResumo(container, id) {
  const secao = el("div", { class: "secao resumo-llm" }, [
    el("h3", { texto: "Resumo (gerado por LLM)" }),
  ]);
  const areaResultado = el("div", { class: "resumo-conteudo" });

  function montarConteudo(dados) {
    areaResultado.textContent = "";
    if (!dados.gerado) {
      areaResultado.appendChild(
        el("p", {
          class: "aviso",
          texto: dados.erro ? `resumo não gerado — ${dados.erro}` : "resumo não gerado",
        })
      );
      return;
    }
    areaResultado.appendChild(el("p", { texto: dados.resumo }));
    areaResultado.appendChild(
      el("p", { class: "aviso", texto: `Próximo passo: ${dados.proximo_passo}` })
    );
    areaResultado.appendChild(
      el("p", {
        class: "aviso",
        texto: `gerado por LLM (${dados.modelo || "?"}) · prompt v${dados.prompt_versao} · ` +
          `há ${dados.mensagens_desde} mensagem(ns)`,
      })
    );
  }

  const cache = await chamarApi(`/conversas/${id}/resumo`);
  montarConteudo(cache);
  // Já existe um resumo: clicar de novo é pedido explícito de "Regerar" —
  // `forcar=true` pula a checagem de cache. Sem resumo ainda, um `POST`
  // sem `forcar` já gera (não há cache para a rota reaproveitar).
  let jaGerado = Boolean(cache.gerado);

  const botaoGerar = el("button", {
    class: "secundario",
    texto: jaGerado ? "Regerar" : "Gerar resumo",
  });
  botaoGerar.addEventListener("click", async () => {
    botaoGerar.disabled = true;
    const rotuloOriginal = botaoGerar.textContent;
    botaoGerar.textContent = "Gerando (chama o LLM)…";
    try {
      const dados = await chamarApiEscrever(`/conversas/${id}/resumo`, {
        por: obterOperador(),
        forcar: jaGerado,
      });
      montarConteudo(dados);
      jaGerado = Boolean(dados.gerado);
      botaoGerar.textContent = jaGerado ? "Regerar" : "Gerar resumo";
    } catch (erro) {
      areaResultado.textContent = `Erro: ${erro.message}`;
      botaoGerar.textContent = rotuloOriginal;
    } finally {
      botaoGerar.disabled = false;
    }
  });

  secao.appendChild(botaoGerar);
  secao.appendChild(areaResultado);
  container.appendChild(secao);
}

/**
 * Change `rascunho-registrado` (§10): gera duas opções via LLM (POST — gasta
 * cota e grava linha, nunca GET), mostra as duas com botão copiar (texto +
 * comando `camucrm enviar` pronto, já com o id do rascunho) e permite
 * registrar a escolha manualmente. O painel NUNCA envia — só gera e grava.
 */
async function renderizarRascunhos(container, id) {
  const secao = el("div", { class: "secao" }, [el("h3", { texto: "Rascunho (§10)" })]);

  const areaResultado = el("div", { class: "rascunho-resultado" });
  const botaoGerar = el("button", { texto: "Gerar rascunho" });
  botaoGerar.addEventListener("click", async () => {
    botaoGerar.disabled = true;
    areaResultado.textContent = "Gerando (chama o LLM)…";
    // Change `painel-preserva-estado-em-refresh`: a chamada ao LLM pode
    // demorar; se um refresh do SSE apagar `conteudo` antes dela voltar,
    // `areaResultado` vira um nó órfão e o rascunho — já gravado no banco —
    // some da tela sem aviso. Suprime o refresh automático até a resposta
    // chegar (ou falhar).
    haEdicaoEmAndamento = true;
    try {
      const dados = await chamarApiEscrever(`/conversas/${id}/rascunho`, {
        por: obterOperador(),
      });
      areaResultado.textContent = "";
      areaResultado.appendChild(montarRascunho(dados));
    } catch (erro) {
      areaResultado.textContent = `Erro: ${erro.message}${erro.regra ? ` (${erro.regra})` : ""}`;
    } finally {
      botaoGerar.disabled = false;
      haEdicaoEmAndamento = false;
    }
  });
  secao.appendChild(botaoGerar);
  secao.appendChild(areaResultado);

  const historico = await chamarApi(`/conversas/${id}/rascunhos?limite=5`);
  secao.appendChild(el("h4", { texto: "Histórico (sem chamar o LLM)" }));
  if (historico.rascunhos.length === 0) {
    secao.appendChild(el("p", { class: "aviso", texto: "nenhum rascunho gerado ainda" }));
  }
  historico.rascunhos.forEach((r) => secao.appendChild(montarRascunho(r)));

  container.appendChild(secao);
}

/** Um rascunho — opções (ou recusa), avisos, escolha já feita, e os botões
 * de copiar/escolher. `textContent`/`el(..., {texto})` sempre; o texto vem
 * de conversa/LLM e nunca deve virar HTML (regra do topo do arquivo). */
function montarRascunho(rascunho) {
  const bloco = el("div", { class: "rascunho-item" });
  bloco.appendChild(
    el("p", {
      class: "aviso",
      texto: `#${rascunho.id} — ${new Date(rascunho.gerado_em).toLocaleString()}`,
    })
  );

  if (rascunho.encerrar) {
    bloco.appendChild(el("p", { texto: `Recusado pelo modelo: ${rascunho.motivo}` }));
    return bloco;
  }

  rascunho.opcoes.forEach((texto, indice) => {
    const numero = indice + 1;
    const opcaoEl = el("div", { class: "linha" });
    opcaoEl.appendChild(el("pre", { texto }));

    const comando = rascunho.comandos ? rascunho.comandos[String(numero)] : null;
    const botaoCopiar = el("button", {
      class: "secundario",
      texto: `Copiar opção ${numero} + comando`,
    });
    botaoCopiar.addEventListener("click", async () => {
      const rotuloOriginal = botaoCopiar.textContent;
      try {
        await navigator.clipboard.writeText(comando ? `${texto}\n\n${comando}` : texto);
        botaoCopiar.textContent = "Copiado!";
      } catch (e) {
        // Clipboard indisponível (sem permissão, contexto não seguro) — o
        // texto e o comando já estão visíveis na tela para copiar à mão.
        botaoCopiar.textContent = "Copie manualmente (clipboard indisponível)";
      }
      setTimeout(() => { botaoCopiar.textContent = rotuloOriginal; }, 2000);
    });
    opcaoEl.appendChild(botaoCopiar);
    if (comando) {
      opcaoEl.appendChild(el("div", { class: "aviso comando-pronto", texto: comando }));
    }

    if (rascunho.escolhida === null && !rascunho.texto_final) {
      const botaoEscolher = el("button", {
        class: "secundario",
        texto: `Registrar escolha: opção ${numero}`,
      });
      botaoEscolher.addEventListener("click", async () => {
        // Change `painel-preserva-estado-em-refresh`: mesma razão do botão
        // "Gerar rascunho" acima — a escrita acontece de qualquer forma; o
        // que a flag evita é a atualização de tela ir parar num nó órfão.
        haEdicaoEmAndamento = true;
        try {
          await chamarApiEscrever(`/rascunhos/${rascunho.id}/escolha`, {
            opcao: numero,
            por: obterOperador(),
          });
          botaoEscolher.textContent = "Registrado";
          botaoEscolher.disabled = true;
        } catch (erro) {
          botaoEscolher.textContent = `Erro: ${erro.message}`;
        } finally {
          haEdicaoEmAndamento = false;
        }
      });
      opcaoEl.appendChild(botaoEscolher);
    }
    bloco.appendChild(opcaoEl);
  });

  if (rascunho.avisos && rascunho.avisos.length > 0) {
    bloco.appendChild(
      el("p", { class: "aviso", texto: `Avisos de tom: ${rascunho.avisos.join("; ")}` })
    );
  }
  if (rascunho.escolhida !== null || rascunho.texto_final) {
    const quem = rascunho.escolhida !== null ? `opção ${rascunho.escolhida}` : "texto próprio";
    bloco.appendChild(
      el("p", {
        class: "aviso",
        texto: `Escolhido: ${quem} — por ${rascunho.escolhido_por || "?"}${
          rascunho.mensagem_id ? ` — vinculado à mensagem #${rascunho.mensagem_id}` : ""
        }`,
      })
    );
  }
  return bloco;
}

async function renderizarMensagens(container, id) {
  const secao = el("div", { class: "secao" }, [el("h3", { texto: "Mensagens" })]);
  const dados = await chamarApi(`/conversas/${id}/mensagens`);
  // Change `painel-mensagens-recentes-e-acoes-seguras`: sem `desde_id`, a
  // API agora traz as mais RECENTES (requirement "Mensagens recentes
  // aparecem por padrão") — `tem_mais` avisa quando a conversa tem mais
  // mensagens do que as exibidas, para a tela nunca fingir estar completa.
  if (dados.tem_mais) {
    secao.appendChild(
      el("p", {
        class: "aviso",
        texto: `mostrando as ${dados.mensagens.length} mais recentes de ${dados.total} — histórico mais antigo não exibido`,
      })
    );
  }
  dados.mensagens.forEach((m) => {
    secao.appendChild(el("div", { class: `msg ${m.direcao}`, texto: m.texto }));
  });
  container.appendChild(secao);
}

async function renderizarMetricas(container) {
  const dados = await chamarApi("/metricas");
  container.appendChild(el("h2", { texto: "As métricas que justificam o sistema (§14)" }));
  dados.conversoes_chave.forEach((c) => {
    const texto =
      c.taxa === null
        ? `${c.de}→${c.para}: sem amostra`
        : `${c.de}→${c.para}: ${(c.taxa * 100).toFixed(0)}% (${c.alcancaram_para}/${c.alcancaram_de})`;
    container.appendChild(el("div", { class: "linha", texto }));
  });

  container.appendChild(el("h3", { texto: "Tempo por estágio (só eventos ao vivo — §8)" }));
  if (dados.tempo_por_estagio.length === 0) {
    container.appendChild(el("p", { class: "aviso", texto: "sem transições ao vivo ainda" }));
  }
  dados.tempo_por_estagio.forEach((t) => {
    const texto =
      t.horas_medianas === null
        ? `${t.estagio}: sem dado ao vivo`
        : `${t.estagio} (${t.estagio_label}): mediana ${t.horas_medianas.toFixed(0)}h, n=${t.conversas}`;
    container.appendChild(el("div", { class: "linha", texto }));
  });

  container.appendChild(el("h3", { texto: "Taxonomia de objeções (§4)" }));
  container.appendChild(el("p", { texto: dados.saude_taxonomia.veredito }));
}

/*
 * Change `analise-desempenho`: tela "O que está funcionando" (`#/funciona`).
 *
 * Regras que este bloco não pode quebrar (CLAUDE.md / spec do change):
 *   - toda porcentagem vem com `n` ao lado; abaixo de `AMOSTRA_MINIMA`
 *     (o servidor já manda `amostra_suficiente: false`) mostra "sem
 *     amostra" em vez do número — a supressão é só de exibição, o valor
 *     calculado já veio no payload.
 *   - NUNCA linha de tendência — tendência sobre poucos pontos é o mesmo
 *     modo de falha que a §7 do documento condena. Só tabela + barra CSS.
 *   - o bloco de rascunhos nasce bloqueado com um contador explícito
 *     ("precisa de N envios vinculados; hoje há M"), nunca um gráfico
 *     vazio — gráfico vazio parece bug, contador parece progresso.
 *   - esta tela não afirma nada sobre acurácia de extração (restrição do
 *     `openspec/project.md`, herdada pelo change `analise-desempenho`):
 *     nenhuma linha abaixo lê ou mostra "acurácia".
 */

function formatarPercentual(valor) {
  return `${(valor * 100).toFixed(0)}%`;
}

/** Uma linha de tabela com barra CSS — `null`/`amostra_suficiente=false`
 * mostra "sem amostra" no lugar do número (nunca esconde a linha inteira,
 * só o percentual). */
function linhaComBarra({ rotulo, n, proporcao, amostraSuficiente, detalhe }) {
  const tr = el("tr");
  tr.appendChild(el("td", { texto: rotulo }));

  const tdValor = el("td");
  if (proporcao === null || proporcao === undefined || !amostraSuficiente) {
    tdValor.appendChild(el("span", { class: "funciona-sem-amostra", texto: "sem amostra" }));
  } else {
    const wrap = el("div", { class: "funciona-barra-wrap" });
    const fundo = el("div", { class: "funciona-barra-fundo" });
    const preenchimento = el("div", { class: "funciona-barra-preenchimento" });
    preenchimento.style.width = `${Math.max(0, Math.min(100, proporcao * 100))}%`;
    fundo.appendChild(preenchimento);
    wrap.appendChild(fundo);
    wrap.appendChild(el("span", { texto: formatarPercentual(proporcao) }));
    tdValor.appendChild(wrap);
  }
  tr.appendChild(tdValor);

  tr.appendChild(el("td", { texto: `n=${n}${detalhe ? ` — ${detalhe}` : ""}` }));
  return tr;
}

function tabelaFunciona(cabecalhos, linhas) {
  const tabela = el("table", { class: "funciona-tabela" });
  const thead = el("thead", {}, [
    el("tr", {}, cabecalhos.map((c) => el("th", { texto: c }))),
  ]);
  const tbody = el("tbody", {}, linhas);
  tabela.appendChild(thead);
  tabela.appendChild(tbody);
  return tabela;
}

function secaoFunciona(titulo, filhos) {
  return el("div", { class: "funciona-secao" }, [el("h3", { texto: titulo }), ...filhos]);
}

function blocoConversao(titulo, linhas) {
  const trs = linhas.map((c) =>
    linhaComBarra({
      rotulo: `${c.de_label} → ${c.para_label} (${c.de}→${c.para})`,
      n: c.n,
      proporcao: c.taxa,
      amostraSuficiente: c.amostra_suficiente,
      detalhe: c.taxa === null ? null : `${c.alcancaram_para}/${c.alcancaram_de}`,
    })
  );
  return secaoFunciona(titulo, [tabelaFunciona(["Transição", "Conversão", "Amostra"], trs)]);
}

function blocoOndeMorrem(dados) {
  if (dados.distribuicao.length === 0) {
    return secaoFunciona("Onde as conversas morrem (encerradas)", [
      el("p", { class: "aviso", texto: "nenhuma conversa encerrada ainda" }),
    ]);
  }
  const maiorN = Math.max(...dados.distribuicao.map((d) => d.n));
  const linhas = dados.distribuicao.map((d) => {
    const tr = el("tr");
    tr.appendChild(el("td", { texto: `${d.estagio_label} (${d.estagio})` }));
    const tdBarra = el("td");
    const wrap = el("div", { class: "funciona-barra-wrap" });
    const fundo = el("div", { class: "funciona-barra-fundo" });
    const preenchimento = el("div", { class: "funciona-barra-preenchimento" });
    preenchimento.style.width = `${maiorN ? (d.n / maiorN) * 100 : 0}%`;
    fundo.appendChild(preenchimento);
    wrap.appendChild(fundo);
    wrap.appendChild(el("span", { texto: String(d.n) }));
    tdBarra.appendChild(wrap);
    tr.appendChild(tdBarra);
    return tr;
  });
  const filhos = [tabelaFunciona(["Estágio", "Conversas encerradas ali"], linhas)];
  if (!dados.amostra_suficiente) {
    filhos.push(el("p", { class: "aviso", texto: `amostra baixa (n=${dados.n}) — leia com cautela` }));
  }
  return secaoFunciona("Onde as conversas morrem (encerradas)", filhos);
}

function blocoTempoPorEstagio(linhas) {
  if (linhas.length === 0) {
    return secaoFunciona("Tempo por estágio (só eventos ao vivo — §8)", [
      el("p", { class: "aviso", texto: "sem transições ao vivo ainda" }),
    ]);
  }
  const trs = linhas.map((t) => {
    const tr = el("tr");
    tr.appendChild(el("td", { texto: `${t.estagio_label} (${t.estagio})` }));
    tr.appendChild(el("td", {
      texto: t.horas_medianas === null ? "sem dado" : `${t.horas_medianas.toFixed(0)}h`,
    }));
    tr.appendChild(el("td", { texto: `n=${t.n}` }));
    return tr;
  });
  return secaoFunciona("Tempo por estágio (só eventos ao vivo — §8)", [
    tabelaFunciona(["Estágio", "Mediana", "Amostra"], trs),
  ]);
}

function blocoObjecoesECorrecoes(objecoes, correcoes) {
  const filhos = [];
  if (objecoes.por_estagio.celulas.length === 0) {
    filhos.push(el("p", { class: "aviso", texto: "nenhuma objeção com estágio registrado ainda" }));
  } else {
    const trs = objecoes.por_estagio.celulas.map((c) => {
      const tr = el("tr");
      tr.appendChild(el("td", { texto: c.estagio_label ? `${c.estagio_label} (${c.estagio})` : "(sem estágio)" }));
      tr.appendChild(el("td", { texto: c.categoria }));
      tr.appendChild(el("td", { texto: String(c.n) }));
      return tr;
    });
    filhos.push(el("h4", { texto: "Objeção por estágio (§4)" }));
    filhos.push(tabelaFunciona(["Estágio", "Objeção", "n"], trs));
  }
  filhos.push(el("p", { class: "aviso", texto: objecoes.saude_taxonomia.veredito }));

  if (correcoes.linhas.length === 0) {
    filhos.push(el("p", { class: "aviso", texto: "nenhuma correção registrada ainda" }));
  } else {
    const trs = correcoes.linhas.map((c) => {
      const tr = el("tr");
      tr.appendChild(el("td", { texto: c.campo }));
      tr.appendChild(el("td", { texto: `"${c.antes ?? ""}" → "${c.depois ?? ""}"` }));
      tr.appendChild(el("td", { texto: String(c.n) }));
      return tr;
    });
    filhos.push(el("h4", { texto: "Padrão de correções (§7)" }));
    filhos.push(tabelaFunciona(["Campo", "De → Para", "n"], trs));
  }
  return secaoFunciona("Objeções e correções", filhos);
}

function blocoFollowups(retorno) {
  if (retorno.length === 0) {
    return secaoFunciona("Retorno por número de follow-up (§6)", [
      el("p", { class: "aviso", texto: "nenhum follow-up enviado ainda" }),
    ]);
  }
  const trs = retorno.map((r) =>
    linhaComBarra({
      rotulo: `${r.numero}º toque`,
      n: r.n,
      proporcao: r.taxa,
      amostraSuficiente: r.amostra_suficiente,
      detalhe: r.taxa === null ? null : `${r.com_retorno}/${r.n} responderam depois`,
    })
  );
  return secaoFunciona("Retorno por número de follow-up (§6)", [
    tabelaFunciona(["Toque", "Taxa de retorno", "Amostra"], trs),
  ]);
}

/** Bloco de rascunhos (§10): bloqueado com contador explícito enquanto
 * `n_vinculados < limiar` — NUNCA um gráfico vazio (requirement "Bloco de
 * rascunhos nasce bloqueado"). */
function blocoRascunhos(dados) {
  if (dados.bloqueado) {
    return secaoFunciona("A/B natural de rascunho (§10)", [
      el("div", { class: "funciona-bloqueado" }, [
        el("div", { class: "contador", texto: `${dados.n_vinculados} / ${dados.limiar}` }),
        el("div", { texto: `precisa de ${dados.limiar} envios vinculados; hoje há ${dados.n_vinculados}` }),
      ]),
    ]);
  }
  const trs = [
    linhaComBarra({
      rotulo: "Opção 1 (viés de posição)",
      n: dados.opcao_1.total,
      proporcao: dados.opcao_1.proporcao,
      amostraSuficiente: dados.opcao_1.amostra_suficiente,
    }),
    linhaComBarra({
      rotulo: "Editado (vs. sem edição)",
      n: dados.editado.total,
      proporcao: dados.editado.proporcao,
      amostraSuficiente: dados.editado.amostra_suficiente,
    }),
    linhaComBarra({
      rotulo: "Avançou estágio em 72h",
      n: dados.avanco_72h.total,
      proporcao: dados.avanco_72h.taxa,
      amostraSuficiente: dados.avanco_72h.amostra_suficiente,
    }),
  ];
  return secaoFunciona("A/B natural de rascunho (§10)", [
    tabelaFunciona(["Pergunta", "Resultado", "Amostra"], trs),
    el("p", { class: "aviso", texto: `escreveu do zero (sem usar opção): ${dados.escreveu_do_zero}` }),
  ]);
}

async function renderizarFunciona(container) {
  const dados = await chamarApi("/o-que-funciona?dias=90");
  container.appendChild(el("h2", { texto: "O que está funcionando" }));
  container.appendChild(
    el("div", {
      class: "funciona-nota",
      texto:
        "Esta tela não afirma nada sobre acurácia de extração — isso depende do " +
        "conjunto de avaliação rotulado à mão (ground truth) e ainda não existe. " +
        "Conversão e tempo por estágio abaixo são medidos direto do histórico de " +
        "eventos, não da extração do modelo.",
    })
  );

  container.appendChild(blocoConversao("Os três números (§14)", dados.funil.metricas_chave));
  container.appendChild(blocoConversao("Conversão adjacente — B2C", dados.funil.conversao_b2c));
  container.appendChild(blocoConversao("Conversão adjacente — B2B", dados.funil.conversao_b2b));
  container.appendChild(blocoOndeMorrem(dados.funil.onde_morrem));
  container.appendChild(blocoTempoPorEstagio(dados.tempo_por_estagio));
  container.appendChild(blocoObjecoesECorrecoes(dados.objecoes, dados.correcoes));
  container.appendChild(blocoFollowups(dados.followups.retorno));
  container.appendChild(blocoRascunhos(dados.rascunhos));
}

/*
 * Change `ground-truth-no-painel` (§7): rotular pelo painel em vez de editar
 * `data/eval/conversas.jsonl` num editor de texto. Duas telas:
 *   - `#/groundtruth` — progresso, avisos, lista com editar/excluir, botão
 *     "rodar eval" (habilitado só com `completo: true`).
 *   - `#/groundtruth/novo/{conversa_id}` e `#/groundtruth/editar/{entrada_id}`
 *     — o mesmo formulário de rotulagem: transcrição somente-leitura em
 *     cima, campos de julgamento embaixo.
 *
 * Taxonomias abaixo espelham `camucrm/taxonomia.py`/`extraction/contract.py`
 * (fechadas, §0/§2/§3/§4) — só para montar `<select>`/checkboxes; a
 * validação de verdade é sempre do servidor (`dataset.validar_entrada`).
 */

const GT_ESTAGIOS_B2C = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "SX"];
const GT_ESTAGIOS_B2B = ["P0", "P1", "P2", "P3", "P4", "P5", "P6", "PX"];
const GT_OBJECOES = [
  "preco", "frete", "prazo", "confianca", "momento", "alternativa", "sem_resposta", "outro",
];
const GT_FATOS = [
  "foto_pet_recebida", "preco_apresentado", "previa_enviada",
  "intencao_compra_explicita", "recusa_explicita", "autorizou_envio_material",
  "visita_aceita",
];
const GT_MARCOS = ["ganho", "consignacao_assinada", "primeira_reposicao"];

function gtSelectEstagio(funilAtual, valorAtual) {
  const sel = el("select", { id: "gt-estagio-final" });
  const estagios = funilAtual === "b2b" ? GT_ESTAGIOS_B2B : GT_ESTAGIOS_B2C;
  estagios.forEach((e) => {
    const opt = el("option", { value: e, texto: `${e} — ${estagio_label_js(e)}` });
    if (e === valorAtual) opt.setAttribute("selected", "selected");
    sel.appendChild(opt);
  });
  return sel;
}

// Rótulos mínimos, só para o `<select>` ficar legível — a fonte de verdade
// continua `camucrm/taxonomia.py::ESTAGIO_LABELS` (servidor).
const GT_ESTAGIO_LABELS = {
  S0: "Lead", S1: "Respondeu", S2: "Foto recebida", S3: "Prévia enviada",
  S4: "Preço apresentado", S5: "Negociação", S6: "Ganho", SX: "Perdido",
  P0: "Não abordado", P1: "Msg 1 enviada", P2: "Autorizou",
  P3: "Proposta apresentada", P4: "Visita agendada",
  P5: "Consignação assinada", P6: "Primeira reposição", PX: "Descartado",
};
function estagio_label_js(e) {
  return GT_ESTAGIO_LABELS[e] || e;
}

function gtCabecalhoProgresso(status) {
  const texto = status.completo
    ? `✓ ${status.total}/${status.minimo} completo`
    : `${status.total}/${status.minimo} rotuladas`;
  const bloco = el("div", { class: "gt-progresso" }, [
    el("strong", { texto }),
  ]);
  status.avisos.forEach((a) => {
    bloco.appendChild(el("p", { class: "aviso", texto: a }));
  });
  return bloco;
}

async function renderizarGroundTruth(container) {
  container.appendChild(el("h2", { texto: "Ground truth (§7)" }));
  container.appendChild(
    el("p", {
      class: "aviso",
      texto:
        "As conversas rotuladas aqui alimentam `make eval` — §7 pede 30, " +
        "rotuladas à mão, uma por uma. Nenhum campo de julgamento é " +
        "sugerido pelo sistema.",
    })
  );

  const status = await chamarApi("/eval/status");
  container.appendChild(gtCabecalhoProgresso(status));

  const botaoNova = el("button", { texto: "Nova entrada (mensagens digitadas)" });
  botaoNova.addEventListener("click", () => {
    window.location.hash = "#/groundtruth/novo";
  });
  container.appendChild(botaoNova);

  const areaRodar = el("div", { class: "gt-rodar" });
  const botaoRodar = el("button", {
    class: status.completo ? "" : "secundario",
    texto: "Rodar eval (chama o LLM)",
  });
  botaoRodar.disabled = !status.completo;
  if (!status.completo) {
    areaRodar.appendChild(
      el("p", {
        class: "aviso",
        texto: `precisa de ${status.minimo} entradas completas para rodar; hoje há ${status.total}`,
      })
    );
  }
  const areaResultadoRodar = el("div");
  botaoRodar.addEventListener("click", async () => {
    botaoRodar.disabled = true;
    areaResultadoRodar.textContent = "Rodando eval (chama o LLM)…";
    try {
      const resultado = await chamarApiEscrever("/eval/rodar", { por: obterOperador() });
      areaResultadoRodar.textContent = "";
      areaResultadoRodar.appendChild(gtResultadoEval(resultado));
    } catch (erro) {
      areaResultadoRodar.textContent = `Erro: ${erro.message}${erro.regra ? ` (${erro.regra})` : ""}`;
    } finally {
      botaoRodar.disabled = !status.completo;
    }
  });
  areaRodar.appendChild(botaoRodar);
  areaRodar.appendChild(areaResultadoRodar);
  container.appendChild(areaRodar);

  const resultadoAtual = await chamarApi("/eval/resultado");
  if (resultadoAtual.disponivel) {
    areaResultadoRodar.appendChild(gtResultadoEval(resultadoAtual));
  }

  const lista = el("div", { class: "gt-lista" }, [el("h3", { texto: "Entradas" })]);
  if (status.entradas.length === 0) {
    lista.appendChild(el("p", { class: "aviso", texto: "nenhuma entrada ainda" }));
  }
  status.entradas.forEach((entrada) => {
    const linha = el("div", { class: "gt-item" }, [
      el("span", { class: "gt-id", texto: entrada.id }),
      el("span", {
        texto: `${entrada.funil.toUpperCase()} — ${entrada.estagio_final_label} ` +
          `(${entrada.estagio_final})${entrada.objecao ? ` — ${entrada.objecao}` : ""} — ` +
          `${entrada.n_mensagens} msg`,
      }),
    ]);
    const botaoEditar = el("button", { class: "secundario", texto: "Editar" });
    botaoEditar.addEventListener("click", () => {
      window.location.hash = `#/groundtruth/editar/${entrada.id}`;
    });
    const botaoExcluir = el("button", { class: "secundario", texto: "Excluir" });
    botaoExcluir.addEventListener("click", async () => {
      if (!window.confirm(`Excluir a entrada ${entrada.id}?`)) return;
      try {
        await chamarApiEscrever(`/eval/rotulos/${entrada.id}`, undefined, "DELETE");
        await renderizarRotaSegura();
      } catch (erro) {
        window.alert(`Erro: ${erro.message}`);
      }
    });
    linha.appendChild(botaoEditar);
    linha.appendChild(botaoExcluir);
    lista.appendChild(linha);
  });
  container.appendChild(lista);
}

function gtResultadoEval(resultado) {
  const bloco = el("div", { class: "gt-resultado" });
  bloco.appendChild(
    el("p", {
      texto: `${resultado.aprovado ? "APROVADO" : "REPROVADO"} — prompt v${resultado.prompt_versao} — ` +
        `${new Date(resultado.rodado_em).toLocaleString()}`,
    })
  );
  bloco.appendChild(
    el("p", {
      class: "aviso",
      texto:
        `fatos: ${resultado.concordancia_fatos === null ? "sem amostra" : formatarPercentual(resultado.concordancia_fatos)} · ` +
        `objeção: ${resultado.acerto_objecao === null ? "sem amostra" : formatarPercentual(resultado.acerto_objecao)} · ` +
        `falsos positivos de avanço: ${resultado.n_falsos_positivos}`,
    })
  );
  return bloco;
}

/** Transcrição somente-leitura — igual em `#/groundtruth/novo/{id}` (mensagens
 * reais da conversa) e `#/groundtruth/editar/{id}` (mensagens já gravadas). */
function gtTranscricao(mensagens) {
  const bloco = el("div", { class: "secao gt-transcricao" }, [
    el("h4", { texto: "Transcrição (somente leitura)" }),
  ]);
  mensagens.forEach((m) => {
    bloco.appendChild(el("div", { class: `msg ${m.direcao}`, texto: m.texto }));
  });
  return bloco;
}

function gtCampoFatos(fatosAtuais) {
  const bloco = el("div", { class: "gt-fatos" }, [el("h4", { texto: "Fatos (§2)" })]);
  GT_FATOS.forEach((chave) => {
    const id = `gt-fato-${chave}`;
    const check = el("input", { type: "checkbox", id });
    if (fatosAtuais && fatosAtuais[chave]) check.setAttribute("checked", "checked");
    const label = el("label", { for: id, texto: ` ${chave}` });
    const linha = el("div", {}, [check, label]);
    bloco.appendChild(linha);
  });
  return bloco;
}

function gtCampoMarcos(marcosAtuais) {
  const bloco = el("div", { class: "gt-marcos" }, [el("h4", { texto: "Marcos (§3)" })]);
  GT_MARCOS.forEach((marco) => {
    const id = `gt-marco-${marco}`;
    const check = el("input", { type: "checkbox", id });
    if (marcosAtuais && marcosAtuais.includes(marco)) check.setAttribute("checked", "checked");
    const label = el("label", { for: id, texto: ` ${marco}` });
    bloco.appendChild(el("div", {}, [check, label]));
  });
  return bloco;
}

function gtLerFatosMarcados() {
  const fatos = {};
  GT_FATOS.forEach((chave) => {
    fatos[chave] = document.getElementById(`gt-fato-${chave}`).checked;
  });
  return fatos;
}

function gtLerMarcosMarcados() {
  return GT_MARCOS.filter((m) => document.getElementById(`gt-marco-${m}`).checked);
}

/**
 * Formulário de rotulagem — três modos:
 *   - `{conversaId}` — nova entrada a partir de uma conversa real do CRM
 *     (`conversa_id`, requirement "Criar entrada a partir de conversa real
 *     puxa as mensagens").
 *   - `{}` (nem conversaId nem entradaId) — nova entrada com mensagens
 *     digitadas (fallback do README, para histórico que já não está no CRM).
 *   - `{entradaId}` — edição de uma entrada já existente.
 */
async function renderizarFormularioEval(container, { conversaId, entradaId } = {}) {
  const modoEdicao = Boolean(entradaId);
  container.appendChild(
    el("h2", { texto: modoEdicao ? `Editar ground truth — ${entradaId}` : "Nova entrada de ground truth (§7)" })
  );

  let mensagens = [];
  let funilAtual = "b2c";
  let rotuloAtual = null;
  let notaAtual = "";
  let mensagensDigitadasTexto = "";

  if (modoEdicao) {
    const detalhe = await chamarApi(`/eval/rotulos/${entradaId}`);
    mensagens = detalhe.mensagens;
    funilAtual = detalhe.funil;
    rotuloAtual = detalhe.rotulo;
    notaAtual = detalhe.nota || "";
  } else if (conversaId) {
    const detalheConversa = await chamarApi(`/conversas/${conversaId}`);
    funilAtual = detalheConversa.card ? detalheConversa.card.funil : "b2c";
    const dadosMensagens = await chamarApi(`/conversas/${conversaId}/mensagens`);
    mensagens = dadosMensagens.mensagens;
  }

  if (mensagens.length > 0) {
    container.appendChild(gtTranscricao(mensagens));
  }

  const form = el("div", { class: "gt-form" });

  const linhaId = el("div", {}, [
    el("label", { texto: "Id: " }),
    (() => {
      const campo = el("input", { type: "text", id: "gt-id" });
      campo.value = modoEdicao ? entradaId : (conversaId ? `conversa-${conversaId}` : "");
      if (modoEdicao) campo.setAttribute("disabled", "disabled");
      return campo;
    })(),
  ]);
  form.appendChild(linhaId);

  const linhaFunil = el("div", {}, [
    el("label", { texto: "Funil: " }),
    (() => {
      const sel = el("select", { id: "gt-funil" });
      ["b2c", "b2b"].forEach((f) => {
        const opt = el("option", { value: f, texto: f.toUpperCase() });
        if (f === funilAtual) opt.setAttribute("selected", "selected");
        sel.appendChild(opt);
      });
      sel.addEventListener("change", () => {
        const novoSel = gtSelectEstagio(sel.value, null);
        const antigo = document.getElementById("gt-estagio-final");
        antigo.replaceWith(novoSel);
      });
      return sel;
    })(),
  ]);
  form.appendChild(linhaFunil);

  if (!conversaId && !modoEdicao) {
    const linhaMensagens = el("div", { class: "gt-mensagens-digitadas" }, [
      el("label", { texto: "Mensagens (uma por linha: \"in|texto\" ou \"out|texto\")" }),
    ]);
    const textarea = el("textarea", { id: "gt-mensagens-digitadas", rows: "6" });
    textarea.value = mensagensDigitadasTexto;
    linhaMensagens.appendChild(textarea);
    form.appendChild(linhaMensagens);
  }

  const linhaEstagio = el("div", {}, [
    el("label", { texto: "Estágio final: " }),
    gtSelectEstagio(funilAtual, rotuloAtual ? rotuloAtual.estagio_final : null),
  ]);
  form.appendChild(linhaEstagio);

  const linhaObjecao = el("div", {}, [
    el("label", { texto: "Objeção: " }),
    (() => {
      const sel = el("select", { id: "gt-objecao" });
      sel.appendChild(el("option", { value: "", texto: "(nenhuma)" }));
      GT_OBJECOES.forEach((o) => {
        const opt = el("option", { value: o, texto: o });
        if (rotuloAtual && rotuloAtual.objecao === o) opt.setAttribute("selected", "selected");
        sel.appendChild(opt);
      });
      return sel;
    })(),
  ]);
  form.appendChild(linhaObjecao);

  form.appendChild(gtCampoFatos(rotuloAtual ? rotuloAtual.fatos : null));
  form.appendChild(gtCampoMarcos(rotuloAtual ? rotuloAtual.marcos : null));

  const linhaNota = el("div", {}, [
    el("label", { texto: "Nota: " }),
    (() => {
      const campo = el("input", { type: "text", id: "gt-nota" });
      campo.value = notaAtual;
      return campo;
    })(),
  ]);
  form.appendChild(linhaNota);

  const areaErro = el("p", { class: "aviso" });
  const botaoSalvar = el("button", { texto: modoEdicao ? "Salvar edição" : "Criar entrada" });
  botaoSalvar.addEventListener("click", async () => {
    areaErro.textContent = "";
    const corpo = {
      id: document.getElementById("gt-id").value.trim() || undefined,
      funil: document.getElementById("gt-funil").value,
      rotulo: {
        estagio_final: document.getElementById("gt-estagio-final").value,
        objecao: document.getElementById("gt-objecao").value || null,
        fatos: gtLerFatosMarcados(),
        marcos: gtLerMarcosMarcados(),
      },
      nota: document.getElementById("gt-nota").value.trim() || null,
    };
    if (conversaId) {
      corpo.conversa_id = Number(conversaId);
    } else if (!modoEdicao) {
      const linhas = document.getElementById("gt-mensagens-digitadas").value
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
      corpo.mensagens = linhas.map((linha) => {
        const [direcao, ...resto] = linha.split("|");
        return {
          direcao: direcao.trim(),
          texto: resto.join("|").trim(),
          enviada_em: new Date().toISOString(),
        };
      });
    }
    botaoSalvar.disabled = true;
    try {
      if (modoEdicao) {
        await chamarApiEscrever(`/eval/rotulos/${entradaId}`, corpo, "PUT");
      } else {
        await chamarApiEscrever("/eval/rotulos", corpo, "POST");
      }
      haEdicaoEmAndamento = false; // enviado: nada mais a perder nesta tela
      window.location.hash = "#/groundtruth";
    } catch (erro) {
      areaErro.textContent = `Erro: ${erro.message}${erro.regra ? ` (${erro.regra})` : ""}`;
    } finally {
      botaoSalvar.disabled = false;
    }
  });
  form.appendChild(botaoSalvar);
  form.appendChild(areaErro);

  // Change `painel-preserva-estado-em-refresh`: qualquer campo do formulário
  // com valor não vazio levanta `haEdicaoEmAndamento`, para o refresh
  // automático do SSE não apagar rotulagem em andamento (§7: rotular é
  // trabalho manual). Delegado no `form` (não em cada campo) porque
  // `gt-estagio-final` é substituído inteiro quando o funil muda
  // (`sel.addEventListener("change", ...)` acima, `antigo.replaceWith`).
  const CAMPOS_EDICAO_GT = ["gt-id", "gt-funil", "gt-estagio-final", "gt-mensagens-digitadas", "gt-nota"];
  const atualizarEdicaoEmAndamentoGt = () => {
    haEdicaoEmAndamento = CAMPOS_EDICAO_GT.some((idCampo) => {
      const campo = document.getElementById(idCampo);
      return Boolean(campo && campo.value && campo.value.trim() !== "");
    });
  };
  form.addEventListener("input", atualizarEdicaoEmAndamentoGt);
  form.addEventListener("change", atualizarEdicaoEmAndamentoGt);

  container.appendChild(form);
}

/*
 * Change `prospeccao-b2b-shortlist`: shortlist B2B levantada externamente
 * (planilha de petshops), SEPARADA de contatos/conversas/kanban/fila/
 * métricas — só duas abas próprias, "Importar prospecção" e "Prospecção".
 *
 * Regras que este bloco não pode quebrar:
 *   - o botão de disparo é um `<a target="_blank">` para
 *     `api.whatsapp.com/send` — nunca uma chamada de servidor deste painel;
 *     o clique só REGISTRA que o operador abriu o link
 *     (`POST /prospeccao/{id}/abrir`), nunca confirma envio.
 *   - linha já convertida (mesmo telefone virou contato/conversa real via
 *     webhook) mostra link para `#/conversas/{id}`, nunca o botão de
 *     disparo — a API já resolve isso (`mensagem`/`link_whatsapp` vêm
 *     `null` quando `convertida: true`).
 */

async function renderizarImportarProspeccao(container) {
  container.appendChild(el("h2", { texto: "Importar prospecção (B2B)" }));
  container.appendChild(
    el("p", {
      class: "aviso",
      texto:
        "CSV com colunas: petshop, bairro, zona, telefone, nota, avaliacoes, " +
        "site, tier_origem, status_origem. Reimportar a mesma planilha " +
        "atualiza por telefone — nunca duplica.",
    })
  );

  const campoArquivo = el("input", { type: "file", accept: ".csv,text/csv" });
  const botaoImportar = el("button", { texto: "Importar" });
  const areaResultado = el("div", { class: "prospeccao-resultado" });

  // Change `painel-preserva-estado-em-refresh`: arquivo selecionado é
  // trabalho manual do operador (escolher o CSV certo) — não deixa o
  // refresh automático do SSE limpar o campo enquanto ele ainda não clicou
  // "Importar".
  campoArquivo.addEventListener("change", () => {
    haEdicaoEmAndamento = Boolean(campoArquivo.files && campoArquivo.files.length > 0);
  });

  botaoImportar.addEventListener("click", async () => {
    if (!campoArquivo.files || campoArquivo.files.length === 0) {
      areaResultado.textContent = "Selecione um arquivo CSV primeiro.";
      return;
    }
    botaoImportar.disabled = true;
    areaResultado.textContent = "Importando…";
    try {
      const form = new FormData();
      form.append("arquivo", campoArquivo.files[0]);
      const resposta = await fetch("/api/prospeccao/importar", {
        method: "POST",
        headers: { "X-Camu-Token": obterToken() },
        body: form,
      });
      const dados = await resposta.json();
      if (!resposta.ok) {
        throw new Error(dados.erro || `erro HTTP ${resposta.status}`);
      }
      areaResultado.textContent = "";
      areaResultado.appendChild(
        el("p", {
          texto: `${dados.novos} novo(s) — ${dados.atualizados} atualizado(s) — ${dados.invalidas.length} inválida(s)`,
        })
      );
      dados.invalidas.forEach((inv) => {
        areaResultado.appendChild(
          el("p", {
            class: "aviso",
            texto: `linha ${inv.linha}${inv.petshop ? ` (${inv.petshop})` : ""}: ${inv.motivo}`,
          })
        );
      });
    } catch (erro) {
      areaResultado.textContent = `Erro: ${erro.message}`;
    } finally {
      botaoImportar.disabled = false;
      haEdicaoEmAndamento = false;
    }
  });

  container.appendChild(campoArquivo);
  container.appendChild(botaoImportar);
  container.appendChild(areaResultado);
}

/**
 * Extrai o número do `link_whatsapp` já presente no payload (`?phone=...`)
 * — change `envio-prospeccao-pela-evolution-api`. Nenhum campo `telefone`
 * novo é pedido ao servidor para isto: `views.prospeccao_para_json` (§12,
 * mesma cautela de `_contato_para_json`) nunca expõe telefone como campo
 * próprio, só embutido nesse link — o popup reaproveita o que já está lá.
 */
function telefoneDoLinkWhatsapp(linkWhatsapp) {
  try {
    return new URL(linkWhatsapp).searchParams.get("phone") || "";
  } catch (e) {
    return "";
  }
}

/**
 * Popup de envio direto pela Evolution API (change
 * `envio-prospeccao-pela-evolution-api`) — telefone e mensagem vêm
 * pré-preenchidos e são editáveis; nada é enviado sem o operador confirmar
 * aqui. `aoConcluir` é chamado só depois de sucesso confirmado pelo
 * servidor, para a linha da lista poder se atualizar (mostrar "enviado às
 * HH:MM").
 */
function abrirPopupEnvioProspeccao(p, aoConcluir) {
  const fundo = el("div", { class: "modal-fundo" });
  const caixa = el("div", { class: "modal-caixa" });

  const fechar = () => {
    document.removeEventListener("keydown", aoTeclarEscape);
    fundo.remove();
  };
  const aoTeclarEscape = (evento) => {
    if (evento.key === "Escape") fechar();
  };
  fundo.addEventListener("click", (evento) => {
    if (evento.target === fundo) fechar();
  });
  document.addEventListener("keydown", aoTeclarEscape);

  caixa.appendChild(el("h3", { texto: `Enviar pela Evolution API — ${p.nome}` }));
  caixa.appendChild(
    el("p", {
      class: "aviso",
      texto: "Revise o número e o texto antes de enviar. Nada é enviado automaticamente.",
    })
  );

  const labelTelefone = el("label", {}, [document.createTextNode("Telefone")]);
  const campoTelefone = el("input", { type: "text" });
  campoTelefone.value = telefoneDoLinkWhatsapp(p.link_whatsapp);
  labelTelefone.appendChild(campoTelefone);

  const labelMensagem = el("label", {}, [document.createTextNode("Mensagem")]);
  const campoMensagem = el("textarea", {});
  campoMensagem.value = p.mensagem || "";
  labelMensagem.appendChild(campoMensagem);

  // Change `escolher-instancia-no-envio-prospeccao`: seletor "Enviar pelo
  // número", populado ao vivo de `/api/prospeccao/instancias`. Fica oculto
  // enquanto a lista não carrega (ou se a Evolution API não responde) — aí o
  // envio segue pela instância única do `.env`, como antes.
  const labelInstancia = el("label", { class: "escondido" }, [
    document.createTextNode("Enviar pelo número"),
  ]);
  const campoInstancia = el("select", {});
  labelInstancia.appendChild(campoInstancia);
  chamarApi("/prospeccao/instancias")
    .then((dados) => {
      const lista = (dados && dados.instancias) || [];
      if (lista.length === 0) return;
      lista.forEach((inst) => {
        const opcao = el("option", {
          value: inst.nome,
          texto: inst.conectada ? inst.nome : `${inst.nome} (desconectado)`,
        });
        campoInstancia.appendChild(opcao);
      });
      labelInstancia.classList.remove("escondido");
    })
    .catch(() => {
      /* sem lista: o seletor fica oculto e o envio usa a instância do .env. */
    });

  const labelOperador = el("label", {}, [document.createTextNode("Aprovado por")]);
  const campoOperador = criarSeletorOperador();
  labelOperador.appendChild(campoOperador);

  const areaErro = el("p", { class: "modal-erro" });

  const botaoCancelar = el("button", { class: "secundario", texto: "Cancelar" });
  botaoCancelar.addEventListener("click", fechar);

  const botaoEnviar = el("button", { texto: "Enviar" });
  botaoEnviar.addEventListener("click", async () => {
    const telefone = campoTelefone.value.trim();
    const mensagem = campoMensagem.value.trim();
    const por = campoOperador.value.trim();
    const instancia = campoInstancia.value || undefined;
    if (!telefone || !mensagem || !por) {
      areaErro.textContent = "Preencha telefone, mensagem e aprovado por.";
      return;
    }
    salvarOperadorEAtualizarTopo(por);
    areaErro.textContent = "";
    botaoEnviar.disabled = true;
    botaoEnviar.textContent = "Enviando…";
    try {
      await chamarApiEscrever(`/prospeccao/${p.id}/enviar`, {
        telefone,
        mensagem,
        por,
        instancia,
      });
      fechar();
      if (aoConcluir) aoConcluir();
    } catch (e) {
      // 502 (Evolution fora do ar/recusou) ou 422 (campo faltando, ainda
      // que já validado acima) — o popup continua aberto e o texto editado
      // permanece nos campos, para o operador tentar de novo ou copiar a
      // mensagem e usar o link `wa.me` como alternativa.
      areaErro.textContent = e.message;
      botaoEnviar.disabled = false;
      botaoEnviar.textContent = "Enviar";
    }
  });

  const acoes = el("div", { class: "modal-acoes" }, [botaoCancelar, botaoEnviar]);

  caixa.appendChild(labelTelefone);
  caixa.appendChild(labelMensagem);
  caixa.appendChild(labelInstancia);
  caixa.appendChild(labelOperador);
  caixa.appendChild(areaErro);
  caixa.appendChild(acoes);
  fundo.appendChild(caixa);
  document.body.appendChild(fundo);
  campoMensagem.focus();
}

/** Grava o operador escolhido e reflete no `<select>` do topo — sem isto,
 * escolher o operador dentro de um popup (`abrirPopupEnvioProspeccao`,
 * `garantirOperador` abaixo) salvava em `localStorage`, mas o dropdown do
 * cabeçalho continuava mostrando o valor antigo até o próximo carregamento
 * de página, dando a impressão de que a escolha não "pegou". */
function salvarOperadorEAtualizarTopo(valor) {
  salvarOperador(valor);
  const topo = document.getElementById("campo-operador");
  if (topo) topo.value = valor;
}

/**
 * Popup "quem está operando?" — usado por ações da Prospecção (marcar como
 * enviada, marcar como não-WhatsApp, desfazer) que precisam de `por`
 * (§1/§10: toda ação fica associada a um humano nomeado) mas não têm um
 * formulário próprio como `abrirPopupEnvioProspeccao`. Antes deste popup,
 * clicar um desses botões sem operador escolhido só estourava o erro do
 * servidor (`por é obrigatório`) num `alert()` cru — o operador tinha que
 * fechar o alerta, ir até o topo, escolher, e clicar de novo.
 *
 * Devolve o operador escolhido (e já grava/reflete no topo via
 * `salvarOperadorEAtualizarTopo`), ou `null` se cancelado. Se já existe um
 * operador válido salvo, resolve na hora sem mostrar nada — o popup só
 * aparece quando falta essa escolha.
 */
function garantirOperador() {
  const atual = obterOperador();
  if (OPERADORES.includes(atual)) return Promise.resolve(atual);

  return new Promise((resolve) => {
    const fundo = el("div", { class: "modal-fundo" });
    const caixa = el("div", { class: "modal-caixa" });

    const fechar = (valor) => {
      document.removeEventListener("keydown", aoTeclarEscape);
      fundo.remove();
      resolve(valor);
    };
    const aoTeclarEscape = (evento) => {
      if (evento.key === "Escape") fechar(null);
    };
    fundo.addEventListener("click", (evento) => {
      if (evento.target === fundo) fechar(null);
    });
    document.addEventListener("keydown", aoTeclarEscape);

    caixa.appendChild(el("h3", { texto: "Quem está operando?" }));
    caixa.appendChild(
      el("p", {
        class: "aviso",
        texto: "Esta ação fica registrada em nome de quem confirmar aqui.",
      })
    );

    const sel = criarSeletorOperador();
    caixa.appendChild(sel);

    const areaErro = el("p", { class: "modal-erro" });
    caixa.appendChild(areaErro);

    const botaoCancelar = el("button", { class: "secundario", texto: "Cancelar" });
    botaoCancelar.addEventListener("click", () => fechar(null));

    const botaoConfirmar = el("button", { texto: "Confirmar" });
    botaoConfirmar.addEventListener("click", () => {
      const valor = sel.value;
      if (!valor) {
        areaErro.textContent = "Escolha um operador.";
        return;
      }
      salvarOperadorEAtualizarTopo(valor);
      fechar(valor);
    });

    caixa.appendChild(el("div", { class: "modal-acoes" }, [botaoCancelar, botaoConfirmar]));
    fundo.appendChild(caixa);
    document.body.appendChild(fundo);
    sel.focus();
  });
}

function linhaProspeccao(p, recarregar) {
  const linha = el("div", { class: "prospeccao-item" });
  const avisoEl = el("span", {
    class: "aviso",
    texto: `nota ${p.nota ?? "?"} (${p.avaliacoes ?? "?"} avaliações) — tier ${p.tier_origem || "?"}`,
  });

  // Revisão visual 2026-08-31: o selo de status (enviado/não-whatsapp) entra
  // logo depois do nome, não depois da nota/avaliações — o operador olha os
  // extremos da linha (nome à esquerda, botões à direita), então o selo
  // precisa ficar colado no extremo esquerdo pra não passar despercebido.
  linha.appendChild(
    el("span", {
      class: "nome",
      texto: `${p.nome} — ${p.bairro || "?"} / ${p.zona || "?"}`,
    })
  );

  if (p.convertida) {
    linha.appendChild(avisoEl);
    const link = el("a", { texto: "já é conversa — abrir" });
    link.href = `#/conversas/${p.conversa_id}`;
    linha.appendChild(link);
    return linha;
  }

  // Change `prospeccao-marcar-enviada-e-nao-whatsapp`: telefone comercial que
  // não atende no WhatsApp — a linha some da fila de disparo (nenhum botão de
  // envio abaixo), mas continua na tabela. Só um "Desfazer" caso tenha sido
  // marca errada.
  if (p.nao_whatsapp) {
    linha.appendChild(
      el("span", { class: "enviado-selo com-erro", texto: "não é número de WhatsApp" })
    );
    linha.appendChild(avisoEl);
    const desfazer = el("button", { class: "secundario", texto: "Desfazer" });
    desfazer.addEventListener("click", async () => {
      const por = await garantirOperador();
      if (!por) return;
      try {
        await chamarApiEscrever(`/prospeccao/${p.id}/nao-whatsapp`, {
          por,
          valor: false,
        });
        recarregar();
      } catch (e) {
        alert(e.message);
      }
    });
    linha.appendChild(desfazer);
    return linha;
  }

  // Resultado da última tentativa de envio pela API — distinto de "abriu o
  // link" (aquilo nunca teve confirmação; isto é o servidor dizendo que a
  // Evolution aceitou, ou não, o envio).
  // Change `escolher-instancia-no-envio-prospeccao`: por qual número saiu.
  const porNumero = p.enviado_instancia ? ` pelo ${p.enviado_instancia}` : "";

  // Pedido do usuário na revisão visual (2026-08-31): linha já enviada e
  // ainda sem conversa vinculada fica tão enxuta quanto o caso `convertida`
  // acima — nome, selo, e UMA ação primária. Depois de mandar a mensagem, o
  // próximo passo natural é trazer a resposta pro CRM (Importar conversa),
  // não continuar oferecendo Copiar/Abrir WhatsApp/Enviar de novo. Erro de
  // envio (`enviado_erro`) fica de fora disso — falha ainda precisa do
  // banco de botões completo pra retry.
  const jaEnviadoSemErro = Boolean(p.enviado_em) && !p.enviado_erro;
  if (jaEnviadoSemErro) {
    const quando = new Date(p.enviado_em);
    linha.appendChild(
      el("span", {
        class: "enviado-selo",
        texto: p.enviado_manual
          ? `marcado como já enviado (${quando.toLocaleString()})`
          : `enviado às ${quando.toLocaleString()}${porNumero}`,
      })
    );
    linha.appendChild(avisoEl);

    const botaoImportar = el("button", { texto: "Importar conversa" });
    botaoImportar.addEventListener("click", () => {
      prefilhoImportacaoWhatsapp = {
        telefone: telefoneDoLinkWhatsapp(p.link_whatsapp),
        nome: p.nome,
      };
      window.location.hash = "#/importar-whatsapp";
    });
    linha.appendChild(botaoImportar);

    if (p.enviado_manual) {
      const desfazer = el("button", { class: "secundario", texto: "Desfazer 'já enviado'" });
      desfazer.addEventListener("click", async () => {
        const por = await garantirOperador();
        if (!por) return;
        try {
          await chamarApiEscrever(`/prospeccao/${p.id}/enviada-manual`, { por, valor: false });
          recarregar();
        } catch (e) {
          alert(e.message);
        }
      });
      linha.appendChild(desfazer);
    }
    return linha;
  }

  if (p.enviado_erro) {
    linha.appendChild(
      el("span", {
        class: "enviado-selo com-erro",
        texto: `envio falhou${porNumero}: ${p.enviado_erro}`,
      })
    );
  }

  linha.appendChild(avisoEl);

  // Change `prospeccao-marcar-enviada-e-nao-whatsapp`: as duas marcas manuais
  // — disponíveis mesmo sem template de mensagem, o operador ainda precisa
  // conseguir triar a linha.
  const botaoEnviadaManual = el("button", {
    class: "secundario",
    texto: p.enviado_manual ? "Desfazer 'já enviado'" : "Marcar como já enviado",
  });
  botaoEnviadaManual.addEventListener("click", async () => {
    const por = await garantirOperador();
    if (!por) return;
    try {
      await chamarApiEscrever(`/prospeccao/${p.id}/enviada-manual`, {
        por,
        valor: !p.enviado_manual,
      });
      recarregar();
    } catch (e) {
      alert(e.message);
    }
  });
  linha.appendChild(botaoEnviadaManual);

  const botaoNaoWhatsapp = el("button", {
    class: "secundario",
    texto: "Não é número de WhatsApp",
  });
  botaoNaoWhatsapp.addEventListener("click", async () => {
    if (!confirm(`Marcar "${p.nome}" como número que não atende no WhatsApp?`)) return;
    const por = await garantirOperador();
    if (!por) return;
    try {
      await chamarApiEscrever(`/prospeccao/${p.id}/nao-whatsapp`, {
        por,
        valor: true,
      });
      recarregar();
    } catch (e) {
      alert(e.message);
    }
  });
  linha.appendChild(botaoNaoWhatsapp);

  if (!p.mensagem || !p.link_whatsapp) {
    linha.appendChild(
      el("span", {
        class: "aviso",
        texto: "sem template de mensagem configurado (CAMU_MENSAGEM_PROSPECCAO)",
      })
    );
    return linha;
  }

  const botaoCopiar = el("button", { class: "secundario", texto: "Copiar mensagem" });
  botaoCopiar.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(p.mensagem);
      botaoCopiar.textContent = "Copiado!";
    } catch (e) {
      botaoCopiar.textContent = "Copie manualmente (clipboard indisponível)";
    }
    setTimeout(() => { botaoCopiar.textContent = "Copiar mensagem"; }, 2000);
  });

  // O clique abre o link do WhatsApp (`target="_blank"`) — nada aqui chama
  // o servidor para "enviar"; a chamada abaixo só REGISTRA a abertura
  // (design.md: intenção registrada, não confirmação de envio).
  const linkWhatsapp = el("a", {
    class: "prospeccao-link-whatsapp",
    texto: "Abrir WhatsApp",
    target: "_blank",
    rel: "noopener noreferrer",
  });
  linkWhatsapp.href = p.link_whatsapp;
  linkWhatsapp.addEventListener("click", () => {
    chamarApiEscrever(`/prospeccao/${p.id}/abrir`, { por: obterOperador() }).catch(() => {
      /* falha ao registrar a abertura não deve impedir o link de abrir. */
    });
  });

  // Change `envio-prospeccao-pela-evolution-api`: caminho alternativo ao
  // link acima — este de fato chama a Evolution API pelo servidor, depois
  // de o operador revisar/editar no popup. Os dois convivem.
  const botaoEnviarApi = el("button", {
    class: "prospeccao-btn-enviar",
    texto: "Enviar pela Evolution API",
  });
  botaoEnviarApi.addEventListener("click", () => {
    abrirPopupEnvioProspeccao(p, recarregar);
  });

  linha.appendChild(botaoCopiar);
  linha.appendChild(linkWhatsapp);
  linha.appendChild(botaoEnviarApi);
  return linha;
}

async function renderizarProspeccao(container) {
  container.appendChild(el("h2", { texto: "Prospecção B2B" }));
  container.appendChild(
    el("p", {
      class: "aviso",
      texto:
        "Shortlist levantada externamente — nunca aparece em kanban, fila, " +
        "conversas ou métricas enquanto não virar conversa real.",
    })
  );

  const filtros = el("div", { class: "filtros" });
  // Placeholder agora é só exemplo — o rótulo fixo (`campoComRotulo`, abaixo)
  // é quem identifica o campo, então repetir o mesmo texto nos dois seria
  // redundante.
  const campoZona = el("input", { type: "text", placeholder: "ex.: Norte" });
  const campoBairro = el("input", { type: "text", placeholder: "ex.: Jardim Paraíso" });
  const campoNota = el("input", { type: "number", step: "0.1", placeholder: "ex.: 4.5" });
  const campoTier = el("input", { type: "text", placeholder: "ex.: 1" });
  const labelNaoConvertidas = el("label", { class: "modo-teste" });
  const checkNaoConvertidas = el("input", { type: "checkbox" });
  labelNaoConvertidas.appendChild(checkNaoConvertidas);
  labelNaoConvertidas.appendChild(document.createTextNode(" só não convertidas"));

  // Change `prospeccao-filtro-e-ordenacao`: mesmas quatro chaves de
  // `camucrm.prospeccao.ORDENS_PROSPECCAO` — chave desconhecida cai em
  // "nome" do lado do servidor, então não há necessidade de validar aqui.
  const selOrdenarProspeccao = el("select", { id: "ordenar-prospeccao" });
  [
    ["nome", "nome"],
    ["relevancia", "relevância"],
    ["nota", "nota"],
    ["avaliacoes", "avaliações"],
  ].forEach(([valor, rotulo]) => {
    selOrdenarProspeccao.appendChild(el("option", { value: valor, texto: rotulo }));
  });

  // Change `painel-preserva-estado-em-refresh`: inicializa a partir do
  // estado de módulo, mesmo padrão de `montarFiltros` (aba Conversas).
  campoZona.value = estadoFiltrosProspeccao.zona;
  campoBairro.value = estadoFiltrosProspeccao.bairro;
  campoNota.value = estadoFiltrosProspeccao.notaMinima;
  campoTier.value = estadoFiltrosProspeccao.tier;
  checkNaoConvertidas.checked = estadoFiltrosProspeccao.naoConvertidas;
  selOrdenarProspeccao.value = estadoFiltrosProspeccao.ordenar || "nome";

  const lista = el("div", { id: "lista-prospeccao" });

  const carregar = async () => {
    estadoFiltrosProspeccao.zona = campoZona.value.trim();
    estadoFiltrosProspeccao.bairro = campoBairro.value.trim();
    estadoFiltrosProspeccao.notaMinima = campoNota.value;
    estadoFiltrosProspeccao.tier = campoTier.value.trim();
    estadoFiltrosProspeccao.naoConvertidas = checkNaoConvertidas.checked;
    estadoFiltrosProspeccao.ordenar = selOrdenarProspeccao.value;

    lista.textContent = "";
    const params = new URLSearchParams();
    if (estadoFiltrosProspeccao.zona) params.set("zona", estadoFiltrosProspeccao.zona);
    if (estadoFiltrosProspeccao.bairro) params.set("bairro", estadoFiltrosProspeccao.bairro);
    if (estadoFiltrosProspeccao.notaMinima) params.set("nota_minima", estadoFiltrosProspeccao.notaMinima);
    if (estadoFiltrosProspeccao.tier) params.set("tier", estadoFiltrosProspeccao.tier);
    if (estadoFiltrosProspeccao.naoConvertidas) params.set("nao_convertidas", "1");
    if (estadoFiltrosProspeccao.ordenar) params.set("ordenar", estadoFiltrosProspeccao.ordenar);
    const dados = await chamarApi(`/prospeccao?${params.toString()}`);
    lista.appendChild(el("p", { class: "aviso", texto: `${dados.prospeccoes.length} petshop(s)` }));
    if (dados.prospeccoes.length === 0) {
      lista.appendChild(el("p", { class: "aviso", texto: "nenhuma linha para os filtros atuais" }));
    }
    dados.prospeccoes.forEach((p) => lista.appendChild(linhaProspeccao(p, carregar)));
  };

  [campoZona, campoBairro, campoNota, campoTier, selOrdenarProspeccao].forEach((campo) => {
    campo.addEventListener("change", carregar);
  });
  checkNaoConvertidas.addEventListener("change", carregar);

  filtros.appendChild(campoComRotulo("zona", campoZona));
  filtros.appendChild(campoComRotulo("bairro", campoBairro));
  filtros.appendChild(campoComRotulo("nota mínima", campoNota));
  filtros.appendChild(campoComRotulo("tier", campoTier));
  filtros.appendChild(labelNaoConvertidas);
  filtros.appendChild(selOrdenarProspeccao);

  container.appendChild(filtros);
  container.appendChild(lista);
  await carregar();

  // Change `prospeccao-tempo-real-sem-pulo`: quando outro operador marca uma
  // linha (já enviado / não é WhatsApp / abriu / enviou pela API), o stream
  // avisa e esta closure recarrega SÓ `#lista-prospeccao` — sem tocar em
  // `#conteudo` nem nos campos de filtro, então o scroll e o que o operador
  // estava digitando ficam onde estavam. Uma mensagem de WhatsApp qualquer
  // (que não mexe na 4ª parte do token) nunca chega aqui.
  refreshSuaveAtual = carregar;
}

/*
 * Change `importacao-conversas-whatsapp`: contato que deixou de acontecer
 * só pelo número da Camu (número pessoal, outro número comercial) entra no
 * CRM via ".txt" que o próprio WhatsApp exporta ("Exportar conversa").
 *
 * Duas chamadas de servidor, nunca uma só:
 *   1. `POST /importacao-whatsapp` (multipart) — grava as mensagens, nunca
 *      chama LLM. Devolve `conversa_id` e o resumo do parse.
 *   2. `POST /conversas/{conversa_id}/extrair` — a rota que JÁ EXISTE
 *      (change `extracao-em-lote-por-janela`, mesma que o botão "Extrair
 *      agora" do detalhe de conversa usa) — só depois que o operador
 *      revisou o resumo e decidiu gastar a chamada de LLM.
 */

async function renderizarImportarConversaWhatsapp(container) {
  container.appendChild(el("h2", { texto: "Importar conversa (fora do número Camu)" }));
  container.appendChild(
    el("p", {
      class: "aviso",
      texto:
        "No WhatsApp: abra a conversa → ⋮ → Mais → Exportar conversa → " +
        "\"sem mídia\". Envie o .txt pra você e selecione abaixo. Conversa " +
        "de grupo não é aceita — só 1:1.",
    })
  );

  // Mesmo padrão visual de `.gt-form` (formulário de ground truth) — linhas
  // empilhadas, cada uma com `<label>` própria e input em largura cheia, em
  // vez de espremer tudo numa única linha (`.filtros`, feito para campo
  // curto de filtro, não para um formulário de upload).
  const form = el("div", { class: "gt-form" });

  const campoArquivo = el("input", { type: "file", accept: ".txt,text/plain" });
  // Change `painel-preserva-estado-em-refresh`: mesma razão do CSV de
  // prospecção — selecionar o .txt certo é trabalho manual do operador.
  campoArquivo.addEventListener("change", () => {
    haEdicaoEmAndamento = Boolean(campoArquivo.files && campoArquivo.files.length > 0);
  });
  form.appendChild(
    el("div", {}, [el("label", { texto: "Arquivo (.txt exportado):" }), campoArquivo])
  );

  const campoTelefone = el("input", {
    type: "text",
    placeholder: "com DDD, ex.: 12988887777",
  });
  form.appendChild(
    el("div", {}, [el("label", { texto: "Telefone do contato:" }), campoTelefone])
  );

  const campoTipo = el("select");
  campoTipo.appendChild(el("option", { value: "b2c", texto: "B2C (consumidor)" }));
  campoTipo.appendChild(el("option", { value: "b2b", texto: "B2B (petshop)" }));
  form.appendChild(el("div", {}, [el("label", { texto: "Tipo:" }), campoTipo]));

  const campoNomeOperador = el("input", {
    type: "text",
    placeholder: "ex.: Camu",
  });
  campoNomeOperador.value = obterOperador();
  form.appendChild(
    el("div", {}, [
      el("label", { texto: "Seu nome, exatamente como aparece no arquivo exportado:" }),
      campoNomeOperador,
    ])
  );

  const campoNomeContato = el("input", { type: "text" });
  form.appendChild(
    el("div", {}, [
      el("label", { texto: "Nome do contato (opcional — senão usa o do arquivo):" }),
      campoNomeContato,
    ])
  );

  // Veio do botão "Importar conversa" de uma linha de Prospecção já enviada
  // — pré-preenche telefone/nome/tipo (B2B, é sempre prospecção) pra
  // completar só o arquivo e o próprio nome. Consumido uma vez só: um
  // refresh manual desta tela não deve reaplicar um preenchimento antigo.
  if (prefilhoImportacaoWhatsapp) {
    if (prefilhoImportacaoWhatsapp.telefone) campoTelefone.value = prefilhoImportacaoWhatsapp.telefone;
    if (prefilhoImportacaoWhatsapp.nome) campoNomeContato.value = prefilhoImportacaoWhatsapp.nome;
    campoTipo.value = "b2b";
    prefilhoImportacaoWhatsapp = null;
  }

  const campoOrigem = el("input", { type: "text", placeholder: "whatsapp-manual" });
  form.appendChild(
    el("div", {}, [el("label", { texto: "Origem (opcional):" }), campoOrigem])
  );

  const botaoImportar = el("button", { texto: "Importar" });
  const areaResultado = el("div", { class: "prospeccao-resultado" });

  botaoImportar.addEventListener("click", async () => {
    areaResultado.textContent = "";
    if (!campoArquivo.files || campoArquivo.files.length === 0) {
      areaResultado.textContent = "Selecione o .txt exportado primeiro.";
      return;
    }
    if (!campoTelefone.value.trim()) {
      areaResultado.textContent = "Telefone é obrigatório.";
      return;
    }
    if (!campoNomeOperador.value.trim()) {
      areaResultado.textContent = "Seu nome (como aparece no arquivo) é obrigatório.";
      return;
    }
    botaoImportar.disabled = true;
    areaResultado.textContent = "Importando…";
    try {
      const form = new FormData();
      form.append("arquivo", campoArquivo.files[0]);
      form.append("telefone", campoTelefone.value.trim());
      form.append("tipo", campoTipo.value);
      form.append("nome_operador", campoNomeOperador.value.trim());
      if (campoNomeContato.value.trim()) form.append("nome", campoNomeContato.value.trim());
      if (campoOrigem.value.trim()) form.append("origem", campoOrigem.value.trim());

      const resposta = await fetch("/api/importacao-whatsapp", {
        method: "POST",
        headers: { "X-Camu-Token": obterToken() },
        body: form,
      });
      const dados = await resposta.json();
      if (!resposta.ok) {
        throw new Error(dados.erro || `erro HTTP ${resposta.status}`);
      }

      areaResultado.textContent = "";
      areaResultado.appendChild(
        el("p", {
          texto:
            `${dados.mensagens_novas} mensagem(ns) nova(s)` +
            (dados.nome_contato ? ` — contato: ${dados.nome_contato}` : "") +
            (dados.midia_preservada
              ? ` — ${dados.midia_preservada} mídia(s) preservada(s)`
              : ""),
        })
      );
      if (dados.ignoradas && dados.ignoradas.length > 0) {
        areaResultado.appendChild(
          el("p", {
            class: "aviso",
            texto: `${dados.ignoradas.length} linha(s) não reconhecida(s) do arquivo:`,
          })
        );
        dados.ignoradas.slice(0, 20).forEach((linha) => {
          areaResultado.appendChild(el("p", { class: "aviso", texto: `  ${linha}` }));
        });
      }

      const botaoExtrair = el("button", { class: "secundario", texto: "Extrair agora" });
      botaoExtrair.addEventListener("click", async () => {
        botaoExtrair.disabled = true;
        const rotuloOriginal = botaoExtrair.textContent;
        botaoExtrair.textContent = "Extraindo (chama o LLM)…";
        try {
          await chamarApiEscrever(`/conversas/${dados.conversa_id}/extrair`);
          botaoExtrair.textContent = "Extraído — ver na aba Conversas";
        } catch (erro) {
          areaResultado.appendChild(
            el("p", { class: "aviso", texto: `Erro na extração: ${erro.message}` })
          );
          botaoExtrair.disabled = false;
          botaoExtrair.textContent = rotuloOriginal;
        }
      });
      areaResultado.appendChild(botaoExtrair);

      const linkConversa = el("a", { texto: "Abrir conversa" });
      linkConversa.href = `#/conversas/${dados.conversa_id}`;
      areaResultado.appendChild(document.createTextNode(" "));
      areaResultado.appendChild(linkConversa);
    } catch (erro) {
      areaResultado.textContent = `Erro: ${erro.message}`;
    } finally {
      botaoImportar.disabled = false;
      haEdicaoEmAndamento = false;
    }
  });

  form.appendChild(botaoImportar);
  container.appendChild(form);
  container.appendChild(areaResultado);
}

// -- Roteador ----------------------------------------------------------------

// Cada render limpa o container e só faz `appendChild` depois de um `await`
// (buscar dados da API). Sem essa guarda, duas chamadas concorrentes a
// `renderizarRotaSegura` intercalam: a segunda limpa o container que a
// primeira ainda não preencheu, e as duas terminam anexando conteúdo — a
// tela duplica cada seção. Isso acontece de verdade: `stream.py` emite um
// evento `mensagem` E um evento `mudanca` no mesmo ciclo quando chega
// mensagem nova (`gerador_sse`), e os dois chamavam `renderizarRota()` sem
// exclusão mútua nenhuma. A correção é coalescer: só uma renderização roda
// por vez, e um pedido que chega no meio vira "rode mais uma vez depois",
// não uma segunda execução paralela.
let renderEmAndamento = false;
let renderPendente = false;

async function renderizarRotaSegura() {
  if (renderEmAndamento) {
    renderPendente = true;
    return;
  }
  renderEmAndamento = true;
  try {
    await renderizarRota();
  } finally {
    renderEmAndamento = false;
    if (renderPendente) {
      renderPendente = false;
      renderizarRotaSegura();
    }
  }
}

// Change `prospeccao-tempo-real-sem-pulo`: uma tela que sabe se redesenhar
// sem limpar `#conteudo` inteiro (mantendo scroll e os filtros que o
// operador digitou) registra aqui o seu "recarregar". O stream chama isto
// em vez de `renderizarRotaSegura` quando só a parte de prospecção do token
// mudou — ver `reagirAMudanca`. `renderizarRota` zera a cada navegação para
// a tela seguinte não herdar o hook da anterior.
let refreshSuaveAtual = null;

async function renderizarRota() {
  // Change `painel-preserva-estado-em-refresh`: qualquer render de verdade
  // (navegação de aba, hashchange, "Atualizar" manual) está prestes a
  // apagar `conteudo` de qualquer forma — a edição em risco que a flag
  // protegia (formulário da rota antiga) não sobrevive a isto de qualquer
  // jeito, então a flag reseta aqui, não só nos pontos de "enviado/vazio".
  haEdicaoEmAndamento = false;
  const conteudo = document.getElementById("conteudo");
  conteudo.textContent = "";
  refreshSuaveAtual = null;
  document.querySelectorAll("nav.abas a").forEach((a) => a.classList.remove("ativa"));

  const hash = window.location.hash.replace(/^#/, "") || "/";
  const partes = hash.split("/").filter(Boolean);

  try {
    if (partes.length === 0) {
      marcarAbaAtiva("/");
      await renderizarFila(conteudo);
    } else if (partes[0] === "kanban") {
      marcarAbaAtiva("/kanban");
      await renderizarKanban(conteudo);
    } else if (partes[0] === "conversas" && partes.length === 1) {
      marcarAbaAtiva("/conversas");
      await renderizarConversas(conteudo);
    } else if (partes[0] === "conversas" && partes.length === 2) {
      await renderizarDetalhe(conteudo, partes[1]);
    } else if (partes[0] === "metricas") {
      marcarAbaAtiva("/metricas");
      await renderizarMetricas(conteudo);
    } else if (partes[0] === "funciona") {
      marcarAbaAtiva("/funciona");
      await renderizarFunciona(conteudo);
    } else if (partes[0] === "groundtruth" && partes.length === 1) {
      marcarAbaAtiva("/groundtruth");
      await renderizarGroundTruth(conteudo);
    } else if (partes[0] === "groundtruth" && partes[1] === "novo") {
      await renderizarFormularioEval(conteudo, { conversaId: partes[2] });
    } else if (partes[0] === "groundtruth" && partes[1] === "editar" && partes[2]) {
      await renderizarFormularioEval(conteudo, { entradaId: partes[2] });
    } else if (partes[0] === "prospeccao" && partes[1] === "importar") {
      marcarAbaAtiva("/prospeccao/importar");
      await renderizarImportarProspeccao(conteudo);
    } else if (partes[0] === "prospeccao") {
      marcarAbaAtiva("/prospeccao");
      await renderizarProspeccao(conteudo);
    } else if (partes[0] === "importar-whatsapp") {
      marcarAbaAtiva("/importar-whatsapp");
      await renderizarImportarConversaWhatsapp(conteudo);
    } else {
      conteudo.appendChild(el("p", { class: "aviso", texto: "rota desconhecida" }));
    }
  } catch (erro) {
    conteudo.appendChild(
      el("p", { class: "aviso", texto: `Erro: ${erro.message}${erro.regra ? ` (${erro.regra})` : ""}` })
    );
  }
}

function marcarAbaAtiva(rota) {
  const link = document.querySelector(`nav.abas a[data-rota="${rota}"]`);
  if (link) link.classList.add("ativa");
}

// -- Tempo real (SSE via fetch + ReadableStream, change `painel-tempo-real`)

// `desde_id` é o cursor de reconexão (design.md): o id da última mensagem
// já recebida do stream. Guardado em memória só — perder isso num reload de
// página não é perda de evento, é o mesmo que abrir o painel de novo.
let ultimoIdStream = null;

const ATRASO_INICIAL_MS = 1000;
const ATRASO_MAXIMO_MS = 10000;
let atrasoReconexaoMs = ATRASO_INICIAL_MS;

// Change `prospeccao-tempo-real-sem-pulo`: último token `"m:e:c:p"` visto no
// evento `mudanca` (camucrm/db.py::token_de_mudanca) — comparado parte a
// parte com o próximo para saber O QUE mudou. `null` até o primeiro evento.
let ultimoTokenMudanca = null;

/**
 * A tela atual reflete o stream de CONVERSAS (mensagens, eventos de estágio,
 * `conversas.atualizado_em` — as três primeiras partes do token)?
 *
 * Só fila (`#/`), kanban, lista de conversas e detalhe de conversa. As
 * demais abas (prospecção, importações, ground truth, métricas, "o que
 * funciona") têm o botão "Atualizar" manual e NÃO são redesenhadas — nem
 * jogadas de volta pro topo — porque a Evolution API recebeu uma mensagem
 * que não muda nada do que elas mostram (change `prospeccao-tempo-real-
 * sem-pulo`, "Correção recomendada" da investigação).
 */
function rotaRefleteConversas() {
  const partes = (window.location.hash.replace(/^#/, "") || "/").split("/").filter(Boolean);
  const raiz = partes[0] || "";
  return raiz === "" || raiz === "kanban" || raiz === "conversas";
}

/** A tela atual é a lista de prospecção (`#/prospeccao`, não `.../importar`)?
 * É a única que reage à 4ª parte do token, e só via `refreshSuaveAtual`. */
function rotaEhListaProspeccao() {
  const partes = (window.location.hash.replace(/^#/, "") || "/").split("/").filter(Boolean);
  return partes.length === 1 && partes[0] === "prospeccao";
}

/**
 * Parser SSE manual (~25 linhas, como o plano pede): um bloco é tudo entre
 * duas quebras de linha duplas, com linhas `id:`/`event:`/`data:`. O
 * heartbeat (`: ping`) não tem `data:` e é ignorado aqui — ele só existe
 * para o proxy não fechar a conexão por inatividade.
 */
function processarBlocoSse(bloco) {
  let evento = "message";
  let dados = null;
  bloco.split("\n").forEach((linha) => {
    if (linha.startsWith("id:")) {
      ultimoIdStream = Number(linha.slice(3).trim());
    } else if (linha.startsWith("event:")) {
      evento = linha.slice(6).trim();
    } else if (linha.startsWith("data:")) {
      const bruto = linha.slice(5).trim();
      try {
        dados = JSON.parse(bruto);
      } catch (e) {
        dados = bruto;
      }
    }
  });
  if (dados === null) return; // heartbeat

  if (evento === "mensagem") {
    // Mensagem nova sempre mexe no stream de conversas.
    if (rotaRefleteConversas()) renderizarOuAdiar(renderizarRotaSegura);
  } else if (evento === "mudanca") {
    reagirAMudanca(dados && typeof dados === "object" ? dados.token : null);
  }
}

// Change `painel-preserva-estado-em-refresh`: se há edição em risco
// (formulário com conteúdo, ou uma escrita em voo), não apaga `conteudo`
// por baixo do operador — só marca que há atualização esperando o botão
// "Atualizar". Usado tanto pelo refresh de conversas quanto pelo suave de
// prospecção (change `prospeccao-tempo-real-sem-pulo`): os dois podem cair
// no meio de uma edição, o gate é o mesmo.
function renderizarOuAdiar(fn) {
  if (haEdicaoEmAndamento) {
    atualizacaoPendente = true;
    atualizarIndicadorAtualizacaoPendente();
    return;
  }
  fn();
}

/**
 * Decide o que redesenhar a partir do token `"m:e:c:p"`. Comparar parte a
 * parte é o que separa "chegou mensagem/mudou estágio" (partes 0-2, mexe na
 * tela de conversas) de "outro operador triou uma linha da prospecção"
 * (parte 3, mexe só na aba de prospecção) — cada tela reage à sua parte e
 * ignora a da outra. Sem token legível, cai no comportamento antigo
 * (conservador: redesenha a tela de conversas se for o caso).
 */
function reagirAMudanca(token) {
  if (typeof token !== "string") {
    if (rotaRefleteConversas()) renderizarOuAdiar(renderizarRotaSegura);
    return;
  }
  const partes = token.split(":");
  const anterior = ultimoTokenMudanca ? ultimoTokenMudanca.split(":") : [];
  ultimoTokenMudanca = token;

  const mudouConversas = [0, 1, 2].some((i) => partes[i] !== anterior[i]);
  const mudouProspeccao = partes[3] !== anterior[3];

  if (mudouConversas && rotaRefleteConversas()) {
    // Mesmos dados que "Atualizar" busca — o stream só avisa que algo mudou,
    // não tenta remendar o DOM por cima do que `renderizarRota` já monta.
    renderizarOuAdiar(renderizarRotaSegura);
  }
  if (mudouProspeccao && rotaEhListaProspeccao() && refreshSuaveAtual) {
    // Recarrega só a lista, sem limpar `#conteudo`: scroll e filtros ficam.
    renderizarOuAdiar(refreshSuaveAtual);
  }
}

// Change `painel-preserva-estado-em-refresh`: aviso não destrutivo ao lado
// do botão "Atualizar" — nunca popup, nunca bloqueia interação (proposal.md
// "What Changes"). Criado uma vez, escondido por padrão via `hidden`.
let indicadorAtualizacaoPendente = null;

function atualizarIndicadorAtualizacaoPendente() {
  if (!indicadorAtualizacaoPendente) return;
  indicadorAtualizacaoPendente.hidden = !atualizacaoPendente;
}

async function conectarStream() {
  const params = new URLSearchParams();
  if (ultimoIdStream !== null) params.set("desde_id", String(ultimoIdStream));
  try {
    const resposta = await fetch(`/api/stream?${params.toString()}`, {
      headers: { "X-Camu-Token": obterToken() },
    });
    if (!resposta.ok || !resposta.body) {
      throw new Error(`stream HTTP ${resposta.status}`);
    }
    atrasoReconexaoMs = ATRASO_INICIAL_MS; // conectou: zera o backoff
    const leitor = resposta.body.getReader();
    const decodificador = new TextDecoder();
    let bufer = "";
    while (true) {
      const { value, done } = await leitor.read();
      if (done) break;
      bufer += decodificador.decode(value, { stream: true });
      let indiceFimBloco;
      while ((indiceFimBloco = bufer.indexOf("\n\n")) !== -1) {
        processarBlocoSse(bufer.slice(0, indiceFimBloco));
        bufer = bufer.slice(indiceFimBloco + 2);
      }
    }
  } catch (e) {
    // Rede caiu, servidor reiniciou, token mudou — qualquer motivo cai aqui
    // e reconecta com backoff abaixo, nunca desiste de vez.
  }
  await new Promise((resolve) => setTimeout(resolve, atrasoReconexaoMs));
  atrasoReconexaoMs = Math.min(atrasoReconexaoMs * 2, ATRASO_MAXIMO_MS);
  conectarStream();
}

// -- Início --------------------------------------------------------------

function iniciar() {
  document.getElementById("campo-token").value = obterToken();
  const salvoOperador = obterOperador();
  if (OPERADORES.includes(salvoOperador)) {
    document.getElementById("campo-operador").value = salvoOperador;
  }
  const campoModoTeste = document.getElementById("campo-modo-teste");
  campoModoTeste.checked = modoTesteAtivo();
  document.body.classList.toggle("modo-teste-ativo", modoTesteAtivo());
  campoModoTeste.addEventListener("change", () => {
    salvarModoTeste(campoModoTeste.checked);
    document.body.classList.toggle("modo-teste-ativo", campoModoTeste.checked);
    renderizarRotaSegura();
  });
  document.getElementById("botao-salvar-token").addEventListener("click", () => {
    salvarToken(document.getElementById("campo-token").value.trim());
    salvarOperador(document.getElementById("campo-operador").value.trim());
    renderizarRotaSegura();
  });
  const botaoAtualizar = document.getElementById("botao-atualizar");
  // Change `painel-preserva-estado-em-refresh`: indicador não-destrutivo,
  // montado por JS (nunca no HTML) — some quando não há nada pendente.
  indicadorAtualizacaoPendente = el("span", {
    class: "aviso-atualizacao-pendente",
    texto: "● atualizações disponíveis",
  });
  indicadorAtualizacaoPendente.hidden = true;
  botaoAtualizar.insertAdjacentElement("afterend", indicadorAtualizacaoPendente);
  botaoAtualizar.addEventListener("click", () => {
    // Botão manual força o refresh mesmo com `haEdicaoEmAndamento` ativa —
    // é escolha do operador, não do sistema (tasks.md 2.5).
    atualizacaoPendente = false;
    atualizarIndicadorAtualizacaoPendente();
    renderizarRotaSegura();
  });
  window.addEventListener("hashchange", renderizarRotaSegura);
  renderizarRotaSegura();
  conectarStream();
}

document.addEventListener("DOMContentLoaded", iniciar);
