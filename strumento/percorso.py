#!/usr/bin/env python3
"""Pipeline separata: divide il testo per fase e dà un tachimetro a ogni termine."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline import (
    MODELLO,
    cartella_risultato,
    chiedi_modello,
    livello_da_punteggio,
    mediana_intensita,
    sistema_punteggio,
    trova_estratto,
)

RADICE = Path(__file__).resolve().parent.parent
CATALOGO = RADICE / "fasi_e_termini.json"
PASSI = 6


def carica_catalogo() -> dict:
    return json.loads(CATALOGO.read_text(encoding="utf-8"))


def testo_definizione(termine: dict) -> str:
    parti = [termine["definizione"]]
    condizioni = termine.get("condizioni") or []
    if condizioni:
        parti.append("Situazioni: " + "; ".join(condizioni) + ".")
    return " ".join(parti)


def chiedi_json(messaggi: list[dict]) -> dict:
    contenuto, uso = chiedi_modello(messaggi)
    return json.loads(contenuto), uso


def somma_uso(totale: dict, uso: dict | None) -> None:
    if not uso:
        return
    for chiave in ("prompt_tokens", "completion_tokens", "total_tokens"):
        totale[chiave] = totale.get(chiave, 0) + int(uso.get(chiave) or 0)


def spezza_frasi(testo: str, base: int) -> list[dict]:
    frasi = []
    tagli = list(re.finditer(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý«\"])|\n+", testo))
    confini = [match.start() for match in tagli] + [len(testo)]
    ripartenze = [0] + [match.end() for match in tagli]
    for inizio_locale, fine_locale in zip(ripartenze, confini):
        grezzo = testo[inizio_locale:fine_locale]
        pezzo = grezzo.strip()
        if len(pezzo) < 40:
            continue
        spostamento = grezzo.find(pezzo)
        inizio = base + inizio_locale + spostamento
        frasi.append({"testo": pezzo, "inizio": inizio, "fine": inizio + len(pezzo)})
    return frasi


AGHI_TERMINE = {
    "cedolino": ["cedolino"],
    "lettera_di_convocazione": ["lettera di convocazione", "orario di convocazione"],
    "calamita_naturale": ["calamità", "calamita naturale"],
    "permesso_per_casi_speciali": ["casi speciali"],
    "gratuito_patrocinio": ["gratuito patrocinio"],
}


def paragrafi_con_ago(documento: str, ago: str) -> list[dict]:
    trovati = []
    basso = documento.casefold()
    ago_basso = ago.casefold()
    cursore = 0
    while True:
        posizione = basso.find(ago_basso, cursore)
        if posizione < 0:
            break
        inizio = documento.rfind("\n\n", 0, posizione)
        inizio = 0 if inizio < 0 else inizio + 2
        fine = documento.find("\n\n", posizione)
        fine = len(documento) if fine < 0 else fine
        if fine - inizio >= 40:
            trovati.append({"testo": documento[inizio:fine], "inizio": inizio, "fine": fine})
        cursore = posizione + len(ago_basso)
    return trovati


def aggiungi_paragrafi_mancanti(documento: str, catalogo: dict, per_fase: dict[str, list[dict]]) -> None:
    """Se un termine è nel testo ma la suddivisione lo ha saltato, recupera il paragrafo."""
    occupati = [estratto for estratti in per_fase.values() for estratto in estratti]
    for fase in catalogo["fasi"]:
        for termine in fase["termini"]:
            for ago in AGHI_TERMINE.get(termine["id"], []):
                for paragrafo in paragrafi_con_ago(documento, ago):
                    if any(
                        paragrafo["inizio"] < altro["fine"] and altro["inizio"] < paragrafo["fine"]
                        for altro in occupati
                    ):
                        continue
                    per_fase[fase["id"]].append(paragrafo)
                    occupati.append(paragrafo)


def tieni_senza_sovrapposizioni(candidati: list[dict]) -> list[dict]:
    ordinati = sorted(candidati, key=lambda voce: (voce["inizio"], -(voce["fine"] - voce["inizio"])))
    tenuti = []
    for voce in ordinati:
        if any(voce["inizio"] < altro["fine"] and altro["inizio"] < voce["fine"] for altro in tenuti):
            continue
        tenuti.append(voce)
    return tenuti


def dividi_in_fasi(documento: str, catalogo: dict) -> tuple[dict[str, list[dict]], dict]:
    fasi = [
        {
            "id": fase["id"],
            "nome": fase["nome"],
            "cosa_comprende": fase["perche_qui"],
            "termini": [
                {"termine": termine["termine"], "definizione": testo_definizione(termine)}
                for termine in fase["termini"]
            ],
        }
        for fase in catalogo["fasi"]
    ]
    sistema = (
        "Dividi un testo sulla protezione internazionale nelle fasi della procedura, in ordine: "
        "presentazione della domanda, attesa della convocazione al colloquio, audizione, decisione, impugnazione. "
        "Rispondi solo con un oggetto JSON. "
        "Ogni estratto deve essere copiato dal documento, carattere per carattere. "
        "Una stessa frase appartiene a una sola fase, quella più adatta. "
        "L'attesa è solo il periodo prima del colloquio: il centro di accoglienza e l'avviso del giorno dell'audizione. "
        "La comunicazione dell'esito, il riconoscimento o il rigetto appartengono alla decisione, non all'attesa."
    )
    utente = {
        "fasi": fasi,
        "istruzioni": [
            "Per ogni fase copia i paragrafi che parlano dei suoi termini, da 1 a 8 estratti.",
            "Ogni estratto è un paragrafo o una risposta del testo, non l'intero documento.",
            "Non inventare e non riassumere.",
            "Se la fase non c'è nel testo, restituisci una lista vuota.",
            "Non mettere lo stesso estratto in due fasi.",
            "Non assegnare all'attesa i paragrafi che spiegano come viene comunicata la decisione.",
        ],
        "formato": {"fasi": [{"id": "id della fase", "estratti": ["testo copiato"]}]},
        "documento": documento,
    }
    dati, uso = chiedi_json(
        [
            {"role": "system", "content": sistema},
            {"role": "user", "content": json.dumps(utente, ensure_ascii=False)},
        ]
    )
    per_fase: dict[str, list[dict]] = {fase["id"]: [] for fase in catalogo["fasi"]}
    for voce in dati.get("fasi") or []:
        if voce.get("id") not in per_fase:
            continue
        for citazione in (voce.get("estratti") or [])[:12]:
            if not isinstance(citazione, str):
                continue
            trovato = trova_estratto(documento, citazione.strip())
            if not trovato:
                continue
            inizio, fine = trovato
            if fine - inizio < 40:
                continue
            per_fase[voce["id"]].append(
                {"testo": documento[inizio:fine], "inizio": inizio, "fine": fine}
            )
    candidati = []
    for fase_id, estratti in per_fase.items():
        for estratto in estratti:
            candidati.append({**estratto, "fase": fase_id})
    tenuti = tieni_senza_sovrapposizioni(candidati)
    puliti: dict[str, list[dict]] = {fase["id"]: [] for fase in catalogo["fasi"]}
    for voce in tenuti:
        fase_id = voce.pop("fase")
        puliti[fase_id].append(voce)
    for estratti in puliti.values():
        estratti.sort(key=lambda voce: voce["inizio"])
    return puliti, uso


def classifica_blocco(frasi: list[dict], fase: dict, partenza: int) -> tuple[list[dict], dict]:
    termini = [
        {"id": termine["id"], "termine": termine["termine"], "definizione": testo_definizione(termine)}
        for termine in fase["termini"]
    ]
    sistema = (
        "Assegni ogni frase al termine o ai termini della fase di cui parla davvero, confrontandola con le definizioni. "
        "Rispondi solo con un oggetto JSON. "
        "Il punteggio dice quanto quella frase, da sola, fa capire il termine a chi non conosce la procedura. "
        "Usa tutto l'intervallo da 0 a 1. "
        "Fra 0,15 e 0,35 se la frase nomina il termine ma non lo spiega. "
        "Fra 0,45 e 0,65 se ne spiega solo una parte. "
        "Fra 0,75 e 1 solo se dalla frase si capisce chi è o che cos'è, senza dover sapere altro. "
        "Non usare sempre lo stesso numero. "
        "Non assegnare un termine solo perché una parola somiglia. "
        "Una frase può avere più termini, oppure nessuno."
    )
    utente = {
        "fase": fase["nome"],
        "termini": termini,
        "nota": (
            "Assegna il termine se la frase parla di ciò che dice la definizione, anche con altre parole. "
            "Colloquio e intervista con la Commissione sono l'audizione."
            if fase["id"] == "audizione"
            else "Assegna il termine se la frase parla di ciò che dice la definizione, anche con altre parole."
        ),
        "frasi": [{"indice": partenza + indice, "testo": frase["testo"][:700]} for indice, frase in enumerate(frasi)],
        "formato": {
            "assegnazioni": [
                {"indice": "numero della frase", "termini": [{"id": "id del termine", "punteggio": "numero fra 0 e 1"}]}
            ]
        },
    }
    return chiedi_json(
        [
            {"role": "system", "content": sistema},
            {"role": "user", "content": json.dumps(utente, ensure_ascii=False)},
        ]
    )


def risolvi_termine(raw, fase: dict) -> str | None:
    if not isinstance(raw, str):
        return None
    chiave = raw.casefold().strip()
    for termine in fase["termini"]:
        if chiave in {
            termine["id"].casefold(),
            termine["termine"].casefold(),
            termine["nome_breve"].casefold(),
        }:
            return termine["id"]
    return None


def integra_definizioni(frasi: list[dict], fase: dict, per_frase: dict[int, dict[str, float]]) -> None:
    """Il testo dice colloquio o intervista dove il dizionario dice audizione."""
    if fase["id"] != "audizione":
        return
    for indice, frase in enumerate(frasi):
        basso = frase["testo"].casefold()
        gia = per_frase.setdefault(indice, {})
        altri = [valore for valore in gia.values()]
        base = round(sum(altri) / len(altri), 2) if altri else 0.55
        if any(parola in basso for parola in ("colloquio", "intervista", "audizione")) and "audizione" not in gia:
            gia["audizione"] = base
        if "commissione territoriale" in basso and "commissione_territoriale" not in gia:
            gia["commissione_territoriale"] = base
    if fase["id"] == "attesa_e_convocazione":
        for indice, frase in enumerate(frasi):
            basso = frase["testo"].casefold()
            if "convocaz" not in basso:
                continue
            gia = per_frase.setdefault(indice, {})
            if "lettera_di_convocazione" in gia:
                continue
            altri = [valore for valore in gia.values()]
            gia["lettera_di_convocazione"] = round(sum(altri) / len(altri), 2) if altri else 0.45


def punteggi_fase(frasi: list[dict], fase: dict) -> tuple[dict[str, list[float]], dict]:
    per_frase: dict[int, dict[str, float]] = {}
    uso_totale: dict = {}
    for partenza in range(0, len(frasi), 30):
        blocco = frasi[partenza : partenza + 30]
        dati, uso = classifica_blocco(blocco, fase, partenza)
        somma_uso(uso_totale, uso)
        for voce in dati.get("assegnazioni") or []:
            try:
                indice = int(voce.get("indice"))
            except (TypeError, ValueError):
                continue
            if indice < partenza or indice >= partenza + len(blocco):
                continue
            for termine in voce.get("termini") or []:
                termine_id = risolvi_termine(termine.get("id"), fase)
                if not termine_id or termine_id in per_frase.get(indice, {}):
                    continue
                per_frase.setdefault(indice, {})[termine_id] = round(sistema_punteggio(termine.get("punteggio")), 2)
    integra_definizioni(frasi, fase, per_frase)
    raccolti: dict[str, list[float]] = {termine["id"]: [] for termine in fase["termini"]}
    for assegnati in per_frase.values():
        for termine_id, voto in assegnati.items():
            raccolti[termine_id].append(voto)
    return raccolti, uso_totale


def analizza_percorso(percorso: Path, nome_originale: str | None = None, avanzamento=None) -> dict:
    percorso = Path(percorso)
    documento = percorso.read_text(encoding="utf-8")
    nome = nome_originale or percorso.name
    catalogo = carica_catalogo()
    cartella = cartella_risultato(nome)
    cartella.mkdir(parents=True, exist_ok=True)
    uso_totale: dict = {}

    def fatto(passo: int, etichetta: str) -> None:
        if avanzamento:
            avanzamento(passo, PASSI, etichetta)

    estratti_per_fase, uso = dividi_in_fasi(documento, catalogo)
    aggiungi_paragrafi_mancanti(documento, catalogo, estratti_per_fase)
    somma_uso(uso_totale, uso)
    fatto(1, "Suddivisione in fasi")

    fasi_salvate = []
    for indice, fase in enumerate(catalogo["fasi"], start=1):
        estratti = estratti_per_fase.get(fase["id"]) or []
        frasi = []
        for estratto in estratti:
            frasi.extend(spezza_frasi(estratto["testo"], estratto["inizio"]))
        if frasi:
            raccolti, uso = punteggi_fase(frasi, fase)
            somma_uso(uso_totale, uso)
        else:
            raccolti = {termine["id"]: [] for termine in fase["termini"]}
        termini = []
        for termine in fase["termini"]:
            punteggi = raccolti.get(termine["id"]) or []
            voto = mediana_intensita([{"punteggio": valore} for valore in punteggi])
            termini.append(
                {
                    "id": termine["id"],
                    "termine": termine["termine"],
                    "nome_breve": termine["nome_breve"],
                    "definizione": termine["definizione"],
                    "condizioni": termine.get("condizioni") or [],
                    "ordine": termine["ordine"],
                    "punteggio": voto,
                    "livello": livello_da_punteggio(voto) if voto is not None else None,
                    "frasi": len(punteggi),
                }
            )
        fasi_salvate.append(
            {
                "id": fase["id"],
                "nome": fase["nome"],
                "ordine": fase["ordine"],
                "perche_qui": fase["perche_qui"],
                "estratti": estratti,
                "termini": termini,
            }
        )
        fatto(1 + indice, fase["nome"])

    risultato = {
        "id": cartella.name,
        "nome_file": nome,
        "modello": MODELLO,
        "creato": datetime.now(timezone.utc).isoformat(),
        "usage": uso_totale,
        "fasi": fasi_salvate,
    }
    (cartella / "percorso.json").write_text(
        json.dumps(risultato, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    meta_percorso = cartella / "meta.json"
    if meta_percorso.is_file():
        meta = json.loads(meta_percorso.read_text(encoding="utf-8"))
    else:
        meta = {"id": cartella.name, "nome_file": nome}
    meta["percorso"] = "completato"
    meta_percorso.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return risultato


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Uso: python3 percorso.py <file.md> [nome originale]")
    origine = Path(sys.argv[1])
    if not origine.is_absolute():
        origine = Path.cwd() / origine
    nome = sys.argv[2] if len(sys.argv) > 2 else None
    esito = analizza_percorso(origine, nome_originale=nome, avanzamento=print)
    print(f"Percorso salvato per {esito['id']}", flush=True)


if __name__ == "__main__":
    main()
