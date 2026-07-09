#!/bin/bash
# install.sh — Installeur Resolve Subtitles (Primo-Studio)
# Installe tout ce qu'il faut pour les scripts sous-titres DaVinci Resolve :
#   1. Python 3 officiel python.org (si absent — requis par Resolve)
#   2. Le SDK Claude (pip install anthropic)
#   3. Les 3 scripts dans le dossier Scripts/Utility de Resolve
#   4. La clé API Claude dans ~/.anthropic/api_key
# 100 % popups graphiques : aucun Terminal requis pour l'utilisateur.
#
# Mode test (CI / vérification) : HOME=$(mktemp -d) RS_INSTALL_TEST=1 bash install.sh
#   → aucune popup, aucune installation Python/pip. EXIGE un HOME temporaire
#   (refuse de tourner sur le HOME réel) ; la copie de scripts et la clé
#   (RS_TEST_KEY) sont réellement écrites, dans ce HOME temporaire.

set -u

PYTHON_PKG_URL="https://www.python.org/ftp/python/3.13.5/python-3.13.5-macos11.pkg"
PYTHON_FRAMEWORK="/Library/Frameworks/Python.framework/Versions"
PYTHON_MIN_MINOR=10   # en dessous de 3.10 : SDK anthropic récent non installable
SCRIPTS=("ScanSubtitlesFR.py" "ApplyCorrectionsFR.py" "TranslateSubtitlesFR_EN.py")
DEST_DIR="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
KEY_DIR="$HOME/.anthropic"
KEY_FILE="$KEY_DIR/api_key"
LOG_DIR="$HOME/Library/Logs"
LOG="$LOG_DIR/ResolveSubtitles-Install.log"
TEST_MODE="${RS_INSTALL_TEST:-0}"

RES_DIR="$(cd "$(dirname "$0")" && pwd)"

# Garde-fou mode test : jamais sur le HOME réel de l'utilisateur.
if [ "$TEST_MODE" = "1" ]; then
    REAL_HOME="$(dscl . -read "/Users/$(id -un)" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
    if [ -n "$REAL_HOME" ] && [ "$HOME" = "$REAL_HOME" ]; then
        echo "TEST : refus de tourner sur le HOME réel. Lance : HOME=\$(mktemp -d) RS_INSTALL_TEST=1 bash install.sh" >&2
        exit 1
    fi
fi

# En mode test lancé depuis le repo, les .py sont à la racine (un cran au-dessus).
SRC_DIR="$RES_DIR"
if [ "$TEST_MODE" = "1" ] && [ ! -f "$RES_DIR/${SCRIPTS[0]}" ] && [ -f "$RES_DIR/../${SCRIPTS[0]}" ]; then
    SRC_DIR="$(cd "$RES_DIR/.." && pwd)"
fi

mkdir -p "$LOG_DIR"
exec >>"$LOG" 2>&1
echo "===== Installation $(date) ====="
echo "Ressources : $SRC_DIR"

TMP_DIR=""
cleanup() { [ -n "$TMP_DIR" ] && rm -rf "$TMP_DIR"; }
trap cleanup EXIT

# ---------- Helpers popups (osascript) ----------
# Convention : le DERNIER bouton est l'action par défaut ; le premier est
# l'option prudente. En mode test, ask() renvoie le bouton par défaut.

# ask "message" "bouton_prudent" ... "bouton_défaut" → écrit le bouton choisi
ask() {
    local msg="$1"; shift
    if [ "$TEST_MODE" = "1" ]; then
        local last=""
        for b in "$@"; do last="$b"; done
        echo "$last"; return 0
    fi
    /usr/bin/osascript - "$msg" "$@" <<'APPLESCRIPT'
on run argv
    set msg to item 1 of argv
    set btns to rest of argv
    set dflt to item -1 of btns
    activate
    try
        set r to display dialog msg buttons btns default button dflt ¬
            with title "Resolve Subtitles — Installation" with icon note
        return button returned of r
    on error
        return item -1 of btns
    end try
end run
APPLESCRIPT
}

# ask_secret "message" → écrit "BOUTON\nTEXTE" (texte saisi masqué)
ask_secret() {
    local msg="$1"
    if [ "$TEST_MODE" = "1" ]; then
        if [ -n "${RS_TEST_KEY:-}" ]; then printf 'Valider\n%s\n' "$RS_TEST_KEY"; else printf 'Plus tard\n\n'; fi
        return 0
    fi
    /usr/bin/osascript - "$msg" <<'APPLESCRIPT'
on run argv
    activate
    try
        set r to display dialog (item 1 of argv) default answer "" with hidden answer ¬
            buttons {"Plus tard", "Valider"} default button "Valider" ¬
            with title "Resolve Subtitles — Clé API" with icon note
        return (button returned of r) & linefeed & (text returned of r)
    on error
        return "Plus tard" & linefeed
    end try
end run
APPLESCRIPT
}

# alert_stop "message" → copie le log sur le Bureau, alerte critique, sortie 1
alert_stop() {
    echo "ERREUR : $1"
    cp -f "$LOG" "$HOME/Desktop/ResolveSubtitles-Install.log" 2>/dev/null || true
    if [ "$TEST_MODE" != "1" ]; then
        /usr/bin/osascript - "$1" <<'APPLESCRIPT' || true
on run argv
    activate
    display alert "Installation interrompue" message (item 1 of argv) & return & return & ¬
        "Un fichier « ResolveSubtitles-Install.log » a été déposé sur ton Bureau : envoie-le à Néto si le problème persiste." ¬
        as critical buttons {"OK"} default button "OK"
end run
APPLESCRIPT
    fi
    exit 1
}

# ---------- 0. Bienvenue ----------

WELCOME="Cet assistant installe les scripts sous-titres DaVinci Resolve de Primo-Studio :

• ScanSubtitlesFR — détecte les fautes des sous-titres FR
• ApplyCorrectionsFR — applique les corrections
• TranslateSubtitlesFR_EN — traduit les sous-titres FR en anglais

Il installe aussi Python et le module Claude si besoin. Durée : 2 à 5 minutes.

Important : certaines étapes durent 1 à 3 minutes sans rien afficher — c'est normal, ne ferme pas l'application."
[ "$(ask "$WELCOME" "Annuler" "Installer")" = "Installer" ] || { echo "Annulé par l'utilisateur."; exit 0; }

# ---------- 1. DaVinci Resolve présent ? ----------

RESOLVE_APP=""
for p in "/Applications/DaVinci Resolve/DaVinci Resolve.app" \
         "/Applications/DaVinci Resolve.app" \
         "/Applications/DaVinci Resolve Studio.app"; do
    if [ -d "$p" ]; then RESOLVE_APP="$p"; break; fi
done

if [ -z "$RESOLVE_APP" ]; then
    R=$(ask "DaVinci Resolve n'a pas été trouvé dans le dossier Applications.

Tu peux continuer l'installation : les scripts seront prêts dès que Resolve sera installé." "Annuler" "Continuer quand même")
    [ "$R" = "Continuer quand même" ] || { echo "Annulé (Resolve absent)."; exit 0; }
else
    echo "Resolve : $RESOLVE_APP"
    RV="$(defaults read "$RESOLVE_APP/Contents/Info" CFBundleShortVersionString 2>/dev/null || true)"
    RV_MAJOR="${RV%%.*}"
    case "$RV_MAJOR" in
        ''|*[!0-9]*) : ;;
        *)
            if [ "$RV_MAJOR" -lt 19 ]; then
                ask "Ta version de DaVinci Resolve ($RV) est ancienne : les scripts sont testés sur Resolve 19 et plus récent.

L'installation va continuer, mais si les scripts n'apparaissent pas dans le menu, mets Resolve à jour." "OK" >/dev/null
            fi
            ;;
    esac
fi

# ---------- 2. Python officiel python.org ----------

find_python() {
    # Le Python python.org est le SEUL que Resolve détecte (ni Homebrew, ni Xcode CLT).
    # On exige 3.10+ (le SDK anthropic récent n'existe pas en dessous).
    local v
    for v in $(ls -1 "$PYTHON_FRAMEWORK" 2>/dev/null | grep -E '^3\.[0-9]+$' \
               | awk -F. -v m="$PYTHON_MIN_MINOR" '$2 >= m' | sort -t. -k2 -rn); do
        if [ -x "$PYTHON_FRAMEWORK/$v/bin/python3" ]; then
            echo "$PYTHON_FRAMEWORK/$v/bin/python3"
            return 0
        fi
    done
    return 1
}

PYBIN="$(find_python || true)"

if [ -z "$PYBIN" ]; then
    if [ "$TEST_MODE" = "1" ]; then
        echo "TEST : Python framework absent — installation sautée (sentinel)."
        PYBIN=""
    else
        R=$(ask "Python (le langage utilisé par les scripts) doit être installé — c'est officiel, gratuit et sans danger.

Après ton clic : rien ne s'affichera pendant 1 à 2 minutes (téléchargement), puis l'installeur Python s'ouvrira tout seul.

Dans l'installeur : clique « Continuer » puis « Installer » jusqu'au bout (le mot de passe d'un compte administrateur du Mac est demandé), et à la fin FERME sa fenêtre — l'installation reprendra alors automatiquement. Si macOS propose de placer l'installeur à la corbeille, accepte." "Annuler" "Installer Python")
        [ "$R" = "Installer Python" ] || { echo "Annulé (Python refusé)."; exit 0; }

        TMP_DIR="$(mktemp -d)"
        echo "Téléchargement de $PYTHON_PKG_URL"
        if ! curl -fsSL --retry 3 --connect-timeout 20 -o "$TMP_DIR/python.pkg" "$PYTHON_PKG_URL"; then
            alert_stop "Le téléchargement de Python a échoué. Vérifie ta connexion Internet puis relance l'installeur."
        fi
        open -W "$TMP_DIR/python.pkg"

        # open -W rend la main dès que l'installeur macOS se ferme, même annulé.
        PYBIN="$(find_python || true)"
        while [ -z "$PYBIN" ]; do
            R=$(ask "L'installation de Python ne semble pas terminée.

• Si la fenêtre d'installation de Python est encore ouverte : termine-la (jusqu'à « L'installation a réussi »), ferme-la, puis clique sur « Réessayer ».
• Si tu l'as fermée ou annulée par erreur : clique sur « Rouvrir l'installeur Python »." "Abandonner" "Rouvrir l'installeur Python" "Réessayer")
            case "$R" in
                "Rouvrir l'installeur Python")
                    if [ ! -f "$TMP_DIR/python.pkg" ]; then
                        TMP_DIR="${TMP_DIR:-$(mktemp -d)}"
                        curl -fsSL --retry 3 --connect-timeout 20 -o "$TMP_DIR/python.pkg" "$PYTHON_PKG_URL" \
                            || alert_stop "Le téléchargement de Python a échoué. Vérifie ta connexion Internet puis relance l'installeur."
                    fi
                    open -W "$TMP_DIR/python.pkg"
                    ;;
                "Réessayer") : ;;
                *) alert_stop "Python n'a pas été installé. Relance cet installeur quand tu veux recommencer." ;;
            esac
            PYBIN="$(find_python || true)"
        done
    fi
fi
if [ -n "$PYBIN" ]; then
    echo "Python : $PYBIN ($("$PYBIN" --version 2>&1))"
else
    echo "Python : (aucun — mode test)"
fi

# ---------- 3. SDK Claude (anthropic) ----------

if [ "$TEST_MODE" = "1" ]; then
    echo "TEST : pip install sauté."
else
    # pip peut manquer sur un framework déjà présent : ensurepip d'abord.
    if ! "$PYBIN" -m pip --version >/dev/null 2>&1; then
        echo "pip absent — ensurepip…"
        "$PYBIN" -m ensurepip --upgrade >/dev/null 2>&1 || "$PYBIN" -m ensurepip --upgrade --user >/dev/null 2>&1 || true
        if ! "$PYBIN" -m pip --version >/dev/null 2>&1; then
            alert_stop "L'outil d'installation Python (pip) est indisponible sur ce Mac. Relance l'installeur ; si ça se reproduit, envoie le fichier du Bureau à Néto."
        fi
    fi

    if "$PYBIN" -c "import anthropic" 2>/dev/null; then
        # Déjà installé : mise à jour best-effort, jamais bloquante (permet de
        # relancer l'installeur hors connexion juste pour configurer la clé).
        echo "Module anthropic déjà présent — tentative de mise à jour…"
        "$PYBIN" -m pip install --upgrade --quiet anthropic \
            || "$PYBIN" -m pip install --upgrade --quiet --user anthropic \
            || echo "Mise à jour sautée (réseau indisponible ?) — version existante conservée."
    else
        ask "L'assistant va maintenant installer le module Claude.

Après ton clic, patiente environ 1 minute sans rien fermer — rien ne s'affichera pendant ce temps." "OK" >/dev/null
        if ! "$PYBIN" -m pip install --upgrade --quiet anthropic; then
            echo "pip système a échoué, tentative en mode utilisateur (--user)…"
            if ! "$PYBIN" -m pip install --upgrade --quiet --user anthropic; then
                alert_stop "L'installation du module Claude a échoué. Vérifie ta connexion Internet puis relance l'installeur."
            fi
        fi
        if ! "$PYBIN" -c "import anthropic" 2>/dev/null; then
            alert_stop "Le module Claude ne se charge pas après installation."
        fi
    fi
    echo "Module anthropic OK sur $PYBIN."

    # Resolve résout son Python via /usr/local/bin/python3 : si ce lien pointe
    # vers un AUTRE python (ex. ancien framework), installer anthropic dessus aussi.
    UL="/usr/local/bin/python3"
    if [ -x "$UL" ]; then
        UL_REAL="$(readlink -f "$UL" 2>/dev/null || echo "$UL")"
        PY_REAL="$(readlink -f "$PYBIN" 2>/dev/null || echo "$PYBIN")"
        if [ "$UL_REAL" != "$PY_REAL" ] && ! "$UL" -c "import anthropic" 2>/dev/null; then
            echo "Installation aussi sur $UL ($UL_REAL) — python utilisé par Resolve…"
            "$UL" -m pip install --upgrade --quiet anthropic 2>/dev/null \
                || "$UL" -m pip install --upgrade --quiet --user anthropic 2>/dev/null \
                || echo "AVERTISSEMENT : anthropic non installé sur $UL — si les scripts signalent le module manquant, envoyer le log à Néto."
        fi
    fi
fi

# ---------- 4. Copie des scripts dans Resolve ----------

for f in "${SCRIPTS[@]}"; do
    [ -f "$SRC_DIR/$f" ] || alert_stop "Fichier manquant dans l'installeur : $f. Retélécharge l'installeur complet."
done
mkdir -p "$DEST_DIR" || alert_stop "Impossible de créer le dossier des scripts Resolve."
for f in "${SCRIPTS[@]}"; do
    cp -f "$SRC_DIR/$f" "$DEST_DIR/$f" || alert_stop "Impossible de copier $f dans le dossier de Resolve."
    # cp propage la quarantine Gatekeeper de l'app téléchargée : on la retire.
    /usr/bin/xattr -d com.apple.quarantine "$DEST_DIR/$f" 2>/dev/null || true
done
echo "Scripts copiés dans : $DEST_DIR"

# ---------- 5. Clé API Claude ----------

configure_key() {
    local attempt raw btn key
    for attempt in 1 2 3 4; do
        raw="$(ask_secret "Colle ici la clé API Claude (fournie par Néto).

Elle commence par « sk-ant- ». Le champ affiche des points ronds : c'est normal, la clé est masquée. Clic droit → Coller, ou Cmd+V.")"
        btn="${raw%%$'\n'*}"
        key="${raw#*$'\n'}"
        key="$(printf '%s' "$key" | tr -d '[:space:]')"
        if [ "$btn" = "Plus tard" ]; then
            ask "Pas de problème — tu pourras configurer la clé plus tard en relançant cet installeur.

Sans clé, les scripts afficheront une erreur au lancement." "OK" >/dev/null
            return 0
        fi
        if [ -z "$key" ]; then
            ask "Le champ était vide — le collage n'a probablement pas fonctionné.

Recopie la clé (Cmd+C sur le message de Néto), puis Cmd+V dans le champ." "OK" >/dev/null
            continue
        fi
        case "$key" in
            sk-ant-*)
                mkdir -p "$KEY_DIR" && chmod 700 "$KEY_DIR"
                umask 077
                printf '%s\n' "$key" > "$KEY_FILE" || alert_stop "Impossible d'enregistrer la clé API."
                chmod 600 "$KEY_FILE"
                /usr/bin/xattr -d com.apple.quarantine "$KEY_FILE" 2>/dev/null || true
                echo "Clé API enregistrée dans $KEY_FILE"
                return 0
                ;;
            *)
                ask "Cette clé ne ressemble pas à une clé Claude (elle doit commencer par « sk-ant- »).

Vérifie que tu as bien tout copié, puis réessaie." "OK" >/dev/null
                ;;
        esac
    done
    ask "La clé n'a pas pu être validée. Tu pourras la configurer plus tard en relançant cet installeur." "OK" >/dev/null
}

if [ -s "$KEY_FILE" ]; then
    R=$(ask "Une clé API Claude est déjà configurée sur ce Mac.

Veux-tu la garder ou la remplacer ?" "Remplacer" "Garder")
    [ "$R" = "Remplacer" ] && configure_key
    echo "Clé API : conservée/reconfigurée."
else
    configure_key
fi

# ---------- 6. Fin ----------

RESTART_NOTE=""
if pgrep -f "DaVinci Resolve( Studio)?\.app/Contents/MacOS" >/dev/null 2>&1; then
    RESTART_NOTE="

⚠️ DaVinci Resolve est ouvert : quitte-le (Cmd+Q) et rouvre-le pour voir les scripts."
fi

ask "✅ Installation terminée !

Dans DaVinci Resolve, les scripts sont dans le menu :
Espace de travail (Workspace) → Scripts

• ScanSubtitlesFR → détecte les fautes
• ApplyCorrectionsFR → applique les corrections
• TranslateSubtitlesFR_EN → traduit en anglais${RESTART_NOTE}" "Terminer" >/dev/null

echo "===== Installation terminée avec succès ====="
exit 0
