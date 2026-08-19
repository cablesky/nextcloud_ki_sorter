#!/bin/sh
# ==============================================================================
# Nextcloud Workflow External Script Trigger
# ------------------------------------------------------------------------------
# Dieses Skript wird von Nextcloud "Abläufe" (Workflow External Scripts)
# aufgerufen, sobald eine neue PDF-Datei hochgeladen wird.
# 
# Nextcloud Parameter-Platzhalter in den Einstellungen:
# Skript: /usr/local/bin/trigger_ki_sorter.sh %n %a
#   %n = Relativer Dateipfad in Nextcloud (z.B. alice/files/Posteingang/rechnung.pdf)
#   %a = Benutzername (actor's user id) oder %o (owner's user id)
# ==============================================================================

FILE_PATH="$1"
USER_NAME="$2"

# Debug Log für Nachvollziehbarkeit schreiben
echo "$(date) - Trigger aufgerufen mit: FILE_PATH='$FILE_PATH', USER_NAME='$USER_NAME'" >> /tmp/trigger_debug.log

SORTER_URL="${SORTER_URL:-http://192.168.178.88:8017/process}"
INBOX_FOLDER=$(echo "${INBOX_FOLDER:-Posteingang}" | sed -E 's#^/+##')

if [ -z "$FILE_PATH" ]; then
    echo "Fehler: Kein Dateipfad übergeben."
    exit 1
fi

# Nur verarbeiten, wenn die Datei im Posteingang liegt
# Nextcloud GroupFolder IDs in deiner Instanz: 1=CookBook, 2=Dokumente, 3=Posteingang
if echo "$FILE_PATH" | grep -q "__groupfolders/"; then
    if echo "$FILE_PATH" | grep -q "__groupfolders/3/"; then
        SUB_PATH=$(echo "$FILE_PATH" | sed -E 's#^.*__groupfolders/3/files/##')
        CLEAN_PATH="${INBOX_FOLDER}/${SUB_PATH}"
    else
        # Datei liegt in einem anderen Gruppenordner (z.B. CookBook oder Dokumente) -> überspringen
        CLEAN_PATH=$(echo "$FILE_PATH" | sed -E 's#^/+##; s#^.*/files/##; s#^files/##')
    fi
else
    CLEAN_PATH=$(echo "$FILE_PATH" | sed -E 's#^/+##; s#^.*/files/##; s#^files/##')
fi

case "$CLEAN_PATH" in
    "$INBOX_FOLDER"*)
        ;;
    *)
        echo "[Nextcloud Workflow Trigger] Überspringe $FILE_PATH: Datei liegt nicht im Ordner '$INBOX_FOLDER'." >> /tmp/trigger_debug.log
        exit 0
        ;;
esac

echo "[Nextcloud Workflow Trigger] Sende Webhook für $FILE_PATH (User: $USER_NAME) an $SORTER_URL..."

curl -s -X POST "$SORTER_URL" \
     -H "Content-Type: application/json" \
     -d "{\"file_path\": \"$FILE_PATH\", \"user\": \"$USER_NAME\"}"

echo ""
exit 0

