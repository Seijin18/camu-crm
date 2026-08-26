/*
 * Painel de leitura do camu-crm — JS puro, sem bundler, sem CDN.
 *
 * Regras que este arquivo não pode quebrar (CLAUDE.md / plano do change
 * `painel-leitura`):
 *   - textContent sempre, innerHTML nunca, para qualquer texto que venha de
 *     conversa/mensagem/nome (evita XSS a partir de conteúdo de cliente).
 *   - token em localStorage, nunca em querystring.
 *   - botão "Atualizar" manual — sem SSE neste change.
 *   - a fila é a tela inicial ("#/"), o kanban é aba secundária (§6).
 */

const CHAVE_TOKEN = "camu_painel_token";

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

async function chamarApi(caminho) {
  const resposta = await fetch(`/api${caminho}`, {
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

function formatarHoras(horas) {
  if (horas === null || horas === undefined) return "—";
  if (horas < 1) return `${Math.round(horas * 60)}min`;
  if (horas < 48) return `${horas.toFixed(0)}h`;
  return `${(horas / 24).toFixed(0)}d`;
}

function tagTemperatura(temperatura) {
  return el("span", { class: `tag ${temperatura}`, texto: temperatura });
}

// -- Telas -----------------------------------------------------------------

async function renderizarFila(container) {
  const dados = await chamarApi("/fila");
  container.appendChild(el("h2", { texto: `Fila de hoje (${dados.itens.length})` }));
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

async function renderizarKanban(container) {
  const dados = await chamarApi("/kanban");
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
        const cardEl = el("div", { class: "card" }, [
          el("span", { class: "nome", texto: card.nome }),
          el("span", { class: "sinal", texto: card.sinal }),
        ]);
        cardEl.addEventListener("click", () => {
          window.location.hash = `#/conversas/${card.id}`;
        });
        filhos.push(cardEl);
      });
      board.appendChild(el("div", { class: classes }, filhos));
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
      const linha = el("div", { class: "fila-item" }, [
        el("span", { class: "nome", texto: `${card.nome} — ${card.estagio_label}` }),
        tagTemperatura(card.temperatura),
        el("span", { class: "acao", texto: formatarHoras(card.horas_esperando) }),
      ]);
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
  const resumo = el("p", { class: "aviso" }, [
    document.createTextNode(`${card.estagio_label} (${card.estagio}) — `),
    tagTemperatura(card.temperatura),
    document.createTextNode(` — sinal: ${card.sinal}`),
  ]);
  container.appendChild(resumo);

  if (detalhe.contato) {
    const contato = detalhe.contato;
    container.appendChild(
      el("p", {
        class: "aviso",
        texto: `Contato: ${contato.nome || "(sem nome)"} — ${contato.tipo.toUpperCase()} — ` +
          (contato.tem_telefone ? "tem telefone cadastrado" : "sem telefone cadastrado"),
      })
    );
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

  await renderizarMensagens(container, id);
}

async function renderizarMensagens(container, id) {
  const secao = el("div", { class: "secao" }, [el("h3", { texto: "Mensagens" })]);
  const dados = await chamarApi(`/conversas/${id}/mensagens`);
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

// -- Roteador ----------------------------------------------------------------

async function renderizarRota() {
  const conteudo = document.getElementById("conteudo");
  conteudo.textContent = "";
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

function iniciar() {
  document.getElementById("campo-token").value = obterToken();
  document.getElementById("botao-salvar-token").addEventListener("click", () => {
    salvarToken(document.getElementById("campo-token").value.trim());
    renderizarRota();
  });
  document.getElementById("botao-atualizar").addEventListener("click", renderizarRota);
  window.addEventListener("hashchange", renderizarRota);
  renderizarRota();
}

document.addEventListener("DOMContentLoaded", iniciar);
