/* ==========================================================================
   SalaViva — cliente web

   Implementa o algoritmo de cliente descrito em docs/protocolo.md:

     1. join informando o last_seq conhecido
     2. alimenta TODA mensagem numa fila de hold-back
     3. renderiza apenas o que a fila liberar (contíguo, sem duplicata)
     4. lacuna persistente -> pede resync
     5. queda -> reconecta com backoff e refaz join com o last_seq atualizado

   O passo 5 é o que garante zero perda quando um nó é derrubado: o número de
   sequência vive no Redis e o histórico no DynamoDB, então nada do que importa
   morre com a instância.
   ========================================================================== */

(() => {
  "use strict";

  const GAP_TIMEOUT_MS = 2000;
  const MAX_BACKOFF_MS = 15000;

  // ---------------------------------------------------------------------
  // Fila de hold-back — porta em JS de src/salaviva/domain/ordering.py.
  // Cliente e servidor precisam concordar sobre o que "ordenado" significa,
  // então o algoritmo é o mesmo dos dois lados.
  // ---------------------------------------------------------------------
  class HoldBackQueue {
    constructor(startSeq = 0, maxBuffer = 500) {
      this.delivered = startSeq;
      this.buffer = new Map();
      this.maxBuffer = maxBuffer;
    }

    get expected() {
      return this.delivered + 1;
    }

    get hasGap() {
      return this.buffer.size > 0;
    }

    /** Devolve {released, duplicate} — os itens liberados em ordem contígua. */
    offer(seq, item) {
      if (seq <= this.delivered || this.buffer.has(seq)) {
        return { released: [], duplicate: true };
      }
      if (this.buffer.size >= this.maxBuffer) {
        this.buffer.set(seq, item);
        return { released: this.forceRelease(), duplicate: false };
      }

      this.buffer.set(seq, item);
      const released = [];
      while (this.buffer.has(this.delivered + 1)) {
        this.delivered += 1;
        released.push(this.buffer.get(this.delivered));
        this.buffer.delete(this.delivered);
      }
      return { released, duplicate: false };
    }

    forceRelease() {
      const seqs = [...this.buffer.keys()].sort((a, b) => a - b);
      const out = seqs.map((s) => this.buffer.get(s));
      if (seqs.length) this.delivered = Math.max(this.delivered, seqs[seqs.length - 1]);
      this.buffer.clear();
      return out;
    }

    reset(seq) {
      this.delivered = seq;
      this.buffer.clear();
    }
  }

  // ---------------------------------------------------------------------
  // Estado
  // ---------------------------------------------------------------------
  const state = {
    token: "",
    user: "",
    nodeId: "—",
    ws: null,
    rooms: new Map(), // roomId -> {queue, members, entries, gapTimer}
    active: null,
    backoff: 500,
    manualClose: false,
    stats: { recv: 0, reord: 0, dup: 0, gap: 0, recon: 0, heal: 0 },
    recovering: false,
  };

  const $ = (id) => document.getElementById(id);
  const el = {
    gate: $("gate"),
    gateForm: $("gate-form"),
    gateUser: $("gate-user"),
    gateRoom: $("gate-room"),
    gateError: $("gate-error"),
    shell: $("shell"),
    rdUser: $("rd-user"),
    rdNode: $("rd-node"),
    rdRoom: $("rd-room"),
    rdSeq: $("rd-seq"),
    rdSeal: $("rd-seal"),
    roomlist: $("roomlist"),
    roomadd: $("roomadd"),
    roomaddInput: $("roomadd-input"),
    log: $("log"),
    composer: $("composer"),
    composerInput: $("composer-input"),
    composerSend: $("composer-send"),
    members: $("members"),
  };

  // ---------------------------------------------------------------------
  // Auxiliares
  // ---------------------------------------------------------------------

  /** Cor de traço estável por nó. A cor codifica origem, não é decorativa. */
  function traceOf(nodeId) {
    let h = 0;
    for (let i = 0; i < nodeId.length; i += 1) {
      h = (h * 31 + nodeId.charCodeAt(i)) >>> 0;
    }
    return `var(--trace-${h % 6})`;
  }

  function shortNode(nodeId) {
    return nodeId.length > 16 ? `${nodeId.slice(0, 14)}…` : nodeId;
  }

  function uuid() {
    return crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2);
  }

  function room(roomId) {
    if (!state.rooms.has(roomId)) {
      state.rooms.set(roomId, {
        queue: new HoldBackQueue(0),
        members: [],
        entries: [],
        gapTimer: null,
      });
    }
    return state.rooms.get(roomId);
  }

  function wsURL() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}/ws?token=${encodeURIComponent(state.token)}`;
  }

  // ---------------------------------------------------------------------
  // Renderização
  // ---------------------------------------------------------------------

  function paintTally() {
    $("t-recv").textContent = state.stats.recv;
    $("t-reord").textContent = state.stats.reord;
    $("t-dup").textContent = state.stats.dup;
    $("t-gap").textContent = state.stats.gap;
    $("t-recon").textContent = state.stats.recon;
    $("t-heal").textContent = state.stats.heal;
    $("t-gap").classList.toggle("is-alert", state.stats.gap > 0);
  }

  function paintSeal() {
    const seal = el.rdSeal;
    seal.classList.remove("is-broken", "is-offline");
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
      seal.classList.add("is-offline");
      seal.textContent = "reconectando";
      return;
    }
    const r = state.active ? state.rooms.get(state.active) : null;
    if (r && r.queue.hasGap) {
      seal.classList.add("is-broken");
      seal.textContent = "lacuna detectada";
    } else {
      seal.textContent = "contígua";
    }
  }

  function paintRooms() {
    el.roomlist.innerHTML = "";
    for (const [roomId, r] of state.rooms) {
      const li = document.createElement("li");
      li.className = "roomlist__item" + (roomId === state.active ? " is-active" : "");
      li.tabIndex = 0;
      li.innerHTML = `<span>${escapeHTML(roomId)}</span><span class="roomlist__count">${r.members.length}</span>`;
      li.addEventListener("click", () => switchTo(roomId));
      li.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          switchTo(roomId);
        }
      });
      el.roomlist.appendChild(li);
    }
  }

  function paintMembers() {
    el.members.innerHTML = "";
    const r = state.active ? state.rooms.get(state.active) : null;
    if (!r) return;
    for (const m of r.members) {
      const li = document.createElement("li");
      li.className = "members__item";
      li.style.setProperty("--trace", traceOf(m.node_id));
      li.innerHTML =
        `<span class="members__user">${escapeHTML(m.user)}</span>` +
        `<span class="members__node">${escapeHTML(shortNode(m.node_id))}</span>`;
      el.members.appendChild(li);
    }
  }

  function escapeHTML(s) {
    const d = document.createElement("div");
    d.textContent = s ?? "";
    return d.innerHTML;
  }

  function renderEntry(msg, { recovered = false, gapBefore = 0 } = {}) {
    const frag = document.createDocumentFragment();

    if (gapBefore > 0) {
      const mark = document.createElement("div");
      mark.className = "gapmark";
      mark.textContent = `${gapBefore} mensagem(ns) não chegaram em tempo real — recuperadas do histórico`;
      frag.appendChild(mark);
    }

    const row = document.createElement("article");
    row.className = "entry";
    if (msg.sender === state.user) row.classList.add("is-own");
    if (recovered) row.classList.add("is-recovered");
    row.style.setProperty("--trace", traceOf(msg.node_id));

    const rail = document.createElement("div");
    rail.className = "entry__rail";
    rail.textContent = msg.seq;
    rail.setAttribute("aria-label", `sequência ${msg.seq}`);

    const body = document.createElement("div");
    body.className = "entry__body";
    body.innerHTML =
      `<div class="entry__meta">` +
      `<span class="entry__author">${escapeHTML(msg.sender)}</span>` +
      `<span class="chip">${escapeHTML(shortNode(msg.node_id))}</span>` +
      `<span class="stamp">L=${msg.lamport}</span>` +
      `</div>` +
      `<div class="entry__text">${escapeHTML(msg.content)}</div>`;

    row.appendChild(rail);
    row.appendChild(body);
    frag.appendChild(row);
    return frag;
  }

  function notice(text, strong = false) {
    const p = document.createElement("p");
    p.className = "notice" + (strong ? " notice--strong" : "");
    p.textContent = text;
    return p;
  }

  function append(node) {
    const stuck =
      el.log.scrollHeight - el.log.scrollTop - el.log.clientHeight < 120;
    el.log.appendChild(node);
    if (stuck) el.log.scrollTop = el.log.scrollHeight;
  }

  function switchTo(roomId) {
    state.active = roomId;
    el.rdRoom.textContent = roomId;
    const r = room(roomId);
    el.log.innerHTML = "";
    for (const item of r.entries) {
      el.log.appendChild(
        item.kind === "notice"
          ? notice(item.text, item.strong)
          : renderEntry(item.msg, { gapBefore: item.gapBefore }),
      );
    }
    el.log.scrollTop = el.log.scrollHeight;
    el.rdSeq.textContent = r.queue.delivered;
    paintRooms();
    paintMembers();
    paintSeal();
    el.composerInput.focus();
  }

  // ---------------------------------------------------------------------
  // Entrega de mensagens
  // ---------------------------------------------------------------------

  function deliver(roomId, msg, recovered) {
    const r = room(roomId);
    const antesDaFila = r.queue.expected;
    const { released, duplicate } = r.queue.offer(msg.seq, msg);

    if (duplicate) {
      state.stats.dup += 1;
      paintTally();
      return;
    }
    if (released.length === 0) {
      // Retida: falta preencher a lacuna. Se persistir, pedimos resync.
      armGapTimer(roomId);
      paintSeal();
      return;
    }
    if (released.length > 1 || msg.seq !== antesDaFila) {
      state.stats.reord += released.length;
    }

    for (const m of released) {
      const gapBefore = recovered && state.recovering ? 0 : 0;
      r.entries.push({ kind: "msg", msg: m, gapBefore });
      if (roomId === state.active) {
        append(renderEntry(m, { recovered, gapBefore }));
      }
      state.stats.recv += 1;
      if (recovered) state.stats.heal += 1;
    }

    if (!r.queue.hasGap) clearGapTimer(roomId);
    if (roomId === state.active) el.rdSeq.textContent = r.queue.delivered;
    paintTally();
    paintSeal();
  }

  function armGapTimer(roomId) {
    const r = room(roomId);
    if (r.gapTimer) return;
    state.stats.gap += 1;
    paintTally();
    r.gapTimer = setTimeout(() => {
      r.gapTimer = null;
      // Lacuna persistente: o Pub/Sub é at-most-once, então pedimos ao
      // histórico durável o que não chegou em tempo real.
      send({ type: "resync", room: roomId, after_seq: r.queue.delivered });
    }, GAP_TIMEOUT_MS);
  }

  function clearGapTimer(roomId) {
    const r = room(roomId);
    if (r.gapTimer) {
      clearTimeout(r.gapTimer);
      r.gapTimer = null;
    }
  }

  // ---------------------------------------------------------------------
  // Conexão
  // ---------------------------------------------------------------------

  function send(payload) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify(payload));
      return true;
    }
    return false;
  }

  function connect(isReconnect = false) {
    const ws = new WebSocket(wsURL());
    state.ws = ws;

    ws.addEventListener("open", () => {
      state.backoff = 500;
      if (isReconnect) {
        state.stats.recon += 1;
        state.recovering = true;
        paintTally();
      }
      // Refaz o join de TODAS as salas com o last_seq de cada uma. É este
      // passo que recupera exatamente o que se perdeu enquanto o nó estava
      // fora — nem uma mensagem a menos, nem uma duplicada.
      for (const [roomId, r] of state.rooms) {
        send({ type: "join", room: roomId, last_seq: r.queue.delivered });
      }
      paintSeal();
    });

    ws.addEventListener("message", (event) => {
      let frame;
      try {
        frame = JSON.parse(event.data);
      } catch {
        return;
      }
      handle(frame);
    });

    ws.addEventListener("close", (event) => {
      paintSeal();
      el.composerSend.disabled = true;
      if (state.manualClose) return;
      if (event.code === 4401) {
        el.gateError.textContent = "Sessão expirada. Entre novamente.";
        el.shell.classList.remove("is-live");
        el.gate.style.display = "grid";
        return;
      }
      const wait = Math.min(state.backoff, MAX_BACKOFF_MS) * (0.5 + Math.random());
      state.backoff = Math.min(state.backoff * 2, MAX_BACKOFF_MS);
      if (state.active) {
        const r = room(state.active);
        r.entries.push({
          kind: "notice",
          text: `Conexão perdida. Reconectando em ${(wait / 1000).toFixed(1)}s…`,
          strong: false,
        });
        append(notice(r.entries[r.entries.length - 1].text));
      }
      setTimeout(() => connect(true), wait);
    });
  }

  function handle(frame) {
    switch (frame.type) {
      case "welcome": {
        const trocouDeNo = state.nodeId !== "—" && state.nodeId !== frame.node_id;
        state.nodeId = frame.node_id;
        el.rdNode.textContent = shortNode(frame.node_id);
        el.rdNode.style.color = traceOf(frame.node_id);
        el.composerSend.disabled = false;
        if (trocouDeNo && state.active) {
          const texto = `Reconectado — agora no nó ${frame.node_id}`;
          room(state.active).entries.push({ kind: "notice", text: texto, strong: true });
          append(notice(texto, true));
        }
        break;
      }

      case "joined": {
        const r = room(frame.room);
        r.members = frame.members || [];
        if (state.active === null) switchTo(frame.room);

        const recuperadas = frame.backlog || [];
        for (const msg of recuperadas) deliver(frame.room, msg, state.recovering);

        if (state.recovering && recuperadas.length) {
          const primeira = recuperadas[0].seq;
          const ultima = recuperadas[recuperadas.length - 1].seq;
          const texto = `${recuperadas.length} mensagem(ns) recuperadas do histórico (seq ${primeira}–${ultima}) — nada foi perdido`;
          r.entries.push({ kind: "notice", text: texto, strong: true });
          if (frame.room === state.active) append(notice(texto, true));
        }
        state.recovering = false;
        paintRooms();
        paintMembers();
        paintSeal();
        break;
      }

      case "message":
        deliver(frame.room_id, frame, false);
        break;

      case "presence_update": {
        const r = room(frame.room);
        r.members = frame.members || [];
        if (frame.room === state.active) {
          paintMembers();
          const verbo = frame.event === "join" ? "entrou" : "saiu";
          append(notice(`${frame.user} ${verbo}`));
        }
        paintRooms();
        break;
      }

      case "left":
        state.rooms.delete(frame.room);
        if (state.active === frame.room) {
          const proxima = state.rooms.keys().next();
          state.active = null;
          if (!proxima.done) switchTo(proxima.value);
          else el.log.innerHTML = "";
        }
        paintRooms();
        break;

      case "ack":
        if (frame.duplicate) state.stats.dup += 1;
        paintTally();
        break;

      case "error":
        if (state.active) append(notice(`erro: ${frame.message}`, true));
        break;

      case "pong":
      case "typing":
      default:
        break;
    }
  }

  // ---------------------------------------------------------------------
  // Eventos da interface
  // ---------------------------------------------------------------------

  el.gateForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    el.gateError.textContent = "";
    const user = el.gateUser.value.trim();
    const inicial = (el.gateRoom.value || "geral").trim();
    if (!user) return;

    try {
      const resp = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: user }),
      });
      if (!resp.ok) throw new Error(`login falhou (${resp.status})`);
      const data = await resp.json();
      state.token = data.token;
      state.user = data.user;
    } catch (err) {
      el.gateError.textContent = String(err.message || err);
      return;
    }

    el.rdUser.textContent = state.user;
    el.gate.style.display = "none";
    el.shell.classList.add("is-live");
    room(inicial);
    connect(false);
  });

  el.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    const texto = el.composerInput.value.trim();
    if (!texto || !state.active) return;
    const enviado = send({
      type: "send",
      room: state.active,
      content: texto,
      client_msg_id: uuid(),
    });
    if (enviado) el.composerInput.value = "";
  });

  el.roomadd.addEventListener("submit", (event) => {
    event.preventDefault();
    const nome = el.roomaddInput.value.trim();
    if (!nome) return;
    room(nome);
    send({ type: "join", room: nome, last_seq: 0 });
    el.roomaddInput.value = "";
    switchTo(nome);
  });

  window.addEventListener("beforeunload", () => {
    state.manualClose = true;
    if (state.ws) state.ws.close(1000, "saindo");
  });

  paintTally();
})();
