"use strict";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const MAX_GAMES = 3;
const MAX_TAGS = 6;
const MAX_OPTIONS = 8;
// Juegos conocidos para el primer clic; solo se muestran los que existen en el catálogo cargado.
const FEATURED_GAMES = ["Hades", "Celeste", "Hollow Knight", "Stardew Valley", "Outer Wilds", "Slay the Spire", "Undertale"];
const NUMBER = new Intl.NumberFormat("es-CL");
// Etiquetas que tienen casi todos los juegos: no ayudan a elegir.
const GENERIC_TAGS = new Set(["Indie", "Single player"]);
// En pantallas angostas la frase usa marcadores más cortos para que quepan en una línea.
const NARROW = window.matchMedia("(max-width: 560px)");

// Géneros, temas y modos de IGDB en español. Las keywords (roguelike, metroidvania…) quedan en inglés.
const LABELS_ES = {
  Adventure: "Aventura",
  Arcade: "Arcade",
  "Card & Board Game": "Cartas y tablero",
  Fighting: "Lucha",
  "Hack and slash/Beat 'em up": "Hack and slash",
  Indie: "Indie",
  MOBA: "MOBA",
  Music: "Música",
  Pinball: "Pinball",
  Platform: "Plataformas",
  "Point-and-click": "Point and click",
  Puzzle: "Puzles",
  "Quiz/Trivia": "Preguntas y trivia",
  Racing: "Carreras",
  "Real Time Strategy (RTS)": "Estrategia en tiempo real",
  "Role-playing (RPG)": "Rol (RPG)",
  Shooter: "Disparos",
  Simulator: "Simulación",
  Sport: "Deportes",
  Strategy: "Estrategia",
  Tactical: "Táctico",
  "Turn-based strategy (TBS)": "Estrategia por turnos",
  "Visual Novel": "Novela visual",
  "4X (explore, expand, exploit, and exterminate)": "4X",
  Action: "Acción",
  Business: "Negocios",
  Comedy: "Comedia",
  Drama: "Drama",
  Educational: "Educativo",
  Erotic: "Erótico",
  Fantasy: "Fantasía",
  Historical: "Histórico",
  Horror: "Terror",
  Kids: "Infantil",
  Mystery: "Misterio",
  "Non-fiction": "No ficción",
  "Open world": "Mundo abierto",
  Party: "Fiesta",
  Romance: "Romance",
  Sandbox: "Sandbox",
  "Science fiction": "Ciencia ficción",
  Stealth: "Sigilo",
  Survival: "Supervivencia",
  Thriller: "Suspenso",
  Warfare: "Guerra",
  "Battle Royale": "Battle royale",
  "Co-operative": "Cooperativo",
  "Massively Multiplayer Online (MMO)": "Multijugador masivo (MMO)",
  Multiplayer: "Multijugador",
  "Single player": "Un jugador",
  "Split screen": "Pantalla dividida",
};
// Otras formas de escribir un tema en español que también deben encontrar su etiqueta de IGDB.
const ALIASES_ES = {
  rol: "Role-playing (RPG)",
  rompecabezas: "Puzzle",
  puzle: "Puzzle",
  plataforma: "Platform",
  miedo: "Horror",
  humor: "Comedy",
  pelea: "Fighting",
  peleas: "Fighting",
  simulador: "Simulator",
  deporte: "Sport",
  cartas: "Card & Board Game",
  aventuras: "Adventure",
  "niños": "Kids",
  coop: "Co-operative",
};

/** Minúsculas y sin tildes, para comparar lo que escribe la persona. Conserva el largo del texto. */
const fold = (text) => text.normalize("NFD").replace(/[̀-ͯ]/g, "").normalize("NFC").toLowerCase();
const labelEs = (label) => LABELS_ES[label] || label;
const ES_TO_IGDB = new Map([
  ...Object.entries(LABELS_ES).map(([igdb, es]) => [fold(es), igdb]),
  ...Object.entries(ALIASES_ES).map(([es, igdb]) => [fold(es), igdb]),
]);
/** "terror" → "Horror": la API busca por la etiqueta de IGDB. Lo que no está en el diccionario pasa igual. */
const toIgdbLabel = (text) => ES_TO_IGDB.get(fold(text.trim().replace(/\s+/g, " "))) || text;

// Escala fija de parecido sobre la similitud real (Tanimoto, de 0 a 1). No depende de los demás
// resultados. Los cortes salen de medir el modelo: al buscar por juegos, la mitad de las recomendaciones
// queda entre 0,28 y 0,42; al buscar por temas el perfil son pocas palabras y la similitud es más baja
// (la mitad queda entre 0,12 y 0,19), así que esa búsqueda tiene sus propios cortes, igual de fijos.
const MATCH_LABELS = ["Algo en común", "Parecido", "Muy parecido"];
const MATCH_CUTS = { games: [0.25, 0.4], tags: [0.1, 0.18] };
function matchLevel(score, byTags) {
  const cuts = byTags ? MATCH_CUTS.tags : MATCH_CUTS.games;
  const level = 1 + cuts.filter((cut) => score >= cut).length;
  return { level, label: MATCH_LABELS[level - 1] };
}

// Portadas de IGDB y enlaces de salida: solo se aceptan con la forma esperada.
const COVER_ID = /^[a-z0-9_]{2,40}$/;
const coverUrl = (id, size) => `https://images.igdb.com/igdb/image/upload/t_${size}/${id}.jpg`;
const STORE_PREFIX = "https://store.steampowered.com/app/";
const IGDB_PREFIX = "https://www.igdb.com/games/";
// Moneda y fecha de los precios; las informa /stats.
let priceInfo = { currency: "CLP", asOf: null };

let mode = "games";
// El token vive solo en esta variable: no se guarda en el navegador y recargar cierra la sesión.
let token = null;
let alias = null;
let pendingVote = null;
let authMode = "login";
let lastBody = null;

const catalog = { titles: [], byKey: new Map(), info: new Map(), tags: [], tagCount: new Map(), loaded: false };

const normalize = (text) => text.trim().replace(/\s+/g, " ").toLowerCase();

/* ---------- Utilidades de DOM (sin HTML dinámico: solo nodos y textContent) ---------- */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.appendChild(use);
  return svg;
}

/** Texto con la parte que coincide marcada en <mark>. */
function highlighted(text, query) {
  const frag = document.createDocumentFragment();
  query = fold(query);
  const at = fold(text).indexOf(query);
  if (!query || at < 0) {
    frag.append(text);
    return frag;
  }
  frag.append(text.slice(0, at), el("mark", "", text.slice(at, at + query.length)), text.slice(at + query.length));
  return frag;
}

/** Miniatura de portada para el autocompletado; un marco vacío del mismo tamaño si el juego no tiene. */
function optionCover(coverId) {
  if (!coverId) return el("span", "option-cover");
  const img = el("img", "option-cover");
  img.width = 30;
  img.height = 42;
  img.loading = "lazy";
  img.decoding = "async";
  img.referrerPolicy = "no-referrer";
  img.alt = ""; // decorativa: el nombre del juego va al lado
  img.src = coverUrl(coverId, "cover_small");
  return img;
}

/* ---------- Campo de fichas con combobox accesible ---------- */

class TokenField {
  constructor({ input, listbox, hint, max, allowFree, source, placeholders, onChange, display, canonical }) {
    this.display = display || ((value) => value); // cómo se muestra un valor (p. ej. traducido)
    this.canonical = canonical || ((text) => text); // cómo se guarda lo que la persona escribe
    this.input = input;
    this.listbox = listbox;
    this.hint = hint;
    this.max = max;
    this.allowFree = allowFree;
    this.source = source;
    this.placeholders = placeholders;
    this.onChange = onChange || (() => {});
    this.values = [];
    this.options = [];
    this.active = -1;
    this.combo = input.closest(".combo");

    input.addEventListener("input", () => {
      this.setHint("");
      this.fit();
      this.suggest();
    });
    input.addEventListener("keydown", (event) => this.onKey(event));
    input.addEventListener("blur", () => this.close());
    input.addEventListener("focus", () => this.suggest());
    // mousedown y no click: así el input no pierde el foco antes de elegir la opción.
    listbox.addEventListener("mousedown", (event) => {
      const option = event.target.closest("[role=option]");
      if (!option) return;
      event.preventDefault();
      this.add(this.options[Number(option.dataset.index)].value);
    });
    this.render();
  }

  get full() {
    return this.values.length >= this.max;
  }

  has(value) {
    return this.values.some((v) => normalize(v) === normalize(value));
  }

  setHint(text) {
    this.hint.textContent = text;
    if (text) this.input.setAttribute("aria-invalid", "true");
    else this.input.removeAttribute("aria-invalid");
  }

  add(value) {
    const clean = value.trim();
    if (!clean || this.has(clean) || this.full) return false;
    this.values.push(clean);
    this.input.value = "";
    this.close();
    this.render();
    this.onChange();
    if (!this.full) this.input.focus();
    return true;
  }

  remove(index) {
    this.values.splice(index, 1);
    this.render();
    this.onChange();
    this.input.focus();
  }

  toggle(value) {
    const i = this.values.findIndex((v) => normalize(v) === normalize(value));
    if (i >= 0) this.remove(i);
    else this.add(value);
  }

  /** Lo que la persona dejó escrito sin confirmar se agrega antes de buscar. */
  commitTyped() {
    const text = this.input.value.trim();
    if (!text) return true;
    const resolved = this.resolve(text);
    if (resolved) {
      this.add(resolved);
      return true;
    }
    // No se adivina: si lo escrito calza con varios juegos, la persona elige cuál.
    this.suggest();
    this.setHint(
      this.listbox.hidden
        ? `Ningún juego del catálogo coincide con «${text}». Revisa el nombre o prueba con uno de los de abajo.`
        : `Hay varios juegos con «${text}». Elige cuál de la lista.`,
    );
    return false;
  }

  resolve(text) {
    if (this.allowFree) return this.canonical(text);
    const exact = catalog.byKey.get(normalize(text));
    if (exact) return exact;
    const matches = this.source(normalize(text)).filter((o) => !this.has(o.value));
    return matches.length === 1 ? matches[0].value : null;
  }

  render() {
    this.combo.parentElement.querySelectorAll(".token").forEach((node) => node.remove());
    this.values.forEach((value, index) => {
      const chip = $("token-tpl").content.firstElementChild.cloneNode(true);
      chip.querySelector(".token-text").textContent = this.display(value);
      const removeBtn = chip.querySelector(".token-remove");
      removeBtn.setAttribute("aria-label", `Quitar ${this.display(value)}`);
      removeBtn.addEventListener("click", () => this.remove(index));
      this.combo.before(chip);
    });
    this.combo.hidden = this.full;
    const set = NARROW.matches ? this.placeholders.narrow : this.placeholders;
    this.input.placeholder = this.values.length ? set.more : set.first;
    this.fit();
  }

  /** El input crece con lo escrito, para que la frase no se rompa con un hueco enorme. */
  fit() {
    const length = Math.max(this.input.value.length, this.input.placeholder.length * 0.86, 3);
    this.combo.style.width = `${Math.ceil(length) + 1}ch`; // el CSS lo limita al ancho de la frase
  }

  suggest() {
    const query = normalize(this.input.value);
    if (!query) {
      this.close();
      return;
    }
    this.options = this.source(query)
      .filter((o) => !this.has(o.value))
      .slice(0, MAX_OPTIONS);
    if (!this.options.length) {
      this.close();
      return;
    }
    this.listbox.replaceChildren(
      ...this.options.map((option, index) => {
        const li = el("li");
        li.id = `${this.listbox.id}-${index}`;
        li.setAttribute("role", "option");
        li.dataset.index = String(index);
        if (option.cover !== undefined) li.append(optionCover(option.cover));
        const name = el("span", "option-name", "");
        name.append(highlighted(option.label || option.value, query));
        li.append(name);
        if (option.extra) li.append(el("span", "option-extra", option.extra));
        return li;
      }),
    );
    // En juegos, Enter elige la primera opción; en temas se puede escribir uno libre.
    this.setActive(this.allowFree ? -1 : 0);
    this.listbox.hidden = false;
    this.input.setAttribute("aria-expanded", "true");
  }

  setActive(index) {
    this.active = index;
    [...this.listbox.children].forEach((li, i) => li.setAttribute("aria-selected", String(i === index)));
    if (index >= 0) {
      this.input.setAttribute("aria-activedescendant", `${this.listbox.id}-${index}`);
      this.listbox.children[index].scrollIntoView({ block: "nearest" });
    } else {
      this.input.removeAttribute("aria-activedescendant");
    }
  }

  close() {
    this.listbox.hidden = true;
    this.input.setAttribute("aria-expanded", "false");
    this.input.removeAttribute("aria-activedescendant");
    this.active = -1;
  }

  onKey(event) {
    const open = !this.listbox.hidden;
    const count = this.options.length;
    if (event.key === "ArrowDown" && open) {
      event.preventDefault();
      this.setActive((this.active + 1) % count);
    } else if (event.key === "ArrowUp" && open) {
      event.preventDefault();
      this.setActive(this.active <= 0 ? count - 1 : this.active - 1);
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      this.close();
    } else if (event.key === "Enter" || (event.key === "," && this.allowFree)) {
      if (!this.input.value.trim()) return; // Enter con el campo vacío envía el formulario
      event.preventDefault();
      if (open && this.active >= 0) this.add(this.options[this.active].value);
      else this.commitTyped();
    } else if (event.key === "Backspace" && !this.input.value && this.values.length) {
      this.remove(this.values.length - 1);
    }
  }
}

// Orden: el título exacto; los que empiezan con lo escrito (los más cortos primero); los que tienen una
// palabra que empieza así; y al final los que solo lo contienen. Cada opción lleva su año y su portada.
function searchTitles(query) {
  const exact = catalog.byKey.get(query);
  const starts = [];
  const wordStarts = [];
  const contains = [];
  for (const title of catalog.titles) {
    if (title === exact) continue;
    const key = title.toLowerCase();
    const at = key.indexOf(query);
    if (at === 0) starts.push(title);
    else if (at > 0 && /[^a-z0-9]/.test(key[at - 1])) {
      if (wordStarts.length < MAX_OPTIONS) wordStarts.push(title);
    } else if (at > 0 && contains.length < MAX_OPTIONS) contains.push(title);
  }
  starts.sort((a, b) => a.length - b.length);
  return [...(exact ? [exact] : []), ...starts, ...wordStarts, ...contains]
    .slice(0, MAX_OPTIONS + MAX_GAMES)
    .map((value) => {
      const info = catalog.info.get(value) || {};
      return { value, extra: info.year ? String(info.year) : "", cover: info.cover || null };
    });
}

// Busca por el nombre en español o por el de IGDB; el valor que se guarda es siempre el de IGDB.
function searchTags(query) {
  const q = fold(query);
  return catalog.tags
    .filter((tag) => fold(labelEs(tag)).includes(q) || fold(tag).includes(q))
    .slice(0, MAX_OPTIONS + MAX_TAGS)
    .map((value) => ({
      value,
      label: labelEs(value),
      extra: `${NUMBER.format(catalog.tagCount.get(value))} juegos`,
    }));
}

const games = new TokenField({
  input: $("games-input"),
  listbox: $("games-list"),
  hint: $("games-hint"),
  max: MAX_GAMES,
  allowFree: false,
  source: searchTitles,
  placeholders: { first: "escribe un juego", more: "otro juego", narrow: { first: "un juego", more: "otro" } },
  onChange: () => syncSuggestions(),
});

const tags = new TokenField({
  input: $("tags-input"),
  listbox: $("tags-list"),
  hint: $("tags-hint"),
  max: MAX_TAGS,
  allowFree: true,
  source: searchTags,
  placeholders: { first: "un tema", more: "otro tema", narrow: { first: "un tema", more: "otro" } },
  onChange: () => syncSuggestions(),
  display: labelEs,
  canonical: toIgdbLabel,
});

const activeField = () => (mode === "games" ? games : tags);
NARROW.addEventListener("change", () => [games, tags].forEach((field) => field.render()));

/* ---------- Sugerencias rápidas ---------- */

function renderSuggestions() {
  const featured = FEATURED_GAMES.filter((title) => catalog.byKey.has(normalize(title))).map(
    (title) => catalog.byKey.get(normalize(title)),
  );
  fillSuggestions($("games-suggestions"), "Prueba con", featured, games);
  fillSuggestions($("tags-suggestions"), "Frecuentes", catalog.tags.slice(0, 12), tags);
  syncSuggestions();
}

function fillSuggestions(container, label, values, field) {
  if (!values.length) {
    container.replaceChildren();
    return;
  }
  container.replaceChildren(
    el("span", "suggestions-label", label),
    ...values.map((value) => {
      const chip = el("button", "chip", field.display(value));
      chip.type = "button";
      chip.dataset.value = value;
      chip.addEventListener("click", () => field.toggle(value));
      return chip;
    }),
  );
}

function syncSuggestions() {
  for (const [id, field] of [
    ["games-suggestions", games],
    ["tags-suggestions", tags],
  ]) {
    $(id)
      .querySelectorAll(".chip")
      .forEach((chip) => {
        const on = field.has(chip.dataset.value);
        chip.setAttribute("aria-pressed", String(on));
        chip.disabled = !on && field.full;
      });
  }
}

/* ---------- Pestañas: juegos o temas ---------- */

function setMode(next, focus = true) {
  mode = next;
  document.querySelectorAll(".mode-switch [role=tab]").forEach((tab) => {
    const on = tab.dataset.mode === mode;
    tab.setAttribute("aria-selected", String(on));
    tab.tabIndex = on ? 0 : -1;
  });
  $("mode-games").hidden = mode !== "games";
  $("mode-tags").hidden = mode !== "tags";
  games.close();
  tags.close();
  if (focus) activeField().input.focus();
}

document.querySelectorAll(".mode-switch [role=tab]").forEach((tab) => {
  tab.addEventListener("click", () => setMode(tab.dataset.mode));
  tab.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const next = mode === "games" ? "tags" : "games";
    setMode(next, false);
    document.querySelector(`.mode-switch [data-mode="${next}"]`).focus();
  });
});

// "/" enfoca el buscador desde cualquier parte de la página.
document.addEventListener("keydown", (event) => {
  const typing = event.target.closest("input, select, textarea, dialog");
  if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey) {
    event.preventDefault();
    activeField().input.focus();
  }
});

/* ---------- Catálogo, para autocompletar ---------- */

// /games pagina de a 500: se recorre una vez al cargar la página.
async function loadCatalog() {
  const tagCount = new Map();
  for (let offset = 0; ; offset += 500) {
    const resp = await fetch(`/games?limit=500&offset=${offset}`);
    if (!resp.ok) break;
    const page = await resp.json();
    for (const game of page) {
      catalog.titles.push(game.title);
      const key = normalize(game.title);
      if (!catalog.byKey.has(key)) catalog.byKey.set(key, game.title);
      if (!catalog.info.has(game.title)) {
        const cover = typeof game.cover_id === "string" && COVER_ID.test(game.cover_id) ? game.cover_id : null;
        catalog.info.set(game.title, { year: game.year || null, cover });
      }
      for (const tag of game.tags) {
        if (!GENERIC_TAGS.has(tag)) tagCount.set(tag, (tagCount.get(tag) || 0) + 1);
      }
    }
    if (page.length < 500) break;
  }
  catalog.tagCount = tagCount;
  catalog.tags = [...tagCount.entries()].sort((a, b) => b[1] - a[1]).map(([tag]) => tag);
  catalog.loaded = true;
  renderSuggestions();
  renderHeroCovers();
}

/* ---------- Fondo del buscador ---------- */

// Portadas de los juegos más conocidos del catálogo, desplazándose lento detrás del titular. Es decorativo:
// el CSS las deja tenues y fuera de la zona del texto, y las detiene si se pide menos movimiento o si el
// buscador sale de la pantalla (no tiene sentido gastar GPU en algo que no se ve).
function renderHeroCovers() {
  const ROWS = 4;
  const PER_ROW = 9;
  const covers = [];
  for (const info of catalog.info.values()) {
    if (info.cover) covers.push(info.cover);
    if (covers.length === ROWS * PER_ROW) break;
  }
  if (covers.length < ROWS * PER_ROW) return;
  const rows = Array.from({ length: ROWS }, (_, r) => {
    const row = el("div", "hero-row");
    const set = covers.slice(r * PER_ROW, (r + 1) * PER_ROW);
    // El juego de portadas va dos veces: al llegar a la mitad, el recorrido vuelve a empezar sin salto.
    for (const id of [...set, ...set]) {
      const img = el("img");
      img.width = 150;
      img.height = 213;
      img.alt = "";
      img.decoding = "async";
      img.referrerPolicy = "no-referrer";
      img.src = coverUrl(id, "cover_small");
      row.append(img);
    }
    return row;
  });
  $("hero-rows").replaceChildren(...rows);
  const bg = $("hero-bg");
  bg.classList.add("is-ready");
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(([entry]) => bg.classList.toggle("is-offscreen", !entry.isIntersecting)).observe(bg);
  }
}

/* ---------- Estadísticas en vivo ---------- */

async function loadStats() {
  try {
    const stats = await (await fetch("/stats")).json();
    $("stat-size").textContent = NUMBER.format(stats.catalog_size);
    $("stat-engine").textContent =
      stats.recommender === "ml" ? "el modelo de contenido (TF-IDF y Tanimoto)" : "la similitud de etiquetas (Jaccard)";
    const p95 = stats.latency_ms && stats.latency_ms.p95;
    $("stat-latency-wrap").hidden = p95 == null;
    if (p95 != null) $("stat-latency").textContent = `${NUMBER.format(p95)} ms`;
    $("live-stats").hidden = false;
    if (stats.prices) {
      priceInfo = { currency: stats.prices.currency || "CLP", asOf: stats.prices.as_of || null };
      renderPriceNote();
    }
  } catch {
    // Las cifras son informativas: si /stats falla, la sección se queda sin ellas.
  }
}

/* ---------- Sesión ---------- */

function showSession(name) {
  alias = name;
  $("session-label").hidden = !name;
  $("session-label").textContent = name || "";
  $("session-button").textContent = name ? "Salir" : "Iniciar sesión";
}

function logout() {
  token = null;
  showSession(null);
}

function setAuthMode(next) {
  authMode = next;
  document.querySelectorAll(".account-switch [data-auth]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.auth === next));
  });
  const register = next === "register";
  $("session-title").textContent = register ? "Crear una cuenta" : "Entrar a GameMatch";
  $("session-submit").textContent = register ? "Crear cuenta" : "Entrar";
  $("session-password").autocomplete = register ? "new-password" : "current-password";
  $("session-error").textContent = "";
}

function openSessionDialog(hint) {
  $("session-hint").textContent = hint;
  $("session-error").textContent = "";
  setAuthMode(authMode);
  $("session-dialog").showModal();
  $("session-alias").focus();
}

async function postJson(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function login(name, password) {
  const resp = await postJson("/auth/login", { alias: name, password });
  if (!resp.ok) {
    if (resp.status === 503) throw new Error("Las cuentas no están disponibles en este servidor.");
    throw new Error("El alias o la contraseña no coinciden.");
  }
  token = (await resp.json()).access_token;
  showSession(name);
}

async function register(name, password) {
  const resp = await postJson("/auth/register", { alias: name, password });
  if (resp.status === 409) throw new Error("Ese alias ya está en uso. Prueba con otro.");
  if (resp.status === 422) {
    throw new Error("Revisa los datos: alias de 3 a 30 letras, números, - o _, y contraseña de 12 a 128 caracteres.");
  }
  if (resp.status === 503) throw new Error("Las cuentas no están disponibles en este servidor.");
  if (!resp.ok) throw new Error("No se pudo crear la cuenta. Inténtalo de nuevo.");
  await login(name, password);
}

$("session-button").addEventListener("click", () => {
  if (token) {
    logout();
    setStatus("Cerraste tu sesión.");
  } else {
    openSessionDialog("Solo pedimos un alias y una contraseña.");
  }
});

document.querySelectorAll(".account-switch [data-auth]").forEach((button) => {
  button.addEventListener("click", () => setAuthMode(button.dataset.auth));
});

$("session-cancel").addEventListener("click", () => {
  pendingVote = null;
  $("session-dialog").close();
});

// Cerrar con Esc también descarta el voto: no debe enviarse en un login posterior.
$("session-dialog").addEventListener("cancel", () => {
  pendingVote = null;
});

$("session-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("session-alias").value.trim();
  const password = $("session-password").value;
  if (!name || !password) {
    $("session-error").textContent = "Escribe tu alias y tu contraseña.";
    return;
  }
  const submit = $("session-submit");
  submit.disabled = true;
  try {
    await (authMode === "register" ? register(name, password) : login(name, password));
  } catch (error) {
    $("session-error").textContent = error.message;
    return;
  } finally {
    submit.disabled = false;
  }
  $("session-password").value = "";
  $("session-dialog").close();
  if (pendingVote) {
    const { game, vote, card } = pendingVote;
    pendingVote = null;
    sendVote(game, vote, card);
  } else {
    setStatus(`Sesión iniciada como ${name}.`);
  }
});

/* ---------- Votos ---------- */

async function sendVote(game, vote, card) {
  if (!token) {
    pendingVote = { game, vote, card };
    openSessionDialog("Inicia sesión o crea una cuenta para guardar tu voto.");
    return;
  }
  let resp;
  try {
    resp = await fetch("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ game, vote }),
    });
  } catch {
    setStatus("No se pudo guardar el voto: no hay conexión con la API.", true);
    return;
  }
  if (resp.status === 401) {
    logout();
    pendingVote = { game, vote, card };
    openSessionDialog("Tu sesión venció. Vuelve a entrar para guardar tu voto.");
    return;
  }
  if (!resp.ok) {
    setStatus(resp.status === 403 ? "Tu cuenta no tiene permiso para votar." : "No se pudo guardar tu voto.", true);
    return;
  }
  card.querySelectorAll("[data-vote]").forEach((b) => b.setAttribute("aria-pressed", String(Number(b.dataset.vote) === vote)));
  setStatus(vote === 1 ? `Guardado: te interesa ${game}.` : `Guardado: no te interesa ${game}.`);
}

/* ---------- Resultados ---------- */

function setStatus(text, isError = false) {
  $("status").textContent = text;
  $("status").classList.toggle("error", isError);
}

function showNotice(text, actions = []) {
  $("notice-text").textContent = text;
  $("notice-actions").replaceChildren(
    ...actions.map(({ label, onClick, iconName, primary }) => {
      const button = el("button", primary ? "btn btn-primary" : "btn btn-quiet");
      button.type = "button";
      if (iconName) button.append(icon(iconName));
      button.append(label);
      button.addEventListener("click", onClick);
      return button;
    }),
  );
  $("notice").hidden = false;
}

function hideNotice() {
  $("notice").hidden = true;
}

function joinList(items) {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} y ${items[items.length - 1]}`;
}

function describeQuery(body) {
  return body.liked_games.length
    ? `Parecidos a ${joinList(body.liked_games)}`
    : `Con ${joinList(body.tags.map(labelEs))}`;
}

function showSkeleton(count) {
  const list = $("results");
  list.classList.remove("is-revealed");
  list.setAttribute("aria-busy", "true");
  $("scale").hidden = true;
  $("price-note").hidden = true;
  $("votes-note").hidden = true;
  list.replaceChildren(
    ...Array.from({ length: Math.min(count, 10) }, () => $("skeleton-tpl").content.firstElementChild.cloneNode(true)),
  );
}

function formatPrice(amount) {
  try {
    return new Intl.NumberFormat("es-CL", { style: "currency", currency: priceInfo.currency }).format(amount);
  } catch {
    return `$${NUMBER.format(amount)}`; // moneda desconocida para el navegador
  }
}

function renderPriceNote() {
  const note = $("price-note");
  if (!document.querySelector("#results .store-price:not([hidden])")) {
    note.hidden = true;
    return;
  }
  let when = "";
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(priceInfo.asOf || "");
  if (parts) {
    const date = new Date(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]));
    when = ` al ${new Intl.DateTimeFormat("es-CL", { dateStyle: "long" }).format(date)}`;
  }
  note.textContent = `Precios base de Steam en Chile${when}, sin ofertas. Pueden haber cambiado: el precio vigente está en la tienda.`;
  note.hidden = false;
}

function showScale(body) {
  $("scale-note").textContent = body.liked_games.length
    ? "La escala es fija: mide cuánto se parece cada juego a los que elegiste, sin compararlo con los demás resultados."
    : "La escala es fija: mide cuánto pesan esos temas en cada juego, sin compararlo con los demás resultados.";
  $("scale").hidden = false;
}

/** Portada con enlace a la tienda o ficha. Sin portada (o si no carga), el marco muestra el nombre. */
function fillCover(card, rec, href) {
  const frame = card.querySelector(".card-cover");
  const img = frame.querySelector(".cover-img");
  frame.querySelector(".cover-fallback").textContent = rec.title;
  if (href) {
    frame.href = href;
  } else {
    // Sin destino, el marco queda como <a> sin href: no es un enlace ni recibe foco.
    frame.removeAttribute("target");
    frame.removeAttribute("rel");
    frame.removeAttribute("tabindex");
  }
  const noCover = () => frame.classList.add("no-cover");
  if (rec.cover_id && COVER_ID.test(rec.cover_id)) {
    img.alt = `Portada de ${rec.title}`;
    img.addEventListener("load", () => img.classList.add("is-loaded"), { once: true });
    img.addEventListener("error", noCover, { once: true });
    img.srcset = `${coverUrl(rec.cover_id, "cover_big")} 1x, ${coverUrl(rec.cover_id, "cover_big_2x")} 2x`;
    img.src = coverUrl(rec.cover_id, "cover_big");
  } else {
    noCover();
  }
}

function renderResults(items, byTags) {
  const list = $("results");
  list.removeAttribute("aria-busy");
  const rows = items.map((rec, i) => {
    const card = $("card-tpl").content.firstElementChild.cloneNode(true);
    card.style.setProperty("--i", String(i));
    card.querySelector(".card-rank").textContent = String(i + 1);
    card.querySelector(".card-title").textContent = rec.title;

    const year = card.querySelector(".card-year");
    year.textContent = rec.year ? String(rec.year) : "";
    year.hidden = !rec.year;
    const genres = [...(rec.genres || []), ...(rec.themes || [])].slice(0, 2).map(labelEs);
    card.querySelector(".card-genres").textContent = genres.join(", ");
    card.querySelector(".card-meta").hidden = !rec.year && !genres.length;

    const match = matchLevel(rec.score, byTags);
    card.querySelector(".card-match .pips").dataset.level = String(match.level);
    card.querySelector(".match-label").textContent = match.label;
    card.querySelector(".card-match").title = `Similitud ${rec.score.toLocaleString("es-CL", { maximumFractionDigits: 2 })} en una escala de 0 a 1`;

    const reasons = (rec.reasons || []).slice(0, 3).map(labelEs);
    card.querySelector(".common-list").textContent = reasons.join(", ");
    card.querySelector(".card-common").hidden = !reasons.length;

    // Salida: la tienda si se conoce (con precio), si no la ficha de IGDB.
    const storeUrl = typeof rec.store_url === "string" && rec.store_url.startsWith(STORE_PREFIX) ? rec.store_url : null;
    const pageUrl = typeof rec.url === "string" && rec.url.startsWith(IGDB_PREFIX) ? rec.url : null;
    const href = storeUrl || pageUrl;
    const store = card.querySelector(".card-store");
    const price = card.querySelector(".store-price");
    const hasPrice = Boolean(storeUrl) && Number.isFinite(rec.price) && rec.price > 0;
    price.hidden = !hasPrice;
    if (hasPrice) price.textContent = formatPrice(rec.price);
    if (href) {
      store.href = href;
      const name = storeUrl ? (hasPrice ? "en Steam" : "Ver en Steam") : "Ver ficha en IGDB";
      card.querySelector(".store-name").textContent = name;
      store.setAttribute(
        "aria-label",
        `${rec.title}: ${hasPrice ? `${price.textContent} ` : ""}${name}, se abre en otra pestaña`,
      );
    } else {
      store.remove();
    }
    fillCover(card, rec, href);

    card.querySelectorAll("[data-vote]").forEach((btn) => {
      const up = btn.dataset.vote === "1";
      btn.setAttribute("aria-label", `${up ? "Me interesa" : "No me interesa"} ${rec.title}`);
      btn.title = up ? "Me interesa" : "No me interesa";
      btn.addEventListener("click", () => sendVote(rec.title, Number(btn.dataset.vote), card));
    });
    return card;
  });
  list.classList.remove("is-revealed");
  list.replaceChildren(...rows);
  void list.offsetWidth; // reinicia la animación de entrada en cada búsqueda
  list.classList.add("is-revealed");
}

function emptyActions() {
  const actions = [];
  if (mode === "games") {
    const alt = FEATURED_GAMES.map((t) => catalog.byKey.get(normalize(t))).find((t) => t && !games.has(t));
    if (alt) {
      actions.push({
        label: `Probar con ${alt}`,
        onClick: () => {
          games.add(alt);
          $("form").requestSubmit();
        },
      });
    }
  } else if (tags.values.length > 1) {
    actions.push({
      label: `Quitar ${labelEs(tags.values[tags.values.length - 1])}`,
      onClick: () => {
        tags.remove(tags.values.length - 1);
        $("form").requestSubmit();
      },
    });
  }
  return actions;
}

async function runSearch(body) {
  lastBody = body;
  const submit = $("submit");
  submit.disabled = true;
  submit.classList.add("is-busy");
  submit.textContent = "Buscando…";
  $("results-section").hidden = false;
  $("results-summary").textContent = describeQuery(body);
  hideNotice();
  setStatus("Buscando juegos parecidos…");
  showSkeleton(body.limit);
  $("results-section").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const resp = await fetch("/recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) {
      $("results").replaceChildren();
      showNotice(
        resp.status === 400
          ? "Ninguno de esos juegos o temas está en el catálogo. Elige juegos de la lista de sugerencias o prueba con otros temas."
          : "El recomendador respondió con un error. Inténtalo de nuevo en unos segundos.",
        resp.status === 400 ? emptyActions() : [{ label: "Reintentar", iconName: "retry", onClick: () => runSearch(lastBody) }],
      );
      return;
    }
    if (!data.length) {
      $("results").replaceChildren();
      showNotice("Ningún juego del catálogo se parece lo suficiente a esta combinación. Quita un tema o prueba con otro juego.", emptyActions());
      return;
    }
    renderResults(data, !body.liked_games.length);
    showScale(body);
    renderPriceNote();
    $("votes-note").hidden = false;
    setStatus(`${data.length} ${data.length === 1 ? "juego" : "juegos"}, del más al menos parecido.`);
    loadStats();
  } catch {
    $("results").replaceChildren();
    showNotice("No hay conexión con la API. Revisa que el servidor esté corriendo e inténtalo de nuevo.", [
      { label: "Reintentar", iconName: "retry", primary: true, onClick: () => runSearch(lastBody) },
    ]);
  } finally {
    $("results").removeAttribute("aria-busy");
    submit.disabled = false;
    submit.classList.remove("is-busy");
    submit.textContent = "Recomendar";
  }
}

$("form").addEventListener("submit", (event) => {
  event.preventDefault();
  const field = activeField();
  if (!field.commitTyped()) {
    field.input.focus();
    return;
  }
  const body = {
    liked_games: mode === "games" ? [...games.values] : [],
    tags: mode === "tags" ? [...tags.values] : [],
    limit: Number($("limit").value),
  };
  if (!body.liked_games.length && !body.tags.length) {
    field.setHint(mode === "games" ? "Agrega al menos un juego que te haya gustado." : "Agrega al menos un género o tema.");
    field.input.focus();
    return;
  }
  runSearch(body);
});

/* ---------- Detalles ---------- */

const topbar = document.querySelector(".topbar");
const onScroll = () => topbar.classList.toggle("is-scrolled", window.scrollY > 8);
window.addEventListener("scroll", onScroll, { passive: true });
onScroll();

loadCatalog();
loadStats();
