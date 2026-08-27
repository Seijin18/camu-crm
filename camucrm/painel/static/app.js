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

/**
 * POST de uma ação humana (marco, funil, correção — change `acoes-no-painel`).
 * Mesmo formato de erro de `chamarApi`: `erro.regra` carrega a seção citada
 * pelo servidor (ex.: "§3") quando a ação é recusada com 422.
 */
async function chamarApiEscrever(caminho, corpo) {
  const resposta = await fetch(`/api${caminho}`, {
    method: "POST",
    headers: {
      "X-Camu-Token": obterToken(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(corpo),
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
        try {
          await chamarApiEscrever(`/rascunhos/${rascunho.id}/escolha`, {
            opcao: numero,
            por: obterOperador(),
          });
          botaoEscolher.textContent = "Registrado";
          botaoEscolher.disabled = true;
        } catch (erro) {
          botaoEscolher.textContent = `Erro: ${erro.message}`;
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
    } else if (partes[0] === "funciona") {
      marcarAbaAtiva("/funciona");
      await renderizarFunciona(conteudo);
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

/**
 * Parser SSE manual (~25 linhas, como o plano pede): um bloco é tudo entre
 * duas quebras de linha duplas, com linhas `id:`/`event:`/`data:`. O
 * heartbeat (`: ping`) não tem `data:` e é ignorado aqui — ele só existe
 * para o proxy não fechar a conexão por inatividade.
 */
function processarBlocoSse(bloco) {
  let evento = "message";
  let temDados = false;
  bloco.split("\n").forEach((linha) => {
    if (linha.startsWith("id:")) {
      ultimoIdStream = Number(linha.slice(3).trim());
    } else if (linha.startsWith("event:")) {
      evento = linha.slice(6).trim();
    } else if (linha.startsWith("data:")) {
      temDados = true;
    }
  });
  if (!temDados) return; // heartbeat
  if (evento === "mensagem" || evento === "mudanca") {
    // Recarrega a tela atual com os mesmos dados que "Atualizar" busca —
    // o stream só avisa que algo mudou, não tenta atualizar o DOM à mão
    // por cima do que `renderizarRota` já sabe montar.
    renderizarRotaSegura();
  }
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
  document.getElementById("campo-operador").value = obterOperador();
  document.getElementById("botao-salvar-token").addEventListener("click", () => {
    salvarToken(document.getElementById("campo-token").value.trim());
    salvarOperador(document.getElementById("campo-operador").value.trim());
    renderizarRotaSegura();
  });
  document.getElementById("botao-atualizar").addEventListener("click", renderizarRotaSegura);
  window.addEventListener("hashchange", renderizarRotaSegura);
  renderizarRotaSegura();
  conectarStream();
}

document.addEventListener("DOMContentLoaded", iniciar);
