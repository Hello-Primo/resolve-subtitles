# Historique des changements

## 28/09/2026 : ScanSubtitlesFR, « qui parle » et résultat visible

Usage réel rappelé par Néto : l'équipe écrit souvent les sous-titres à la main (sans le sous-titrage génératif de Resolve) et veut juste confirmer qu'il n'y a pas de faute. Le scan ne modifie jamais la timeline.

### 1. Dire qui parle (commit `42a9902`)

Premier popup, remplace l'ancien choix Single / Multi Auto / Multi Visual :

| Choix | Ce qui se passe |
|---|---|
| 1 personne : un homme | les « je » s'accordent au masculin (avant : seul cas possible sans éditer le code) |
| 1 personne : une femme | les « je » s'accordent au féminin |
| Plusieurs personnes : je dis combien et qui | popups nombre de personnes (2 à 8), homme ou femme pour chacune, précisions libres (prénoms, qui interviewe qui). Claude attribue chaque sous-titre à une personne d'après le texte, puis le popup « Vérifie qui parle » montre les numéros de sous-titres de chacune : **Confirmer** ou **Corriger** (3 essais) |
| Plusieurs personnes + capture vidéo | mêmes popups, plus l'analyse d'images de la timeline. Si l'image propose d'autres genres que ceux déclarés, on bascule sur l'attribution d'après le texte |

Règles de correction ajoutées :
- le « je » s'accorde avec la personne qui parle **dans ce sous-titre**, pas avec un narrateur principal ;
- un sous-titre non attribué garde son accord à la 1re personne tel qu'écrit (jamais forcé au masculin ou au féminin) ;
- les précisions et corrections tapées par l'utilisateur sont transmises à la passe de correction (avant, le bouton « Modifier » était ignoré).

Le rapport JSON garde le même format (`global_context`, `subtitle_speakers_map`) : `ApplyCorrectionsFR` et `TranslateSubtitlesFR_EN` fonctionnent sans changement.

### 2. Résultat toujours visible (commit `07ad290`)

Retour d'Alex (Mac mini) : scan terminé, 5 fautes trouvées, mais rien d'affiché. La Console Resolve ne s'ouvre pas sur tous les postes (droits d'accessibilité).

- Popup de fin systématique : « Aucune faute trouvée sur N sous-titres » ou la liste des changements, ex. `n° 40 (00:00:42) : « forts » → « fortes »` (12 premières lignes, le reste dans le rapport).
- Bouton **Voir le rapport** : `~/Desktop/Fautes sous-titres FR.html`, phrase complète, mot faux barré en rouge, correction en vert, colonne « Qui parle » s'il y a plusieurs personnes. Écrasé à chaque scan.
- Popup aussi quand le scan échoue (sauf si l'utilisateur a annulé).

### Tests

Banc de test `tests/` (faux Resolve, popups simulées mais compilées par `osacompile`, vrais appels Claude). 7 scénarios : homme seul, femme seule, interview homme + femme (texte, capture vidéo avec et sans images), sous-titres sans faute, timeline vide. Sur l'interview : 7 fautes attendues sur 7, aucune fausse correction.

Non testé dans le vrai Resolve par Claude (Resolve occupé par les monteurs) ; testé en réel par Alex sur le Mac mini pour la partie « qui parle » (le scan a tourné et produit le rapport).

### Déploiement

Installé à la main (SSH) le 28/09 sur les 3 Macs, dans `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`, version md5 `949f303a...` : MacBook Pro de Néto, Mac Studio (Warren), Mac mini (Alexandre). Resolve n'a été fermé nulle part.

### Reste à faire

- La release **v1.0.0 de l'installeur** contient encore l'ancien `ScanSubtitlesFR.py` : la refaire (`bash installer/build_app.sh 1.1.0` + nouvelle release) seulement sur décision de Néto.
- `TranslateSubtitlesFR_EN` a toujours ses anciens popups « speakers » ; il réutilise le rapport du scan s'il date de moins de 24 h sur la même timeline, donc les genres déclarés.
- Modèle toujours `claude-sonnet-4-6` dans les 3 scripts.
