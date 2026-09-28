# Resolve Subtitles — scripts sous-titres DaVinci Resolve (Primo-Studio)

Trois scripts pour DaVinci Resolve qui corrigent et traduisent la piste de sous-titres de la timeline active via l'API Claude (Anthropic).

| Script | Rôle |
|---|---|
| `ScanSubtitlesFR.py` | Analyse la piste de sous-titres FR et détecte les fautes (orthographe / grammaire / ponctuation) **sans rien modifier**. Rapport JSON sur `~/Desktop/scan_subtitles_fr_report.json`. |
| `ApplyCorrectionsFR.py` | Applique les corrections du rapport en créant une **nouvelle piste** de sous-titres FR corrigée (les sous-titres modifiés sont colorés en orange pour comparaison). |
| `TranslateSubtitlesFR_EN.py` | Traduit la piste FR en anglais et l'ajoute sur une nouvelle piste de sous-titres. |

---

## 🚀 Installation en 2 minutes (aucune connaissance technique requise)

1. **Télécharge** `Installer-Resolve-Subtitles.zip` depuis la page [Releases](../../releases/latest) de ce repo.
2. **Double-clique** sur le zip téléchargé (il se décompresse tout seul dans Téléchargements).
3. **Double-clique** sur l'app **« Installer Resolve Subtitles »**. macOS affiche une confirmation du type « app téléchargée depuis Internet — Apple a vérifié qu'elle ne contient pas de logiciel malveillant » : clique **Ouvrir**.
4. **Suis les fenêtres** : l'assistant installe tout automatiquement. Quand il te demande la **clé API Claude**, colle celle que Néto t'a envoyée (elle commence par `sk-ant-`). Si Python doit être installé, une fenêtre d'installation classique s'ouvre : clique « Continuer » puis « Installer » jusqu'au bout.
5. **Redémarre DaVinci Resolve** (Cmd+Q puis rouvre).

C'est tout ✅ — les scripts apparaissent dans le menu **Espace de travail (Workspace) → Scripts**.

> 💡 Pour mettre à jour plus tard : retélécharge le zip et relance l'app, elle écrase les anciennes versions (ta clé API est conservée).

## Utilisation

Dans Resolve, timeline ouverte avec une piste de sous-titres :

1. **Workspace → Scripts → ScanSubtitlesFR** — analyse et affiche le rapport des fautes dans la console.
2. **Workspace → Scripts → ApplyCorrectionsFR** — crée la piste FR corrigée (lance le scan d'abord ; le rapport doit dater de moins de 24 h).
3. **Workspace → Scripts → TranslateSubtitlesFR_EN** — traduit la piste FR en EN sur une nouvelle piste.

Au lancement, un popup demande **qui parle** (indispensable pour les accords : « je suis venu » ou « venue ») :
- **1 personne : un homme** / **1 personne : une femme** : le plus courant, rapide et fiable
- **Plusieurs personnes : je dis combien et qui** : on indique le nombre de personnes, homme ou femme pour chacune, et au besoin des précisions (« Personne 1 = Néto, il pose les questions »). Claude attribue chaque sous-titre à une personne, puis un popup montre qui dit quoi (numéros de sous-titres) : **Confirmer** ou **Corriger**
- **Plusieurs personnes + capture vidéo** : mêmes questions, plus une analyse d'images de la timeline (le plus précis, plus lent)

Avec plusieurs personnes, un sous-titre que Claude n'a pas pu attribuer garde son accord à la 1re personne tel qu'il est écrit : le script ne force jamais un « je » au masculin ou au féminin sans savoir qui parle.

## Dépannage

| Problème | Solution |
|---|---|
| Les scripts n'apparaissent pas dans le menu Scripts | Quitte complètement Resolve (Cmd+Q) et rouvre-le — il ne scanne les scripts qu'au démarrage. |
| « Python 3 was not found » au lancement d'un script | Relance l'app « Installer Resolve Subtitles » : elle installe le Python officiel (Resolve ne détecte ni Homebrew ni le Python d'Xcode). |
| « Clé API Claude introuvable » | Relance l'app « Installer Resolve Subtitles » et colle la clé quand elle est demandée. |
| J'ai cliqué « Annuler » sur la fenêtre au premier lancement de l'app | Re-double-clique l'app et clique « Ouvrir ». |
| Autre souci | Un fichier `ResolveSubtitles-Install.log` est déposé sur le Bureau en cas d'erreur : envoie-le à Néto (sinon il est dans `~/Library/Logs/`). |

---

## Pour les devs

### Installation manuelle (sans l'app)

```bash
# 1. Python officiel python.org requis (PAS Homebrew — Resolve ne le détecte pas)
# 2. SDK Claude
/Library/Frameworks/Python.framework/Versions/Current/bin/python3 -m pip install anthropic
# 3. Scripts
cp *.py "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/"
# 4. Clé API (au choix : fichier OU variable d'environnement)
mkdir -p ~/.anthropic && echo "sk-ant-..." > ~/.anthropic/api_key && chmod 600 ~/.anthropic/api_key
```

Les scripts cherchent la clé dans `ANTHROPIC_API_KEY` (env) puis dans `~/.anthropic/api_key` (fichier).

### Reconstruire l'app d'installation

```bash
bash installer/build_app.sh 1.0.0
```

Nécessite le certificat « Developer ID Application: Primo Studio » dans le trousseau et la clé ASC de notarisation (machine de Néto). Produit `build/Installer-Resolve-Subtitles.zip`, à attacher à la Release GitHub.

### Notes techniques

- L'API Resolve ne permet pas de modifier le texte d'un sous-titre existant : les corrections passent par l'export/import d'un SRT sur une **nouvelle piste** — l'originale n'est jamais touchée.
- Modèle : `claude-sonnet-4-6` (variable `MODEL` en tête de chaque script). Lots de 150 sous-titres (`BATCH_SIZE`).
- Le menu Workspace → Scripts est contextuel à la page : le dossier `Utility/` est le seul visible depuis **toutes** les pages.
