#!/bin/bash
# Doppio clic su questo file: apre il programma nel browser.
cd "$(dirname "$0")" || exit 1
CARTELLA="$(pwd)"

echo ""
echo "Sto aprendo il programma."
echo "Lascia aperta questa finestra. Se la chiudi, il programma si ferma."
echo ""

if ! command -v python3 >/dev/null 2>&1; then
  echo "Su questo computer manca Python."
  echo "Apri https://www.python.org/downloads/ , installa Python 3 e poi fai di nuovo doppio clic su AVVIA."
  echo ""
  read -r -p "Premi Invio per chiudere."
  exit 1
fi

VERSIONE="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python è troppo vecchio (c'è la versione $VERSIONE). Serve Python 3.9 o più recente."
  echo "Apri https://www.python.org/downloads/ , installa Python 3 e poi fai di nuovo doppio clic su AVVIA."
  echo ""
  read -r -p "Premi Invio per chiudere."
  exit 1
fi

if [ ! -f "$CARTELLA/chiave_gpt.txt" ]; then
  echo "Manca il file chiave_gpt.txt in questa cartella."
  echo "I testi già esaminati si possono rivedere. Per esaminarne uno nuovo serve quel file."
  echo ""
fi

if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Il programma è già avviato. Apro la pagina."
  open "http://127.0.0.1:8765"
  echo ""
  read -r -p "Premi Invio per chiudere questa finestra. Il programma resta aperto."
  exit 0
fi

(sleep 1 && open "http://127.0.0.1:8765") &
cd "$CARTELLA/strumento" || exit 1
python3 server.py
echo ""
echo "Il programma si è fermato."
read -r -p "Premi Invio per chiudere."
