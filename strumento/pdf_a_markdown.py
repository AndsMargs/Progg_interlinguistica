#!/usr/bin/env python3
"""Trasforma il testo di un PDF nel markdown che le pipeline già sanno leggere."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DIPENDENZE = Path(__file__).resolve().parent / "dipendenze"
if str(DIPENDENZE) not in sys.path:
    sys.path.insert(0, str(DIPENDENZE))

BULLET = re.compile(r"^[•▪‣]\s+")
NUMERO = re.compile(r"^(\d{1,2})\.\s+\S")
FINE_FRASE = re.compile(r"[.!?…:;»”\"']\s*$")
LIGATURE = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}


def pdf_a_markdown(dati: bytes, nome: str) -> str:
    """Estrae il testo del PDF e lo riscrive in paragrafi, elenchi e titoli."""
    if not dati.startswith(b"%PDF"):
        raise ValueError("Il file non sembra un PDF.")
    grezzo = _estrai_testo(dati)
    markdown = _in_markdown(grezzo)
    if len(re.sub(r"\s+", "", markdown)) < 40:
        raise ValueError(
            "In questo PDF non ho trovato testo selezionabile. "
            "Se è una scansione, carica un file .md o .txt."
        )
    fonte = Path(nome).name.replace("`", "")
    return f"Fonte: `{fonte}`.\n\n{markdown}\n"


def _estrai_testo(dati: bytes) -> str:
    testo = _estrai_pdftotext(dati)
    if testo and testo.strip():
        return _pulisci_caratteri(testo)
    return _pulisci_caratteri(_estrai_pypdf(dati))


def _estrai_pdftotext(dati: bytes) -> str | None:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as cartella:
        origine = Path(cartella) / "documento.pdf"
        origine.write_bytes(dati)
        esito = subprocess.run(
            [exe, "-layout", "-enc", "UTF-8", str(origine), "-"],
            capture_output=True,
            check=False,
        )
    if esito.returncode != 0:
        return None
    return esito.stdout.decode("utf-8", "replace")


def _estrai_pypdf(dati: bytes) -> str:
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    from io import BytesIO

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(BytesIO(dati))
    except PdfReadError as errore:
        raise ValueError("Non riesco a leggere questo PDF.") from errore
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as errore:
            raise ValueError("Questo PDF è protetto da password e non posso leggerlo.") from errore
    pagine = []
    for pagina in reader.pages:
        testo = pagina.extract_text() or ""
        testo = re.sub(r"(?<=\w)/f_(?=\w)", "fi", testo)
        pagine.append(testo)
    return "\f".join(pagine)


def _pulisci_caratteri(testo: str) -> str:
    for origine, destino in LIGATURE.items():
        testo = testo.replace(origine, destino)
    testo = testo.replace("\u00a0", " ").replace("\u200b", "")
    return testo


def _in_markdown(grezzo: str) -> str:
    pagine = []
    for pagina in grezzo.split("\f"):
        righe = [riga.rstrip() for riga in pagina.splitlines()]
        righe = [riga for riga in righe if not re.fullmatch(r"\s*\d{1,4}\s*", riga)]
        pagine.append(righe)
    pagine = _togli_testate(pagine)
    blocchi: list[tuple[str, str]] = []
    for righe in pagine:
        blocchi.extend(_blocchi_pagina(righe))
    blocchi = _unisci_spezzati(blocchi)
    pezzi = []
    for tipo, testo in blocchi:
        if tipo == "titolo":
            pezzi.append(f"## {testo}")
        elif tipo == "voce":
            pezzi.append(f"- {testo}")
        else:
            pezzi.append(testo)
    return "\n\n".join(pezzi).strip()


def _togli_testate(pagine: list[list[str]]) -> list[list[str]]:
    prime = []
    for righe in pagine:
        piene = [riga.strip() for riga in righe if riga.strip()]
        if piene:
            prime.append(_chiave_riga(piene[0]))
    ripetute = {riga for riga in prime if prime.count(riga) >= 2 and len(riga) < 110}
    pulite = []
    for righe in pagine:
        piene = [indice for indice, riga in enumerate(righe) if riga.strip()]
        scarta = set()
        if piene and _chiave_riga(righe[piene[0]]) in ripetute:
            scarta.add(piene[0])
        pulite.append([riga for indice, riga in enumerate(righe) if indice not in scarta])
    return pulite


def _chiave_riga(riga: str) -> str:
    return re.sub(r"\s+", " ", riga).strip().casefold()


def _blocchi_pagina(righe: list[str]) -> list[tuple[str, str]]:
    piene = [riga for riga in righe if riga.strip()]
    if not piene:
        return []
    base = min(len(riga) - len(riga.lstrip(" ")) for riga in piene)
    corrente: tuple[str, int, list[str]] | None = None
    uscita: list[tuple[str, str]] = []

    def chiudi() -> None:
        nonlocal corrente
        if corrente is None:
            return
        tipo, _rientro, parti = corrente
        testo = _unisci_righe(parti)
        if testo:
            if tipo == "paragrafo" and _e_titolo(testo):
                tipo = "titolo"
            uscita.append((tipo, testo))
        corrente = None

    for riga in righe:
        if not riga.strip():
            chiudi()
            continue
        rientro = len(riga) - len(riga.lstrip(" ")) - base
        testo = riga.strip()
        if BULLET.match(testo):
            chiudi()
            corrente = ("voce", rientro, [BULLET.sub("", testo).strip()])
            continue
        if NUMERO.match(testo):
            chiudi()
            corrente = ("numero", rientro, [testo])
            continue
        if _e_titolo(testo):
            chiudi()
            uscita.append(("titolo", testo))
            continue
        if corrente and corrente[0] in {"voce", "numero"} and rientro > corrente[1]:
            corrente[2].append(testo)
            continue
        if corrente and corrente[0] == "paragrafo":
            corrente[2].append(testo)
            continue
        chiudi()
        corrente = ("paragrafo", rientro, [testo])
    chiudi()
    return uscita


def _e_titolo(testo: str) -> bool:
    if not testo or "\n" in testo:
        return False
    if testo.endswith("?") and 3 <= len(testo.split()) and len(testo) <= 200:
        return True
    if (
        len(testo) <= 60
        and len(testo.split()) <= 8
        and testo[:1].isupper()
        and not FINE_FRASE.search(testo)
    ):
        return True
    return False


def _unisci_righe(parti: list[str]) -> str:
    if not parti:
        return ""
    testo = parti[0].strip()
    for pezzo in parti[1:]:
        pezzo = pezzo.strip()
        if not pezzo:
            continue
        testo = _attacca(testo, pezzo)
    testo = _ricuci_lettere(testo)
    return re.sub(r"\s+", " ", testo).strip()


def _attacca(sinistra: str, destra: str) -> str:
    if sinistra.endswith("-") and destra[:1].islower():
        parola_destra = re.match(r"[^\s]+", destra)
        coda = parola_destra.group(0) if parola_destra else destra
        senza = sinistra[:-1] + coda
        con = sinistra + coda
        if _conosciuta(senza) and not _conosciuta(con):
            return sinistra[:-1] + destra
        return sinistra + destra
    return f"{sinistra} {destra}"


def _ricuci_lettere(testo: str) -> str:
    def sostituisci(trovato: re.Match[str]) -> str:
        unito = trovato.group(1) + trovato.group(2)
        if _conosciuta(unito) and not _conosciuta(trovato.group(2)):
            return unito
        return trovato.group(0)

    return re.sub(r"\b([A-ZÀ-Ý]) ([a-zà-ÿ]{4,})\b", sostituisci, testo)


def _conosciuta(parola: str) -> bool:
    try:
        import simplemma
    except ImportError:
        return False
    pulita = parola.strip(".,;:!?«»\"“”()[]")
    if len(pulita) < 4:
        return False
    return bool(simplemma.is_known(pulita.casefold(), lang="it"))


def _finisci(testo: str) -> bool:
    return bool(FINE_FRASE.search(testo))


def _unisci_spezzati(blocchi: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if not blocchi:
        return []
    uniti = [blocchi[0]]
    for tipo, testo in blocchi[1:]:
        precedente, gia = uniti[-1]
        if (
            tipo == "paragrafo"
            and precedente in {"paragrafo", "voce", "numero"}
            and not _finisci(gia)
            and testo[:1].islower()
        ):
            uniti[-1] = (precedente, _attacca(gia, testo))
            continue
        uniti.append((tipo, testo))
    return uniti
