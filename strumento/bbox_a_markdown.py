#!/usr/bin/env python3
"""Estrae un PDF in markdown usando la posizione e la dimensione delle parole.

È lo stesso procedimento della guida pratica: le parole si raggruppano in righe,
i titoli si riconoscono dalla dimensione, i paragrafi spezzati si ricuciono.
"""

from __future__ import annotations

import html
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

DIPENDENZE = Path(__file__).resolve().parent / "dipendenze"
if str(DIPENDENZE) not in sys.path:
    sys.path.insert(0, str(DIPENDENZE))

PAROLA = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>',
    re.S,
)
PAGINA = re.compile(
    r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>',
    re.S,
)
BULLET = re.compile(r"^[•▪‣❖❑□■◦]\s*")
VOCE_NUMERO = re.compile(r"^\d+\.\s+[a-zà-ù]")
VOCE_LETTERA = re.compile(r"^[a-z]\)\s+")
CAPITOLO = re.compile(r"^\d+\.\s+[A-ZÀ-Ú0-9]")
SOTTOCAPITOLO = re.compile(r"^\d+(?:\.\d+)+\.?\s+\S")
INDICE = re.compile(r"(?:[.·…]{4,}).{0,12}\d\s*$|\bpag\.\s*\d+\s*$", re.I)
PREFISSI = {
    "anti",
    "auto",
    "co",
    "ex",
    "extra",
    "inter",
    "multi",
    "neo",
    "non",
    "post",
    "pseudo",
    "semi",
    "socio",
    "sopra",
    "sotto",
    "super",
    "ultra",
    "vice",
}
TIMBRO = re.compile(r"Layout\s+1\b.*\bPagina\s+\d+|\bCopertina\b.*\bPagina\s+\d+", re.I)
TITOLI_NOTI = {
    "indice",
    "index",
    "glossario",
    "introduzione",
    "premessa",
    "abbreviazioni",
    "elenco delle abbreviazioni",
}


def _testo(raw: str) -> str:
    testo = html.unescape(raw).replace("\xa0", " ").replace("\u200b", "").replace("\u00ad", "")
    testo = testo.replace("\x08", "").strip()
    return testo


def _pdftotext(pdf: Path, prima: int | None, ultima: int | None) -> str:
    comando = ["pdftotext", "-bbox-layout", "-enc", "UTF-8"]
    if prima:
        comando.extend(["-f", str(prima)])
    if ultima:
        comando.extend(["-l", str(ultima)])
    comando.extend([str(pdf), "-"])
    esito = subprocess.run(comando, capture_output=True, check=False)
    if esito.returncode != 0:
        dettaglio = esito.stderr.decode("utf-8", "replace")[:400]
        raise RuntimeError(f"pdftotext non ha letto {pdf.name}: {dettaglio}")
    return esito.stdout.decode("utf-8", "replace")


def _parole(pagina: str, larghezza: float) -> list[dict]:
    parole = []
    for x0, y0, x1, y1, raw in PAROLA.findall(pagina):
        testo = _testo(raw)
        if not testo:
            continue
        parola = {
            "x0": float(x0),
            "y0": float(y0),
            "x1": float(x1),
            "y1": float(y1),
            "t": testo,
        }
        larghezza_parola = max(parola["x1"] - parola["x0"], 0.1)
        altezza = parola["y1"] - parola["y0"]
        verticale = altezza > larghezza_parola * 2.2 and len(testo) > 2
        in_margine = parola["x0"] > larghezza * 0.82 or parola["x1"] < larghezza * 0.08
        if verticale and in_margine:
            continue
        parole.append(parola)
    return parole


def _raggruppa(parole: list[dict]) -> list[dict]:
    if not parole:
        return []
    altezze = [p["y1"] - p["y0"] for p in parole]
    mediana = statistics.median(altezze)
    righe: list[list[dict]] = []
    for parola in sorted(parole, key=lambda item: (item["y0"], item["x0"])):
        altezza = parola["y1"] - parola["y0"]
        apice = altezza < mediana * 0.72 and len(parola["t"]) <= 3
        messo = False
        if apice:
            for riga in righe:
                y0 = min(item["y0"] for item in riga)
                y1 = max(item["y1"] for item in riga)
                x0 = min(item["x0"] for item in riga)
                x1 = max(item["x1"] for item in riga)
                if parola["y0"] < y1 and parola["y1"] > y0 - 2 and x0 - 8 <= parola["x0"] <= x1 + 60:
                    riga.append(parola)
                    messo = True
                    break
        if not messo:
            for riga in righe:
                riferimento = sum(item["y0"] for item in riga) / len(riga)
                altezza_riga = sum(item["y1"] - item["y0"] for item in riga) / len(riga)
                fine = max(item["x1"] for item in riga)
                if (
                    abs(parola["y0"] - riferimento) <= 5.2
                    and abs(altezza - altezza_riga) <= 6
                    and parola["x0"] - fine <= 36
                ):
                    riga.append(parola)
                    messo = True
                    break
        if not messo:
            righe.append([parola])
    impacchettate = []
    for riga in righe:
        riga.sort(key=lambda item: item["x0"])
        testo = re.sub(r"\s+", " ", " ".join(item["t"] for item in riga)).strip()
        if not testo:
            continue
        impacchettate.append(
            {
                "y": min(item["y0"] for item in riga),
                "x": min(item["x0"] for item in riga),
                "x1": max(item["x1"] for item in riga),
                "h": sum(item["y1"] - item["y0"] for item in riga) / len(riga),
                "text": testo,
            }
        )
    impacchettate.sort(key=lambda item: (item["y"], item["x"]))
    return impacchettate


def _e_numero_pagina(riga: dict, precedente: dict | None = None) -> bool:
    if not re.fullmatch(r"\d{1,3}", riga["text"]):
        return False
    posizione = riga["y"] / riga["page_h"]
    if posizione < 0.08:
        return True
    if posizione > 0.82:
        if (
            precedente
            and precedente["page"] == riga["page"]
            and 0 <= riga["y"] - precedente["y"] < 28
        ):
            return False
        return True
    return False


def _e_marcatore(testo: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}[.)]?|[•▪‣❖❑□■◦]", testo.strip()))


def _attacca_marcatori(righe: list[dict]) -> list[dict]:
    usati = set()
    for indice, corrente in enumerate(righe):
        if not _e_marcatore(corrente["text"]):
            continue
        migliore = None
        distanza_migliore = 16
        for altro, candidato in enumerate(righe):
            if altro == indice or _e_marcatore(candidato["text"]):
                continue
            if candidato["page"] != corrente["page"]:
                continue
            distanza = abs(candidato["y"] - corrente["y"])
            if distanza < distanza_migliore and candidato["x"] + 8 >= corrente["x"]:
                migliore = altro
                distanza_migliore = distanza
        if migliore is None:
            continue
        prefisso = corrente["text"]
        if re.fullmatch(r"\d", prefisso):
            prefisso = f"{prefisso}."
        righe[migliore]["text"] = f"{prefisso} {righe[migliore]['text']}"
        righe[migliore]["x"] = min(corrente["x"], righe[migliore]["x"])
        usati.add(indice)
    return [riga for indice, riga in enumerate(righe) if indice not in usati]


def _togli_testate(righe: list[dict]) -> list[dict]:
    pagine_per_testo: dict[str, set[int]] = defaultdict(set)
    for riga in righe:
        if len(riga["text"]) < 160:
            pagine_per_testo[_chiave(riga["text"])].add(riga["page"])
    tenute = []
    for riga in righe:
        if TIMBRO.search(riga["text"]) or _e_numero_pagina(riga, tenute[-1] if tenute else None):
            continue
        if re.search(r"Layout\s+1\b", riga["text"]) or re.fullmatch(r"Pagina\s+\d+", riga["text"]):
            continue
        if re.fullmatch(r"\d{2}-\d{2}-\d{4}", riga["text"]) or re.fullmatch(r"\d{1,2}:\d{2}", riga["text"]):
            continue
        if _chiave(riga["text"]) in {
            "italiano", "amarico", "curdo", "farsi", "francese", "inglese",
            "serbo", "somalo", "spagnolo", "tigrino", "arabo",
        }:
            continue
        if "linee guida" in _chiave(riga["text"]) and len(riga["text"]) < 36:
            continue
        if riga["y"] / riga["page_h"] < 0.055 and len(riga["text"].split()) >= 2:
            continue
        chiave = _chiave(riga["text"])
        ripetizioni = len(pagine_per_testo.get(chiave, ()))
        posizione = riga["y"] / riga["page_h"]
        if ripetizioni >= 3 and len(riga["text"]) < 150 and posizione < 0.14:
            continue
        if ripetizioni >= 3 and len(riga["text"]) < 48 and posizione > 0.86:
            continue
        tenute.append(riga)
    return tenute


def _ricuci_iniziali(righe: list[dict]) -> list[dict]:
    """Riattacca la lettera grande staccata a inizio riga, come la P di Polizia."""
    if not righe:
        return righe
    risultato = []
    indice = 0
    while indice < len(righe):
        riga = righe[indice]
        if (
            re.fullmatch(r"[A-ZÀ-Ý]", riga["text"])
            and indice + 1 < len(righe)
            and righe[indice + 1]["page"] == riga["page"]
            and abs(righe[indice + 1]["y"] - riga["y"]) < 22
            and righe[indice + 1]["x"] + 8 >= riga["x"]
        ):
            seguente = righe[indice + 1]
            seguente["text"] = f"{riga['text']}{seguente['text']}"
            seguente["x"] = min(riga["x"], seguente["x"])
            seguente["h"] = max(riga["h"], seguente["h"])
            indice += 1
            continue
        if re.fullmatch(r"[A-Za-zÀ-ú]", riga["text"]):
            indice += 1
            continue
        risultato.append(riga)
        indice += 1
    return risultato


def _numeri_di_pagina_laterali(righe: list[dict]) -> list[dict]:
    """Il numero a destra di una voce dell’indice diventa «pag. N»."""
    tenute = []
    for riga in righe:
        if (
            tenute
            and re.fullmatch(r"\d{1,3}", riga["text"])
            and riga["page"] == tenute[-1]["page"]
            and "pag." not in tenute[-1]["text"].casefold()
            and riga["x"] > tenute[-1]["x"] + 40
            and abs(riga["y"] - tenute[-1]["y"]) < 20
            and riga["h"] < tenute[-1]["h"] * 1.5
        ):
            tenute[-1]["text"] = f"{tenute[-1]['text']} — pag. {riga['text']}"
            tenute[-1]["x1"] = max(tenute[-1]["x1"], riga["x1"])
            continue
        tenute.append(riga)
    return tenute


def _chiave(testo: str) -> str:
    return re.sub(r"\s+", " ", testo).strip().casefold()


def _maiuscole(testo: str) -> float:
    lettere = [carattere for carattere in testo if carattere.isalpha()]
    if not lettere:
        return 0
    return sum(carattere.isupper() for carattere in lettere) / len(lettere)


def _tutto_maiuscolo(testo: str) -> bool:
    return _maiuscole(testo) > 0.85 and not any(carattere.islower() for carattere in testo)


def _e_titolo_aperto(riga: dict, corpo: float) -> bool:
    """Una riga che è già un titolo non deve assorbire il paragrafo sotto."""
    testo = riga["text"]
    if _tutto_maiuscolo(testo) and len(testo) < 150:
        return True
    if riga["h"] >= corpo * 1.4 and len(testo.split()) <= 18 and not testo.endswith((".", ",", ";")):
        return True
    if (CAPITOLO.match(testo) or SOTTOCAPITOLO.match(testo)) and len(testo) < 180 and not testo.endswith((".", ",", ";")):
        return True
    return _chiave(testo) in TITOLI_NOTI


def _inizia_blocco(riga: dict, corpo: float) -> bool:
    testo = riga["text"]
    if BULLET.match(testo) or re.match(r"^[-–]\s+\S", testo):
        return True
    if VOCE_LETTERA.match(testo) or VOCE_NUMERO.match(testo):
        return True
    if SOTTOCAPITOLO.match(testo) and len(testo) < 220:
        return True
    if CAPITOLO.match(testo) and len(testo) < 180:
        return True
    if testo.startswith("[") and len(testo) < 200:
        return True
    if riga["h"] >= corpo * 1.45 and len(testo.split()) <= 18:
        return True
    if _tutto_maiuscolo(testo) and len(testo) < 140:
        return True
    return False


def _conosciuta(parola: str) -> bool:
    import simplemma

    pulita = parola.strip(".,;:!?«»\"“”()[]")
    if len(pulita) < 4:
        return False
    return bool(simplemma.is_known(pulita.casefold(), lang="it"))


def _attacca_parole(sinistra: str, destra: str) -> str:
    if sinistra.endswith("-") and not sinistra.endswith(" -") and destra[:1].islower():
        coda = re.match(r"[^\s.,;:]+", destra)
        pezzo = coda.group(0) if coda else destra
        troncata = re.search(r"(\S+)-$", sinistra)
        frammento = troncata.group(1) if troncata else sinistra[:-1]
        unita = frammento + pezzo
        con_trattino = f"{frammento}-{pezzo}"
        if _conosciuta(unita) and not _conosciuta(con_trattino):
            return f"{sinistra[:-1]}{destra}"
        if (
            frammento.isalpha()
            and frammento.casefold() not in PREFISSI
            and len(frammento) <= 6
            and len(pezzo) >= 3
        ):
            return f"{sinistra[:-1]}{destra}"
        return f"{sinistra}{destra}"
    return f"{sinistra} {destra}"


def _soglia_a_capo(righe: list[dict], corpo: float) -> float:
    distanze = []
    precedente = None
    for riga in righe:
        if precedente and riga["page"] == precedente["page"] and abs(riga["h"] - corpo) < 4:
            distanza = riga["y"] - precedente["y"]
            if 0 < distanza < 48:
                distanze.append(distanza)
        precedente = riga
    if not distanze:
        return 16
    return max(12, min(22, statistics.median(distanze) * 1.28))


def _unisci(righe: list[dict], corpo: float) -> list[dict]:
    soglia = _soglia_a_capo(righe, corpo)
    paragrafi = []
    buffer = None
    for riga in righe:
        if buffer is None:
            buffer = dict(riga)
            continue
        stessa = riga["page"] == buffer["page"]
        distanza = riga["y"] - buffer["y"] if stessa else 99
        continua_minuscolo = (
            riga["text"][:1].islower()
            and not _inizia_blocco(riga, corpo)
            and not _e_titolo_aperto(buffer, corpo)
        )
        trattino = buffer["text"].endswith("-") and not buffer["text"].endswith(" -")
        if continua_minuscolo and (not stessa or distanza < max(soglia * 2.2, 28)):
            buffer["text"] = _attacca_parole(buffer["text"], riga["text"])
            buffer["y"] = riga["y"]
            buffer["h"] = max(buffer["h"], riga["h"])
            continue
        if trattino and riga["text"][:1].islower() and (not stessa or distanza < 40):
            buffer["text"] = _attacca_parole(buffer["text"], riga["text"])
            buffer["y"] = riga["y"]
            buffer["h"] = max(buffer["h"], riga["h"])
            continue
        if (
            stessa
            and distanza < soglia
            and abs(buffer["h"] - riga["h"]) < 4
            and not _inizia_blocco(riga, corpo)
            and not _e_titolo_aperto(buffer, corpo)
            and riga["x"] <= buffer["x1"] + 24
        ):
            buffer["text"] = _attacca_parole(buffer["text"], riga["text"])
            buffer["y"] = riga["y"]
            buffer["h"] = max(buffer["h"], riga["h"])
            continue
        if (
            stessa
            and buffer["text"].endswith((",", ";", ":", "(", "/"))
            and not _inizia_blocco(riga, corpo)
            and not BULLET.match(riga["text"])
            and distanza < max(soglia * 2.4, 36)
            and riga["x"] <= buffer["x1"] + 40
        ):
            buffer["text"] = _attacca_parole(buffer["text"], riga["text"])
            buffer["y"] = riga["y"]
            buffer["h"] = max(buffer["h"], riga["h"])
            continue
        titoli_grandi = buffer["h"] >= corpo * 1.45 and riga["h"] >= corpo * 1.45
        titoli_maiuscoli = _tutto_maiuscolo(buffer["text"]) and _tutto_maiuscolo(riga["text"])
        voce_nuova = bool(re.match(r"^\d+\.\s+", riga["text"])) or "pag." in buffer["text"].casefold()
        if (
            stessa
            and not voce_nuova
            and not buffer["text"].endswith((".", "!", "?", ";", ":"))
            and riga["x"] <= buffer["x1"] + 24
            and (
                (titoli_grandi and distanza < 36 and len(riga["text"].split()) <= 10)
                or (titoli_maiuscoli and distanza < 24 and len(buffer["text"]) > 12)
            )
        ):
            buffer["text"] = f"{buffer['text']} {riga['text']}"
            buffer["y"] = riga["y"]
            buffer["h"] = max(buffer["h"], riga["h"])
            continue
        paragrafi.append(buffer)
        buffer = dict(riga)
    if buffer:
        paragrafi.append(buffer)
    return paragrafi


def _sciogli_trattini(testo: str) -> str:
    def sistema(trovato: re.Match[str]) -> str:
        sinistra, destra = trovato.group(1), trovato.group(2)
        if sinistra.casefold() in PREFISSI:
            return trovato.group(0)
        unita = sinistra + destra
        if _conosciuta(unita):
            return unita
        return trovato.group(0)

    return re.sub(r"\b([A-Za-zÀ-ú]{2,12})-([a-zà-ù]{2,})\b", sistema, testo)


def _ricuci_capolettera(testo: str) -> str:
    """La lettera grande staccata: «R ICORDA» torna «RICORDA», «I Paesi» resta."""

    def sistema(trovato: re.Match[str]) -> str:
        if trovato.group(1) in set("AEIOÀÈÉÍÒÙ"):
            return trovato.group(0)
        return trovato.group(1) + trovato.group(2)

    return re.sub(r"\b([A-ZÀ-Ý]) ([A-ZÀ-Ú]{3,})\b", sistema, testo)


def _pulisci(testo: str) -> str:
    testo = _sciogli_trattini(testo)
    testo = _ricuci_capolettera(testo)
    testo = re.sub(r"^ll (?=\d)", "Il ", testo)

    def sistema_spaziato(trovato: re.Match[str]) -> str:
        unita = trovato.group(1) + trovato.group(2)
        if _conosciuta(unita):
            return unita
        if (
            trovato.group(1).isupper()
            and trovato.group(2).isupper()
            and not _conosciuta(trovato.group(2))
            and len(trovato.group(1)) <= 6
        ):
            return unita
        return trovato.group(0)

    testo = re.sub(r"\b([A-Za-zÀ-ú]{3,}) - ([A-Za-zà-ù]{3,})\b", sistema_spaziato, testo)
    testo = re.sub(r"\b([A-ZÀ-Ý]{2,})\s+['’]\s+([A-ZÀ-Ý]{3,})\b", r"\1'\2", testo)
    testo = re.sub(r"(—\s*){2,}", "— ", testo)
    testo = re.sub(r"\s+", " ", testo).strip()
    testo = re.sub(r"\s+([,.;:!?])", r"\1", testo)
    parole = testo.split()
    if len(parole) >= 6 and len(parole) % 2 == 0:
        meta = len(parole) // 2
        if parole[:meta] == parole[meta:]:
            testo = " ".join(parole[:meta])
    return testo


def _pulisci_indice(testo: str) -> str:
    testo = re.sub(r"\s*[.·…]{2,}\s*", " ", testo)
    testo = re.sub(r"^[•▪‣❖❑□■◦]\s*", "", testo)
    testo = re.sub(r"\s+", " ", testo).strip(" -—")
    if re.search(r"\bpag\.?\s*\d+", testo, re.I):
        testo = re.sub(r"\s*\bpag\.?\s*(\d+).*", r" — pag. \1", testo, count=1, flags=re.I)
        return _pulisci(testo)
    numero = re.search(r"\s(\d{1,3})$", testo)
    if numero and len(testo) > 12:
        testo = f"{testo[:numero.start()]} — pag. {numero.group(1)}"
    return _pulisci(testo)


def _classifica(paragrafo: dict, corpo: float) -> tuple[str, str] | None:
    testo = _pulisci(paragrafo["text"])
    if not testo or re.fullmatch(r"[\d.\-–]+", testo):
        return None
    if INDICE.search(testo):
        return ("li", _pulisci_indice(testo))
    if BULLET.match(testo):
        return ("li", _pulisci(BULLET.sub("", testo)))
    if re.match(r"^[-–]\s+", testo):
        return ("li", _pulisci(re.sub(r"^[-–]\s+", "", testo)))
    if VOCE_LETTERA.match(testo) or VOCE_NUMERO.match(testo):
        return ("li", testo)
    if _chiave(testo) in TITOLI_NOTI:
        return ("h1", testo)
    parole = testo.split()
    grande = paragrafo["h"] >= corpo * 1.55
    medio = paragrafo["h"] >= corpo * 1.15
    tutto_maiuscolo = _tutto_maiuscolo(testo)
    if CAPITOLO.match(testo) and len(testo) < 180 and not testo.endswith((".", ";")):
        if grande or (paragrafo["h"] >= corpo * 1.35 and len(parole) <= 14):
            return ("h1", testo)
        return ("h2", testo)
    if SOTTOCAPITOLO.match(testo) and len(testo) < 220 and not testo.endswith(";"):
        return ("h2", testo)
    if testo.endswith("?") and len(testo) < 220 and len(parole) <= 28:
        if tutto_maiuscolo and len(parole) <= 16:
            return ("h1", testo)
        return ("h2", testo)
    if tutto_maiuscolo and 1 <= len(parole) <= 18 and len(testo) < 150 and not testo.endswith("."):
        if len(parole) <= 4:
            return ("h3", testo)
        return ("h2", testo)
    if grande and 1 < len(parole) <= 16 and not testo.endswith((".", ";", ",")):
        return ("h1", testo)
    if medio and 1 < len(parole) <= 16 and not testo.endswith((".", ";", ",")):
        return ("h2", testo)
    return ("p", testo)


def _render(titolo: str, fonte: str, note: list[str], blocchi: list[tuple[str, str]]) -> str:
    righe = [f"# {titolo}", "", f"Fonte: `{fonte}`.", *note, ""]
    elenco = False
    for tipo, testo in blocchi:
        if _chiave(testo) == _chiave(titolo):
            continue
        if tipo != "li" and elenco:
            righe.append("")
            elenco = False
        if righe and righe[-1] != "" and tipo in {"h1", "h2", "h3"}:
            righe.append("")
        if tipo == "h1":
            righe.extend([f"## {testo}", ""])
        elif tipo == "h2":
            righe.extend([f"### {testo}", ""])
        elif tipo == "h3":
            righe.extend([f"#### {testo}", ""])
        elif tipo == "li":
            if testo:
                righe.append(f"- {testo}")
                elenco = True
        else:
            righe.extend([testo, ""])
    if elenco:
        righe.append("")
    testo = "\n".join(righe)
    testo = re.sub(r"\n{3,}", "\n\n", testo).strip() + "\n"
    return testo


RIGA_LAYOUT = re.compile(
    r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>',
    re.S,
)


def _righe_layout(pagina: str, altezza_pagina: float, numero: int) -> list[dict]:
    righe = []
    for x0, y0, x1, _y1, corpo in RIGA_LAYOUT.findall(pagina):
        parole = []
        altezze = []
        for _a, b, _c, d, raw in PAROLA.findall(corpo):
            testo = _testo(raw)
            if not testo:
                continue
            parole.append(testo)
            altezze.append(float(d) - float(b))
        if not parole or not altezze:
            continue
        righe.append(
            {
                "y": float(y0),
                "x": float(x0),
                "x1": float(x1),
                "h": statistics.median(altezze),
                "text": re.sub(r"\s+", " ", " ".join(parole)).strip(),
                "page": numero,
                "page_h": altezza_pagina,
            }
        )
    return righe


def pdf_in_markdown(
    pdf: Path,
    titolo: str,
    note: list[str],
    prima: int | None = None,
    ultima: int | None = None,
) -> str:
    grezzo = _pdftotext(pdf, prima, ultima)
    righe = []
    for numero, (_larghezza, altezza, pagina) in enumerate(PAGINA.findall(grezzo), prima or 1):
        righe.extend(_righe_layout(pagina, float(altezza), numero))
    righe = _ricuci_iniziali(righe)
    righe = _togli_testate(righe)
    righe = _attacca_marcatori(righe)
    righe = _numeri_di_pagina_laterali(righe)
    altezze = [riga["h"] for riga in righe if len(riga["text"].split()) >= 5]
    corpo = statistics.median(altezze) if altezze else 12
    paragrafi = _unisci(righe, corpo)
    blocchi = []
    for paragrafo in paragrafi:
        voce = _classifica(paragrafo, corpo)
        if voce is None:
            continue
        if blocchi and blocchi[-1][1] == voce[1]:
            continue
        blocchi.append(voce)
    return _render(titolo, pdf.name, note, blocchi)


def converti(pdf: Path, destinazione: Path, titolo: str, note: list[str], prima=None, ultima=None) -> None:
    testo = pdf_in_markdown(pdf, titolo, note, prima, ultima)
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    destinazione.write_text(testo, encoding="utf-8")
    print(f"{destinazione.name}  {len(testo)} caratteri  {testo.count(chr(10))} righe")
