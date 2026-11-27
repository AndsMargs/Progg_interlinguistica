const famiglie = [
  ["complessita_linguistica", "Complessità linguistica"],
  ["complessita_procedurale", "Complessità procedurale"],
];

const CENTRO_X = 100;
const CENTRO_Y = 104;
const RAGGIO = 78;
const LUNGHEZZA_ARCO = Math.PI * RAGGIO;

const stato = {
  analisi: null,
  aspetto: null,
  vista: "documento",
  percorso: null,
  fase: null,
  termine: null,
  pdf: null,
  pagina: 1,
  totalePagine: 0,
  compito: null,
  disegno: 0,
  topicSegno: 0,
};

pdfjsLib.GlobalWorkerOptions.workerSrc = "/pdfjs/pdf.worker.min.js";

const documentoEl = document.querySelector("#documento");
const famiglieEl = document.querySelector("#famiglie");
const fasiEl = document.querySelector("#fasi");
const layoutEl = document.querySelector("#layout");
const archivioEl = document.querySelector("#archivio");
const barraEl = document.querySelector("#barra");
const statoTesto = document.querySelector("#stato-testo");
const statoBarra = document.querySelector("#stato-barra");

function livello(punteggio) {
  if (punteggio == null || Number.isNaN(punteggio)) return "In attesa";
  if (punteggio <= 0.2) return "Per niente comprensibile";
  if (punteggio <= 0.4) return "Poco comprensibile";
  if (punteggio <= 0.6) return "Moderatamente comprensibile";
  if (punteggio <= 0.8) return "Abbastanza comprensibile";
  return "Molto comprensibile";
}

function colore(punteggio) {
  if (punteggio == null) return "#b0b0b0";
  if (punteggio <= 0.2) return "#c2212a";
  if (punteggio <= 0.4) return "#cf4500";
  if (punteggio <= 0.6) return "#f29300";
  if (punteggio <= 0.8) return "#76b72a";
  return "#2f6b22";
}

function escapa(testo) {
  return String(testo)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function formato(numero) {
  return Number(numero).toFixed(2).replace(".", ",");
}

function medianaIntensita(aspetto) {
  const centesimi = (aspetto.estratti || [])
    .map((voce) => Number(voce.punteggio))
    .filter((valore) => Number.isFinite(valore))
    .map((valore) => Math.round(valore * 100))
    .sort((a, b) => a - b);
  if (!centesimi.length) return aspetto.punteggio;
  const mezzo = Math.floor(centesimi.length / 2);
  if (centesimi.length % 2) return centesimi[mezzo] / 100;
  return Math.round((centesimi[mezzo - 1] + centesimi[mezzo]) / 2) / 100;
}

function tachimetro(aspetto) {
  const punteggio = medianaIntensita(aspetto);
  const valore = punteggio == null ? 0 : Math.min(1, Math.max(0, punteggio));
  const angolo = -90 + valore * 180;
  const resto = (LUNGHEZZA_ARCO * (1 - valore)).toFixed(2);
  const tinta = colore(punteggio);
  const etichetta = livello(punteggio);
  return `
    <button class="tachimetro" type="button" data-aspetto="${escapa(aspetto.id)}" aria-expanded="false">
      <svg viewBox="0 0 200 124" aria-hidden="true">
        <path d="M22 104 A78 78 0 0 1 178 104" fill="none" stroke="#e6e6e6" stroke-width="14"></path>
        <path class="arco" d="M22 104 A78 78 0 0 1 178 104" fill="none" stroke="${tinta}" stroke-width="14"
          stroke-dasharray="${LUNGHEZZA_ARCO.toFixed(2)}" stroke-dashoffset="${LUNGHEZZA_ARCO.toFixed(2)}" data-offset="${resto}"></path>
        <g class="ago" data-angolo="${angolo.toFixed(2)}" transform="rotate(-90 ${CENTRO_X} ${CENTRO_Y})">
          <line x1="${CENTRO_X}" y1="${CENTRO_Y}" x2="${CENTRO_X}" y2="44" stroke="#333" stroke-width="3"></line>
        </g>
        <circle cx="${CENTRO_X}" cy="${CENTRO_Y}" r="7" fill="#312783"></circle>
      </svg>
      <p class="valore">${punteggio == null ? "—" : formato(punteggio)}</p>
      <p class="nome-aspetto">${escapa(aspetto.nome)}</p>
      <p class="livello" style="color:${tinta}">${escapa(etichetta)}</p>
      <p class="stato-aperto">Aperto</p>
    </button>`;
}

function animaQuadranti(radice = document) {
  const inizio = performance.now();
  const durata = 1100;
  const aghi = [...radice.querySelectorAll(".ago")];
  const archi = [...radice.querySelectorAll(".arco")];
  function passo(ora) {
    const t = Math.min(1, (ora - inizio) / durata);
    const fluido = 1 - (1 - t) ** 3;
    aghi.forEach((ago) => {
      const angolo = -90 + (Number(ago.dataset.angolo) + 90) * fluido;
      ago.setAttribute("transform", `rotate(${angolo} ${CENTRO_X} ${CENTRO_Y})`);
    });
    archi.forEach((arco) => {
      const pieno = Number(arco.getAttribute("stroke-dasharray"));
      const meta = Number(arco.dataset.offset);
      arco.setAttribute("stroke-dashoffset", String(pieno + (meta - pieno) * fluido));
    });
    if (t < 1) requestAnimationFrame(passo);
  }
  requestAnimationFrame(passo);
}

const BLU_IULM = [0, 58, 112];
const CELESTE = [126, 214, 245];

function miscelaColore(quota) {
  const canale = (indice) => Math.round(BLU_IULM[indice] + (CELESTE[indice] - BLU_IULM[indice]) * quota);
  return `rgb(${canale(0)}, ${canale(1)}, ${canale(2)})`;
}

function stileBlocco(indice, totale) {
  const inizio = totale <= 1 ? 0 : indice / totale;
  const fine = totale <= 1 ? 1 : (indice + 1) / totale;
  const mezzo = (inizio + fine) / 2;
  return {
    sfondo: `linear-gradient(90deg, ${miscelaColore(inizio)}, ${miscelaColore(fine)})`,
    inchiostro: mezzo > 0.62 ? "#003a70" : "#ffffff",
  };
}

function rettangolo(numero, nome, corpo, extra) {
  const tinta = stileBlocco(numero - 1, extra.totale);
  const info = extra.info
    ? `<button class="info-fase" type="button" aria-expanded="false" aria-label="Cosa vogliono dire queste parole">i</button>`
    : "";
  return `
    <section class="blocco" style="--sfondo:${tinta.sfondo};--inchiostro-blocco:${tinta.inchiostro}">
      <div class="blocco-barra">
        <button class="blocco-apri" type="button" aria-expanded="false">
          ${extra.numero === false ? "" : `<span class="blocco-numero">${numero}</span>`}
          <span class="blocco-nome">${escapa(nome)}</span>
        </button>
        ${info}
      </div>
      <div class="blocco-corpo" hidden>${corpo}</div>
    </section>`;
}

function alternaBlocco(barra) {
  const blocco = barra.closest(".blocco");
  const corpo = blocco.querySelector(".blocco-corpo");
  const pila = blocco.parentElement;
  const apre = corpo.hidden;
  pila.querySelectorAll(".blocco").forEach((altro) => {
    altro.querySelector(".blocco-corpo").hidden = true;
    altro.querySelector(".blocco-apri").setAttribute("aria-expanded", "false");
    altro.classList.remove("aperto");
    const scheda = altro.querySelector(".scheda-termini");
    const info = altro.querySelector(".info-fase");
    if (scheda) scheda.hidden = true;
    if (info) {
      info.classList.remove("aperto");
      info.setAttribute("aria-expanded", "false");
    }
  });
  if (!apre) return;
  corpo.hidden = false;
  barra.setAttribute("aria-expanded", "true");
  blocco.classList.add("aperto");
  animaQuadranti(corpo);
}

function disegnaIndicatori() {
  famiglieEl.innerHTML = `<div class="pila">${famiglie.map(([id, titolo], indice) => {
    const voci = stato.analisi.aspetti.filter((voce) => voce.famiglia === id);
    const corpo = `<div class="griglia">${voci.map(tachimetro).join("")}</div>`;
    return rettangolo(indice + 1, titolo, corpo, { totale: famiglie.length, numero: false });
  }).join("")}</div>`;
}

function quadranteTermine(termine, faseId) {
  const punteggio = termine.punteggio;
  const valore = punteggio == null ? 0 : Math.min(1, Math.max(0, punteggio));
  const angolo = -90 + valore * 180;
  const resto = (LUNGHEZZA_ARCO * (1 - valore)).toFixed(2);
  const tinta = colore(punteggio);
  return `
    <button class="tachimetro" type="button" data-fase="${escapa(faseId)}" data-termine="${escapa(termine.id)}" aria-expanded="false">
      <svg viewBox="0 0 200 124" aria-hidden="true">
        <path d="M22 104 A78 78 0 0 1 178 104" fill="none" stroke="#e6e6e6" stroke-width="14"></path>
        <path class="arco" d="M22 104 A78 78 0 0 1 178 104" fill="none" stroke="${tinta}" stroke-width="14"
          stroke-dasharray="${LUNGHEZZA_ARCO.toFixed(2)}" stroke-dashoffset="${LUNGHEZZA_ARCO.toFixed(2)}" data-offset="${resto}"></path>
        <g class="ago" data-angolo="${angolo.toFixed(2)}" transform="rotate(-90 ${CENTRO_X} ${CENTRO_Y})">
          <line x1="${CENTRO_X}" y1="${CENTRO_Y}" x2="${CENTRO_X}" y2="44" stroke="#333" stroke-width="3"></line>
        </g>
        <circle cx="${CENTRO_X}" cy="${CENTRO_Y}" r="7" fill="#312783"></circle>
      </svg>
      <p class="valore">${punteggio == null ? "—" : formato(punteggio)}</p>
      <p class="nome-aspetto">${escapa(termine.nome_breve || termine.termine)}</p>
      <p class="livello" style="color:${tinta}">${escapa(livello(punteggio))}</p>
      <p class="stato-aperto">Aperto</p>
    </button>`;
}

function schedaTermini(termini, attivo) {
  return (termini || []).map((termine) => {
    const condizioni = (termine.condizioni || []).map((voce) => `<li>${escapa(voce)}</li>`).join("");
    const classe = termine.id === attivo ? " in-vista" : "";
    return `
      <dt class="${classe.trim()}">${escapa(termine.termine)}</dt>
      <dd>${escapa(termine.definizione || "")}</dd>
      ${condizioni ? `<dd><ul class="condizioni">${condizioni}</ul></dd>` : ""}`;
  }).join("");
}

function chiudiTermine() {
  document.querySelectorAll("#fasi .tachimetro").forEach((nodo) => {
    nodo.classList.remove("selezionato");
    nodo.setAttribute("aria-expanded", "false");
  });
  stato.termine = null;
  stato.fase = null;
  const dettaglio = document.querySelector("#dettaglio-termine");
  const spiegazione = document.querySelector("#spiegazione-percorso");
  if (dettaglio) {
    dettaglio.hidden = true;
    dettaglio.replaceChildren();
  }
  if (spiegazione) spiegazione.hidden = false;
}

function mostraTermine(faseId, termineId) {
  if (stato.fase === faseId && stato.termine === termineId) {
    chiudiTermine();
    return;
  }
  const fase = (stato.percorso?.fasi || []).find((voce) => voce.id === faseId);
  const termine = fase && (fase.termini || []).find((voce) => voce.id === termineId);
  const bottone = document.querySelector(`#fasi .tachimetro[data-termine="${termineId}"]`);
  if (!fase || !termine || !bottone) return;
  document.querySelectorAll("#fasi .tachimetro").forEach((nodo) => {
    nodo.classList.remove("selezionato");
    nodo.setAttribute("aria-expanded", "false");
  });
  stato.fase = faseId;
  stato.termine = termineId;
  bottone.classList.add("selezionato");
  bottone.setAttribute("aria-expanded", "true");
  const condizioni = (termine.condizioni || []).map((voce) => `<li>${escapa(voce)}</li>`).join("");
  const altri = (fase.termini || []).filter((voce) => voce.id !== termineId);
  const pannello = document.createElement("article");
  pannello.className = "scheda";
  pannello.innerHTML = `
    <div class="azioni-scheda">
      <button class="chiudi" type="button">Chiudi</button>
    </div>
    <p class="occhi">Stai guardando</p>
    <h3>${escapa(termine.termine)}</h3>
    <p class="verdetto" style="color:${colore(termine.punteggio)}">${escapa(livello(termine.punteggio))} · ${termine.punteggio == null ? "—" : formato(termine.punteggio)}</p>
    <p>${escapa(termine.definizione || "Questo testo non spiega questa parola.")}</p>
    ${condizioni ? `<ul class="condizioni">${condizioni}</ul>` : ""}
    <p class="occhi resto-passo">Le parole di ${escapa(fase.nome)}</p>
    <dl class="scheda-termini">${schedaTermini(fase.termini, termineId)}</dl>
  `;
  if (!altri.length) {
    pannello.querySelector(".resto-passo")?.remove();
    pannello.querySelector(".scheda-termini")?.remove();
  }
  const dettaglio = document.querySelector("#dettaglio-termine");
  dettaglio.replaceChildren(pannello);
  dettaglio.hidden = false;
  document.querySelector("#spiegazione-percorso").hidden = true;
  document.querySelector("#vista-percorso .lato-spiegazione").scrollTop = 0;
}

function disegnaPercorso(percorso) {
  stato.percorso = percorso;
  chiudiTermine();
  const fasi = (percorso && percorso.fasi) || [];
  if (!fasi.length) {
    fasiEl.innerHTML = `<p class="manca-fase">Questo testo non ha ancora i passaggi della procedura.</p>`;
    return;
  }
  fasiEl.innerHTML = `<div class="pila">${fasi.map((fase, indice) => {
    const assegnati = (fase.termini || []).filter((termine) => termine.frasi > 0 && termine.punteggio != null);
    const quadranti = assegnati.length
      ? `<div class="griglia">${assegnati.map((termine) => quadranteTermine(termine, fase.id)).join("")}</div>`
      : `<p class="manca-fase">In questo testo non ho trovato questo passaggio.</p>`;
    return rettangolo(indice + 1, fase.nome, quadranti, { totale: fasi.length, numero: true });
  }).join("")}</div>`;
}

function preparaSegni(estratti) {
  return (estratti || [])
    .map((voce, indice) => ({ ...voce, indice }))
    .filter((voce) => Number.isInteger(voce.inizio) && Number.isInteger(voce.fine) && voce.fine > voce.inizio)
    .sort((a, b) => a.inizio - b.inizio || a.fine - b.fine);
}

function htmlNelMezzo(testo, da, a, segni, gia) {
  const utili = segni.filter((segno) => segno.fine > da && segno.inizio < a);
  if (!utili.length) return escapa(testo.slice(da, a));
  let html = "";
  let cursore = da;
  utili.forEach((segno) => {
    const inizio = Math.max(cursore, segno.inizio);
    const fine = Math.min(a, segno.fine);
    if (fine <= inizio) return;
    if (inizio > cursore) html += escapa(testo.slice(cursore, inizio));
    const classe = segno.effetto === "riduce" ? "peggio" : "meglio";
    const id = gia.has(segno.indice) ? "" : ` id="segno-${segno.indice}"`;
    gia.add(segno.indice);
    html += `<mark class="${classe}"${id}>${escapa(testo.slice(inizio, fine))}</mark>`;
    cursore = fine;
  });
  if (cursore < a) html += escapa(testo.slice(cursore, a));
  return html;
}

function urlSicuro(url) {
  const pulito = url.trim();
  if (/^(https?:|mailto:)/i.test(pulito)) return escapa(pulito);
  return "";
}

function inline(testo, da, a, segni, gia) {
  let html = "";
  let i = da;
  while (i < a) {
    if (testo.startsWith("**", i) || testo.startsWith("__", i)) {
      const marca = testo.slice(i, i + 2);
      const fine = testo.indexOf(marca, i + 2);
      if (fine !== -1 && fine < a) {
        html += `<strong>${inline(testo, i + 2, fine, segni, gia)}</strong>`;
        i = fine + 2;
        continue;
      }
    }
    if (testo[i] === "`") {
      const fine = testo.indexOf("`", i + 1);
      if (fine !== -1 && fine < a) {
        html += `<code>${htmlNelMezzo(testo, i + 1, fine, segni, gia)}</code>`;
        i = fine + 1;
        continue;
      }
    }
    if (testo[i] === "[") {
      const chiudi = testo.indexOf("]", i + 1);
      if (chiudi !== -1 && chiudi < a && testo[chiudi + 1] === "(") {
        const fineUrl = testo.indexOf(")", chiudi + 2);
        if (fineUrl !== -1 && fineUrl < a) {
          const href = urlSicuro(testo.slice(chiudi + 2, fineUrl));
          const corpo = inline(testo, i + 1, chiudi, segni, gia);
          html += href ? `<a href="${href}">${corpo}</a>` : corpo;
          i = fineUrl + 1;
          continue;
        }
      }
    }
    let j = i + 1;
    while (j < a && !"*_`[".includes(testo[j])) j += 1;
    html += htmlNelMezzo(testo, i, j, segni, gia);
    i = j;
  }
  return html;
}

function righeDocumento(testo) {
  const righe = [];
  let i = 0;
  while (i <= testo.length) {
    const fine = testo.indexOf("\n", i);
    const a = fine === -1 ? testo.length : fine;
    righe.push({ da: i, a, dopo: fine === -1 ? testo.length : fine + 1 });
    if (fine === -1) break;
    i = fine + 1;
  }
  return righe;
}

function disegnaDocumento(estratti = []) {
  const testo = stato.analisi.documento || "";
  const segni = preparaSegni(estratti);
  const gia = new Set();
  const righe = righeDocumento(testo);
  let html = "";
  let i = 0;

  function paragrafo(blocco) {
    const corpo = blocco.map((riga, indice) => {
      const pezzo = inline(testo, riga.da, riga.a, segni, gia);
      return indice ? ` ${pezzo}` : pezzo;
    }).join("");
    html += `<p>${corpo}</p>`;
  }

  while (i < righe.length) {
    const riga = righe[i];
    const contenuto = testo.slice(riga.da, riga.a);
    if (!contenuto.trim()) {
      i += 1;
      continue;
    }
    const titolo = /^(#{1,6})[ \t]+(.*)$/.exec(contenuto);
    if (titolo) {
      const livelloTitolo = titolo[1].length;
      let da = riga.da + titolo[1].length;
      while (da < riga.a && (testo[da] === " " || testo[da] === "\t")) da += 1;
      html += `<h${livelloTitolo}>${inline(testo, da, riga.a, segni, gia)}</h${livelloTitolo}>`;
      i += 1;
      continue;
    }
    if (/^[-*][ \t]+/.test(contenuto)) {
      html += "<ul>";
      while (i < righe.length && /^[-*][ \t]+/.test(testo.slice(righe[i].da, righe[i].a))) {
        const voce = testo.slice(righe[i].da, righe[i].a);
        const spazi = /^[-*][ \t]+/.exec(voce)[0].length;
        html += `<li>${inline(testo, righe[i].da + spazi, righe[i].a, segni, gia)}</li>`;
        i += 1;
      }
      html += "</ul>";
      continue;
    }
    if (/^\d+\.[ \t]+/.test(contenuto)) {
      html += "<ol>";
      while (i < righe.length && /^\d+\.[ \t]+/.test(testo.slice(righe[i].da, righe[i].a))) {
        const voce = testo.slice(righe[i].da, righe[i].a);
        const spazi = /^\d+\.[ \t]+/.exec(voce)[0].length;
        html += `<li>${inline(testo, righe[i].da + spazi, righe[i].a, segni, gia)}</li>`;
        i += 1;
      }
      html += "</ol>";
      continue;
    }
    const blocco = [riga];
    i += 1;
    while (i < righe.length && testo.slice(righe[i].da, righe[i].a).trim() && !/^(#{1,6})[ \t]+/.test(testo.slice(righe[i].da, righe[i].a)) && !/^[-*][ \t]+/.test(testo.slice(righe[i].da, righe[i].a)) && !/^\d+\.[ \t]+/.test(testo.slice(righe[i].da, righe[i].a))) {
      blocco.push(righe[i]);
      i += 1;
    }
    paragrafo(blocco);
  }
  documentoEl.innerHTML = html;
}

const PALETTE_NUBE = [
  "#312783",
  "#e23b4a",
  "#00a3d9",
  "#6aaf22",
  "#f29300",
  "#cf4500",
  "#7b4fd6",
  "#0e8a7d",
  "#d63d8a",
  "#2f6b22",
  "#3d6fd8",
  "#c9a227",
];

function paletteMescolata() {
  const colori = [...PALETTE_NUBE];
  for (let indice = colori.length - 1; indice > 0; indice -= 1) {
    const altro = Math.floor(Math.random() * (indice + 1));
    [colori[indice], colori[altro]] = [colori[altro], colori[indice]];
  }
  return colori;
}

function chiudiNube() {
  const nube = document.querySelector("#nube");
  if (!nube || nube.hidden) return;
  nube.hidden = true;
  stato.topicSegno += 1;
  document.querySelector("#nube-parole").innerHTML = "";
  document.querySelector("#nube-topic").innerHTML = "";
}

function siSovrappongono(a, b) {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

function trovaPosto(w, h, larghezza, altezza, occupati) {
  const margine = 7;
  for (let passo = 0; passo < 2800; passo += 1) {
    const angolo = passo * 0.28;
    const raggio = passo * 0.42;
    const x = larghezza / 2 + Math.cos(angolo) * raggio - w / 2;
    const y = altezza / 2 + Math.sin(angolo) * raggio * 0.72 - h / 2;
    if (x < 2 || y < 2 || x + w > larghezza - 2 || y + h > altezza - 2) continue;
    const box = { x: x - margine, y: y - margine, w: w + margine * 2, h: h + margine * 2 };
    if (occupati.every((altro) => !siSovrappongono(box, altro))) return { x, y, box };
  }
  return null;
}

function disegnaNube(voci) {
  const area = document.querySelector("#nube-parole");
  area.innerHTML = "";
  area.style.height = "";
  if (!voci.length) {
    area.innerHTML = `<p class="nube-vuota">In questo testo non ho trovato parole che richiamano il concetto.</p>`;
    return;
  }
  const ordinate = [...voci].sort((a, b) => b.frequenza - a.frequenza || b.parola.length - a.parola.length);
  const massimo = ordinate[0].frequenza;
  const minimo = ordinate[ordinate.length - 1].frequenza;
  const colori = paletteMescolata();
  const elementi = ordinate.map((voce, indice) => {
    const span = document.createElement("span");
    span.className = "nube-parola";
    span.textContent = voce.parola;
    const peso = massimo === minimo ? 1 : (voce.frequenza - minimo) / (massimo - minimo);
    span.style.fontSize = `${15 + peso * 42}px`;
    span.style.color = colori[indice % colori.length];
    span.style.setProperty("--attesa", `${indice * 42}ms`);
    span.style.setProperty("--giro", `${(indice % 2 ? 8 : -8)}deg`);
    span.title = `${voce.frequenza} volte`;
    area.appendChild(span);
    return span;
  });
  const sistema = () => {
    const larghezza = area.clientWidth || 640;
    let altezza = area.clientHeight || 460;
    const occupati = [];
    elementi.forEach((span) => {
      let taglia = parseFloat(span.style.fontSize);
      while (span.offsetWidth > larghezza - 16 && taglia > 14) {
        taglia -= 1;
        span.style.fontSize = `${taglia}px`;
      }
      let posto = trovaPosto(span.offsetWidth, span.offsetHeight, larghezza, altezza, occupati);
      while (!posto && taglia > 14) {
        taglia -= 1;
        span.style.fontSize = `${taglia}px`;
        posto = trovaPosto(span.offsetWidth, span.offsetHeight, larghezza, altezza, occupati);
      }
      if (!posto) {
        const w = span.offsetWidth;
        const h = span.offsetHeight;
        const fondo = occupati.reduce((max, box) => Math.max(max, box.y + box.h), 8);
        const x = Math.max(2, Math.min((larghezza - w) / 2, larghezza - w - 2));
        const y = fondo + 4;
        altezza = Math.max(altezza, y + h + 10);
        area.style.height = `${altezza}px`;
        posto = { x, y, box: { x: x - 7, y: y - 7, w: w + 14, h: h + 14 } };
      }
      occupati.push(posto.box);
      span.style.left = `${posto.x}px`;
      span.style.top = `${posto.y}px`;
      span.classList.add("entra");
    });
  };
  const quandoPronto = () => requestAnimationFrame(() => requestAnimationFrame(sistema));
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(quandoPronto);
  else quandoPronto();
}

function htmlTopic(topic) {
  const voci = ((topic && topic.voci) || []).filter((voce) => voce.visibile !== false && voce.etichetta);
  if (!voci.length) {
    return `<p class="nube-vuota">Con queste parole non escono gruppi abbastanza chiari.</p>`;
  }
  const blocchi = voci.map((voce) => `
    <section class="topic-voce">
      <h4>${escapa(voce.etichetta)}</h4>
      ${voce.descrizione ? `<p class="topic-desc">${escapa(voce.descrizione)}</p>` : ""}
      <ul>${(voce.parole || []).map((parola) => `<li>${escapa(parola)}</li>`).join("")}</ul>
    </section>`).join("");
  return `<div class="topic-elenco">${blocchi}</div>`;
}

async function disegnaTopic(aspetto) {
  const box = document.querySelector("#nube-topic");
  const segno = ++stato.topicSegno;
  const salvato = aspetto.concetto && aspetto.concetto.topic;
  if (salvato && salvato.versione && !salvato.errore) {
    box.innerHTML = htmlTopic(salvato);
    return;
  }
  box.innerHTML = `<p class="nube-vuota">Sto raggruppando le parole…</p>`;
  try {
    const risposta = await fetch(
      `/api/documenti/${encodeURIComponent(stato.analisi.id)}/topic/${encodeURIComponent(aspetto.id)}`,
      { method: "POST" }
    );
    const dati = await risposta.json();
    if (segno !== stato.topicSegno) return;
    if (!risposta.ok) throw new Error(dati.errore || "Non riesco a preparare i topic.");
    aspetto.concetto = aspetto.concetto || {};
    aspetto.concetto.topic = dati.topic;
    box.innerHTML = htmlTopic(dati.topic);
  } catch (errore) {
    if (segno !== stato.topicSegno) return;
    const testo = errore && errore.message ? errore.message : "Non riesco a preparare i topic.";
    box.innerHTML = `<p class="nube-vuota">${escapa(testo)}</p>`;
  }
}

function apriNube() {
  const aspetto = stato.analisi?.aspetti.find((voce) => voce.id === stato.aspetto);
  if (!aspetto) return;
  const nube = document.querySelector("#nube");
  const pannello = nube.querySelector(".nube-pannello");
  document.querySelector("#nube-titolo").textContent = aspetto.nome;
  nube.hidden = false;
  const larghezza = Math.min(window.innerWidth - 40, 1040);
  pannello.style.width = `${larghezza}px`;
  pannello.style.left = `${Math.max(16, (window.innerWidth - larghezza) / 2)}px`;
  pannello.style.top = "72px";
  disegnaNube(((aspetto.concetto && aspetto.concetto.aggregato) || []).slice(0, 28));
  disegnaTopic(aspetto);
}

function chiudiTesto() {
  const finestra = document.querySelector("#finestra-testo");
  const colonna = document.querySelector(".colonna-testo");
  if (colonna && documentoEl.parentElement !== colonna) colonna.appendChild(documentoEl);
  if (finestra) finestra.hidden = true;
  document.body.classList.remove("testo-aperto");
}

function apriTesto(indice) {
  const finestra = document.querySelector("#finestra-testo");
  document.querySelector("#finestra-corpo").appendChild(documentoEl);
  document.querySelector("#finestra-titolo").textContent = "Nel testo";
  finestra.hidden = false;
  document.body.classList.add("testo-aperto");
  const segno = indice == null ? null : documentoEl.querySelector(`#segno-${indice}`);
  documentoEl.querySelectorAll("mark").forEach((nodo) => nodo.classList.remove("attivo"));
  if (!segno) {
    documentoEl.scrollTop = 0;
    return;
  }
  segno.classList.add("attivo");
  requestAnimationFrame(() => segno.scrollIntoView({ block: "center" }));
}

function chiudiScheda() {
  chiudiNube();
  chiudiTesto();
  document.querySelector(".scheda")?.remove();
  document.querySelectorAll(".tachimetro").forEach((nodo) => {
    nodo.classList.remove("selezionato");
    nodo.setAttribute("aria-expanded", "false");
  });
  stato.aspetto = null;
  const dettaglio = document.querySelector("#dettaglio-aspetto");
  const spiegazione = document.querySelector("#spiegazione-complessita");
  if (dettaglio) {
    dettaglio.hidden = true;
    dettaglio.replaceChildren();
  }
  if (spiegazione) spiegazione.hidden = false;
  if (stato.analisi) disegnaDocumento();
}

function mostraDettaglio(id) {
  chiudiNube();
  chiudiTesto();
  if (stato.aspetto === id) {
    chiudiScheda();
    return;
  }
  const aspetto = stato.analisi.aspetti.find((voce) => voce.id === id);
  const bottone = document.querySelector(`.tachimetro[data-aspetto="${id}"]`);
  if (!aspetto || !bottone) return;
  document.querySelector(".scheda")?.remove();
  document.querySelectorAll(".tachimetro").forEach((nodo) => {
    nodo.classList.remove("selezionato");
    nodo.setAttribute("aria-expanded", "false");
  });
  stato.aspetto = id;
  bottone.classList.add("selezionato");
  bottone.setAttribute("aria-expanded", "true");
  disegnaDocumento(aspetto.estratti || []);
  const quantita = aspetto.quantitativo || {};
  const punteggio = medianaIntensita(aspetto);
  const etichetta = livello(punteggio);
  const estratti = (aspetto.estratti || []).map((estratto, indice) => {
    const voto = estratto.punteggio == null ? "" : `
      <span class="estratto-voto" style="color:${colore(estratto.punteggio)}" title="${escapa(livello(estratto.punteggio))}">
        ${formato(estratto.punteggio)}
      </span>`;
    return `
    <button class="estratto-btn ${estratto.effetto}" type="button" data-segno="${indice}">
      <span class="estratto-corpo">
        <strong>${estratto.effetto === "riduce" ? "Questa frase lo rende più difficile" : "Questa frase lo rende più chiaro"}</strong>
        <span>${escapa(estratto.perche || "")}</span>
      </span>
      ${voto}
    </button>`;
  }).join("");
  const motivi = (aspetto.motivazioni || []).map((voce) => `<li>${escapa(voce)}</li>`).join("");
  const scheda = document.createElement("article");
  scheda.className = "scheda";
  scheda.innerHTML = `
    <div class="azioni-scheda">
      <button class="mostra-concetto" type="button">Mostra Concetto</button>
      <button class="chiudi" type="button">Chiudi</button>
    </div>
    <p class="occhi">Stai guardando</p>
    <h3>${escapa(aspetto.nome)}</h3>
    <p class="verdetto" style="color:${colore(punteggio)}">${escapa(etichetta)} · ${punteggio == null ? "—" : formato(punteggio)}</p>
    <p class="domanda">${escapa(aspetto.cosa_si_potra_osservare || "")}</p>
    <p>${escapa(aspetto.giudizio || aspetto.errore || "Giudizio non disponibile.")}</p>
    ${motivi ? `<ul>${motivi}</ul>` : ""}
    ${estratti || "<p>Nessuna frase del testo è stata agganciata a questo giudizio.</p>"}
    <p class="conti">Nel testo compaiono ${quantita.concetti_presenti ?? 0} parole del glossario su ${quantita.concetti_collegati ?? 0}, per ${quantita.occorrenze_termini ?? 0} volte. Il giudizio usa ${quantita.estratti_validi ?? (aspetto.estratti || []).length} frasi.</p>
  `;
  const dettaglio = document.querySelector("#dettaglio-aspetto");
  const spiegazione = document.querySelector("#spiegazione-complessita");
  dettaglio.replaceChildren(scheda);
  dettaglio.hidden = false;
  spiegazione.hidden = true;
  document.querySelector("#lato-spiegazione").scrollTop = 0;
}

function mostraAnalisi(analisi) {
  chiudiNube();
  chiudiTesto();
  stato.analisi = analisi;
  stato.aspetto = null;
  document.querySelector("#dettaglio-aspetto").hidden = true;
  document.querySelector("#dettaglio-aspetto").replaceChildren();
  document.querySelector("#spiegazione-complessita").hidden = false;
  document.querySelector("#nome-doc").textContent = (analisi.nome_file || analisi.id || "Documento").replace(/\.md$/i, "");
  disegnaDocumento();
  disegnaIndicatori();
  applicaVista();
}

async function caricaElenco(selezionato) {
  const risposta = await fetch("/api/documenti");
  const dati = await risposta.json();
  const documenti = (dati.documenti || []).filter((voce) => voce.nome_pdf);
  archivioEl.innerHTML = documenti.map((voce) => (
    `<option value="${escapa(voce.id)}">${escapa(voce.nome_pdf)}</option>`
  )).join("");
  if (selezionato) archivioEl.value = selezionato;
}

function applicaVista() {
  const sulDocumento = stato.vista === "documento";
  document.querySelector("#lettore").hidden = !sulDocumento;
  layoutEl.hidden = sulDocumento || !stato.analisi;
  document.querySelector("#vista-chiarezza").hidden = stato.vista !== "chiarezza";
  document.querySelector("#vista-percorso").hidden = stato.vista !== "percorso";
  layoutEl.classList.toggle("senza-testo", stato.vista === "chiarezza" || stato.vista === "percorso");
  document.querySelector("#tab-chiarezza").classList.toggle("attiva", stato.vista === "chiarezza");
  document.querySelector("#tab-percorso").classList.toggle("attiva", stato.vista === "percorso");
  document.querySelector("#tab-chiarezza").setAttribute("aria-selected", stato.vista === "chiarezza" ? "true" : "false");
  document.querySelector("#tab-percorso").setAttribute("aria-selected", stato.vista === "percorso" ? "true" : "false");
}

function mostraVista(nome) {
  chiudiNube();
  chiudiScheda();
  chiudiTermine();
  if (stato.vista === nome) nome = "documento";
  stato.vista = nome;
  if (nome !== "chiarezza") {
    documentoEl.querySelectorAll("mark.attivo").forEach((nodo) => nodo.classList.remove("attivo"));
  }
  applicaVista();
}

function pulisciTela(canvas) {
  canvas.hidden = true;
  canvas.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
}

function aggiornaNumero() {
  const nodo = document.querySelector("#numero-pagina");
  const prima = document.querySelector("#pagina-prima");
  const dopo = document.querySelector("#pagina-dopo");
  if (!stato.totalePagine) {
    nodo.textContent = "";
    prima.disabled = true;
    dopo.disabled = true;
    return;
  }
  const destra = stato.pagina > 1 && stato.pagina + 1 <= stato.totalePagine ? stato.pagina + 1 : null;
  nodo.textContent = destra ? `${stato.pagina} – ${destra}` : String(stato.pagina);
  prima.disabled = stato.pagina <= 1;
  dopo.disabled = stato.pagina <= 1
    ? stato.totalePagine <= 1
    : stato.pagina + 2 > stato.totalePagine;
}

async function disegnaFacciata(canvas, numero, segno) {
  if (!stato.pdf || numero < 1 || numero > stato.totalePagine) {
    pulisciTela(canvas);
    return;
  }
  const pagina = await stato.pdf.getPage(numero);
  if (segno !== stato.disegno) return;
  const base = pagina.getViewport({ scale: 1 });
  canvas.parentElement.style.aspectRatio = `${base.width} / ${base.height}`;
  const larghezza = canvas.parentElement.clientWidth || 420;
  const scala = (larghezza * (window.devicePixelRatio || 1)) / base.width;
  const vista = pagina.getViewport({ scale: scala });
  canvas.width = Math.floor(vista.width);
  canvas.height = Math.floor(vista.height);
  canvas.hidden = false;
  await pagina.render({ canvasContext: canvas.getContext("2d"), viewport: vista }).promise;
}

async function disegnaPagine() {
  const segno = ++stato.disegno;
  const sola = stato.pagina <= 1 || stato.pagina + 1 > stato.totalePagine;
  document.querySelector(".facciate").classList.toggle("singola", sola);
  await disegnaFacciata(document.querySelector("#tela-sinistra"), stato.pagina, segno);
  if (segno !== stato.disegno) return;
  if (sola) pulisciTela(document.querySelector("#tela-destra"));
  else await disegnaFacciata(document.querySelector("#tela-destra"), stato.pagina + 1, segno);
  if (segno !== stato.disegno) return;
  aggiornaNumero();
}

async function mostraPdf(id) {
  const messaggio = document.querySelector("#messaggio-pdf");
  if (stato.compito) {
    try { await stato.compito.destroy(); } catch (errore) { /* il documento precedente è già chiuso */ }
    stato.compito = null;
  }
  stato.pdf = null;
  stato.pagina = 1;
  stato.totalePagine = 0;
  stato.disegno += 1;
  pulisciTela(document.querySelector("#tela-sinistra"));
  pulisciTela(document.querySelector("#tela-destra"));
  aggiornaNumero();
  messaggio.hidden = false;
  messaggio.textContent = "Sto aprendo il documento…";
  const controllo = await fetch(`/api/documenti/${encodeURIComponent(id)}/pdf`, { method: "HEAD" });
  if (!controllo.ok) {
    messaggio.textContent = "Questo corpus non ha un PDF da sfogliare.";
    return;
  }
  const compito = pdfjsLib.getDocument(`/api/documenti/${encodeURIComponent(id)}/pdf`);
  stato.compito = compito;
  try {
    const documento = await compito.promise;
    if (stato.compito !== compito) return;
    stato.pdf = documento;
    stato.totalePagine = documento.numPages;
    messaggio.hidden = true;
    await disegnaPagine();
  } catch (errore) {
    if (stato.compito !== compito) return;
    messaggio.textContent = "Non riesco ad aprire questo PDF.";
  }
}

async function apri(id) {
  const risposta = await fetch(`/api/documenti/${encodeURIComponent(id)}`);
  if (!risposta.ok) return;
  mostraAnalisi(await risposta.json());
  const percorso = await fetch(`/api/documenti/${encodeURIComponent(id)}/percorso`);
  if (percorso.ok) disegnaPercorso(await percorso.json());
  else disegnaPercorso(null);
  await mostraPdf(id);
}

document.querySelector("#vista-percorso").addEventListener("click", (evento) => {
  if (evento.target.closest(".chiudi")) {
    chiudiTermine();
    return;
  }
  const barra = evento.target.closest(".blocco-apri");
  if (barra) {
    chiudiTermine();
    alternaBlocco(barra);
    return;
  }
  const bottone = evento.target.closest(".tachimetro");
  if (!bottone) return;
  mostraTermine(bottone.dataset.fase, bottone.dataset.termine);
});

function vaiAllAnalisi() {
  const corpo = document.body;
  if (!corpo.classList.contains("in-copertina") || corpo.classList.contains("in-transizione")) return;
  const riduci = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const testata = document.querySelector(".testata");
  const titolo = testata.querySelector("h1");
  const autori = document.querySelector(".autori");
  const tasto = document.querySelector("#vai-analisi");
  if (riduci) {
    corpo.classList.remove("in-copertina");
    return;
  }
  const daTitolo = titolo.getBoundingClientRect();
  const daAutori = autori.getBoundingClientRect();
  const daTasto = tasto.getBoundingClientRect();
  const altezzaInizio = testata.getBoundingClientRect().height;
  corpo.classList.remove("in-copertina");
  const aTitolo = titolo.getBoundingClientRect();
  const altezzaFine = testata.getBoundingClientRect().height;
  corpo.classList.add("in-transizione");
  testata.style.height = `${altezzaInizio}px`;
  testata.style.overflow = "hidden";
  titolo.style.width = `${daTitolo.width}px`;
  titolo.style.maxWidth = "none";
  titolo.style.textAlign = "left";
  titolo.style.transformOrigin = "top left";
  titolo.style.transform = `translate(${daTitolo.left - aTitolo.left}px, ${daTitolo.top - aTitolo.top}px)`;
  [ [autori, daAutori], [tasto, daTasto] ].forEach(([elemento, riquadro]) => {
    elemento.style.position = "fixed";
    elemento.style.left = `${riquadro.left}px`;
    elemento.style.top = `${riquadro.top}px`;
    elemento.style.width = `${riquadro.width}px`;
    elemento.style.margin = "0";
    elemento.style.zIndex = "5";
  });
  requestAnimationFrame(() => {
    testata.style.transition = "height 0.85s cubic-bezier(.4, 0, .2, 1)";
    titolo.style.transition = "transform 0.85s cubic-bezier(.4, 0, .2, 1), width 0.85s cubic-bezier(.4, 0, .2, 1)";
    autori.style.transition = "opacity 0.4s ease";
    tasto.style.transition = "opacity 0.4s ease";
    testata.style.height = `${altezzaFine}px`;
    titolo.style.transform = "translate(0, 0)";
    titolo.style.width = `${aTitolo.width}px`;
    autori.style.opacity = "0";
    tasto.style.opacity = "0";
  });
  const chiudi = (evento) => {
    if (evento && evento.target !== testata) return;
    testata.removeEventListener("transitionend", chiudi);
    testata.style.cssText = "";
    titolo.style.cssText = "";
    autori.style.cssText = "";
    tasto.style.cssText = "";
    corpo.classList.remove("in-transizione");
  };
  testata.addEventListener("transitionend", chiudi);
}

document.querySelector("#vai-analisi").addEventListener("click", vaiAllAnalisi);

document.querySelector("#tab-chiarezza").addEventListener("click", () => mostraVista("chiarezza"));
document.querySelector("#tab-percorso").addEventListener("click", () => mostraVista("percorso"));

document.querySelector("#vista-chiarezza").addEventListener("click", (evento) => {
  const barra = evento.target.closest(".blocco-apri");
  if (barra) {
    chiudiScheda();
    alternaBlocco(barra);
    return;
  }
  if (evento.target.closest(".mostra-concetto")) {
    apriNube();
    return;
  }
  const estratto = evento.target.closest("[data-segno]");
  if (estratto) {
    chiudiNube();
    document.querySelectorAll(".estratto-btn").forEach((nodo) => nodo.classList.remove("attivo"));
    estratto.classList.add("attivo");
    apriTesto(estratto.dataset.segno);
    return;
  }
  if (evento.target.closest(".chiudi")) {
    chiudiScheda();
    return;
  }
  const bottone = evento.target.closest(".tachimetro");
  if (!bottone) return;
  mostraDettaglio(bottone.dataset.aspetto);
});

document.querySelector("#chiudi-testo").addEventListener("click", chiudiTesto);
document.querySelector(".finestra-sfondo").addEventListener("click", chiudiTesto);
document.addEventListener("keydown", (evento) => {
  if (evento.key !== "Escape") return;
  const finestra = document.querySelector("#finestra-testo");
  if (finestra && !finestra.hidden) chiudiTesto();
  else chiudiNube();
});

document.addEventListener("click", (evento) => {
  const nube = document.querySelector("#nube");
  if (!nube || nube.hidden) return;
  if (evento.target.closest(".nube-pannello, .mostra-concetto")) return;
  chiudiNube();
});

archivioEl.addEventListener("change", () => apri(archivioEl.value));

document.querySelector("#pagina-prima").addEventListener("click", () => {
  if (stato.pagina <= 1) return;
  stato.pagina = stato.pagina <= 2 ? 1 : stato.pagina - 2;
  disegnaPagine();
});

document.querySelector("#pagina-dopo").addEventListener("click", () => {
  if (!stato.totalePagine) return;
  if (stato.pagina <= 1) {
    if (stato.totalePagine <= 1) return;
    stato.pagina = 2;
  } else if (stato.pagina + 2 <= stato.totalePagine) {
    stato.pagina += 2;
  } else {
    return;
  }
  disegnaPagine();
});

document.querySelector("#file").addEventListener("change", (evento) => {
  const file = evento.target.files[0];
  if (file) inviaDocumento(file);
  evento.target.value = "";
});

document.querySelector("#carica").addEventListener("submit", (evento) => {
  evento.preventDefault();
});

async function inviaDocumento(file) {
  const dati = new FormData();
  dati.append("documento", file);
  barraEl.hidden = false;
  statoTesto.textContent = "Sto leggendo il testo";
  statoBarra.style.width = "4%";
  const risposta = await fetch("/api/analizza", { method: "POST", body: dati });
  const avvio = await risposta.json();
  if (!risposta.ok) {
    statoTesto.textContent = avvio.errore || "Non sono riuscito a leggere il file";
    return;
  }
  const timer = setInterval(async () => {
    const statoRisposta = await fetch(`/api/stato/${avvio.lavoro}`);
    const lavoro = await statoRisposta.json();
    const fatti = lavoro.fatti || 0;
    const totale = lavoro.totale || 12;
    statoBarra.style.width = `${Math.max(4, Math.round((fatti / totale) * 100))}%`;
    const faseLettura = fatti > 12 ? "Sto guardando i passaggi" : "Sto leggendo il testo";
    statoTesto.textContent = lavoro.stato === "errore"
      ? lavoro.errore
      : `${faseLettura}: ${fatti} di ${totale}`;
    if (lavoro.stato === "completato") {
      clearInterval(timer);
      barraEl.hidden = true;
      await caricaElenco(lavoro.documento_id);
      await apri(lavoro.documento_id);
    }
    if (lavoro.stato === "errore") clearInterval(timer);
  }, 1500);
}

async function avvio() {
  await caricaElenco();
  const opzioni = [...archivioEl.options];
  const guida = opzioni.find((opzione) => opzione.value === "guida_pratica_2026");
  if (guida) {
    archivioEl.value = guida.value;
    await apri(guida.value);
    return;
  }
  if (opzioni.length) await apri(opzioni[0].value);
}

avvio();
