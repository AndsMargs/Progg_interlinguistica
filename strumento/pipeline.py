#!/usr/bin/env python3
"""Analizza un documento sugli aspetti del dizionario e salva l'esito."""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIZIONARIO = ROOT / "dizionario_concetti_protezione.json"
CHIAVE = ROOT / "chiave_gpt.txt"
RISULTATI = ROOT / "risultati"
MODELLO = "gpt-4.1-mini"
API_URL = "https://api.openai.com/v1/chat/completions"

LIVELLI = (
    (0.20, "Per niente comprensibile"),
    (0.40, "Poco comprensibile"),
    (0.60, "Moderatamente comprensibile"),
    (0.80, "Abbastanza comprensibile"),
    (1.01, "Molto comprensibile"),
)


def livello_da_punteggio(punteggio: float) -> str:
    for soglia, nome in LIVELLI:
        if punteggio <= soglia:
            return nome
    return LIVELLI[-1][1]


def leggi_chiave() -> str:
    if not CHIAVE.is_file():
        raise FileNotFoundError(f"Manca la chiave in {CHIAVE.name}")
    chiave = CHIAVE.read_text(encoding="utf-8").strip().strip('"').strip("'")
    if not chiave.startswith("sk-"):
        raise ValueError("La chiave in chiave_gpt.txt non ha il formato atteso.")
    return chiave


def carica_dizionario() -> dict:
    return json.loads(DIZIONARIO.read_text(encoding="utf-8"))


def etichetta_ricercabile(termine: str) -> str:
    testo = re.sub(r"^\([^)]*\)\s*", "", termine)
    testo = re.sub(r"\s*\([^)]*\)\s*$", "", testo)
    return testo.replace("“", "").replace("”", "").replace('"', "").strip()


def conta_occorrenze(testo: str, etichetta: str) -> int:
    if len(etichetta) < 4:
        return 0
    return len(re.findall(re.escape(etichetta), testo, flags=re.IGNORECASE))


PAROLE_VUOTE = {
    "anche", "ancora", "che", "chi", "come", "con", "contro", "cui", "dei", "del", "della", "delle",
    "dello", "dopo", "dove", "fare", "fatto", "gli", "hai", "hanno", "lei", "loro", "nel", "nella", "nelle",
    "non", "per", "perché", "perche", "più", "piu", "qua", "qui", "sei", "sia", "solo", "sono",
    "sua", "sue", "sui", "sul", "sulla", "tra", "tua", "tue", "una", "uno",
}

# Articoli e preposizioni dentro un termine: restano come sono, non diventano un altro lemma.
PAROLE_INVARIATE = PAROLE_VUOTE | {
    "a", "ad", "ai", "al", "all", "alla", "alle", "allo", "agli",
    "da", "dai", "dal", "dall", "dalla", "dalle", "dallo", "dagli",
    "di", "d", "e", "ed", "i", "il", "in", "la", "le", "lo", "o", "od", "un",
}

DIPENDENZE = Path(__file__).resolve().parent / "dipendenze"
_NLP = None
_LOCK_NLP = threading.Lock()
_LOCK_LEMMI = threading.Lock()
_LETTURE: dict[int, "_Lettura"] = {}
_TOKEN_TESTO = re.compile(r"[^\W_]+", re.UNICODE)
# Parole grammaticali: si lascia la forma del testo. Il lemma di «della» sarebbe «di il».
_CHIUSURA = {"ADP", "DET", "CCONJ", "SCONJ", "PART", "PRON", "NUM", "PUNCT", "SPACE", "SYM", "X"}


def chiave_parola(parola: str) -> str:
    testo = parola.casefold().replace("’", "'").replace("`", "'")
    testo = unicodedata.normalize("NFKD", testo)
    return "".join(carattere for carattere in testo if not unicodedata.combining(carattere))


def _token_invariato(token: str) -> bool:
    chiave = chiave_parola(token)
    if not chiave or chiave in PAROLE_INVARIATE or len(chiave) <= 2:
        return True
    return any(carattere.isdigit() for carattere in token)


class _Lettura:
    """Token del testo, con la superficie, il lemma e la categoria nella frase."""

    def __init__(
        self,
        superfici: list[str],
        grezzi: list[str],
        lemmi: list[str],
        categorie: list[str],
        maiuscole: list[bool],
        conteggi: dict[str, int],
    ):
        self.superfici = superfici
        self.grezzi = grezzi
        self.lemmi = lemmi
        self.categorie = categorie
        self.maiuscole = maiuscole
        self.conteggi = conteggi


def prepara_lemmatizzatore():
    """Carica il modello italiano di spaCy."""
    global _NLP
    if _NLP is None:
        if str(DIPENDENZE) not in sys.path:
            sys.path.insert(0, str(DIPENDENZE))
        import spacy

        _NLP = spacy.load("it_core_news_lg", exclude=["parser", "ner"])
        _NLP.max_length = 2_000_000
    return _NLP


def _forma_da_token(token, precedente: str) -> str:
    superficie = token.text.casefold().replace("’", "'").replace("`", "'")
    # «banca dati» è il nome dell'archivio: «dati» non diventa «dato».
    if chiave_parola(superficie) == "dati" and chiave_parola(precedente) in {"banca", "banche"}:
        return "dati"
    if token.pos_ in _CHIUSURA or _token_invariato(token.text):
        return superficie
    lemma = (token.lemma_ or token.text).casefold().replace("’", "'").replace("`", "'")
    if not lemma or any(carattere.isspace() for carattere in lemma):
        return superficie
    if not any(carattere.isalpha() for carattere in lemma):
        return superficie
    return lemma


def _genere_token(token) -> str:
    generi = set(token.morph.get("Gender"))
    if "Fem" in generi and "Masc" not in generi:
        return "Fem"
    if "Masc" in generi and "Fem" not in generi:
        return "Masc"
    return ""


def _genere_nome(forma: str, genere: str) -> str:
    if genere:
        return genere
    if forma.endswith("a") and not forma.endswith("ista"):
        return "Fem"
    if forma.endswith("o"):
        return "Masc"
    return ""


def _aggettivo_accordato(lemma: str, genere: str) -> str:
    """Singolare, con il genere del nome: «mediche» con «cura» diventa «medica»."""
    if genere == "Fem" and len(lemma) > 3 and lemma.endswith("o"):
        return lemma[:-1] + "a"
    return lemma


def _testa_nome(indice: int, categorie: list[str]) -> int | None:
    for posizione in range(indice - 1, max(-1, indice - 5), -1):
        categoria = categorie[posizione]
        if categoria in {"NOUN", "PROPN"}:
            return posizione
        if categoria in {"VERB", "AUX"}:
            break
    for posizione in range(indice + 1, min(len(categorie), indice + 3)):
        categoria = categorie[posizione]
        if categoria in {"NOUN", "PROPN"}:
            return posizione
        if categoria not in {"ADV", "DET", "ADP"}:
            break
    return None


def _accorda(lemmi: list[str], categorie: list[str], generi: list[str]) -> None:
    for indice, categoria in enumerate(categorie):
        if categoria != "ADJ":
            continue
        testa = _testa_nome(indice, categorie)
        genere = generi[testa] if testa is not None else ""
        if not genere:
            genere = generi[indice]
        lemmi[indice] = _aggettivo_accordato(lemmi[indice], genere)


def _radice(voce: str) -> str:
    if len(voce) <= 4:
        return voce
    if voce.endswith(("he", "hi")):
        return voce[:-2]
    if voce.endswith(("o", "a", "i", "e")):
        return voce[:-1]
    return voce


def _compatibile(pezzo: str, superficie: str, grezzo: str, lemma: str) -> bool:
    if pezzo in {superficie, grezzo, lemma}:
        return True
    if len(pezzo) <= 4:
        return False
    base = _radice(pezzo)
    return base == _radice(superficie) or base == _radice(grezzo) or base == _radice(lemma)


def _leggi_testo(documento: str) -> _Lettura:
    chiave = id(documento)
    with _LOCK_LEMMI:
        trovato = _LETTURE.get(chiave)
    if trovato is not None:
        return trovato
    with _LOCK_NLP:
        doc = prepara_lemmatizzatore()(documento)
    superfici: list[str] = []
    lemmi: list[str] = []
    categorie: list[str] = []
    generi: list[str] = []
    maiuscole: list[bool] = []
    precedente = ""
    for token in doc:
        if token.is_space or token.is_punct:
            continue
        forma = _forma_da_token(token, precedente)
        if not forma:
            continue
        superfici.append(token.text.casefold().replace("’", "'").replace("`", "'"))
        lemmi.append(forma)
        categoria = token.pos_ or ""
        categorie.append(categoria)
        genere = _genere_token(token)
        if categoria in {"NOUN", "PROPN"}:
            genere = _genere_nome(forma, genere)
        generi.append(genere)
        maiuscole.append(bool(token.text[:1].isupper()))
        precedente = token.text
    grezzi = list(lemmi)
    _accorda(lemmi, categorie, generi)
    conteggi: dict[str, int] = {}

    def aggiungi(voce: str) -> None:
        conteggi[voce] = conteggi.get(voce, 0) + 1

    for pezzo in lemmi:
        aggiungi(pezzo)
    for n in range(2, 7):
        for i in range(len(lemmi) - n + 1):
            aggiungi(" ".join(lemmi[i:i + n]))
    lettura = _Lettura(superfici, grezzi, lemmi, categorie, maiuscole, conteggi)
    with _LOCK_LEMMI:
        if len(_LETTURE) > 2:
            _LETTURE.clear()
        _LETTURE[chiave] = lettura
    return lettura


def rilascia_testo() -> None:
    """Libera l'analisi linguistica tenuta in memoria."""
    with _LOCK_LEMMI:
        _LETTURE.clear()


def lemmi_nel_testo(documento: str) -> dict[str, int]:
    """Quante volte compare ogni lemma, anche dentro un termine di più parole."""
    return _leggi_testo(documento).conteggi


def normalizza_voce(parola: str, documento: str | None = None) -> str:
    """Minuscolo e lemma della parola, scelto sulle frasi del documento.

    Un verbo va all'infinito e un nome al singolare. L'aggettivo di un termine
    concorda con quel nome: «cure mediche» diventa «cura medica». Senza il
    documento non si indovina la categoria: si lascia la forma già scritta, solo in minuscolo.
    """
    pulita = _pulisci_parola(parola).replace("’", "'").replace("`", "'")
    pezzi = [
        token.casefold().replace("’", "'").replace("`", "'")
        for token in _TOKEN_TESTO.findall(pulita)
    ]
    if not pezzi:
        return ""
    if not documento:
        return " ".join(pezzi)
    lettura = _leggi_testo(documento)
    n = len(pezzi)
    candidati: list[tuple[str, str, bool]] = []
    superfici = lettura.superfici

    def corrisponde(inizio: int, elastico: bool) -> bool:
        for scarto, pezzo in enumerate(pezzi):
            if elastico:
                if not _compatibile(
                    pezzo,
                    superfici[inizio + scarto],
                    lettura.grezzi[inizio + scarto],
                    lettura.lemmi[inizio + scarto],
                ):
                    return False
            elif superfici[inizio + scarto] != pezzo:
                return False
        return True

    for elastico in (False, True) if n > 1 else (False,):
        if candidati:
            break
        for i in range(len(superfici) - n + 1):
            if not corrisponde(i, elastico):
                continue
            testa = 0
            for scarto, categoria in enumerate(lettura.categorie[i:i + n]):
                if categoria not in _CHIUSURA:
                    testa = scarto
                    break
            candidati.append(
                (
                    " ".join(lettura.lemmi[i:i + n]),
                    lettura.categorie[i + testa],
                    lettura.maiuscole[i + testa],
                )
            )
    if not candidati:
        return " ".join(pezzi)
    scelto = _scegli_lemma(candidati)
    # «è stato» non deve rubare il nome «Stato».
    if scelto.split()[0] in {"essere", "avere"} and pezzi[0] not in {"essere", "avere"}:
        return " ".join(pezzi)
    return scelto


def _piu_frequente(voci: list[str]) -> str:
    conteggi: dict[str, int] = {}
    for voce in voci:
        conteggi[voce] = conteggi.get(voce, 0) + 1
    return max(conteggi.items(), key=lambda voce: (voce[1], -len(voce[0])))[0]


def _scegli_lemma(candidati: list[tuple[str, str, bool]]) -> str:
    """Preferisce il nome, se c'è, rispetto all'ausiliare omografo."""
    nomi_propri = [
        forma
        for forma, categoria, maiuscola in candidati
        if maiuscola and categoria in {"NOUN", "PROPN"} and " " not in forma
    ]
    if nomi_propri:
        return _piu_frequente(nomi_propri)
    nomi = [
        forma
        for forma, categoria, _maiuscola in candidati
        if categoria in {"NOUN", "PROPN"} and forma.split()[0] not in {"essere", "avere", "dare", "fare"}
    ]
    if len(nomi) >= 2 or (nomi and len(nomi) / len(candidati) >= 0.2):
        return _piu_frequente(nomi)
    contenuti = [
        forma
        for forma, categoria, _maiuscola in candidati
        if categoria in {"NOUN", "PROPN", "ADJ", "VERB"} and forma.split()[0] not in {"essere", "avere"}
    ]
    if contenuti:
        return _piu_frequente(contenuti)
    altri = [forma for forma, _categoria, _maiuscola in candidati if forma.split()[0] not in {"essere", "avere"}]
    if altri:
        return _piu_frequente(altri)
    return _piu_frequente([forma for forma, _categoria, _maiuscola in candidati])


def chiave_lemma(parola: str, documento: str | None = None) -> str:
    """Chiave unica per le forme flesse della stessa parola o dello stesso termine."""
    return " ".join(chiave_parola(token) for token in normalizza_voce(parola, documento).split())


def _voci_normalizzate(parole: list[str], documento: str | None = None) -> list[str]:
    """Elenco unico, già in minuscolo e al lemma."""
    viste: set[str] = set()
    uscita = []
    for parola in parole:
        forma = normalizza_voce(parola, documento)
        chiave = chiave_parola(forma)
        if not forma or not chiave or chiave in viste:
            continue
        if " " not in forma and chiave in PAROLE_VUOTE:
            continue
        viste.add(chiave)
        uscita.append(forma)
    return uscita


def _pulisci_parola(voce) -> str:
    return str(voce).strip().strip(".,;:!?«»\"“”()[]")


def chiedi_parole_aspetti(documento: str, aspetti: list[dict]) -> tuple[dict[str, list[str]], dict]:
    """Una sola chiamata: ogni parola va a un solo aspetto, così le nuvole non si ripetono."""
    sistema = (
        "Per ogni aspetto, scegli le parole del testo che lo distinguono dagli altri. "
        "Rispondi solo con un oggetto JSON. "
        "Ogni parola deve essere copiata dal testo, come è scritta. "
        "Una stessa parola può comparire in un solo aspetto. "
        "Niente articoli, preposizioni o congiunzioni. "
        "Preferisci parole singole. Usa due parole solo se sono un termine unico, "
        "per esempio «protezione internazionale»."
    )
    utente = {
        "aspetti": [
            {
                "id": aspetto.get("id"),
                "nome": aspetto.get("nome"),
                "cosa_osservare": aspetto.get("cosa_si_potra_osservare"),
            }
            for aspetto in aspetti
        ],
        "istruzioni": [
            "Circa 15 parole per aspetto, mai meno di 12 e mai più di 18.",
            "Le liste devono essere diverse: lette da sole, devono far capire di quale aspetto si tratta.",
            "Se una parola riguarda più aspetti, mettila solo in quello che richiama di più.",
            "Non riempire gli aspetti con le stesse parole di sfondo, come procedura, domanda, "
            "decisione o colloquio, salvo che quella parola sia l'oggetto di un solo aspetto.",
            "Non copiare le parole con cui è descritto l'aspetto.",
            "Non inventare parole e non parafrasarle.",
        ],
        "formato": {"aspetti": [{"id": "id dell'aspetto", "parole": ["parola copiata dal testo"]}]},
        "documento": documento,
    }
    contenuto, uso = chiedi_modello(
        [
            {"role": "system", "content": sistema},
            {"role": "user", "content": json.dumps(utente, ensure_ascii=False)},
        ]
    )
    dati = json.loads(contenuto)
    assegnate: dict[str, list[str]] = {}
    viste: set[str] = set()
    for voce in dati.get("aspetti") or []:
        aspetto_id = str(voce.get("id") or "")
        if not aspetto_id:
            continue
        parole = []
        for grezza in voce.get("parole") or []:
            parola = normalizza_voce(grezza, documento)
            if not parola or (" " not in parola and chiave_parola(parola) in PAROLE_VUOTE):
                continue
            try:
                chiave = chiave_lemma(parola, documento)
            except Exception:
                chiave = chiave_parola(parola)
            if not chiave or chiave in viste:
                continue
            viste.add(chiave)
            parole.append(parola)
        assegnate[aspetto_id] = parole
    return assegnate, uso or {}


def _eco_consegna(aspetto: dict) -> set[str]:
    """Parole della consegna, da non trattare come se fossero nel testo."""
    testo = aspetto.get("cosa_si_potra_osservare") or ""
    return {
        chiave_parola(token)
        for token in re.findall(r"[^\W\d_]{4,}", testo, flags=re.UNICODE)
    }


def _senza_eco(aspetto: dict, parole: list[str]) -> list[str]:
    eco = _eco_consegna(aspetto)
    pulite = []
    for parola in parole:
        pezzi = [chiave_parola(token) for token in parola.split()]
        if eco and all(pezzo in eco for pezzo in pezzi if pezzo):
            continue
        pulite.append(parola)
    return pulite


def applica_nuvole(documento: str, aspetti: list[dict]) -> dict:
    """Costruisce una nuvola diversa per ogni aspetto e la scrive sul risultato."""
    assegnate, uso = chiedi_parole_aspetti(documento, aspetti)
    for aspetto in aspetti:
        parole = _senza_eco(aspetto, assegnate.get(aspetto.get("id")) or [])
        aspetto["concetto"] = costruisci_concetto(documento, aspetto, parole)
    uso_extra = completa_nuvole_scarne(documento, aspetti)
    return {
        "prompt_tokens": (uso or {}).get("prompt_tokens", 0) + (uso_extra or {}).get("prompt_tokens", 0),
        "completion_tokens": (uso or {}).get("completion_tokens", 0) + (uso_extra or {}).get("completion_tokens", 0),
    }


def _chiave_assegnazione(parola: str, documento: str | None = None) -> str:
    try:
        return chiave_lemma(parola, documento)
    except Exception:
        return chiave_parola(parola)


def completa_nuvole_scarne(documento: str, aspetti: list[dict], minimo: int = 15) -> dict:
    """Se un aspetto ha poche parole, ne cerca altre nei passaggi già citati per quell'aspetto."""
    povere = []
    for aspetto in aspetti:
        if len((aspetto.get("concetto") or {}).get("aggregato") or []) >= minimo:
            continue
        passaggi = []
        for voce in (aspetto.get("estratti") or [])[:8]:
            testo = re.sub(r"\s+", " ", str(voce.get("testo") or "")).strip()
            if testo:
                passaggi.append(testo[:500])
        if passaggi:
            povere.append((aspetto, passaggi))
    if not povere:
        return {}

    gia_usate = []
    viste: set[str] = set()
    for aspetto in aspetti:
        for parola in (aspetto.get("concetto") or {}).get("parole") or []:
            chiave = _chiave_assegnazione(parola, documento)
            if chiave and chiave not in viste:
                viste.add(chiave)
                gia_usate.append(parola)

    sistema = (
        "Per gli aspetti che hanno ancora poche parole, scegline altre copiate dai passaggi. "
        "Rispondi solo con un oggetto JSON. "
        "Ogni parola deve essere copiata da un passaggio, non dalla descrizione dell'aspetto. "
        "Non ripetere una parola già usata. "
        "Preferisci parole singole. Due o tre parole solo se sono un termine unico, "
        "per esempio «Commissione territoriale» o «permesso di soggiorno»."
    )
    utente = {
        "aspetti": [
            {
                "id": aspetto.get("id"),
                "nome": aspetto.get("nome"),
                "parole_gia_presenti": len((aspetto.get("concetto") or {}).get("aggregato") or []),
                "passaggi": passaggi,
            }
            for aspetto, passaggi in povere
        ],
        "parole_gia_usate": gia_usate,
        "istruzioni": [
            "Aggiungi parole nuove fino ad arrivare a 15 per aspetto, senza superare 18.",
            "Se il passaggio non contiene altre parole adatte, fermati: non inventare e non riusare una parola già assegnata.",
            "Devono comparire nei passaggi.",
            "Non spezzare un termine in due voci.",
        ],
        "formato": {"aspetti": [{"id": "id dell'aspetto", "parole": ["parola copiata dal passaggio"]}]},
    }
    contenuto, uso = chiedi_modello(
        [
            {"role": "system", "content": sistema},
            {"role": "user", "content": json.dumps(utente, ensure_ascii=False)},
        ]
    )
    dati = json.loads(contenuto)
    aggiunte: dict[str, list[str]] = {}
    for voce in dati.get("aspetti") or []:
        aspetto_id = str(voce.get("id") or "")
        parole = []
        for grezza in voce.get("parole") or []:
            parola = normalizza_voce(grezza, documento)
            if not parola or (" " not in parola and chiave_parola(parola) in PAROLE_VUOTE):
                continue
            chiave = _chiave_assegnazione(parola, documento)
            if not chiave or chiave in viste:
                continue
            viste.add(chiave)
            parole.append(parola)
        aggiunte[aspetto_id] = parole

    for aspetto, _passaggi in povere:
        esistenti = (aspetto.get("concetto") or {}).get("parole") or []
        nuove = _senza_eco(aspetto, aggiunte.get(aspetto.get("id")) or [])
        aspetto["concetto"] = costruisci_concetto(documento, aspetto, esistenti + nuove)
    return uso or {}


def conta_frequenze(
    documento: str, parole: list[str], indice: dict[str, int] | None = None
) -> list[dict]:
    """Conta le forme già normalizzate, sommando anche i plurali nel testo."""
    if indice is None:
        indice = lemmi_nel_testo(documento)
    viste: set[str] = set()
    frequenze = []
    for parola in parole:
        forma = normalizza_voce(parola, documento)
        if len(forma) < 3 or (" " not in forma and chiave_parola(forma) in PAROLE_VUOTE):
            continue
        chiave = chiave_parola(forma)
        if chiave in viste:
            continue
        viste.add(chiave)
        occorrenze = int(indice.get(forma, 0))
        if occorrenze:
            frequenze.append({"parola": forma, "frequenza": occorrenze})
    frequenze.sort(key=lambda voce: (-voce["frequenza"], voce["parola"].casefold()))
    return frequenze


def aggrega_parole(voci: list[dict]) -> list[dict]:
    """Dopo il conteggio, porta ogni forma al lemma e somma le frequenze."""
    gruppi: dict[str, dict] = {}
    for voce in sorted(voci, key=lambda voce: -int(voce.get("frequenza") or 0)):
        parola = str(voce.get("parola") or "").strip()
        frequenza = int(voce.get("frequenza") or 0)
        if not parola or frequenza <= 0:
            continue
        chiave = chiave_lemma(parola)
        if not chiave:
            continue
        etichetta = normalizza_voce(parola)
        gruppo = gruppi.get(chiave)
        if gruppo is None:
            gruppi[chiave] = {"parola": etichetta, "frequenza": frequenza, "top": frequenza}
            continue
        gruppo["frequenza"] += frequenza
        if frequenza > gruppo["top"] or (
            frequenza == gruppo["top"] and len(etichetta) < len(gruppo["parola"])
        ):
            gruppo["parola"] = etichetta
            gruppo["top"] = max(gruppo["top"], frequenza)
    aggregato = [
        {"parola": gruppo["parola"], "frequenza": gruppo["frequenza"]}
        for gruppo in gruppi.values()
    ]
    aggregato.sort(key=lambda voce: (-voce["frequenza"], voce["parola"].casefold()))
    return _senza_unigrammi_coperti(aggregato)


def _senza_unigrammi_coperti(voci: list[dict]) -> list[dict]:
    """Se una parola sola è già dentro un termine, resta solo il termine."""
    coperti: set[str] = set()
    for voce in voci:
        parola = str(voce.get("parola") or "")
        if " " not in parola:
            continue
        for pezzo in parola.split():
            chiave = chiave_parola(pezzo)
            if chiave and chiave not in PAROLE_INVARIATE:
                coperti.add(chiave)
    return [
        voce
        for voce in voci
        if " " in str(voce.get("parola") or "") or chiave_parola(str(voce.get("parola") or "")) not in coperti
    ]


def unisci_termini(
    documento: str, parole: list[str], indice: dict[str, int] | None = None
) -> list[str]:
    """Se due parole della stessa nuvola formano un termine nel testo, le tiene insieme."""
    voci = []
    viste: set[str] = set()
    for parola in parole:
        pulita = _pulisci_parola(parola)
        chiave = chiave_parola(pulita)
        if not pulita or chiave in viste or chiave in PAROLE_VUOTE:
            continue
        viste.add(chiave)
        voci.append(pulita)

    if indice is None:
        indice = lemmi_nel_testo(documento)

    def occorrenze(frase: str) -> int:
        return int(indice.get(normalizza_voce(frase, documento), 0))

    aggiunte = []
    for parola in list(voci):
        pezzi = [
            pezzo
            for pezzo in parola.split()
            if chiave_parola(pezzo) not in PAROLE_INVARIATE and len(chiave_parola(pezzo)) >= 4
        ]
        if len(pezzi) < 2:
            continue
        volte_frase = occorrenze(parola)
        for pezzo in pezzi:
            chiave = chiave_parola(pezzo)
            if chiave in viste:
                continue
            volte = occorrenze(pezzo)
            if volte >= max(8, volte_frase * 6):
                viste.add(chiave)
                aggiunte.append(pezzo)
    voci.extend(aggiunte)
    if len(voci) < 2:
        return voci

    candidati = []
    for prima in voci:
        for seconda in voci:
            if chiave_parola(prima) == chiave_parola(seconda):
                continue
            if chiave_parola(prima) in chiave_parola(seconda) or chiave_parola(seconda) in chiave_parola(prima):
                continue
            for mezzo in (" ", " di ", " del ", " della ", " dei ", " per ", " non "):
                frase = f"{prima}{mezzo}{seconda}".strip()
                volte = occorrenze(frase)
                volte_prima = occorrenze(prima)
                volte_seconda = occorrenze(seconda)
                if volte < 1 or not volte_prima or not volte_seconda:
                    continue
                dentro_prima = volte / volte_prima
                dentro_seconda = volte / volte_seconda
                if dentro_prima >= 0.7 and dentro_seconda >= 0.7 and (
                    volte >= 2 or max(volte_prima, volte_seconda) <= 2
                ):
                    candidati.append((volte, frase, {prima, seconda}))
                elif volte >= 2 and max(dentro_prima, dentro_seconda) >= 0.7:
                    rara = prima if dentro_prima >= dentro_seconda else seconda
                    candidati.append((volte, frase, {rara}))
    candidati.sort(key=lambda voce: (-voce[0], -len(voce[1])))
    consumate: set[str] = set()
    termini = []
    for _volte, frase, da_togliere in candidati:
        chiavi = {chiave_parola(voce) for voce in da_togliere}
        if chiavi & consumate:
            continue
        consumate |= chiavi
        termini.append(frase)
    termini.extend(voce for voce in voci if chiave_parola(voce) not in consumate)
    return termini


def costruisci_concetto(documento: str, aspetto: dict, parole: list[str] | None = None) -> dict:
    """Normalizza le voci, poi conta e aggrega. Nuvola e topic usano questo elenco."""
    if parole is None:
        parole = []
    indice = lemmi_nel_testo(documento)
    parole = _voci_normalizzate(parole, documento)
    parole = _voci_normalizzate(unisci_termini(documento, parole, indice), documento)
    frequenze = conta_frequenze(documento, parole, indice)
    aggregato = aggrega_parole(frequenze)
    tenute = {voce["parola"] for voce in aggregato}
    return {
        "parole": [parola for parola in parole if parola in tenute],
        "frequenze": aggregato,
        "aggregato": aggregato,
    }


def concetti_per_aspetto(dizionario: dict, aspetto_id: str) -> list[dict]:
    voci = []
    for concetto in dizionario["concetti"]:
        if aspetto_id not in concetto.get("aspetti", []):
            continue
        voce = {
            "id": concetto["id"],
            "termine": concetto["termine"],
            "definizione": concetto["definizione"],
        }
        if concetto.get("glossa"):
            voce["glossa"] = concetto["glossa"]
        if concetto.get("condizioni"):
            voce["condizioni"] = concetto["condizioni"]
        if concetto.get("rinvii"):
            voce["rinvii"] = concetto["rinvii"]
        voci.append(voce)
    return voci


def metriche_locali(testo: str, concetti: list[dict]) -> dict:
    dettaglio = []
    presenti = 0
    occorrenze = 0
    for concetto in concetti:
        etichetta = etichetta_ricercabile(concetto["termine"])
        n = conta_occorrenze(testo, etichetta)
        if n:
            presenti += 1
        occorrenze += n
        dettaglio.append({"id": concetto["id"], "termine": etichetta, "occorrenze": n})
    collegati = len(concetti)
    return {
        "concetti_collegati": collegati,
        "concetti_presenti": presenti,
        "occorrenze_termini": occorrenze,
        "copertura_termini": round(presenti / collegati, 3) if collegati else 0,
        "termini": dettaglio,
    }


def chiedi_modello(messaggi: list[dict]) -> tuple[str, dict]:
    corpo = json.dumps(
        {
            "model": MODELLO,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": messaggi,
        }
    ).encode("utf-8")
    richiesta = urllib.request.Request(
        API_URL,
        data=corpo,
        headers={
            "Authorization": f"Bearer {leggi_chiave()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    ultimo_errore = None
    for tentativo in range(4):
        try:
            with urllib.request.urlopen(richiesta, timeout=180) as risposta:
                payload = json.loads(risposta.read().decode("utf-8"))
            contenuto = payload["choices"][0]["message"]["content"]
            return contenuto, payload.get("usage") or {}
        except urllib.error.HTTPError as errore:
            dettaglio = errore.read().decode("utf-8", "replace")[:500]
            ultimo_errore = RuntimeError(f"HTTP {errore.code}: {dettaglio}")
            credito_esaurito = "insufficient_quota" in dettaglio or "credit_balance_exhausted" in dettaglio
            if credito_esaurito or errore.code not in {429, 500, 502, 503}:
                raise ultimo_errore from errore
            time.sleep(2 ** tentativo)
        except urllib.error.URLError as errore:
            ultimo_errore = errore
            time.sleep(2 ** tentativo)
    raise RuntimeError(f"Chiamata al modello non riuscita: {ultimo_errore}")


def compatta(testo: str) -> tuple[str, list[int]]:
    caratteri = []
    mappa = []
    spazio_aperto = False
    for indice, carattere in enumerate(testo):
        if carattere.isspace():
            if caratteri and not spazio_aperto:
                caratteri.append(" ")
                mappa.append(indice)
            spazio_aperto = True
            continue
        caratteri.append(carattere)
        mappa.append(indice)
        spazio_aperto = False
    return "".join(caratteri), mappa


def trova_per_parole(documento: str, citazione: str) -> tuple[int, int] | None:
    parole = [parola.casefold() for parola in re.findall(r"\w+", citazione, flags=re.UNICODE)]
    if len(parole) < 6:
        return None
    presenti = list(re.finditer(r"\w+", documento, flags=re.UNICODE))
    bassi = [trovato.group().casefold() for trovato in presenti]
    migliore = 0
    intervallo = None
    for indice, token in enumerate(bassi):
        for origine, atteso in enumerate(parole):
            if token != atteso:
                continue
            lunghezza = 0
            while (
                indice + lunghezza < len(bassi)
                and origine + lunghezza < len(parole)
                and bassi[indice + lunghezza] == parole[origine + lunghezza]
            ):
                lunghezza += 1
            if lunghezza > migliore:
                migliore = lunghezza
                intervallo = (presenti[indice].start(), presenti[indice + lunghezza - 1].end())
        if migliore >= len(parole):
            break
    if intervallo is None or migliore < 8:
        return None
    inizio, fine = intervallo
    if fine - inizio < 25:
        return None
    return inizio, fine


def trova_estratto(documento: str, citazione: str) -> tuple[int, int] | None:
    citazione = citazione.strip()
    if len(citazione) < 25:
        return None
    posizione = documento.find(citazione)
    if posizione >= 0:
        return posizione, posizione + len(citazione)
    documento_compatto, mappa = compatta(documento)
    citazione_compatta, _ = compatta(citazione)
    if len(citazione_compatta) >= 25:
        posizione = documento_compatto.find(citazione_compatta)
        if posizione >= 0:
            inizio = mappa[posizione]
            ultimo = mappa[posizione + len(citazione_compatta) - 1]
            return inizio, ultimo + 1
    return trova_per_parole(documento, citazione)


def mediana_intensita(estratti: list[dict]) -> float | None:
    """Il voto del tachimetro è la mediana dei punteggi delle frasi, a due decimali."""
    centesimi = []
    for voce in estratti:
        if voce.get("punteggio") is None:
            continue
        centesimi.append(int((Decimal(str(voce["punteggio"])) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
    if not centesimi:
        return None
    centesimi.sort()
    mezzo = len(centesimi) // 2
    if len(centesimi) % 2:
        return centesimi[mezzo] / 100
    mediana = (Decimal(centesimi[mezzo - 1] + centesimi[mezzo]) / Decimal(2)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(mediana) / 100


def ricalibra_punteggio(punteggio: float, estratti: list[dict]) -> float:
    """Un caso misto non può restare alto: due aiuti e due ostacoli valgono circa 0,50."""
    positivi = sum(1 for voce in estratti if voce.get("effetto") == "aumenta")
    negativi = sum(1 for voce in estratti if voce.get("effetto") == "riduce")
    if positivi + negativi == 0:
        return round(min(punteggio, 0.40), 2)
    if negativi == 0:
        return round(min(punteggio, 0.88), 2)
    if abs(positivi - negativi) <= 1:
        return round(min(punteggio, 0.50 + 0.03 * (positivi - negativi)), 2)
    quota = (positivi - negativi) / (positivi + negativi)
    return round(min(punteggio, 0.50 + 0.35 * quota), 2)


def sistema_punteggio(valore) -> float:
    try:
        numero = float(valore)
    except (TypeError, ValueError):
        return 0.0
    if numero > 1 and numero <= 100:
        numero = numero / 100
    return round(min(1.0, max(0.0, numero)), 3)


def prompt_aspetto(aspetto: dict, concetti: list[dict], documento: str) -> list[dict]:
    sistema = (
        "Analizzi quanto un testo istituzionale sulla protezione internazionale si fa capire "
        "da una persona che non conosce la procedura. "
        "Rispondi solo con un oggetto JSON, in italiano. "
        "Il punteggio non misura quanto l'aspetto è presente, ma quanto è facile capirlo. "
        "Sii severo: spiegare un concetto non basta per un punteggio alto. "
        "0,81-1,00 solo se si capisce subito, senza rinvii ad altre pagine, senza termini opachi "
        "e senza condizioni ammassate. "
        "Se i passaggi che chiariscono e quelli che ostacolano sono circa in pari, "
        "il punteggio sta fra 0,45 e 0,55. Due aiuti e due ostacoli valgono circa 0,50, non 0,75. "
        "Non usare 0,85 come valore di default."
    )
    utente = {
        "aspetto": {
            "id": aspetto["id"],
            "nome": aspetto["nome"],
            "famiglia": aspetto["famiglia"],
            "cosa_osservare": aspetto["cosa_si_potra_osservare"],
        },
        "concetti_collegati": concetti,
        "scala": {
            "0.00-0.20": "Per niente comprensibile",
            "0.21-0.40": "Poco comprensibile",
            "0.41-0.60": "Moderatamente comprensibile",
            "0.61-0.80": "Abbastanza comprensibile",
            "0.81-1.00": "Molto comprensibile",
        },
        "istruzioni_estratti": [
            "Copia dal documento porzioni contigue, carattere per carattere.",
            "Non parafrasare, non usare ellissi, non unire passaggi distanti.",
            "Ogni estratto deve essere lungo tra 40 e 450 caratteri.",
            "Scegline da 4 a 6, quelli che pesano di più sul punteggio.",
            "effetto vale aumenta se il passaggio rende l'aspetto più comprensibile, riduce se lo rende più opaco.",
            "Ogni estratto ha un punteggio proprio, da 0 a 1: quanto quella sola frase fa capire l'aspetto.",
            "Il punteggio della frase non è il punteggio del testo. Una frase chiarissima può valere 0,90 anche se il testo nel complesso vale 0,50. Una frase opaca può valere 0,20.",
            "Non assegnare lo stesso punteggio a tutte le frasi.",
            "Cerca anche gli ostacoli: rinvii ad altre pagine, termini non spiegati, elenchi di condizioni, frasi lunghe, soggetti istituzionali dati per scontati.",
            "Se nel testo c'è un ostacolo reale, almeno due estratti devono avere effetto riduce.",
            "Non restituire solo passaggi positivi quando il testo ha anche punti difficili.",
        ],
        "formato": {
            "punteggio": "numero tra 0 e 1",
            "giudizio": "da quattro a otto frasi",
            "motivazioni": ["elenco di ragioni brevi"],
            "estratti": [{"testo": "", "effetto": "aumenta oppure riduce", "perche": "", "punteggio": "numero tra 0 e 1, solo per questa frase"}],
        },
        "documento": documento,
    }
    return [
        {"role": "system", "content": sistema},
        {"role": "user", "content": json.dumps(utente, ensure_ascii=False)},
    ]


def valida_estratti(documento: str, grezzi: list) -> tuple[list[dict], list[str]]:
    validi = []
    scartati = []
    occupati: list[tuple[int, int]] = []
    for voce in grezzi or []:
        if not isinstance(voce, dict):
            continue
        testo = str(voce.get("testo") or "").strip()
        effetto = voce.get("effetto")
        if effetto not in {"aumenta", "riduce"}:
            effetto = "aumenta"
        intervallo = trova_estratto(documento, testo)
        if intervallo is None:
            if testo:
                scartati.append(testo[:180])
            continue
        inizio, fine = intervallo
        if any(not (fine <= a or inizio >= b) for a, b in occupati):
            continue
        occupati.append((inizio, fine))
        citazione = documento[inizio:fine]
        voce_estratto = {
            "testo": citazione,
            "inizio": inizio,
            "fine": fine,
            "effetto": effetto,
            "perche": str(voce.get("perche") or "").strip(),
        }
        if voce.get("punteggio") is not None and voce.get("punteggio") != "":
            voce_estratto["punteggio"] = sistema_punteggio(voce.get("punteggio"))
        validi.append(voce_estratto)
    return validi, scartati


def analizza_aspetto(documento: str, aspetto: dict, concetti: list[dict]) -> dict:
    contenuto, uso = chiedi_modello(prompt_aspetto(aspetto, concetti, documento))
    try:
        dati = json.loads(contenuto)
    except json.JSONDecodeError as errore:
        raise RuntimeError(f"JSON non valido per {aspetto['id']}: {errore}") from errore
    estratti, scartati = valida_estratti(documento, dati.get("estratti") or [])
    if not estratti:
        riparazione = prompt_aspetto(aspetto, concetti, documento)
        riparazione.append({"role": "assistant", "content": contenuto})
        riparazione.append(
            {
                "role": "user",
                "content": (
                    "Nessun estratto coincide con il documento. "
                    "Rispondi di nuovo in JSON. Gli estratti devono essere copie esatte di passaggi presenti nel documento."
                ),
            }
        )
        contenuto, uso_2 = chiedi_modello(riparazione)
        uso = {
            "prompt_tokens": (uso or {}).get("prompt_tokens", 0) + (uso_2 or {}).get("prompt_tokens", 0),
            "completion_tokens": (uso or {}).get("completion_tokens", 0) + (uso_2 or {}).get("completion_tokens", 0),
        }
        dati = json.loads(contenuto)
        estratti, scartati = valida_estratti(documento, dati.get("estratti") or [])
    punteggio_modello = sistema_punteggio(dati.get("punteggio"))
    punteggio = mediana_intensita(estratti)
    if punteggio is None:
        punteggio = ricalibra_punteggio(punteggio_modello, estratti)
    motivazioni = [str(voce).strip() for voce in (dati.get("motivazioni") or []) if str(voce).strip()]
    quantitativo = metriche_locali(documento, concetti)
    quantitativo["estratti_validi"] = len(estratti)
    quantitativo["estratti_scartati"] = len(scartati)
    return {
        "id": aspetto["id"],
        "nome": aspetto["nome"],
        "famiglia": aspetto["famiglia"],
        "cosa_si_potra_osservare": aspetto["cosa_si_potra_osservare"],
        "punteggio": punteggio,
        "punteggio_modello": punteggio_modello,
        "livello": livello_da_punteggio(punteggio),
        "giudizio": str(dati.get("giudizio") or "").strip(),
        "motivazioni": motivazioni,
        "estratti": estratti,
        "estratti_scartati": scartati,
        "concetto": {"parole": [], "frequenze": [], "aggregato": []},
        "quantitativo": quantitativo,
        "usage": uso,
    }


def slug_documento(nome: str) -> str:
    base = Path(nome).stem
    normalizzato = unicodedata.normalize("NFKD", base)
    normalizzato = "".join(carattere for carattere in normalizzato if not unicodedata.combining(carattere))
    normalizzato = re.sub(r"[^A-Za-z0-9._-]+", "-", normalizzato).strip("-._")
    return normalizzato or "documento"


def cartella_risultato(nome_file: str) -> Path:
    return RISULTATI / slug_documento(nome_file)


def salva_parziale(cartella: Path, analisi: dict) -> None:
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / "analisi.json").write_text(
        json.dumps(analisi, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def analizza_documento(
    percorso: Path,
    nome_originale: str | None = None,
    avanzamento=None,
    lavoratori: int = 3,
) -> dict:
    percorso = Path(percorso)
    documento = percorso.read_text(encoding="utf-8")
    nome = nome_originale or percorso.name
    dizionario = carica_dizionario()
    cartella = cartella_risultato(nome)
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / "documento.md").write_text(documento, encoding="utf-8")

    aspetti = dizionario["aspetti"]
    analisi = {
        "id": slug_documento(nome),
        "nome_file": nome,
        "modello": MODELLO,
        "creato": datetime.now(timezone.utc).isoformat(),
        "stato": "in_corso",
        "documento": documento,
        "aspetti": [],
        "errori": [],
    }
    meta = {
        "id": analisi["id"],
        "nome_file": nome,
        "modello": MODELLO,
        "creato": analisi["creato"],
        "stato": "in_corso",
        "caratteri": len(documento),
    }
    (cartella / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    salva_parziale(cartella, analisi)

    def lavoro(indice_aspetto: tuple[int, dict]) -> tuple[int, dict]:
        indice, aspetto = indice_aspetto
        concetti = concetti_per_aspetto(dizionario, aspetto["id"])
        try:
            risultato = analizza_aspetto(documento, aspetto, concetti)
        except Exception as errore:  # noqa: BLE001 - un aspetto non deve bloccare gli altri
            risultato = {
                "id": aspetto["id"],
                "nome": aspetto["nome"],
                "famiglia": aspetto["famiglia"],
                "cosa_si_potra_osservare": aspetto["cosa_si_potra_osservare"],
                "punteggio": None,
                "livello": None,
                "giudizio": "",
                "motivazioni": [],
                "estratti": [],
                "estratti_scartati": [],
                "quantitativo": metriche_locali(documento, concetti),
                "errore": str(errore),
            }
        return indice, risultato

    completati: dict[int, dict] = {}
    scrittura = threading.Lock()
    with ThreadPoolExecutor(max_workers=lavoratori) as esecutore:
        futuri = [esecutore.submit(lavoro, voce) for voce in enumerate(aspetti)]
        for futuro in as_completed(futuri):
            indice, risultato = futuro.result()
            with scrittura:
                completati[indice] = risultato
                analisi["aspetti"] = [completati[i] for i in sorted(completati)]
                analisi["errori"] = [voce["id"] for voce in analisi["aspetti"] if voce.get("errore")]
                salva_parziale(cartella, analisi)
                fatti = len(completati)
            if avanzamento:
                avanzamento(fatti, len(aspetti), risultato["id"])

    analisi["aspetti"] = [completati[i] for i in range(len(aspetti))]
    try:
        uso_nuvole = applica_nuvole(documento, analisi["aspetti"])
    except Exception as errore:  # noqa: BLE001 - le nuvole non annullano i giudizi
        uso_nuvole = {}
        for aspetto in analisi["aspetti"]:
            aspetto["concetto"] = {
                "parole": [],
                "frequenze": [],
                "aggregato": [],
                "errore": str(errore),
            }
    try:
        from topic import applica_topic

        uso_topic = applica_topic(analisi["aspetti"])
    except Exception:  # noqa: BLE001 - i topic non annullano il resto
        uso_topic = {}
    analisi["stato"] = "completato"
    analisi["usage"] = {
        "prompt_tokens": sum((voce.get("usage") or {}).get("prompt_tokens", 0) for voce in analisi["aspetti"])
        + (uso_nuvole or {}).get("prompt_tokens", 0)
        + (uso_topic or {}).get("prompt_tokens", 0),
        "completion_tokens": sum((voce.get("usage") or {}).get("completion_tokens", 0) for voce in analisi["aspetti"])
        + (uso_nuvole or {}).get("completion_tokens", 0)
        + (uso_topic or {}).get("completion_tokens", 0),
    }
    salva_parziale(cartella, analisi)
    meta["stato"] = "completato"
    meta["usage"] = analisi["usage"]
    (cartella / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return analisi


def elenco_risultati() -> list[dict]:
    if not RISULTATI.is_dir():
        return []
    voci = []
    for cartella in sorted(RISULTATI.iterdir()):
        meta_path = cartella / "meta.json"
        if not meta_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["id"] = cartella.name
        voci.append(meta)
    return voci


def prompt_punteggi_citazioni(aspetto: dict, estratti: list[dict]) -> list[dict]:
    sistema = (
        "Assegni un punteggio di comprensibilità a ogni citazione, da 0 a 1. "
        "Il punteggio riguarda solo quella frase, non il voto complessivo del testo. "
        "Una frase chiara può valere fra 0,70 e 0,95 anche se il testo nel complesso vale 0,50. "
        "Una frase con rinvii, termini opachi o condizioni ammassate può valere fra 0,10 e 0,40. "
        "Usa valori diversi: non dare lo stesso numero a tutte le frasi. "
        "Rispondi solo con un oggetto JSON."
    )
    utente = {
        "aspetto": aspetto.get("nome"),
        "cosa_osservare": aspetto.get("cosa_si_potra_osservare"),
        "voto_complessivo_del_testo": aspetto.get("punteggio"),
        "scala": {
            "0.00-0.20": "Per niente comprensibile",
            "0.21-0.40": "Poco comprensibile",
            "0.41-0.60": "Moderatamente comprensibile",
            "0.61-0.80": "Abbastanza comprensibile",
            "0.81-1.00": "Molto comprensibile",
        },
        "citazioni": [
            {
                "effetto": voce.get("effetto"),
                "perche": voce.get("perche"),
                "testo": voce.get("testo"),
            }
            for voce in estratti
        ],
        "formato": {"punteggi": ["un numero tra 0 e 1 per ogni citazione, nello stesso ordine"]},
    }
    return [
        {"role": "system", "content": sistema},
        {"role": "user", "content": json.dumps(utente, ensure_ascii=False)},
    ]


def punteggi_per_citazioni(aspetto: dict, estratti: list[dict]) -> tuple[list[float], dict]:
    contenuto, uso = chiedi_modello(prompt_punteggi_citazioni(aspetto, estratti))
    dati = json.loads(contenuto)
    grezzi = dati.get("punteggi") or []
    if len(grezzi) != len(estratti):
        raise RuntimeError(f"Punteggi attesi {len(estratti)}, ricevuti {len(grezzi)} per {aspetto.get('id')}")
    return [sistema_punteggio(valore) for valore in grezzi], uso or {}


def assegna_punteggi_citazioni(identificativo: str, lavoratori: int = 4) -> dict:
    """Aggiunge il punteggio di ogni citazione già salvata, senza rileggere il documento."""
    cartella = RISULTATI / identificativo
    percorso = cartella / "analisi.json"
    analisi = json.loads(percorso.read_text(encoding="utf-8"))
    aspetti = [voce for voce in analisi.get("aspetti") or [] if voce.get("estratti")]

    def lavoro(aspetto: dict) -> tuple[str, list[float], dict]:
        punteggi, uso = punteggi_per_citazioni(aspetto, aspetto["estratti"])
        return aspetto["id"], punteggi, uso

    usi = []
    with ThreadPoolExecutor(max_workers=lavoratori) as esecutore:
        futuri = [esecutore.submit(lavoro, aspetto) for aspetto in aspetti]
        for futuro in as_completed(futuri):
            aspetto_id, punteggi, uso = futuro.result()
            usi.append(uso)
            for aspetto in analisi["aspetti"]:
                if aspetto.get("id") != aspetto_id:
                    continue
                for estratto, punteggio in zip(aspetto["estratti"], punteggi):
                    estratto["punteggio"] = punteggio
                mediana = mediana_intensita(aspetto["estratti"])
                if mediana is not None:
                    aspetto["punteggio"] = mediana
                    aspetto["livello"] = livello_da_punteggio(mediana)
            percorso.write_text(json.dumps(analisi, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{aspetto_id} {' '.join(f'{valore:.2f}' for valore in punteggi)}", flush=True)

    extra_prompt = sum(voce.get("prompt_tokens", 0) for voce in usi)
    extra_completion = sum(voce.get("completion_tokens", 0) for voce in usi)
    uso_totale = analisi.get("usage") or {}
    analisi["usage"] = {
        "prompt_tokens": uso_totale.get("prompt_tokens", 0) + extra_prompt,
        "completion_tokens": uso_totale.get("completion_tokens", 0) + extra_completion,
    }
    percorso.write_text(json.dumps(analisi, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_path = cartella / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["usage"] = analisi["usage"]
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return analisi


def aggiorna_lemmi_salvati(identificativo: str) -> dict:
    """Ricalcola le nuvole già salvate, senza richiamare il modello."""
    percorso = RISULTATI / identificativo / "analisi.json"
    analisi = json.loads(percorso.read_text(encoding="utf-8"))
    for aspetto in analisi.get("aspetti") or []:
        concetto = aspetto.get("concetto") or {}
        frequenze = concetto.get("frequenze") or []
        if frequenze:
            concetto["aggregato"] = aggrega_parole(frequenze)
            aspetto["concetto"] = concetto
    percorso.write_text(json.dumps(analisi, ensure_ascii=False, indent=2), encoding="utf-8")
    return analisi


def assegna_concetti(identificativo: str, lavoratori: int = 4) -> dict:
    """Rifà le word cloud degli aspetti già salvati, senza rifare il giudizio."""
    del lavoratori
    cartella = RISULTATI / identificativo
    percorso = cartella / "analisi.json"
    analisi = json.loads(percorso.read_text(encoding="utf-8"))
    documento = analisi.get("documento") or ""
    uso = applica_nuvole(documento, analisi.get("aspetti") or [])
    for aspetto in analisi.get("aspetti") or []:
        print(f"{aspetto.get('id')} {len((aspetto.get('concetto') or {}).get('aggregato') or [])} parole", flush=True)
    uso_totale = analisi.get("usage") or {}
    analisi["usage"] = {
        "prompt_tokens": uso_totale.get("prompt_tokens", 0) + uso.get("prompt_tokens", 0),
        "completion_tokens": uso_totale.get("completion_tokens", 0) + uso.get("completion_tokens", 0),
    }
    percorso.write_text(json.dumps(analisi, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_path = cartella / "meta.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["usage"] = analisi["usage"]
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return analisi


def carica_risultato(identificativo: str) -> dict:
    cartella = RISULTATI / identificativo
    analisi_path = cartella / "analisi.json"
    if not analisi_path.is_file():
        raise FileNotFoundError(identificativo)
    return json.loads(analisi_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import sys

    origine = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dataset" / "guida_pratica_2026.md"
    if not origine.is_absolute():
        origine = (Path.cwd() / origine).resolve()

    def stampa(fatti, totale, aspetto_id):
        print(f"{fatti}/{totale} {aspetto_id}", flush=True)

    esito = analizza_documento(origine, avanzamento=stampa)
    print(esito["id"], esito["stato"], esito.get("usage"))
