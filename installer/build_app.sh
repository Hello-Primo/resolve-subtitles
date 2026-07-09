#!/bin/bash
# build_app.sh — Construit, signe et notarise « Installer Resolve Subtitles.app »
# Usage : bash installer/build_app.sh          (depuis la racine du repo ou n'importe où)
# Produit : build/Installer-Resolve-Subtitles.zip (prêt pour la Release GitHub)
#
# Prérequis (machine de Néto uniquement — les équipes n'en ont pas besoin) :
#   - Certificat « Developer ID Application: Primo Studio (4QB44XVHNL) » dans le trousseau
#   - Clé ASC ~/.appstoreconnect/private_keys/AuthKey_M6648V2CAK.p8 (notarisation)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$REPO_DIR/build"
APP_NAME="Installer Resolve Subtitles"
APP="$BUILD_DIR/$APP_NAME.app"
ZIP="$BUILD_DIR/Installer-Resolve-Subtitles.zip"
VERSION="${1:-1.0.0}"
NOTARIZE="${NOTARIZE:-1}"   # NOTARIZE=0 : zip signé sans notarisation (friction Gatekeeper)

SIGN_ID="Developer ID Application: Primo Studio (4QB44XVHNL)"
ASC_KEY="$HOME/.appstoreconnect/private_keys/AuthKey_M6648V2CAK.p8"
ASC_KEY_ID="M6648V2CAK"
ASC_ISSUER="59fee8b1-14ce-433c-a073-780ef258b24e"

echo "==> Compilation de l'app AppleScript"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
osacompile -o "$APP" "$REPO_DIR/installer/launcher.applescript"

echo "==> Copie des ressources (install.sh + scripts Python)"
RES="$APP/Contents/Resources"
cp "$REPO_DIR/installer/install.sh" "$RES/"
chmod 755 "$RES/install.sh"
for f in ScanSubtitlesFR.py ApplyCorrectionsFR.py TranslateSubtitlesFR_EN.py; do
    cp "$REPO_DIR/$f" "$RES/"
done

echo "==> Info.plist (identifiant + version)"
PLIST="$APP/Contents/Info.plist"
set_or_add() {
    /usr/libexec/PlistBuddy -c "Set :$1 $2" "$PLIST" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Add :$1 string $2" "$PLIST"
}
set_or_add CFBundleIdentifier fr.primo-studio.resolve-subtitles-installer
set_or_add CFBundleShortVersionString "$VERSION"
set_or_add CFBundleVersion "$VERSION"
set_or_add LSMinimumSystemVersion 12.0

echo "==> Signature (Developer ID + hardened runtime)"
codesign --force --options runtime --timestamp --sign "$SIGN_ID" "$APP"
codesign --verify --strict --verbose=2 "$APP"

if [ "$NOTARIZE" != "1" ]; then
    echo "==> Notarisation SAUTÉE (NOTARIZE=0) — Gatekeeper demandera « Ouvrir quand même »"
    ditto -c -k --keepParent "$APP" "$ZIP"
    echo "✅ Prêt (non notarisé) : $ZIP"
    exit 0
fi

echo "==> Notarisation Apple (peut prendre quelques minutes)"
ditto -c -k --keepParent "$APP" "$ZIP"
SUBMIT_JSON="$(xcrun notarytool submit "$ZIP" \
    --key "$ASC_KEY" --key-id "$ASC_KEY_ID" --issuer "$ASC_ISSUER" \
    --wait --output-format json)"
echo "$SUBMIT_JSON"
SUB_ID="$(printf '%s' "$SUBMIT_JSON" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
SUB_STATUS="$(printf '%s' "$SUBMIT_JSON" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
if [ "$SUB_STATUS" != "Accepted" ]; then
    echo "❌ Notarisation refusée (status: $SUB_STATUS) — log Apple :"
    xcrun notarytool log "$SUB_ID" --key "$ASC_KEY" --key-id "$ASC_KEY_ID" --issuer "$ASC_ISSUER" || true
    exit 1
fi
xcrun stapler staple "$APP"

echo "==> Zip final pour distribution"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"

echo "==> Vérification Gatekeeper"
spctl -a -vv "$APP"

echo ""
echo "✅ Prêt : $ZIP"
