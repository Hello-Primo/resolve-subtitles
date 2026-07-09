#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ApplyCorrectionsFR.py — Primo-Studio
Applique les corrections suggerees par ScanSubtitlesFR en CREANT UNE NOUVELLE
PISTE de sous-titres FR corrigee a cote de l'originale.

(L'API Resolve ne permet pas de modifier le texte d'un subtitle existant via
SetName, on doit donc passer par l'import d'un SRT sur une nouvelle piste.)

Les sous-titres MODIFIES sont colores en ORANGE sur la nouvelle piste pour
que tu compares visuellement avec l'originale.

Necessite que ScanSubtitlesFR ait ete lance prealablement
(rapport JSON sur ~/Desktop/scan_subtitles_fr_report.json).

Utilisation dans Resolve :
  Workspace -> Scripts -> ApplyCorrectionsFR
"""

import os
import re
import sys
import json
import time
import tempfile
import subprocess

REPORT_PATH = os.path.expanduser("~/Desktop/scan_subtitles_fr_report.json")
MAX_REPORT_AGE_SECONDS = 24 * 3600
MODIFIED_COLOR = "Orange"
MARKER_COLOR = "Cream"  # plus proche du blanc dispo dans Resolve. Alternatives :
# "Sand", "Yellow", "Lemon", "Mint", "Lavender", "Sky"


# ============== Helpers ==============
def log(msg):
    print(f"[ApplySRT-FR] {msg}")
    try:
        sys.stdout.flush()
    except Exception:
        pass


def notify(title, message):
    try:
        safe_title = title.replace('"', '\\"')
        safe_msg = message.replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "{safe_title}" sound name "Pop"'],
            capture_output=True, timeout=5
        )
    except Exception:
        pass


def open_resolve_console():
    candidates = [
        ("Resolve", "Workspace", "Console"),
        ("Resolve", "Espace de travail", "Console"),
        ("DaVinci Resolve", "Workspace", "Console"),
        ("DaVinci Resolve", "Espace de travail", "Console"),
    ]
    for proc, menu, item in candidates:
        script = (
            f'tell application "System Events" to tell process "{proc}" '
            f'to click menu item "{item}" of menu "{menu}" of menu bar 1'
        )
        try:
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=3)
            if not r.stderr:
                return True
        except Exception:
            pass
    return False


def fail(msg, code=1):
    log(f"ERREUR : {msg}")
    try:
        notify("Application corrections - ERREUR", msg[:120])
    except Exception:
        pass
    sys.exit(code)


def open_finder_at_path(path):
    """Ouvre le Finder avec le fichier selectionne (pour drag-drop facile)."""
    try:
        subprocess.run(["open", "-R", path], capture_output=True, timeout=5)
    except Exception:
        pass


def show_drag_drop_dialog(diff_path, full_path, n_diff, n_total):
    """Popup macOS expliquant le drag-drop manuel."""
    msg_lines = [
        f"Le SRT contenant SEULEMENT les {n_diff} sous-titres corriges est pret",
        "sur ton Bureau (le Finder vient de l'ouvrir).",
        "",
        "Pour creer la piste corrigee :",
        "1. Drag-drop le fichier DIFF sur la zone vide de ta timeline",
        "2. Resolve cree une nouvelle piste subtitle aux bons timecodes",
        "3. Les " + str(n_diff) + " sous-titres corriges sont visibles AU-DESSUS de ST1",
        "   (donc affiches en priorite sur ces timecodes).",
        "",
        "Sur le Bureau tu trouveras aussi le SRT _TOUT (les " + str(n_total) + " complets)",
        "au cas ou tu prefererais remplacer toute la piste."
    ]
    msg = " & return & ".join(f'"{l}"' for l in msg_lines)
    apple_script = (
        'tell application "System Events" to activate\n'
        f'display dialog ({msg}) '
        'with title "Corrections pretes - drag-drop manuel" '
        'buttons {"OK"} default button "OK"'
    )
    try:
        subprocess.run(["osascript", "-e", apple_script],
                       capture_output=True, text=True, timeout=600)
    except Exception:
        pass


def frames_to_srt_tc(frames, fps):
    total_ms = int(round(frames * 1000.0 / fps))
    hh = total_ms // 3_600_000
    mm = (total_ms % 3_600_000) // 60_000
    ss = (total_ms % 60_000) // 1000
    ms = total_ms % 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


# ============== Lecture du rapport ==============
if not os.path.exists(REPORT_PATH):
    fail(
        f"Aucun rapport trouve a {REPORT_PATH}.\n"
        "Lance d'abord : Workspace > Scripts > ScanSubtitlesFR"
    )

try:
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        report = json.load(f)
except Exception as e:
    fail(f"Rapport illisible : {e}")

scan_ts = report.get("scan_timestamp", 0)
age = int(time.time()) - scan_ts
if age > MAX_REPORT_AGE_SECONDS:
    log(f"ATTENTION : rapport vieux de {age // 3600}h. Relance ScanSubtitlesFR si la timeline a evolue.")

fautes = report.get("fautes", [])
if not fautes:
    log("Aucune faute dans le rapport. Rien a appliquer.")
    sys.exit(0)

expected_timeline_name = report.get("timeline_name")
source_track = report.get("track_index")
log(f"Rapport charge : {len(fautes)} correction(s) ({report.get('scan_date')})")
log(f"Timeline attendue : '{expected_timeline_name}' | piste source #{source_track}")

# Index fautes par idx pour application
fautes_by_idx = {str(f["idx"]): f for f in fautes}


# ============== Connexion Resolve ==============
def get_resolve():
    try:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except ImportError:
        sys.path.append(
            "/Library/Application Support/Blackmagic Design/"
            "DaVinci Resolve/Developer/Scripting/Modules"
        )
        try:
            import DaVinciResolveScript as dvr
            return dvr.scriptapp("Resolve")
        except Exception as e:
            fail(f"Impossible de charger l'API Resolve : {e}")


resolve = get_resolve()
open_resolve_console()
notify("Application des corrections", "Demarrage... Suis la Console Resolve.")

project = resolve.GetProjectManager().GetCurrentProject()
if not project:
    fail("Aucun projet ouvert.")

timeline = project.GetCurrentTimeline()
if not timeline:
    fail("Aucune timeline active.")

current_name = timeline.GetName()
if current_name != expected_timeline_name:
    log(f"ATTENTION : timeline active = '{current_name}', rapport pour '{expected_timeline_name}'.")
    log("Application quand meme...")


# ============== Recuperation des entries actuelles depuis la timeline ==============
def get_fps():
    fps_str = None
    try:
        fps_str = timeline.GetSetting("timelineFrameRate")
    except Exception:
        pass
    if not fps_str:
        try:
            fps_str = project.GetSetting("timelineFrameRate")
        except Exception:
            pass
    return float(fps_str) if fps_str else 25.0


fps = get_fps()
log(f"Frame rate : {fps}")

items = timeline.GetItemListInTrack("subtitle", source_track) or []
if not items:
    fail(f"Aucun sous-titre sur la piste #{source_track}.")
log(f"Sous-titres sources : {len(items)}")

tl_start = timeline.GetStartFrame() or 0

# Construit DEUX versions du SRT :
#  - DIFF : seulement les sous-titres corriges (= 5 items). C'est celui qu'on
#    utilise par defaut : place sur une nouvelle piste subtitle, il OVERRIDE
#    visuellement ST1 a ces timecodes precis.
#  - FULL : tous les sous-titres (corriges + inchanges). En backup si besoin.
modified_indexes = set()
srt_diff_blocks = []
srt_full_blocks = []
diff_counter = 0

for i, item in enumerate(items, start=1):
    idx = str(i)
    original_text = item.GetName() or ""
    start_f = item.GetStart() - tl_start
    end_f = item.GetEnd() - tl_start
    tc = f"{frames_to_srt_tc(start_f, fps)} --> {frames_to_srt_tc(end_f, fps)}"

    final_text = original_text
    is_modified = False
    if idx in fautes_by_idx:
        f = fautes_by_idx[idx]
        scan_orig = f.get("text", "")
        if original_text.strip() == scan_orig.strip():
            final_text = f["suggested"]
            is_modified = True
            modified_indexes.add(idx)
        else:
            log(f"  #{idx} : texte modifie depuis le scan, on garde l'original.")

    srt_full_blocks.append(f"{idx}\n{tc}\n{final_text}\n")
    if is_modified:
        diff_counter += 1
        srt_diff_blocks.append(f"{diff_counter}\n{tc}\n{final_text}\n")

srt_diff_content = "\n".join(srt_diff_blocks)
srt_full_content = "\n".join(srt_full_blocks)

# Sauvegarde des 2 versions sur le Bureau (visible facilement)
tl_safe = re.sub(r"[^\w\-]+", "_", current_name)
diff_path = os.path.expanduser(f"~/Desktop/{tl_safe}_FR_corrige_DIFF.srt")
full_path = os.path.expanduser(f"~/Desktop/{tl_safe}_FR_corrige_TOUT.srt")
with open(diff_path, "w", encoding="utf-8") as f:
    f.write(srt_diff_content)
with open(full_path, "w", encoding="utf-8") as f:
    f.write(srt_full_content)
log(f"SRT DIFF (juste {diff_counter} corriges) : {diff_path}")
log(f"SRT TOUT ({len(items)} sous-titres complets) : {full_path}")

# Le SRT que le script va tenter d'importer = DIFF
srt_path = diff_path
desktop_backup = diff_path


# ============== Markers sur la timeline aux timecodes des corrections ==============
# Pose un marker (couleur Cream = quasi-blanc) a chaque correction. Tu peux
# alors naviguer rapidement via le menu Markers ou en cliquant sur les marker
# dans la regle de la timeline. Markers poses MEME si l'import SRT echoue.
log("Pose des markers timeline aux timecodes des corrections...")
markers_placed = 0
for f in fautes:
    idx = str(f["idx"])
    if idx not in modified_indexes:
        continue
    rel_frame = int(f.get("start_frame", 0)) - tl_start
    if rel_frame < 0:
        continue
    suggested = f.get("suggested", "")
    short = (suggested[:40] + "...") if len(suggested) > 40 else suggested
    note = (f"Correction #{idx}\n"
            f"AVANT : {f.get('text', '')}\n"
            f"APRES : {suggested}")
    try:
        if timeline.AddMarker(rel_frame, MARKER_COLOR, short, note, 1, ""):
            markers_placed += 1
    except Exception as e:
        log(f"  AddMarker #{idx} echoue : {e}")
log(f"Markers poses : {markers_placed}/{len(modified_indexes)} en {MARKER_COLOR} "
    f"(visibles dans la regle de la timeline)")


# ============== Snapshot avant pour identifier ou les items atterrissent ==============
def snapshot_subs():
    n = timeline.GetTrackCount("subtitle") or 0
    snap = {}
    for t in range(1, n + 1):
        its = timeline.GetItemListInTrack("subtitle", t) or []
        uids = set()
        for it in its:
            try:
                uids.add(it.GetUniqueId())
            except Exception:
                uids.add(id(it))
        snap[t] = uids
    return snap


snapshot_before = snapshot_subs()


# ============== Cree une nouvelle piste subtitle vide + import + append ==============
media_pool = project.GetMediaPool()
target_track = None
try:
    if timeline.AddTrack("subtitle"):
        target_track = (timeline.GetTrackCount("subtitle") or 0)
        log(f"Nouvelle piste subtitle creee : #{target_track}")
except Exception as e:
    log(f"AddTrack echoue ({e}). Import quand meme.")

imported = media_pool.ImportMedia([srt_path])
if not imported:
    log(f"Import auto echoue. SRT dispo : {srt_path}\n"
        f"                  ou Bureau : {desktop_backup}")
    fail("Import du SRT corrige impossible. Voir le SRT sur le Bureau.")

# Lock toutes les pistes subtitle SAUF la nouvelle cible.
# Resolve va etre force d'utiliser la seule piste non-verrouillee = la cible.
total_sub_tracks = timeline.GetTrackCount("subtitle") or 0
locked_tracks = []
for t in range(1, total_sub_tracks + 1):
    if t == target_track:
        continue
    try:
        if timeline.SetTrackLock("subtitle", t, True):
            locked_tracks.append(t)
            log(f"  Lock piste subtitle #{t} (pour forcer routing vers piste #{target_track})")
    except Exception as e:
        log(f"  Lock piste #{t} echoue ({e})")

# Append SIMPLE (juste mediaPoolItem, pas de clipInfo).
# Le clipInfo dict avec trackIndex N'EST PAS supporte pour subtitles dans
# l'API Resolve V20. Avec le lock des autres pistes, Resolve devrait router
# vers la seule piste subtitle libre (target_track).
appended = False
try:
    res = media_pool.AppendToTimeline([imported[0]])
    if res:
        appended = True
        log(f"AppendToTimeline OK : {len(res) if hasattr(res, '__len__') else '?'} items places")
    else:
        log("AppendToTimeline a retourne vide.")
except Exception as e:
    log(f"AppendToTimeline a leve une exception : {e}")

# Deverrouille les pistes lockees
for t in locked_tracks:
    try:
        timeline.SetTrackLock("subtitle", t, False)
    except Exception:
        pass

if not appended:
    log("Append auto impossible (limitation API subtitle Resolve).")
    log("Pas grave : le drag-and-drop manuel marche parfaitement.")
    open_finder_at_path(diff_path)
    show_drag_drop_dialog(diff_path, full_path, diff_counter, len(items))
    sys.exit(0)


# ============== Coloration des sous-titres modifies ==============
snapshot_after = snapshot_subs()
new_items_per_track = {}
for t, uids_after in snapshot_after.items():
    uids_before = snapshot_before.get(t, set())
    diff = uids_after - uids_before
    if diff:
        new_items_per_track[t] = diff

if not new_items_per_track:
    log("Resolve n'a pas place les sous-titres automatiquement.")
    log("Pas grave : le drag-and-drop manuel marche parfaitement.")
    open_finder_at_path(diff_path)
    show_drag_drop_dialog(diff_path, full_path, diff_counter, len(items))
    sys.exit(0)

# Identifie la piste cible reelle
arrived_on = list(new_items_per_track.keys())
if target_track and target_track in new_items_per_track and len(arrived_on) == 1:
    real_track = target_track
    log(f"OK : sous-titres corriges sur piste neuve #{real_track}")
else:
    real_track = arrived_on[0]
    log(f"Sous-titres atterris sur piste #{real_track}")

# Recupere les nouveaux items et colore TOUS (en mode DIFF ils sont tous modifies)
new_items = timeline.GetItemListInTrack("subtitle", real_track) or []
colored = 0
for new_item in new_items:
    try:
        if new_item.SetClipColor(MODIFIED_COLOR):
            colored += 1
    except Exception as e:
        log(f"  SetClipColor echoue : {e}")
log(f"Coloration : {colored} sous-titres en {MODIFIED_COLOR}")


# ============== Resume final ==============
log("")
log("=" * 70)
log(f"RESULTAT : {diff_counter} correction(s) appliquee(s) sur "
    f"nouvelle piste #{real_track}")
log(f"  Piste #{source_track} = originale (conservee, 65 sous-titres)")
log(f"  Piste #{real_track} = juste les {diff_counter} corriges (en Orange)")
log("=" * 70)
log("Affichage : a ces timecodes, ST2 (correction) prime sur ST1 (originale).")
log("Pour annuler : Cmd+Z dans Resolve (ou supprime la nouvelle piste).")
notify(
    "Corrections appliquees",
    f"{diff_counter} correction(s) sur nouvelle piste #{real_track}. "
    f"Orange = modifie. Cmd+Z pour annuler."
)
