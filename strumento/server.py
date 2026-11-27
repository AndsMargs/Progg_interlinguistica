#!/usr/bin/env python3
"""Server locale: interfaccia e avvio della pipeline di analisi."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from pdf_a_markdown import pdf_a_markdown
from percorso import analizza_percorso
from pipeline import (
    RISULTATI,
    analizza_documento,
    carica_risultato,
    elenco_risultati,
    prepara_lemmatizzatore,
)

WEB = Path(__file__).resolve().parent / "web"
RADICE = Path(__file__).resolve().parent.parent
PORTA = int(os.environ.get("PORT", "8765"))
LAVORI: dict[str, dict] = {}
BLOCCO = threading.Lock()


def identificativo_valido(valore: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._-]+", valore))


def risolvi_nel_progetto(relativo: str) -> Path | None:
    if not relativo or ".." in Path(relativo).parts:
        return None
    candidato = (RADICE / relativo).resolve()
    try:
        candidato.relative_to(RADICE)
    except ValueError:
        return None
    return candidato if candidato.is_file() else None


def pdf_del_documento(identificativo: str) -> Path | None:
    if not identificativo_valido(identificativo):
        return None
    cartella = RISULTATI / identificativo
    meta_path = cartella / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        trovato = risolvi_nel_progetto(str(meta.get("pdf") or ""))
        if trovato:
            return trovato
    originale = cartella / "originale.pdf"
    return originale if originale.is_file() else None


def calcola_topic_salvato(identificativo: str, aspetto_id: str) -> dict:
    """Calcola i topic di un aspetto solo se non sono già salvati."""
    from topic import etichetta_topic, topic_gia_fatto

    percorso = RISULTATI / identificativo / "analisi.json"
    if not percorso.is_file():
        raise FileNotFoundError(identificativo)
    with BLOCCO:
        analisi = json.loads(percorso.read_text(encoding="utf-8"))
        aspetto = next((voce for voce in analisi.get("aspetti") or [] if voce.get("id") == aspetto_id), None)
        if aspetto is None or not isinstance(aspetto.get("concetto"), dict):
            raise LookupError(aspetto_id)
        if topic_gia_fatto(aspetto["concetto"].get("topic")):
            return aspetto["concetto"]["topic"]
        aggregato = list(aspetto["concetto"].get("aggregato") or [])
        nome = aspetto.get("nome") or ""
    topic, uso = etichetta_topic(aggregato, nome)
    with BLOCCO:
        analisi = json.loads(percorso.read_text(encoding="utf-8"))
        aspetto = next((voce for voce in analisi.get("aspetti") or [] if voce.get("id") == aspetto_id), None)
        if aspetto is None or not isinstance(aspetto.get("concetto"), dict):
            raise LookupError(aspetto_id)
        if topic_gia_fatto(aspetto["concetto"].get("topic")):
            return aspetto["concetto"]["topic"]
        aspetto["concetto"]["topic"] = topic
        uso_totale = analisi.get("usage") or {}
        analisi["usage"] = {
            "prompt_tokens": uso_totale.get("prompt_tokens", 0) + int(uso.get("prompt_tokens") or 0),
            "completion_tokens": uso_totale.get("completion_tokens", 0) + int(uso.get("completion_tokens") or 0),
        }
        percorso.write_text(json.dumps(analisi, ensure_ascii=False, indent=2), encoding="utf-8")
        meta_path = percorso.parent / "meta.json"
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["usage"] = analisi["usage"]
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return topic


def elenco_per_interfaccia() -> list[dict]:
    voci = []
    for voce in elenco_risultati():
        pdf = pdf_del_documento(voce["id"])
        voce["nome_pdf"] = pdf.name if pdf else ""
        voci.append(voce)
    return voci


def conserva_pdf(identificativo: str, origine: Path) -> None:
    if origine.suffix.lower() != ".pdf" or not origine.is_file():
        return
    cartella = RISULTATI / identificativo
    destinazione = cartella / "originale.pdf"
    if origine.resolve() != destinazione.resolve():
        shutil.copyfile(origine, destinazione)
    meta_path = cartella / "meta.json"
    if not meta_path.is_file():
        return
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["pdf"] = f"risultati/{identificativo}/originale.pdf"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def leggi_caricamento(handler: BaseHTTPRequestHandler) -> tuple[str, bytes]:
    tipo = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in tipo or "boundary=" not in tipo:
        raise ValueError("Serve un file inviato come multipart.")
    confine = tipo.split("boundary=", 1)[1].split(";", 1)[0].strip().strip('"')
    lunghezza = int(handler.headers.get("Content-Length", "0"))
    if lunghezza <= 0 or lunghezza > 32_000_000:
        raise ValueError("Il file è vuoto oppure supera i 32 MB.")
    corpo = handler.rfile.read(lunghezza)
    marca = ("--" + confine).encode()
    for parte in corpo.split(marca):
        if b"filename=" not in parte:
            continue
        testa, _, dati = parte.partition(b"\r\n\r\n")
        dati = dati.removesuffix(b"\r\n")
        trovato = re.search(br'filename="([^"]*)"', testa)
        nome = unquote(trovato.group(1).decode("utf-8", "replace")) if trovato else "documento.md"
        if not nome.lower().endswith((".md", ".txt", ".markdown", ".pdf")):
            raise ValueError("Il file deve essere .md, .txt o .pdf.")
        return Path(nome).name, dati
    raise ValueError("Non ho trovato un file nel caricamento.")


class Gestore(BaseHTTPRequestHandler):
    def log_message(self, formato, *argomenti):
        print("[server]", formato % argomenti, flush=True)

    def invia_json(self, codice: int, payload: dict) -> None:
        corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(codice)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def invia_file(self, percorso: Path, tipo: str) -> None:
        if not percorso.is_file():
            self.send_error(404)
            return
        dati = percorso.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dati)))
        self.end_headers()
        self.wfile.write(dati)

    def invia_intervallo(self, percorso: Path, tipo: str) -> None:
        dimensione = percorso.stat().st_size
        inizio = 0
        fine = dimensione - 1
        codice = 200
        richiesto = self.headers.get("Range")
        if richiesto and richiesto.startswith("bytes="):
            parte = richiesto.split("=", 1)[1].split(",", 1)[0].strip()
            sinistra, _, destra = parte.partition("-")
            if sinistra == "":
                lunghezza = int(destra or "0")
                inizio = max(0, dimensione - lunghezza)
            else:
                inizio = int(sinistra)
                fine = int(destra) if destra else dimensione - 1
            if inizio >= dimensione:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{dimensione}")
                self.end_headers()
                return
            fine = min(fine, dimensione - 1)
            codice = 206
        lunghezza = fine - inizio + 1
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(lunghezza))
        if codice == 206:
            self.send_header("Content-Range", f"bytes {inizio}-{fine}/{dimensione}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with percorso.open("rb") as origine:
            origine.seek(inizio)
            rimanente = lunghezza
            while rimanente:
                blocco = origine.read(min(rimanente, 1024 * 256))
                if not blocco:
                    break
                self.wfile.write(blocco)
                rimanente -= len(blocco)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_GET(self):  # noqa: N802
        percorso = urlparse(self.path).path
        if percorso in {"/", "/index.html"}:
            self.invia_file(WEB / "index.html", "text/html; charset=utf-8")
            return
        if percorso == "/stile.css":
            self.invia_file(WEB / "stile.css", "text/css; charset=utf-8")
            return
        if percorso == "/app.js":
            self.invia_file(WEB / "app.js", "text/javascript; charset=utf-8")
            return
        if percorso == "/pdfjs/pdf.min.js":
            self.invia_file(WEB / "pdfjs" / "pdf.min.js", "text/javascript; charset=utf-8")
            return
        if percorso == "/pdfjs/pdf.worker.min.js":
            self.invia_file(WEB / "pdfjs" / "pdf.worker.min.js", "text/javascript; charset=utf-8")
            return
        if percorso == "/api/documenti":
            self.invia_json(200, {"documenti": elenco_per_interfaccia()})
            return
        if percorso.startswith("/api/documenti/"):
            resto = unquote(percorso.removeprefix("/api/documenti/"))
            if resto.endswith("/pdf"):
                identificativo = resto[: -len("/pdf")].strip("/")
                if not identificativo_valido(identificativo):
                    self.invia_json(400, {"errore": "Identificativo non valido."})
                    return
                file_pdf = pdf_del_documento(identificativo)
                if not file_pdf:
                    self.invia_json(404, {"errore": "Questo corpus non ha un PDF."})
                    return
                self.invia_intervallo(file_pdf, "application/pdf")
                return
            if resto.endswith("/percorso"):
                identificativo = resto[: -len("/percorso")]
                if not identificativo_valido(identificativo):
                    self.invia_json(400, {"errore": "Identificativo non valido."})
                    return
                file_percorso = RISULTATI / identificativo / "percorso.json"
                if not file_percorso.is_file():
                    self.invia_json(404, {"errore": "Percorso non trovato."})
                    return
                self.invia_json(200, json.loads(file_percorso.read_text(encoding="utf-8")))
                return
            identificativo = resto
            if not re.fullmatch(r"[A-Za-z0-9._-]+", identificativo):
                self.invia_json(400, {"errore": "Identificativo non valido."})
                return
            try:
                self.invia_json(200, carica_risultato(identificativo))
            except FileNotFoundError:
                self.invia_json(404, {"errore": "Analisi non trovata."})
            return
        if percorso.startswith("/api/stato/"):
            lavoro = unquote(percorso.removeprefix("/api/stato/"))
            with BLOCCO:
                stato = LAVORI.get(lavoro)
            if not stato:
                self.invia_json(404, {"errore": "Lavoro non trovato."})
                return
            self.invia_json(200, stato)
            return
        self.send_error(404)

    def _topic_se_richiesto(self) -> None:
        percorso = urlparse(self.path).path
        prefisso = "/api/documenti/"
        marca = "/topic/"
        if not percorso.startswith(prefisso) or marca not in percorso:
            self.send_error(404)
            return
        resto = unquote(percorso.removeprefix(prefisso))
        identificativo, _, aspetto_id = resto.partition(marca)
        identificativo = identificativo.strip("/")
        aspetto_id = aspetto_id.strip("/")
        if not identificativo_valido(identificativo) or not re.fullmatch(r"[A-Za-z0-9_-]+", aspetto_id):
            self.invia_json(400, {"errore": "Identificativo non valido."})
            return
        try:
            topic = calcola_topic_salvato(identificativo, aspetto_id)
        except FileNotFoundError:
            self.invia_json(404, {"errore": "Analisi non trovata."})
            return
        except LookupError:
            self.invia_json(404, {"errore": "Questo indicatore non ha una nuvola."})
            return
        except Exception as errore:  # noqa: BLE001 - il messaggio torna all'interfaccia
            testo = str(errore)
            if "insufficient_quota" in testo or "credit_balance_exhausted" in testo:
                testo = "La chiave non ha più credito, quindi non posso calcolare i topic."
            self.invia_json(502, {"errore": testo})
            return
        self.invia_json(200, {"topic": topic})

    def do_POST(self):  # noqa: N802
        if urlparse(self.path).path != "/api/analizza":
            self._topic_se_richiesto()
            return
        try:
            nome, dati = leggi_caricamento(self)
        except ValueError as errore:
            self.invia_json(400, {"errore": str(errore)})
            return
        lavoro = uuid.uuid4().hex[:12]
        ingresso = RISULTATI / "_caricamenti"
        ingresso.mkdir(parents=True, exist_ok=True)
        destinazione = ingresso / f"{lavoro}-{nome}"
        destinazione.write_bytes(dati)
        with BLOCCO:
            LAVORI[lavoro] = {
                "id": lavoro,
                "stato": "in_corso",
                "fatti": 0,
                "totale": 18,
                "aspetto": None,
                "documento_id": None,
                "errore": None,
            }

        def esegui() -> None:
            def avanzamento(fatti, totale, aspetto_id):
                with BLOCCO:
                    LAVORI[lavoro].update({"fatti": fatti, "totale": 18, "aspetto": aspetto_id})

            def avanzamento_percorso(fatti, totale, etichetta):
                with BLOCCO:
                    LAVORI[lavoro].update({"fatti": 12 + fatti, "totale": 18, "aspetto": etichetta})

            try:
                origine = destinazione
                if destinazione.suffix.lower() == ".pdf":
                    markdown = pdf_a_markdown(destinazione.read_bytes(), nome)
                    origine = destinazione.with_suffix(".md")
                    origine.write_text(markdown, encoding="utf-8")
                esito = analizza_documento(origine, nome_originale=nome, avanzamento=avanzamento)
                if destinazione.suffix.lower() == ".pdf":
                    conserva_pdf(esito["id"], destinazione)
                try:
                    analizza_percorso(
                        origine,
                        nome_originale=nome,
                        avanzamento=avanzamento_percorso,
                    )
                except Exception as errore_percorso:  # noqa: BLE001
                    with BLOCCO:
                        LAVORI[lavoro]["percorso_errore"] = str(errore_percorso)
                with BLOCCO:
                    LAVORI[lavoro].update(
                        {"stato": "completato", "documento_id": esito["id"], "fatti": 18, "totale": 18}
                    )
            except Exception as errore:  # noqa: BLE001
                with BLOCCO:
                    LAVORI[lavoro].update({"stato": "errore", "errore": str(errore)})

        threading.Thread(target=esegui, daemon=True).start()
        self.invia_json(202, {"lavoro": lavoro})


def main() -> None:
    RISULTATI.mkdir(parents=True, exist_ok=True)
    prepara_lemmatizzatore()
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, PORTA), Gestore)
    print(f"Interfaccia su http://{host}:{PORTA}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
