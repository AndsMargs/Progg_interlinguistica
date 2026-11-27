#!/usr/bin/env python3
"""STM-LLM: etichette di topic a partire dalle parole della nuvola.

La nuvola di un aspetto fa la parte dell'elenco di keyword. Nel disegno
originale quelle keyword sarebbero Highest Probability, FREX e Lift di uno
Structural Topic Model. Qui non stimiamo un topic model, quindi non
inventiamo FREX né Lift: il peso di ogni parola è la sua frequenza nel testo.

I passaggi, nell'ordine, sono:

1. Analisi delle keyword: si tengono le parole della nuvola, al massimo 28,
   già ordinate per frequenza.
2. Quanti topic: 2 se le parole sono al massimo 12, 3 se sono di più.
   Con meno di 4 parole non si costruisce nessun topic.
3. Generazione: il modello ripartisce tutte le parole date e propone
   un'etichetta e una frase. Non può aggiungere parole.
4. Controllo interno, su cinque dimensioni da 0 a 1:
   coerenza, copertura, specificità, distinguibilità, ancoraggio lessicale.
   L'ancoraggio è il più stretto: l'etichetta deve poggiare sulle parole.
5. Se un topic non passa, il modello lo corregge e il controllo si ripete.
   Dopo due tentativi un topic ancora debole resta segnato come da rivedere
   e, se ne avevamo chiesti 3, non viene mostrato: tre topic solo se reggono.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

_FREQUENZA = re.compile(r"\s*\(\d+\)\s*$")

METODO = "STM-LLM"
VERSIONE = 1
MASSIMO_PAROLE = 28
SOGLIA = 0.60
SOGLIA_ANCORAGGIO = 0.70
TENTATIVI = 2

DIMENSIONI = ("coerenza", "copertura", "specificita", "distinguibilita", "ancoraggio")


def quanti_topic(numero_parole: int) -> int:
    """2 topic fino a 12 parole, 3 solo quando le parole sono di più."""
    if numero_parole < 4:
        return 0
    if numero_parole <= 12:
        return 2
    return 3


def parole_nuvola(aggregato: list[dict] | None) -> list[dict]:
    """Le stesse parole che la nuvola può disegnare, con la loro frequenza."""
    voci = []
    viste: set[str] = set()
    for voce in aggregato or []:
        parola = str(voce.get("parola") or "").strip()
        frequenza = int(voce.get("frequenza") or 0)
        chiave = parola.casefold()
        if not parola or frequenza <= 0 or chiave in viste:
            continue
        viste.add(chiave)
        voci.append({"parola": parola, "frequenza": frequenza})
    voci.sort(key=lambda voce: (-voce["frequenza"], voce["parola"].casefold()))
    return voci[:MASSIMO_PAROLE]


def topic_gia_fatto(topic: dict | None) -> bool:
    if not isinstance(topic, dict):
        return False
    return (
        topic.get("metodo") == METODO
        and topic.get("versione") == VERSIONE
        and "errore" not in topic
    )


def _chiedi(messaggi: list[dict]) -> tuple[dict, dict]:
    from pipeline import chiedi_modello

    contenuto, uso = chiedi_modello(messaggi)
    return json.loads(contenuto), uso or {}


def _somma(totale: dict, uso: dict) -> None:
    totale["prompt_tokens"] = totale.get("prompt_tokens", 0) + int(uso.get("prompt_tokens") or 0)
    totale["completion_tokens"] = totale.get("completion_tokens", 0) + int(uso.get("completion_tokens") or 0)


def _elenco(parole: list[dict]) -> str:
    return ", ".join(f"{voce['parola']} ({voce['frequenza']})" for voce in parole)


def _genera(parole: list[dict], quanti: int, contesto: str, critiche: list[dict] | None) -> tuple[list[dict], dict]:
    sistema = (
        "Ripartisci le parole date in topic e dai a ciascuno un'etichetta in italiano. "
        "Puoi usare solo le parole che ti vengono passate, scritte come sono. "
        "Non aggiungere concetti che quelle parole non dicono. "
        "L'etichetta è breve e dice il filo comune delle sue parole. "
        "La descrizione è una sola frase, ancorata a quelle parole. "
        "Ogni parola sta in un solo topic. "
        "Rispondi solo con un oggetto JSON."
    )
    utente = {
        "contesto": contesto,
        "quanti_topic": quanti,
        "parole_con_frequenza": _elenco(parole),
        "istruzioni": [
            f"Costruisci esattamente {quanti} topic.",
            "Le parole più frequenti pesano di più: sono quelle che il testo ripete.",
            "Non lasciare fuori una parola se puoi metterla in un topic senza forzare il senso.",
            "Due etichette non devono dire la stessa cosa.",
        ],
        "formato": {
            "topic": [
                {"etichetta": "nome breve", "descrizione": "una frase", "parole": ["parola data"]}
            ]
        },
    }
    if critiche:
        utente["correzione"] = critiche
        utente["istruzioni"].append(
            "I topic segnati non vanno bene. Tieni le parole, cambia l'etichetta "
            "e la frase secondo le criticità. Se un gruppo mescola sensi diversi, "
            "sposta le parole, ma resta nel numero di topic chiesto."
        )
    dati, uso = _chiedi(
        [
            {"role": "system", "content": sistema},
            {"role": "user", "content": json.dumps(utente, ensure_ascii=False)},
        ]
    )
    return list(dati.get("topic") or []), uso


def _forma_ammessa(testo: str) -> str:
    from pipeline import normalizza_voce

    return normalizza_voce(_FREQUENZA.sub("", str(testo).strip()))


def _normalizza(gruppi: list[dict], parole: list[dict], quanti: int) -> list[dict]:
    ammesse = {_forma_ammessa(voce["parola"]): voce["parola"] for voce in parole}
    usate: set[str] = set()
    puliti = []
    for gruppo in gruppi:
        if len(puliti) >= quanti:
            break
        tenute = []
        for grezza in gruppo.get("parole") or []:
            chiave = _forma_ammessa(grezza)
            if chiave in ammesse and chiave not in usate:
                usate.add(chiave)
                tenute.append(ammesse[chiave])
        if len(tenute) < 2:
            continue
        etichetta = str(gruppo.get("etichetta") or "").strip()
        descrizione = str(gruppo.get("descrizione") or "").strip()
        if not etichetta:
            continue
        puliti.append({"etichetta": etichetta, "descrizione": descrizione, "parole": tenute})
    return puliti


def _valuta(gruppi: list[dict], parole: list[dict]) -> tuple[list[dict], dict]:
    sistema = (
        "Controlli le etichette dei topic. Non ne scrivi di nuove. "
        "Per ogni topic dai cinque punteggi da 0 a 1. "
        "coerenza: le parole stanno bene insieme sotto quell'etichetta. "
        "copertura: l'etichetta racconta le parole del topic, non solo una. "
        "specificita: l'etichetta dice qualcosa di preciso, non una formula vuota. "
        "distinguibilita: non si confonde con le altre etichette. "
        "ancoraggio: l'etichetta non introduce idee assenti dalle parole. "
        "Se l'etichetta nomina un concetto che le parole non supportano, "
        "l'ancoraggio sta sotto 0,5. "
        "Rispondi solo con un oggetto JSON."
    )
    utente = {
        "parole_ammesse": [voce["parola"] for voce in parole],
        "topic": [
            {"etichetta": gruppo["etichetta"], "descrizione": gruppo["descrizione"], "parole": gruppo["parole"]}
            for gruppo in gruppi
        ],
        "formato": {
            "topic": [
                {
                    "etichetta": "la stessa etichetta",
                    "coerenza": 0.0,
                    "copertura": 0.0,
                    "specificita": 0.0,
                    "distinguibilita": 0.0,
                    "ancoraggio": 0.0,
                    "criticita": "vuoto se va bene, altrimenti una frase",
                }
            ]
        },
    }
    dati, uso = _chiedi(
        [
            {"role": "system", "content": sistema},
            {"role": "user", "content": json.dumps(utente, ensure_ascii=False)},
        ]
    )
    return list(dati.get("topic") or []), uso


def _punteggio(valore) -> float:
    try:
        numero = float(valore)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, round(numero, 2)))


def _passa(qualita: dict) -> bool:
    if any(qualita[nome] < SOGLIA for nome in DIMENSIONI if nome != "ancoraggio"):
        return False
    return qualita["ancoraggio"] >= SOGLIA_ANCORAGGIO


def _unisci(gruppi: list[dict], giudizi: list[dict], tentativo: int) -> list[dict]:
    per_etichetta = {
        str(voce.get("etichetta") or "").strip().casefold(): voce
        for voce in giudizi
    }
    voci = []
    for indice, gruppo in enumerate(gruppi):
        giudizio = per_etichetta.get(gruppo["etichetta"].casefold())
        if giudizio is None and indice < len(giudizi):
            giudizio = giudizi[indice]
        giudizio = giudizio or {}
        qualita = {nome: _punteggio(giudizio.get(nome)) for nome in DIMENSIONI}
        voci.append(
            {
                "etichetta": gruppo["etichetta"],
                "descrizione": gruppo["descrizione"],
                "parole": gruppo["parole"],
                "qualita": qualita,
                "criticita": str(giudizio.get("criticita") or "").strip(),
                "esito": "valida" if _passa(qualita) else "da_rivedere",
                "tentativi": tentativo,
            }
        )
    return voci


def _visibili(voci: list[dict], richiesti: int) -> None:
    valide = [voce for voce in voci if voce["esito"] == "valida"]
    altre = [voce for voce in voci if voce["esito"] != "valida"]
    if richiesti >= 3 and len(valide) >= 3:
        scelte = valide[:3]
    elif len(valide) >= 2:
        scelte = valide[:2]
    else:
        scelte = (valide + altre)[:2]
    scelte_id = {id(voce) for voce in scelte}
    for voce in voci:
        voce["visibile"] = id(voce) in scelte_id


def etichetta_topic(aggregato: list[dict], contesto: str = "") -> tuple[dict, dict]:
    """Esegue la metodologia e restituisce il blocco da salvare, più i token usati."""
    parole = parole_nuvola(aggregato)
    richiesti = quanti_topic(len(parole))
    uso = {"prompt_tokens": 0, "completion_tokens": 0}
    base = {
        "metodo": METODO,
        "versione": VERSIONE,
        "parole": len(parole),
        "richiesti": richiesti,
        "voci": [],
    }
    if richiesti == 0:
        return base, uso

    critiche = None
    voci: list[dict] = []
    for tentativo in range(1, TENTATIVI + 1):
        grezzi, consumo = _genera(parole, richiesti, contesto, critiche)
        _somma(uso, consumo)
        gruppi = _normalizza(grezzi, parole, richiesti)
        if len(gruppi) < 2:
            critiche = [
                {
                    "etichetta": "",
                    "criticita": (
                        "Servono almeno due topic, e ogni topic almeno due delle parole date, "
                        "scritte senza il numero di frequenza."
                    ),
                }
            ]
            continue
        giudizi, consumo = _valuta(gruppi, parole)
        _somma(uso, consumo)
        voci = _unisci(gruppi, giudizi, tentativo)
        if all(voce["esito"] == "valida" for voce in voci) and len(voci) >= min(2, richiesti):
            break
        critiche = [
            {
                "etichetta": voce["etichetta"],
                "parole": voce["parole"],
                "criticita": voce["criticita"] or "L'etichetta non è abbastanza ancorata alle parole o si confonde con un'altra.",
                "qualita": voce["qualita"],
            }
            for voce in voci
            if voce["esito"] != "valida"
        ]
    if richiesti >= 2 and len(voci) < 2:
        raise RuntimeError("Non sono usciti almeno due topic dalle parole della nuvola.")
    _visibili(voci, richiesti)
    base["voci"] = voci
    return base, uso


def applica_topic(aspetti: list[dict], lavoratori: int = 3) -> dict:
    """Calcola i topic mancanti. Quelli già salvati con questa versione non si rifanno."""
    uso = {"prompt_tokens": 0, "completion_tokens": 0}
    mancanti = []
    for aspetto in aspetti:
        concetto = aspetto.get("concetto") or {}
        if topic_gia_fatto(concetto.get("topic")):
            continue
        if "aggregato" not in concetto and "frequenze" not in concetto:
            continue
        mancanti.append(aspetto)
    if not mancanti:
        return uso

    def lavoro(aspetto: dict) -> tuple[str, dict, dict]:
        try:
            topic, consumo = etichetta_topic(
                (aspetto.get("concetto") or {}).get("aggregato") or [],
                aspetto.get("nome") or "",
            )
        except Exception as errore:  # noqa: BLE001 - un aspetto non blocca gli altri
            topic = {
                "metodo": METODO,
                "versione": VERSIONE,
                "errore": str(errore),
                "voci": [],
            }
            consumo = {}
        return aspetto.get("id") or "", topic, consumo

    fatti: dict[str, tuple[dict, dict]] = {}
    with ThreadPoolExecutor(max_workers=lavoratori) as esecutore:
        for futuro in as_completed(esecutore.submit(lavoro, aspetto) for aspetto in mancanti):
            aspetto_id, topic, consumo = futuro.result()
            fatti[aspetto_id] = (topic, consumo)
            _somma(uso, consumo)
    for aspetto in aspetti:
        coppia = fatti.get(aspetto.get("id") or "")
        if not coppia:
            continue
        concetto = aspetto.setdefault("concetto", {})
        concetto["topic"] = coppia[0]
    return uso
