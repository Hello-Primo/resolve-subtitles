#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ScanSubtitlesFR.py — Primo-Studio
Analyse la piste de sous-titres FR active et rapporte les fautes detectees
(orthographe / grammaire / ponctuation) SANS modifier la timeline.

Affiche dans la console : pour chaque sous-titre fautif, le timecode,
le texte original et la correction suggeree. Sauvegarde le rapport en JSON
sur ~/Desktop pour qu'ApplyCorrectionsFR puisse l'appliquer ensuite.

Utilisation dans Resolve :
  Workspace -> Scripts -> ScanSubtitlesFR
  (puis lire le rapport, et eventuellement lancer ApplyCorrectionsFR)

Prerequis :
  Python 3 officiel python.org installe
  pip3 install anthropic
  Cle API : fichier ~/.anthropic/api_key contenant la cle (sk-ant-...)
  — cree automatiquement par l'installeur Resolve Subtitles.
  (La variable d'environnement ANTHROPIC_API_KEY marche aussi, mais uniquement
  si Resolve est lance depuis un terminal — pas depuis le Dock/Finder.)
"""

import os
import re
import sys
import json
import time
import base64
import tempfile
import subprocess

# ============== Config ==============
MODEL = "claude-sonnet-4-6"
BATCH_SIZE = 150  # videos courtes <= ~12 min passent en 1 batch

# ────────── SELECTEUR DE MODE SPEAKER ──────────
# Choisis selon ta video :
#
#   "single"        → 1 seule personne parle. Le popup demande homme / femme
#                     (SINGLE_* = valeurs utilisees si le mode est fixe ici).
#                     Plus rapide, gratuit, fiable. Cas le plus simple.
#
#   "multi_auto"    → plusieurs intervenants, d'apres le texte seul. Popups :
#                     combien de personnes, homme ou femme pour chacune,
#                     precisions (prenoms, qui interviewe qui). Claude attribue
#                     ensuite chaque sous-titre a une personne, tu valides.
#
#   "multi_visual"  → plusieurs intervenants, memes popups + capture de N
#                     frames de la timeline : Claude VOIT qui parle. Le plus
#                     precis. Coute ~+0,1-0,5€ selon duree video.
# Par defaut "ask" : popup macOS au lancement pour choisir.
# Mets une valeur fixe ("single"/"multi_auto"/"multi_visual") pour skip le popup.
SPEAKER_MODE = "ask"

# ── Si SPEAKER_MODE = "single" fixe (le popup, lui, demande homme / femme) ──
SINGLE_NARRATOR_NAME = "Néto"
SINGLE_NARRATOR_GENDER = "masculin"  # "masculin" / "feminin"

# Nombre max de personnes proposees dans le popup "combien de personnes"
MAX_DECLARED_SPEAKERS = 8

# ── Si SPEAKER_MODE = "multi_visual" ──
# Frequence de capture des frames :
#   "auto" -> adapte a la duree : <30s=3 | 30-120s=5 | 120-600s=8 | >10min=12
#   "30s"  -> 1 frame toutes les 30 secondes
#   "60s"  -> 1 frame toutes les 60 secondes
#   "120s" -> 1 frame toutes les 2 minutes
#   nombre -> ce nb exact de frames
MULTI_VISUAL_FRAMES = "auto"

# Emplacement du rapport JSON (lu par ApplyCorrectionsFR)
REPORT_PATH = os.path.expanduser("~/Desktop/scan_subtitles_fr_report.json")


# ============== Helpers ==============
def log(msg):
    print(f"[ScanSRT-FR] {msg}")
    try:
        sys.stdout.flush()
    except Exception:
        pass


def fail(msg, code=1):
    log(f"ERREUR : {msg}")
    try:
        notify("Scan sous-titres FR - ERREUR", msg[:120])
    except Exception:
        pass
    sys.exit(code)


def notify(title, message):
    """Affiche une notification macOS native (top-right)."""
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
    """Ouvre la fenetre Console de Resolve via le menu Workspace > Console.
    Essaie plusieurs combinaisons (FR/EN, Resolve / DaVinci Resolve)."""
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
if not resolve:
    fail("Aucune instance Resolve detectee.")

# Ouvre la Console Resolve pour que l'utilisateur voie le progres
open_resolve_console()
notify("Scan sous-titres FR", "Demarrage... Suis le progres dans la Console Resolve.")

project = resolve.GetProjectManager().GetCurrentProject()
if not project:
    fail("Aucun projet ouvert.")

timeline = project.GetCurrentTimeline()
if not timeline:
    fail("Aucune timeline active.")

log(f"Timeline active : {timeline.GetName()}")


# ============== Recuperation des sous-titres depuis la timeline ==============
def frames_to_srt_tc(frames, fps):
    total_ms = int(round(frames * 1000.0 / fps))
    hh = total_ms // 3_600_000
    mm = (total_ms % 3_600_000) // 60_000
    ss = (total_ms % 60_000) // 1000
    ms = total_ms % 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


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
log(f"Frame rate timeline : {fps}")

sub_track_count = timeline.GetTrackCount("subtitle") or 0
if sub_track_count == 0:
    fail("Aucune piste de sous-titres trouvee sur la timeline.")

items = None
chosen_track = None
for t in range(1, sub_track_count + 1):
    it = timeline.GetItemListInTrack("subtitle", t) or []
    if it:
        items = it
        chosen_track = t
        break

if not items:
    fail("Toutes les pistes de sous-titres sont vides.")

log(f"Piste sous-titres source : #{chosen_track} ({len(items)} items)")
tl_start = timeline.GetStartFrame() or 0

# Construit les entries : on garde aussi un mapping idx -> unique_id pour Apply
entries = []
for i, item in enumerate(items, start=1):
    text = item.GetName() or ""
    start_f = item.GetStart() - tl_start
    end_f = item.GetEnd() - tl_start
    unique_id = None
    try:
        unique_id = item.GetUniqueId()
    except Exception:
        pass
    entries.append({
        "idx": str(i),
        "track": chosen_track,
        "unique_id": unique_id,
        "start_frame": int(item.GetStart()),
        "end_frame": int(item.GetEnd()),
        "timecode": f"{frames_to_srt_tc(start_f, fps)} --> {frames_to_srt_tc(end_f, fps)}",
        "text": text,
    })

log(f"{len(entries)} sous-titres a analyser.")


# ============== Appel Claude ==============
api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    # Fallback : fichier cree par l'installeur Resolve Subtitles
    try:
        with open(os.path.expanduser("~/.anthropic/api_key")) as _f:
            api_key = _f.read().strip()
    except OSError:
        pass
if not api_key:
    fail(
        "Cle API Claude introuvable.\n"
        "Relance l'installeur Resolve Subtitles pour la configurer, ou cree\n"
        "le fichier ~/.anthropic/api_key contenant la cle (sk-ant-...)."
    )

try:
    from anthropic import Anthropic
except ImportError:
    fail("Module 'anthropic' manquant. Lance : pip3 install anthropic")

client = Anthropic(api_key=api_key)


# ============== PASSE 1 : extraction du contexte global ==============
def extract_global_context(entries):
    """Lit tous les sous-titres pour extraire sujet, personnages, ton, glossaire."""
    log("Passe 1/2 : extraction du contexte global de la video...")
    all_texts = "\n".join(f"[{e['idx']}] {e['text']}" for e in entries)

    ctx_system = (
        "Tu es un analyste de scripts video. Tu lis l'integralite des sous-titres "
        "et tu en extrais le contexte global pour qu'un correcteur/traducteur "
        "puisse travailler de maniere coherente."
    )
    ctx_user = (
        f"Voici TOUS les sous-titres d'une video ({len(entries)} lignes). "
        "Analyse-les en globalite et extrais :\n"
        "- summary : resume du contenu en 1-2 phrases\n"
        "- topic : theme principal en 3-5 mots\n"
        "- tone : ton dominant (formel/familier/promotionnel/technique/oral spontane/journalistique/etc)\n"
        "- characters : LISTE DE STRINGS (juste les noms, pas de description) - "
        "prenoms / noms propres de personnes cites\n"
        "- glossary : LISTE DE STRINGS (juste les termes, pas de definition) - "
        "marques, organisations, lieux, termes specifiques a NE PAS modifier\n"
        "- narrator_name : STRING - nom du narrateur/narratrice PRINCIPAL(E) (celui qui "
        "dit 'je/moi/me' le plus souvent, qui semble structurer le discours). "
        "Pattern frequent : 1 narrateur principal + plusieurs intervenants secondaires. "
        "Cherche : presentation initiale, signature, mention 'je suis X', recurrence.\n"
        "- narrator_gender : STRING - 'masculin' / 'feminin' uniquement. "
        "REGLE IMPORTANTE : si genre vraiment inconnu/ambigu, repond 'masculin' "
        "(regle du masculin generique en francais en cas de doute). "
        "Deduis-le du nom (Néto=M, Marie=F, Camille=ambigu), du contexte, du ton, "
        "des tournures. CRITIQUE pour les accords des participes passes.\n"
        "- gender_clues : STRING - indices sur le genre des AUTRES sujets cites "
        "('plusieurs intervenantes feminines', 'mixte', etc.). Utile pour 'ils/elles', "
        "'porteurs/porteuses', 'venu/venue/venus/venues'.\n\n"
        "Sous-titres :\n"
        f"{all_texts}\n\n"
        'Reponse en JSON STRICT, aucun texte autour : '
        '{"summary":"...","topic":"...","tone":"...","characters":["nom1","nom2"],'
        '"glossary":["terme1","terme2"],"narrator_name":"...",'
        '"narrator_gender":"masculin|feminin|inconnu","gender_clues":"..."}'
    )

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1000,
            temperature=0,
            system=ctx_system,
            messages=[{"role": "user", "content": ctx_user}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            log(f"  Contexte non parsable, on continue sans : {raw[:200]}")
            return None
        context = json.loads(m.group(0))
        # Normalise : si Claude a renvoye une liste de dicts au lieu de strings
        for key in ("characters", "glossary"):
            val = context.get(key, [])
            if val and isinstance(val[0], dict):
                context[key] = [
                    str(d.get("term") or d.get("name") or d.get("text") or next(iter(d.values()), ""))
                    for d in val if d
                ]
            elif not isinstance(val, list):
                context[key] = []
        log(f"  Sujet      : {context.get('topic', '?')}")
        log(f"  Ton        : {context.get('tone', '?')}")
        log(f"  Narrateur  : {context.get('narrator_name', '?')} ({context.get('narrator_gender', '?')})")
        log(f"  Autres genres : {context.get('gender_clues', '?')}")
        chars = context.get('characters') or []
        if chars:
            log(f"  Personnages: {', '.join(chars[:10])}{'...' if len(chars) > 10 else ''}")
        gloss = context.get('glossary') or []
        if gloss:
            log(f"  Glossaire  : {', '.join(gloss[:10])}{'...' if len(gloss) > 10 else ''}")
        return context
    except Exception as e:
        log(f"  Extraction contexte echouee, on continue sans : {e}")
        return None


# ============== Capture frames (mode multi_visual uniquement) ==============
def auto_frame_count(duration_sec):
    if duration_sec < 30:
        return 3
    if duration_sec < 120:
        return 5
    if duration_sec < 600:
        return 8
    return 12


def resolve_frame_count(config, duration_sec):
    """Convertit MULTI_VISUAL_FRAMES en nombre concret de frames.
    Accepte 'auto', '30s', '60s', '120s', ou un entier."""
    if config == "auto" or config is None:
        return auto_frame_count(duration_sec)
    if isinstance(config, str) and config.endswith("s"):
        try:
            interval = int(config[:-1])
            return max(1, int(duration_sec / interval))
        except ValueError:
            return auto_frame_count(duration_sec)
    try:
        return max(1, int(config))
    except (ValueError, TypeError):
        return auto_frame_count(duration_sec)


def frames_to_timecode_hhmmssff(frames, fps):
    """Convertit frames en timecode HH:MM:SS:FF pour SetCurrentTimecode."""
    total_sec = frames / fps
    hh = int(total_sec // 3600)
    mm = int((total_sec % 3600) // 60)
    ss = int(total_sec % 60)
    ff = int(frames % int(round(fps)))
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def capture_timeline_frames(n_frames=None):
    """Capture n_frames JPG repartis sur la timeline.
    Retourne [{path, timecode, frame}] ou liste vide si echec."""
    start = timeline.GetStartFrame() or 0
    end = timeline.GetEndFrame() or 0
    duration_frames = max(0, end - start)
    if duration_frames <= 0:
        log("Timeline vide ou GetEndFrame indisponible — pas de capture.")
        return []
    duration_sec = duration_frames / fps

    n_frames = resolve_frame_count(n_frames, duration_sec)

    log(f"Capture de {n_frames} frame(s) sur {duration_sec:.1f}s de timeline...")
    capture_dir = tempfile.mkdtemp(prefix="resolve_frames_")
    captures = []

    for i in range(n_frames):
        # Position equi-repartie (eviter exactement debut et fin)
        offset_frames = int(duration_frames * (i + 0.5) / n_frames)
        tc_frames = start + offset_frames
        tc_str = frames_to_timecode_hhmmssff(tc_frames - start, fps)

        try:
            ok = timeline.SetCurrentTimecode(tc_str)
            if not ok:
                log(f"  Frame {i+1}: SetCurrentTimecode({tc_str}) a retourne False")
                continue
        except Exception as e:
            log(f"  Frame {i+1}: SetCurrentTimecode echoue ({e})")
            continue

        out_path = os.path.join(capture_dir, f"frame_{i+1:02d}.jpg")
        try:
            if project.ExportCurrentFrameAsStill(out_path) and os.path.exists(out_path):
                captures.append({"path": out_path, "timecode": tc_str, "frame": tc_frames})
                log(f"  Frame {i+1}/{n_frames} OK @ {tc_str}")
            else:
                log(f"  Frame {i+1}: ExportCurrentFrameAsStill a echoue")
        except Exception as e:
            log(f"  Frame {i+1}: export echoue ({e})")

    return captures


def capture_one_frame_per_subtitle(entries_local):
    """Capture une image au MILIEU du timecode de chaque sous-titre.
    Retourne [{path, timecode, subtitle_idx}]. Plus precis sur montage rapide
    (Reels) ou les coupes sont tres courtes."""
    if not entries_local:
        return []
    fps_local = float(timeline.GetSetting("timelineFrameRate")
                      or project.GetSetting("timelineFrameRate") or 25)
    tl_start = timeline.GetStartFrame() or 0
    capture_dir = tempfile.mkdtemp(prefix="resolve_per_sub_")
    captures = []

    log(f"Capture par sous-titre : {len(entries_local)} captures prevues...")
    for i, entry in enumerate(entries_local, 1):
        mid_frame = (entry["start_frame"] + entry["end_frame"]) // 2
        tc_str = frames_to_timecode_hhmmssff(mid_frame - tl_start, fps_local)
        try:
            if not timeline.SetCurrentTimecode(tc_str):
                continue
        except Exception:
            continue
        out_path = os.path.join(capture_dir, f"sub_{i:03d}.jpg")
        try:
            if project.ExportCurrentFrameAsStill(out_path) and os.path.exists(out_path):
                captures.append({
                    "path": out_path,
                    "timecode": tc_str,
                    "subtitle_idx": entry["idx"],
                })
                if i % 5 == 0 or i == len(entries_local):
                    log(f"  Capture {i}/{len(entries_local)}...")
        except Exception:
            continue
    log(f"Captures par sous-titre : {len(captures)}/{len(entries_local)} reussies")
    return captures


def resize_image_for_vision(path, max_size=512):
    """Redimensionne une image via sips macOS natif pour reduire le poids
    avant envoi a Claude vision. Retourne le path redimensionne (ou l'original
    si echec)."""
    try:
        resized_path = path + ".resized.jpg"
        result = subprocess.run(
            ["/usr/bin/sips", "-Z", str(max_size),
             "-s", "format", "jpeg",
             "-s", "formatOptions", "70",  # qualite 70%
             path, "--out", resized_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and os.path.exists(resized_path):
            return resized_path
    except Exception as e:
        log(f"  Resize echoue ({e}), on garde l'original.")
    return path


def encode_image_b64(path, resize=True):
    """Encode une image en base64. Si resize=True, redimensionne d'abord."""
    if resize:
        path = resize_image_for_vision(path)
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


# ============== PASSE 0 : analyse pre-validation des speakers ==============
def analyze_speakers_with_vision(entries, captures, user_hint=""):
    """Passe 0 : Claude analyse les frames + texte pour proposer une
    identification des speakers. Retourne un dict structure pour validation."""
    # Echantillonnage pour la passe 0 : pas besoin de toutes les images,
    # Claude a juste besoin de quelques exemples pour identifier les speakers
    MAX_SAMPLES = 20
    if len(captures) > MAX_SAMPLES:
        step = len(captures) / MAX_SAMPLES
        sampled = [captures[int(i * step)] for i in range(MAX_SAMPLES)]
        log(f"Passe 0/2 : echantillonnage {MAX_SAMPLES} images sur {len(captures)} "
            f"(pour rester sous la limite API)...")
    else:
        sampled = captures
        log(f"Passe 0/2 : analyse pre-validation ({len(sampled)} images)...")

    content = []
    if user_hint:
        content.append({"type": "text", "text": (
            f"INDICATION UTILISATEUR (priorite haute, mais peut etre incomplete) :\n"
            f"« {user_hint} »\n\nUtilise-la pour t'orienter."
        )})

    for i, cap in enumerate(sampled):
        try:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg",
                           "data": encode_image_b64(cap["path"])},
            })
            label = (f"Image du sous-titre #{cap['subtitle_idx']} @ {cap['timecode']}"
                     if "subtitle_idx" in cap else f"Frame #{i+1} @ {cap['timecode']}")
            content.append({"type": "text", "text": f"^ {label}"})
        except Exception as e:
            log(f"  Image {i+1} non encodee : {e}")

    all_texts = "\n".join(f"[{e['idx']}] {e['text']}" for e in entries)
    content.append({"type": "text", "text": (
        f"PATTERN FREQUENT : 1 narrateur/narratrice principal + plusieurs "
        "intervenants secondaires. Le narrateur principal est celui qui dit "
        "'je / moi' le plus souvent ou qui structure le discours.\n\n"
        "ATTENTION : montage avec B-roll / J-cut / L-cut. L'image au timecode "
        "d'un sous-titre ne montre pas forcement le speaker. Priorise CONTENU "
        "TEXTE + indication user, puis image.\n\n"
        f"Sous-titres :\n{all_texts}\n\n"
        "Identifie les intervenants. JSON STRICT, AUCUN texte autour :\n"
        "{\n"
        '  "narrator":{"name":"...","gender":"masculin|feminin",'
        '"description":"apparence ou role (ex: homme 30 ans / Néto / homme barbu)"},\n'
        '  "other_speakers":[{"label":"Speaker 2","gender":"masculin|feminin",'
        '"description":"..."}],\n'
        '  "subtitle_speakers":{"1":"narrator","2":"Speaker 2","3":"narrator"},\n'
        '  "summary":"resume 1 phrase","topic":"sujet 3-5 mots","tone":"...",\n'
        '  "glossary":["mots/marques a ne pas modifier"]\n'
        "}\n"
        "REGLES :\n"
        "- narrator_gender : si vraiment ambigu, mets 'masculin' (regle francaise du masculin generique)\n"
        "- subtitle_speakers : pour CHAQUE idx fourni, indique le label du speaker "
        "(soit 'narrator' soit 'Speaker N')\n"
        "- description : courte (max 50 chars), focalisee sur l'apparence si visible\n"
    )})

    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=4000, temperature=0,
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            log(f"  Reponse non-JSON : {raw[:200]}")
            return None
        proposal = json.loads(m.group(0))
        # Normalisation glossaire
        gl = proposal.get("glossary", [])
        if gl and isinstance(gl[0], dict):
            proposal["glossary"] = [str(d.get("term") or d.get("name") or next(iter(d.values()),"")) for d in gl if d]
        return proposal
    except Exception as e:
        log(f"  Passe 0 echouee : {e}")
        return None


# ============== Popup macOS : validation des speakers proposes ==============
def format_proposal_for_dialog(proposal):
    """Formate la proposition Claude pour affichage dans la popup."""
    narrator = proposal.get("narrator") or {}
    others = proposal.get("other_speakers") or []

    lines = []
    nname = narrator.get("name", "?")
    ngender = narrator.get("gender", "?")
    ndesc = narrator.get("description", "")
    lines.append(f"NARRATEUR PRINCIPAL : {nname} ({ngender})")
    if ndesc:
        lines.append(f"  {ndesc[:80]}")

    for sp in others:
        lab = sp.get("label", "?")
        gen = sp.get("gender", "?")
        desc = sp.get("description", "")
        lines.append(f"{lab} : {gen}")
        if desc:
            lines.append(f"  {desc[:80]}")

    return "\n".join(lines)


def validate_speakers_dialog(proposal):
    """Affiche la proposition Claude. Retourne :
    - proposal tel quel si user confirme
    - proposal modifie si user a tape une correction
    - None si user annule"""
    summary = format_proposal_for_dialog(proposal)
    # AppleScript display dialog avec multi-lignes via & return &
    # Echappage des guillemets
    summary_safe = summary.replace('"', "''")
    # Decoupe en lignes pour & return &
    lines = summary_safe.split("\n")
    apple_lines = ' & return & '.join(f'"{l}"' for l in lines)
    title = "Claude a detecte les speakers suivants"

    apple_script = (
        'tell application "System Events" to activate\n'
        f'set userChoice to display dialog ({apple_lines}) '
        f'with title "{title}" '
        'buttons {"Annuler", "Modifier", "Confirmer"} '
        'default button "Confirmer"\n'
        'return button returned of userChoice'
    )
    try:
        result = subprocess.run(["osascript", "-e", apple_script],
                                capture_output=True, text=True, timeout=300)
        out = (result.stdout or "").strip()
        if "Confirmer" in out:
            return proposal
        if "Modifier" in out:
            # Ouvre popup texte pour correction libre
            return prompt_user_correction(proposal)
        return None  # Annuler
    except Exception as e:
        log(f"Popup validation echoue ({e}) — on garde la proposition telle quelle.")
        return proposal


def prompt_user_correction(proposal):
    """Popup pour modifier la description manuellement.
    L'utilisateur peut redecrire les speakers, ces infos remplaceront le mapping."""
    current = format_proposal_for_dialog(proposal).replace('"', "''").replace("\n", " | ")
    current = current[:200]  # limite affichage

    apple_script = (
        'tell application "System Events" to activate\n'
        'set userDialog to display dialog '
        '"Decris la composition correcte des speakers." & return & return & '
        f'"Detection actuelle : {current}" & return & return & '
        '"Tape la version corrigee (ex: 1 homme principal + 2 femmes) :" '
        'default answer "" '
        'with title "Corriger la composition" '
        'buttons {"Annuler", "Valider"} default button "Valider"\n'
        'if button returned of userDialog is "Annuler" then\n'
        '  return ""\n'
        'else\n'
        '  return text returned of userDialog\n'
        'end if'
    )
    try:
        result = subprocess.run(["osascript", "-e", apple_script],
                                capture_output=True, text=True, timeout=300)
        user_text = (result.stdout or "").strip()
        if not user_text:
            return None
        # On garde la proposition mais on note la correction user
        proposal["user_override"] = user_text
        log(f"Correction user : {user_text}")
        return proposal
    except Exception as e:
        log(f"Popup correction echoue ({e}).")
        return proposal


# ============== PASSE 1 enrichie : avec frames si multi_visual ==============
def extract_context_with_vision(entries, captures, user_description="", per_subtitle=False):
    """Passe 1 + images. Si per_subtitle=True, chaque image correspond a un
    sous-titre precis (annotee avec son idx), Claude peut faire le mapping direct."""
    # Limite pour eviter erreur 413 (request too large)
    MAX_IMAGES_VISION = 30
    if len(captures) > MAX_IMAGES_VISION:
        step = len(captures) / MAX_IMAGES_VISION
        captures = [captures[int(i * step)] for i in range(MAX_IMAGES_VISION)]
        log(f"  (Limitation : {MAX_IMAGES_VISION} images max pour eviter erreur 413)")
    if per_subtitle:
        log(f"Passe 1/2 : analyse vision avec {len(captures)} images (1 par sous-titre)...")
    else:
        log(f"Passe 1/2 : analyse vision avec {len(captures)} images reparties...")

    content = []
    if user_description:
        content.append({
            "type": "text",
            "text": (
                f"INFO FOURNIE PAR L'UTILISATEUR (priorite haute) :\n"
                f"« {user_description} »\n\n"
                "Utilise cette description pour identifier les speakers ci-dessous. "
                "Si elle contredit ce que tu vois sur les frames, fais confiance a l'utilisateur."
            ),
        })

    for i, cap in enumerate(captures):
        try:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": encode_image_b64(cap["path"]),
                },
            })
            if per_subtitle and "subtitle_idx" in cap:
                content.append({
                    "type": "text",
                    "text": f"^ Image du sous-titre #{cap['subtitle_idx']} @ {cap['timecode']}",
                })
            else:
                content.append({
                    "type": "text",
                    "text": f"^ Frame #{i+1} @ {cap['timecode']}",
                })
        except Exception as e:
            log(f"  Image {i+1} non encodee : {e}")

    all_texts = "\n".join(f"[{e['idx']}] {e['text']}" for e in entries)
    content.append({
        "type": "text",
        "text": (
            f"Voici {len(captures)} frame(s) et les {len(entries)} sous-titres FR.\n\n"
            "IMPORTANT — particularites du montage video :\n"
            "  - L'image au timecode d'un sous-titre ne montre PAS forcement le speaker\n"
            "    en train de parler. Le monteur utilise frequemment :\n"
            "    * B-ROLL : illustrations (paysages, mains qui travaillent, etc.)\n"
            "      pendant qu'une voix off parle\n"
            "    * J-CUT  : l'audio du speaker suivant commence avant que l'image change\n"
            "    * L-CUT  : l'audio du speaker continue alors que l'image change deja\n"
            "  Donc : utilise les images comme INDICES, pas comme verite absolue.\n"
            "  ORDRE DE PRIORITE pour identifier qui parle :\n"
            "    1) La description fournie par l'utilisateur (en haut)\n"
            "    2) Le CONTENU du texte (style, ton, pronoms, vocabulaire personnel)\n"
            "    3) L'image (utile pour confirmer / lever ambiguites)\n\n"
            "Identifie chaque INTERVENANT et son genre. Pour chaque sous-titre, "
            "deduis quel intervenant parle.\n\n"
            f"Sous-titres :\n{all_texts}\n\n"
            "Reponse JSON STRICT, aucun texte autour :\n"
            "{\n"
            '  "summary":"...","topic":"...","tone":"...",\n'
            '  "speakers":[{"name":"...","gender":"masculin|feminin","description":"..."}],\n'
            '  "subtitle_speakers":{"1":"nom_speaker","2":"nom_speaker",...},\n'
            '  "characters":["nom1"],"glossary":["terme1"],\n'
            '  "narrator_name":"speaker principal","narrator_gender":"masculin|feminin|mixte",\n'
            '  "gender_clues":"resume des indices"\n'
            "}"
        ),
    })

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=4000,
            temperature=0,
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            log(f"  Contexte vision non parsable : {raw[:200]}")
            return None
        context = json.loads(m.group(0))
        # Normalisation list-of-dict -> list-of-str
        for key in ("characters", "glossary"):
            val = context.get(key, [])
            if val and isinstance(val[0], dict):
                context[key] = [
                    str(d.get("term") or d.get("name") or next(iter(d.values()), ""))
                    for d in val if d
                ]
            elif not isinstance(val, list):
                context[key] = []
        speakers = context.get("speakers", [])
        log(f"  Intervenants detectes : {len(speakers)}")
        for sp in speakers:
            log(f"    - {sp.get('name', '?')} ({sp.get('gender', '?')}) : {sp.get('description', '')}")
        return context
    except Exception as e:
        log(f"  Extraction vision echouee : {e}")
        return None


# ============== Popup macOS : frequence de capture des frames ==============
def ask_frames_frequency_dialog():
    """Dropdown pour choisir le rythme de capture des frames video."""
    options = [
        "Une frame par sous-titre (PRECISION MAX, recommande Reels rapides)",
        "Auto - adapte a la duree (recommande general)",
        "Toutes les 15 secondes (precis, video courte)",
        "Toutes les 30 secondes (equilibre)",
        "Toutes les 60 secondes (videos longues)",
        "Toutes les 120 secondes (videos tres longues)",
        "Toutes les 5 minutes (long format)",
    ]
    options_str = ", ".join(f'"{o}"' for o in options)
    apple_script = (
        'tell application "System Events" to activate\n'
        'set userChoice to choose from list '
        f'{{{options_str}}} '
        'with title "Frequence des captures video" '
        'with prompt "A quel rythme capturer des frames de ta timeline ? '
        '(Plus frequent = plus precis mais plus lent / cher)" '
        f'default items {{"{options[0]}"}} '
        'OK button name "Suivant" '
        'cancel button name "Annuler"\n'
        'if userChoice is false then\n'
        '  return "cancel"\n'
        'else\n'
        '  return userChoice as string\n'
        'end if'
    )
    try:
        result = subprocess.run(["osascript", "-e", apple_script],
                                capture_output=True, text=True, timeout=300)
        out = (result.stdout or "").strip()
        if "cancel" in out or not out:
            return None
        if "par sous-titre" in out: return "per_subtitle"
        if "Auto" in out: return "auto"
        if "15 secondes" in out: return "15s"
        if "30 secondes" in out: return "30s"
        if "60 secondes" in out: return "60s"
        if "120 secondes" in out: return "120s"
        if "5 minutes" in out: return "300s"
    except Exception as e:
        log(f"Popup frequence echoue ({e}) — fallback auto.")
    return "auto"


# ============== Popups macOS : qui parle dans la video ==============
def _as_text(s):
    """Convertit un texte Python (multi-lignes) en expression AppleScript."""
    return " & return & ".join(
        '"' + line.replace("\\", "\\\\").replace('"', '\\"') + '"'
        for line in str(s).split("\n")
    )


def _run_dialog(apple_script, what):
    """Lance un popup osascript. Retourne la reponse, ou None si le popup plante."""
    try:
        result = subprocess.run(["osascript", "-e", apple_script],
                                capture_output=True, text=True, timeout=300)
        return (result.stdout or "").strip()
    except Exception as e:
        log(f"Popup {what} echoue ({e}).")
        return None


def gender_word(gender):
    return "femme" if gender == "feminin" else "homme"


def ask_people_count_dialog():
    """Dropdown : nb de personnes distinctes qui parlent. None si annule."""
    options = [f"{n} personnes" for n in range(2, MAX_DECLARED_SPEAKERS + 1)]
    options_str = ", ".join(f'"{o}"' for o in options)
    apple_script = (
        'tell application "System Events" to activate\n'
        f'set userChoice to choose from list {{{options_str}}} '
        'with title "Nombre de personnes" '
        'with prompt "Combien de personnes différentes parlent dans cette vidéo ?" '
        f'default items {{"{options[0]}"}} '
        'OK button name "Suivant" '
        'cancel button name "Annuler"\n'
        'if userChoice is false then\n'
        '  return "cancel"\n'
        'else\n'
        '  return userChoice as string\n'
        'end if'
    )
    out = _run_dialog(apple_script, "nombre de personnes")
    if not out or "cancel" in out:
        return None
    m = re.match(r"(\d+)", out)
    return int(m.group(1)) if m else None


def ask_person_gender_dialog(num, total):
    """Popup a boutons : 'masculin' / 'feminin', ou None si annule."""
    apple_script = (
        'tell application "System Events" to activate\n'
        f'set userDialog to display dialog "Personne {num} sur {total} : un homme ou une femme ?" '
        '& return & return & "(Si possible dans l\'ordre où elles parlent.)" '
        f'with title "Qui parle ? ({num}/{total})" '
        'buttons {"Annuler", "Une femme", "Un homme"} '
        'default button "Un homme"\n'
        'return button returned of userDialog'
    )
    out = _run_dialog(apple_script, "homme/femme")
    if not out or "Annuler" in out:
        return None
    return "feminin" if "femme" in out else "masculin"


def ask_people_details_dialog(summary):
    """Champ texte facultatif : prenoms, roles, qui parle en premier."""
    prompt = (
        f"{summary}\n\n"
        "Précisions (facultatif) : prénoms, rôles, qui parle en premier.\n"
        "Ex : Personne 1 = Néto, il pose les questions. Personne 2 = Marie, elle répond."
    )
    apple_script = (
        'tell application "System Events" to activate\n'
        f'set userDialog to display dialog {_as_text(prompt)} '
        'default answer "" '
        'with title "Précisions sur les personnes (facultatif)" '
        'buttons {"Passer", "Valider"} '
        'default button "Valider"\n'
        'if button returned of userDialog is "Passer" then\n'
        '  return ""\n'
        'else\n'
        '  return text returned of userDialog\n'
        'end if'
    )
    return _run_dialog(apple_script, "precisions") or ""


def describe_declared(declared):
    """Resume texte de la composition declaree par l'utilisateur."""
    people = declared.get("people") or []
    text = f"{len(people)} personnes : " + ", ".join(
        f"{p['label']} = {gender_word(p['gender'])}" for p in people
    )
    if declared.get("details"):
        text += f". Precisions : {declared['details']}"
    return text


def ask_declared_speakers():
    """Popups : combien de personnes + homme/femme pour chacune + precisions.
    Retourne {"people": [{"label", "gender"}], "details": str} ou None si annule."""
    total = ask_people_count_dialog()
    if total is None:
        return None
    people = []
    for num in range(1, total + 1):
        gender = ask_person_gender_dialog(num, total)
        if gender is None:
            return None
        people.append({"label": f"Personne {num}", "gender": gender})
    declared = {"people": people, "details": ""}
    declared["details"] = ask_people_details_dialog(describe_declared(declared)).strip()
    return declared


# ============== Mode multi_auto : qui dit quoi, d'apres le texte ==============
def attribute_speakers_from_text(entries, declared, correction=""):
    """Claude attribue chaque sous-titre a une des personnes declarees.
    Les genres viennent de l'utilisateur et ne sont jamais remis en cause.
    Retourne (people, subtitle_map, ctx) ou None si echec."""
    labels = [p["label"] for p in declared["people"]]
    compo = "\n".join(f"- {p['label']} : {gender_word(p['gender'])}" for p in declared["people"])
    hints = ""
    if declared.get("details"):
        hints += f"\nPrecisions de l'utilisateur : « {declared['details']} »"
    if correction:
        hints += (f"\nCORRECTION de l'utilisateur sur ta proposition precedente "
                  f"(PRIORITAIRE) : « {correction} »")
    all_texts = "\n".join(f"[{e['idx']}] {e['text']}" for e in entries)
    user_msg = (
        f"Dans cette video, {len(labels)} personnes parlent. Composition donnee par "
        "l'utilisateur (FIABLE, ne la remets pas en cause) :\n"
        f"{compo}{hints}\n"
        "La numerotation suit a priori l'ordre d'apparition, mais c'est indicatif.\n\n"
        f"Sous-titres ({len(entries)} lignes) :\n{all_texts}\n\n"
        "Pour chaque sous-titre, determine QUI le prononce. Indices a utiliser :\n"
        "- tours de parole : question / reponse, relances, remerciements, changement de sujet\n"
        "- presentations ('je suis X', 'je m'appelle'), prenoms cites quand on s'adresse a quelqu'un\n"
        "- role et style (intervieweur vs invite, voix off vs temoin), precisions de l'utilisateur\n"
        "ATTENTION : NE TE FIE PAS aux accords au masculin/feminin a la 1re personne "
        "('je suis venu' / 'je suis venue') pour deviner qui parle : ces sous-titres "
        "n'ont pas encore ete corriges, les fautes d'accord sont justement ce qu'on cherche.\n"
        "Les sous-titres consecutifs d'une meme phrase ont le meme locuteur. "
        "Si tu ne peux vraiment pas trancher pour un passage, mets \"?\".\n\n"
        "JSON STRICT, aucun texte autour :\n"
        "{\n"
        '  "people":[{"label":"Personne 1","name":"prenom si clairement identifie, sinon vide",'
        '"role":"ex : intervieweur, invitee, voix off (max 40 caracteres)"}],\n'
        '  "segments":[{"from":1,"to":8,"speaker":"Personne 1"},'
        '{"from":9,"to":12,"speaker":"Personne 2"}],\n'
        '  "summary":"resume 1 phrase","topic":"sujet 3-5 mots","tone":"...",\n'
        '  "glossary":["marques, lieux, noms propres a ne pas modifier"]\n'
        "}\n"
        f"Les segments couvrent tous les idx de 1 a {len(entries)}, dans l'ordre, sans trou. "
        f"speaker = un de ces labels exacts : {', '.join(labels)}, ou \"?\"."
    )
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=8000, temperature=0,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            log(f"  Reponse non-JSON : {raw[:200]}")
            return None
        data = json.loads(m.group(0))
    except Exception as e:
        log(f"  Attribution echouee : {e}")
        return None

    infos = {str(p.get("label", "")).strip(): p for p in (data.get("people") or [])
             if isinstance(p, dict)}
    people = []
    for p in declared["people"]:
        info = infos.get(p["label"], {})
        prenom = str(info.get("name") or "").strip()
        people.append({
            "label": p["label"],
            "name": f"{p['label']} ({prenom})" if prenom else p["label"],
            "gender": p["gender"],  # genre = celui declare par l'utilisateur
            "description": str(info.get("role") or "").strip()[:50],
            "prenom": prenom,
        })
    name_by_label = {p["label"]: p["name"] for p in people}
    valid_idx = {e["idx"] for e in entries}
    subtitle_map = {}
    for seg in data.get("segments") or []:
        speaker = str(seg.get("speaker", "")).strip()
        if speaker.isdigit():
            speaker = f"Personne {speaker}"
        name = name_by_label.get(speaker)
        if not name:
            continue
        try:
            start, end = int(seg.get("from")), int(seg.get("to"))
        except (TypeError, ValueError):
            continue
        for i in range(start, end + 1):
            if str(i) in valid_idx:
                subtitle_map[str(i)] = name

    gl = data.get("glossary") or []
    if not isinstance(gl, list):
        gl = []
    ctx = {
        "summary": data.get("summary", ""),
        "topic": data.get("topic", ""),
        "tone": data.get("tone", ""),
        "glossary": [str(g.get("term") or next(iter(g.values()), "")) if isinstance(g, dict) else str(g)
                     for g in gl if g],
    }
    return people, subtitle_map, ctx


def idx_ranges(idxs, max_len=70):
    """[1,2,3,9,10] -> '1-3, 9-10' (tronque si trop long pour le popup)."""
    nums = sorted(int(i) for i in idxs)
    parts = []
    for n in nums:
        if parts and n == parts[-1][1] + 1:
            parts[-1][1] = n
        else:
            parts.append([n, n])
    text = ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in parts)
    return text if len(text) <= max_len else text[:max_len].rsplit(",", 1)[0] + ", ..."


def validate_attribution_dialog(people, subtitle_map, entries):
    """Montre qui dit quoi. Retourne "ok", None (annule) ou le texte de correction."""
    lines = ["Voici qui parle, d'après Claude :", ""]
    for p in people:
        said = [e for e in entries if subtitle_map.get(e["idx"]) == p["name"]]
        role = f", {p['description']}" if p.get("description") else ""
        lines.append(f"{p['name']} : {gender_word(p['gender'])}{role}, {len(said)} sous-titre(s)")
        if said:
            lines.append(f"     n° {idx_ranges(e['idx'] for e in said)}")
            first = said[0]["text"].replace("\n", " ")
            lines.append(f"     ex. #{said[0]['idx']} « {first[:60]} »")
    missing = sum(1 for e in entries if e["idx"] not in subtitle_map)
    if missing:
        lines += ["", f"Non attribués : {missing} (leurs accords au « je » ne seront pas touchés)"]
    apple_script = (
        'tell application "System Events" to activate\n'
        f'set userChoice to display dialog {_as_text(chr(10).join(lines))} '
        'with title "Vérifie qui parle" '
        'buttons {"Annuler", "Corriger", "Confirmer"} '
        'default button "Confirmer"\n'
        'return button returned of userChoice'
    )
    out = _run_dialog(apple_script, "validation des personnes")
    if out is None or "Confirmer" in out:
        return "ok"
    if "Corriger" not in out:
        return None
    apple_script = (
        'tell application "System Events" to activate\n'
        'set userDialog to display dialog "Qu\'est-ce qui ne va pas ?" & return & return & '
        '"Ex : la femme parle aussi du sous-titre 20 au 35 / Personne 2 = Marie" '
        'default answer "" '
        'with title "Corriger qui parle" '
        'buttons {"Annuler", "Valider"} default button "Valider"\n'
        'if button returned of userDialog is "Annuler" then\n'
        '  return "cancel"\n'
        'else\n'
        '  return text returned of userDialog\n'
        'end if'
    )
    out = _run_dialog(apple_script, "correction des personnes")
    if not out or out == "cancel":
        return None
    return out


def build_multi_context(people, subtitle_map, ctx, declared_text, user_override=""):
    """Contexte global (meme format que multi_visual, relu par Translate)."""
    counts = {p["name"]: sum(1 for v in subtitle_map.values() if v == p["name"]) for p in people}
    main = max(people, key=lambda p: counts[p["name"]])
    return {
        "topic": ctx.get("topic", ""),
        "summary": ctx.get("summary", ""),
        "tone": ctx.get("tone", ""),
        "characters": [p["prenom"] for p in people if p.get("prenom")],
        "glossary": ctx.get("glossary") or [],
        "narrator_name": main["name"],
        "narrator_gender": main["gender"],
        "gender_clues": declared_text,
        "speakers": [{"name": p["name"], "gender": p["gender"], "description": p["description"]}
                     for p in people],
        "user_description": declared_text,
        "user_override": user_override,
    }


def run_text_attribution(declared):
    """Attribution texte + validation par l'utilisateur (3 essais max).
    Retourne (global_context, subtitle_speakers_map)."""
    declared_text = describe_declared(declared)
    correction = ""
    for _attempt in range(3):
        log("Attribution des sous-titres aux personnes (d'apres le texte)...")
        res = attribute_speakers_from_text(entries, declared, correction)
        if not res:
            break
        people, smap, ctx = res
        for p in people:
            n = sum(1 for v in smap.values() if v == p["name"])
            log(f"  {p['name']} ({p['gender']}) : {n} sous-titre(s)")
        answer = validate_attribution_dialog(people, smap, entries)
        if answer is None:
            fail("Annule par l'utilisateur (validation des personnes).")
        if answer == "ok":
            return build_multi_context(people, smap, ctx, declared_text, correction), smap
        correction = f"{correction} / {answer}" if correction else answer
        log(f"Correction user : {answer} -> nouvelle attribution")

    # Pas d'attribution fiable : on garde les personnes declarees, sans mapping
    log("Pas d'attribution validee -> correction sans mapping (accords au 'je' non forces).")
    ctx = extract_global_context(entries) or {}
    people = [{"label": p["label"], "name": p["label"], "gender": p["gender"],
               "description": "", "prenom": ""} for p in declared["people"]]
    return build_multi_context(people, {}, ctx, declared_text, correction), {}


# ============== Popup macOS pour choisir le mode ==============
def ask_speaker_mode_dialog():
    """Popup : qui parle dans la video.
    Retourne 'single_m' / 'single_f' / 'multi_auto' / 'multi_visual', ou None si annule."""
    opt_man = "1 personne : un homme"
    opt_woman = "1 personne : une femme"
    opt_auto = "Plusieurs personnes : je dis combien et qui (rapide)"
    opt_visual = "Plusieurs personnes + capture vidéo (plus précis, plus lent)"
    apple_script = (
        'tell application "System Events" to activate\n'
        'set userChoice to choose from list '
        f'{{"{opt_man}", "{opt_woman}", "{opt_auto}", "{opt_visual}"}} '
        'with title "Vérification des sous-titres FR" '
        'with prompt "Qui parle dans cette vidéo ? (pour les accords : je suis venu / venue)" '
        f'default items {{"{opt_man}"}} '
        'OK button name "Suivant" '
        'cancel button name "Annuler"\n'
        'if userChoice is false then\n'
        '  return "cancel"\n'
        'else\n'
        '  return userChoice as string\n'
        'end if'
    )
    out = _run_dialog(apple_script, "choix du mode")
    if out is None:
        log("Popup choix mode en echec -> 1 personne par defaut.")
        return "single_f" if SINGLE_NARRATOR_GENDER == "feminin" else "single_m"
    if not out or "cancel" in out:
        return None
    if "capture" in out:
        return "multi_visual"
    if "je dis combien" in out:
        return "multi_auto"
    if "une femme" in out:
        return "single_f"
    return "single_m"


# ============== Dispatcher selon SPEAKER_MODE ==============
# Resolution du mode si "ask" -> popup
effective_mode = SPEAKER_MODE
single_gender = SINGLE_NARRATOR_GENDER
if effective_mode == "ask":
    log("Choix : qui parle dans la video (popup macOS)...")
    chosen = ask_speaker_mode_dialog()
    if chosen is None:
        fail("Annule par l'utilisateur. Aucune correction effectuee.")
    if chosen in ("single_m", "single_f"):
        effective_mode = "single"
        single_gender = "feminin" if chosen == "single_f" else "masculin"
    else:
        effective_mode = chosen
    log(f"Mode selectionne : {effective_mode}")

global_context = None
subtitle_speakers_map = {}

if effective_mode == "single":
    if single_gender == SINGLE_NARRATOR_GENDER and SINGLE_NARRATOR_NAME:
        single_name = SINGLE_NARRATOR_NAME
    else:
        single_name = "la narratrice" if single_gender == "feminin" else "le narrateur"
    log(f"Mode SPEAKER : single ({single_name}, {single_gender})")
    global_context = {
        "topic": "",
        "summary": "",
        "tone": "",
        "characters": [single_name] if single_name == SINGLE_NARRATOR_NAME else [],
        "glossary": [],
        "narrator_name": single_name,
        "narrator_gender": single_gender,
        "gender_clues": f"une seule personne parle : {gender_word(single_gender)}",
    }
elif effective_mode == "multi_visual":
    log("Mode SPEAKER : multi_visual (capture frames + vision Claude)")

    # Popup 2 : Frequence de capture (dropdown)
    log("Choix de la frequence de capture...")
    freq_choice = ask_frames_frequency_dialog()
    if freq_choice is None:
        fail("Annule par l'utilisateur.")
    frames_config = freq_choice
    log(f"Frequence capture : {frames_config}")

    # Popups 3 : combien de personnes + homme/femme + precisions
    log("Qui parle ? (nombre de personnes + homme/femme)")
    declared = ask_declared_speakers()
    if declared is None:
        fail("Annule par l'utilisateur.")
    final_description = describe_declared(declared)
    log(f"Composition : {final_description}")

    if frames_config == "per_subtitle":
        captures = capture_one_frame_per_subtitle(entries)
    else:
        captures = capture_timeline_frames(frames_config)

    if captures:
        # PASSE 0 : Claude analyse + propose
        proposal = analyze_speakers_with_vision(
            entries, captures,
            f"COMPOSITION CERTAINE (genres exacts, a respecter) : {final_description}")
        if proposal:
            # POPUP validation : user confirme/modifie/annule
            log("Affichage popup validation des speakers...")
            validated = validate_speakers_dialog(proposal)
            if validated is None:
                fail("Annule par l'utilisateur (validation des speakers).")

            # Construit le contexte global a partir de la proposition validee
            narrator = validated.get("narrator") or {}
            others = validated.get("other_speakers") or []
            # Les genres declares par l'utilisateur priment sur ceux vus a l'image
            proposed_genders = sorted(
                [narrator.get("gender", "masculin")]
                + [sp.get("gender", "masculin") for sp in others]
            )
            declared_genders = sorted(p["gender"] for p in declared["people"])
            if proposed_genders != declared_genders:
                log(f"Vision : composition {proposed_genders} differente de celle "
                    f"declaree {declared_genders} -> attribution d'apres le texte")
            else:
                global_context = {
                    "topic": validated.get("topic", ""),
                    "summary": validated.get("summary", ""),
                    "tone": validated.get("tone", ""),
                    "characters": [narrator.get("name")] if narrator.get("name") else [],
                    "glossary": validated.get("glossary") or [],
                    "narrator_name": narrator.get("name", "inconnu"),
                    "narrator_gender": narrator.get("gender", "masculin"),
                    "gender_clues": (
                        f"narrateur principal {narrator.get('gender', '?')} + "
                        f"{len(others)} autre(s) intervenant(s)"
                    ),
                    "speakers": ([{
                        "name": narrator.get("name") or "narrator",
                        "gender": narrator.get("gender", "masculin"),
                        "description": narrator.get("description", ""),
                    }] + [
                        {"name": sp.get("label", "?"),
                         "gender": sp.get("gender", "masculin"),
                         "description": sp.get("description", "")}
                        for sp in others
                    ]),
                    "user_description": final_description,
                    "user_override": validated.get("user_override", ""),
                }
                # Mapping sous-titre -> nom speaker
                raw_map = validated.get("subtitle_speakers") or {}
                # Normalise "narrator" -> nom reel
                narrator_label = narrator.get("name") or "narrator"
                subtitle_speakers_map = {
                    str(k): (narrator_label if v == "narrator" else v)
                    for k, v in raw_map.items()
                }
                log(f"Proposition validee : narrateur '{narrator_label}' "
                    f"({narrator.get('gender')}) + {len(others)} speaker(s)")

    if not global_context:
        log("Fallback : sans vision -> attribution d'apres le texte")
        global_context, subtitle_speakers_map = run_text_attribution(declared)
else:
    # multi_auto : l'utilisateur dit combien de personnes et qui est homme/femme,
    # Claude attribue chaque sous-titre d'apres le texte, l'utilisateur valide
    log("Mode SPEAKER : multi_auto (composition donnee par l'utilisateur, texte seul)")
    declared = ask_declared_speakers()
    if declared is None:
        fail("Annule par l'utilisateur.")
    log(f"Composition : {describe_declared(declared)}")
    global_context, subtitle_speakers_map = run_text_attribution(declared)

global_context = global_context or {}

# Default masculin si genre du narrateur non determine (regle français du masculin generique)
if not global_context.get("narrator_gender") or global_context.get("narrator_gender") in ("inconnu", "mixte", "unknown", "?"):
    log("narrator_gender non determine -> defaut MASCULIN (regle francaise du masculin generique)")
    global_context["narrator_gender"] = "masculin"


# ============== PASSE 2 : correction batch par batch avec contexte ==============
def build_system_prompt(context):
    base = (
        "Tu es un correcteur EXIGEANT de sous-titres en francais. "
        "Ton role : trouver TOUTES les fautes, MEME mineures.\n\n"
        "PRINCIPE FONDAMENTAL — LES SOUS-TITRES SONT DES MORCEAUX D'UNE PHRASE :\n"
        "Un sous-titre dure 1-3s pour faciliter la lecture. Une vraie phrase parlee "
        "s'etale souvent sur 2-5 sous-titres consecutifs. Avant de juger un accord, "
        "RECONSTITUE MENTALEMENT la phrase complete en lisant les sous-titres adjacents.\n\n"
        "Exemple :\n"
        "  [3] 'Le programme doit aussi'\n"
        "  [4] 'etre financee par l'Europe'\n"
        "  -> phrase reconstituee : 'Le programme doit aussi etre financee par l'Europe'\n"
        "  -> sujet 'Le programme' masculin singulier -> 'financee' = FAUTE -> 'finance'\n\n"
        "Tu DOIS verifier chaque participe passe en remontant au sujet/COD meme s'il "
        "se trouve dans un sous-titre PRECEDENT. Utilise le contexte fourni.\n\n"
        "TU CHERCHES ACTIVEMENT :\n"
        "1. Orthographe : lettres manquantes/en trop ('comance' -> 'commence'), "
        "homophones (a/à, ou/où, ce/se, ces/ses, c'est/s'est)\n"
        "2. Accords : pluriel oublie ('les enfant'), genre incorrect "
        "('un belle'), participe passe mal accorde\n"
        "3. Conjugaison : verbes mal conjugues ('il sait pas' VS 'il c'est'), "
        "infinitif vs participe (-er VS -e)\n"
        "4. Ponctuation : virgule manquante (apres un complement, entre propositions), "
        "absence de point final, espace avant ! ? : ; (espace insecable FR)\n"
        "5. Typographie : majuscule en debut de phrase, apostrophes droites VS courbes\n"
        "6. Mots oublies, doubles ('le le', 'que que')\n"
        "7. Cedille manquante (commenca -> commenca avec cedille), "
        "accents oublies (a -> à, ou -> où, etc.)\n\n"
        "EXAMINE CHAQUE MOT, CHAQUE PONCTUATION. Une faute mineure reste une faute.\n\n"
    )
    if context:
        chars = ", ".join(context.get("characters", []) or [])
        gloss = ", ".join(context.get("glossary", []) or [])
        narrator = context.get("narrator_name", "inconnu")
        narrator_gender = context.get("narrator_gender", "inconnu")
        speakers = context.get("speakers") or []
        multi = len(speakers) > 1
        base += "CONTEXTE GLOBAL de la video (lu deja entierement) :\n"
        base += f"- Sujet         : {context.get('topic', '?')}\n"
        base += f"- Resume        : {context.get('summary', '?')}\n"
        base += f"- Ton           : {context.get('tone', '?')}\n"
        if multi:
            base += f"- Intervenants  : {len(speakers)} personnes parlent (liste ci-dessous)\n"
        else:
            base += f"- Narrateur     : {narrator} (genre : {narrator_gender})\n"
        base += f"- Autres genres : {context.get('gender_clues', '?')}\n"
        if context.get("user_description"):
            base += f"- Precisions de l'utilisateur : {context['user_description']}\n"
        if context.get("user_override"):
            base += f"- Correction de l'utilisateur (PRIORITAIRE) : {context['user_override']}\n"
        if chars:
            base += f"- Personnages cites : {chars}\n"
        if gloss:
            base += f"- A NE PAS modifier (glossaire) : {gloss}\n"
        if multi:
            people = "\n".join(
                f"    - {sp.get('name', '?')} : {sp.get('gender', '?')}"
                + (f" ({sp['description']})" if sp.get("description") else "")
                for sp in speakers
            )
            je_rule = (
                "  PLUSIEURS PERSONNES PARLENT dans cette video :\n"
                f"{people}\n"
                "  Quand TU VOIS 'je / me / moi / m'a' dans un sous-titre, l'accord du "
                "participe passe suit le genre de LA PERSONNE QUI PARLE DANS CE SOUS-TITRE "
                "(indiquee dans le message, rubrique 'Qui parle'), PAS celui d'un narrateur "
                "principal.\n"
                "  Si un sous-titre n'a PAS de personne indiquee, NE CHANGE PAS le genre "
                "d'un accord a la 1re personne (laisse-le tel qu'ecrit) : il peut etre "
                "prononce par une personne de l'autre genre. Toutes les autres fautes "
                "se corrigent normalement.\n\n"
            )
        else:
            je_rule = (
                f"  Le NARRATEUR principal est {narrator_gender}. Quand TU VOIS 'je / me / moi / "
                "m'a' dans un sous-titre, l'accord du participe passe doit suivre CE genre.\n\n"
            )
        base += (
            "\nACCORDS — TRES IMPORTANT (souvent rate par les correcteurs auto) :\n\n"
            + je_rule +
            "  EXEMPLES CONCRETS A APPLIQUER SANS HESITER :\n"
            f"  Si la personne qui dit 'je' est un HOMME :\n"
            "    'je suis venue'    -> FAUTE -> 'je suis venu'\n"
            "    'je me suis trompee' -> FAUTE -> 'je me suis trompe'\n"
            "    'on m'a appelee'   -> FAUTE -> 'on m'a appele'\n"
            "    'je suis arrivee'  -> FAUTE -> 'je suis arrive'\n"
            "    'je me suis fait avoir' -> CORRECT (pas d'accord avec 'fait + infinitif')\n"
            "  Si la personne qui dit 'je' est une FEMME :\n"
            "    'je suis venu'     -> FAUTE -> 'je suis venue'\n"
            "    'je me suis trompe' -> FAUTE -> 'je me suis trompee'\n\n"
            "  REGLES GENERALES :\n"
            "  - Apres 'etre' (passe compose pronominal inclus) : accord avec le SUJET\n"
            "    'Elle est venue', 'Ils sont venus', 'Elles sont parties'\n"
            "  - Apres 'avoir' : accord avec le COD si place AVANT le verbe\n"
            "    'la fleur qu'elle a cueillie' (cod fem sing 'la fleur' avant)\n"
            "    'elle a cueilli la fleur' (cod apres = pas d'accord)\n"
            "  - Sujet pluriel masculin : 'Les projets ont ete finances' (S)\n"
            "  - Sujet feminin singulier : 'L'initiative a ete financee' (E)\n"
            "  - Sujet feminin pluriel : 'Les initiatives ont ete financees' (ES)\n"
            "  - Sujet masculin singulier : 'Le projet a ete finance' (sans E)\n\n"
            "  NE LAISSE PASSER AUCUNE FAUTE D'ACCORD DU PARTICIPE PASSE.\n\n"
        )
    base += (
        "CE QU'IL NE FAUT *PAS* MODIFIER :\n"
        "- Ne reformule PAS le sens. Corrige seulement.\n"
        "- Preserve le ton/registre (familier, oral, argot, contractions style 'jsuis')\n"
        "- Preserve la LONGUEUR approximative (lecture rapide)\n"
        "- Conserve les sauts de ligne (un \\n = un \\n)\n"
        "- Ne corrige PAS les noms propres, marques, hashtags, URLs, glossaire\n\n"
        "PRINCIPE FINAL : si tu hesites entre corriger ou laisser, CORRIGE. "
        "C'est mieux de proposer une correction discutable que de manquer une vraie faute. "
        "L'utilisateur validera. Si VRAIMENT rien a corriger, renvoie le texte identique.\n"
        "(Seule exception : le genre des accords a la 1re personne quand plusieurs "
        "personnes parlent, voir la regle plus haut.)\n\n"
        "FORMAT DE SORTIE (JSON strict, aucun texte autour) :\n"
        '{ "corrections": [ { "idx": "1", "text": "..." } ] }\n'
        "- idx doit etre identique a celui fourni\n"
        "- Retourne TOUS les idx, meme ceux sans changement"
    )
    return base


SYSTEM_PROMPT = build_system_prompt(global_context)


def correct_batch(batch, prev_overlap):
    user_parts = []

    # Si plusieurs personnes : injecte qui parle pour chaque sous-titre
    # (contexte precedent inclus, pour les phrases a cheval sur deux batches)
    speakers = (global_context.get("speakers") or []) if global_context else []
    if subtitle_speakers_map and speakers:
        speakers_by_name = {sp.get("name"): sp for sp in speakers if sp.get("name")}
        lines = []
        for e in list(prev_overlap) + list(batch):
            sp_name = subtitle_speakers_map.get(e["idx"])
            if sp_name and sp_name in speakers_by_name:
                sp = speakers_by_name[sp_name]
                lines.append(f"  [{e['idx']}] parle par {sp_name} ({sp.get('gender', '?')})")
        if lines:
            user_parts.append(
                "Qui parle, par sous-titre (pour les accords du participe passe) :\n"
                + "\n".join(lines)
            )

    if prev_overlap:
        prev_str = "\n".join(f"  [{e['idx']}] {e['text']}" for e in prev_overlap)
        user_parts.append(
            "Contexte des sous-titres precedents (NE PAS retourner dans ta reponse, "
            "uniquement pour les accords) :\n" + prev_str + "\n---"
        )
    payload = [{"idx": e["idx"], "text": e["text"]} for e in batch]
    user_parts.append(
        "Sous-titres a corriger (retourne UNIQUEMENT ces idx) :\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    user_msg = "\n\n".join(user_parts)

    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        temperature=0,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = resp.content[0].text.strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        raise RuntimeError(f"Reponse non-JSON : {raw[:300]}")
    data = json.loads(m.group(0))
    out = {}
    for t in data.get("corrections", []):
        if "idx" in t and "text" in t:
            out[str(t["idx"])] = t["text"]
    return out


OVERLAP_SIZE = 10  # nb de sous-titres precedents inclus en contexte (pour accords cross-sous-titres)

corrections = {}
total_batches = (len(entries) - 1) // BATCH_SIZE + 1
log(f"Passe 2/2 : correction par batches (size {BATCH_SIZE}, overlap {OVERLAP_SIZE})...")
for i in range(0, len(entries), BATCH_SIZE):
    batch = entries[i:i + BATCH_SIZE]
    prev_overlap = entries[max(0, i - OVERLAP_SIZE):i] if i > 0 else []
    n = i // BATCH_SIZE + 1
    log(f"  Batch {n}/{total_batches} ({len(batch)} sous-titres, +{len(prev_overlap)} en contexte)...")
    try:
        corrections.update(correct_batch(batch, prev_overlap))
    except Exception as e:
        log(f"  -> echec : {e}. Batch {n} ignore (les originaux seront conserves).")


# ============== Rapport ==============
import unicodedata


def normalize_for_compare(s):
    """Normalise un texte pour comparaison robuste (ignore les differences
    typographiques invisibles : apostrophes courbes vs droites, espaces
    insecables, tirets, normalisation Unicode NFC)."""
    s = unicodedata.normalize("NFC", s)
    s = s.replace("’", "'")  # apostrophe courbe -> droite
    s = s.replace("‘", "'")
    s = s.replace(" ", " ")  # espace insecable -> espace
    s = s.replace(" ", " ")  # espace fine insecable
    s = s.replace("–", "-")  # tiret demi-cadratin
    s = s.replace("—", "-")  # tiret cadratin
    s = re.sub(r"\s+", " ", s)  # normalise espaces multiples
    return s.strip()


fautes = []
for e in entries:
    suggested = corrections.get(e["idx"], e["text"])
    if normalize_for_compare(suggested) != normalize_for_compare(e["text"]):
        fautes.append({
            **e,
            "suggested": suggested,
        })

log("")
log("=" * 70)
log(f"RAPPORT — {len(fautes)} faute(s) sur {len(entries)} sous-titre(s)")
log("=" * 70)

if not fautes:
    log("Tous tes sous-titres sont propres. Rien a corriger.")
else:
    for f in fautes:
        log("")
        log(f"#{f['idx']}  [{f['timecode']}]")
        log(f"  AVANT  : {f['text']}")
        log(f"  APRES  : {f['suggested']}")
    log("")
    log("=" * 70)
    log(f"TOTAL : {len(fautes)} correction(s) suggeree(s) sur {len(entries)} sous-titre(s)")
    log("=" * 70)


# ============== Sauvegarde du rapport pour ApplyCorrectionsFR ==============
report = {
    "scan_timestamp": int(time.time()),
    "scan_date": time.strftime("%Y-%m-%d %H:%M:%S"),
    "timeline_name": timeline.GetName(),
    "track_index": chosen_track,
    "fps": fps,
    "total_subtitles": len(entries),
    "total_fautes": len(fautes),
    "fautes": fautes,
    # Pour reutilisation par TranslateSubtitlesFR_EN : evite de refaire la passe 0 vision
    "global_context": global_context,
    "subtitle_speakers_map": subtitle_speakers_map,
}

try:
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log("")
    log(f"Rapport JSON sauvegarde : {REPORT_PATH}")
    if fautes:
        log("Pour appliquer ces corrections : Workspace > Scripts > ApplyCorrectionsFR")
        log("Sinon ferme cette console, rien n'a ete modifie sur ta timeline.")
except Exception as e:
    log(f"Sauvegarde du rapport echouee : {e}")

# Notification finale macOS
if fautes:
    notify(
        "Scan termine",
        f"{len(fautes)} faute(s) detectee(s) sur {len(entries)} sous-titres. "
        f"Lance ApplyCorrectionsFR pour appliquer."
    )
else:
    notify(
        "Scan termine",
        f"Aucune faute detectee sur {len(entries)} sous-titres. Tes sous-titres sont propres !"
    )
