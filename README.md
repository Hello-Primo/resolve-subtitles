# Resolve Subtitles — scripts sous-titres DaVinci Resolve (Primo-Studio)

Trois scripts Python pour DaVinci Resolve qui corrigent et traduisent la piste de sous-titres de la timeline active via l'API Claude (Anthropic).

| Script | Rôle |
|---|---|
| `ScanSubtitlesFR.py` | Analyse la piste de sous-titres FR et détecte les fautes (orthographe / grammaire / ponctuation) **sans rien modifier**. Rapport JSON sur `~/Desktop/scan_subtitles_fr_report.json`. |
| `ApplyCorrectionsFR.py` | Applique les corrections du rapport en créant une **nouvelle piste** de sous-titres FR corrigée (les sous-titres modifiés sont colorés en orange pour comparaison). |
| `TranslateSubtitlesFR_EN.py` | Traduit la piste FR en anglais et l'ajoute sur une nouvelle piste de sous-titres. |

## Prérequis

- DaVinci Resolve **Studio** (l'API scripting est requise)
- Python 3 officiel [python.org](https://www.python.org/downloads/) (pas celui de Homebrew)
- Le SDK Anthropic :
  ```bash
  pip3 install anthropic
  ```
- Une clé API Anthropic dans l'environnement :
  ```bash
  # dans ~/.zshrc
  export ANTHROPIC_API_KEY="sk-ant-..."
  # et pour que Resolve (app GUI) la voie :
  launchctl setenv ANTHROPIC_API_KEY "$ANTHROPIC_API_KEY"
  ```
  ⚠️ `launchctl setenv` doit être relancé après chaque redémarrage du Mac (ou ajouté à un LaunchAgent).

## Installation

Copier les 3 scripts dans le dossier scripts Utility de Resolve :

```bash
cp *.py "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/"
```

Puis redémarrer Resolve (ou rouvrir le menu Scripts).

## Utilisation

Dans Resolve, timeline ouverte avec une piste de sous-titres active :

1. **Workspace → Scripts → ScanSubtitlesFR** — lance l'analyse, lis le rapport dans la console.
2. **Workspace → Scripts → ApplyCorrectionsFR** — crée la piste corrigée (nécessite un rapport de moins de 24 h).
3. **Workspace → Scripts → TranslateSubtitlesFR_EN** — traduit la piste FR en EN sur une nouvelle piste.

### Mode speaker

`ScanSubtitlesFR` et `TranslateSubtitlesFR_EN` proposent 3 modes (popup au lancement, ou valeur fixe via `SPEAKER_MODE` en tête de script) :

- `single` — une seule personne parle (le plus simple et le plus fiable)
- `multi_auto` — plusieurs intervenants, détection automatique
- `multi_visual` — plusieurs intervenants, avec analyse visuelle

## Notes techniques

- L'API Resolve ne permet pas de modifier le texte d'un sous-titre existant : les corrections passent par l'export/import d'un SRT sur une **nouvelle piste** — l'originale n'est jamais touchée.
- Modèle utilisé : `claude-sonnet-4-6` (configurable via `MODEL` en tête de chaque script).
- Les scripts traitent par lots de 150 sous-titres (`BATCH_SIZE`).
