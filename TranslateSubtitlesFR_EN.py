#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TranslateSubtitlesFR_EN.py — Primo-Studio
Traduit la piste de sous-titres FR de la timeline active en EN via Claude API,
puis ajoute le SRT traduit sur une nouvelle piste de sous-titres.

Utilisation dans Resolve :
  Workspace -> Scripts -> Edit -> TranslateSubtitlesFR_EN

Prerequis :
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
import tempfile
import base64
import subprocess

# ============== Config ==============
MODEL = "claude-sonnet-4-6"
BATCH_SIZE = 150
SOURCE_LANG = "francais"
TARGET_LANG = "anglais"

# ────────── SELECTEUR DE MODE SPEAKER ──────────
# "single" / "multi_auto" / "multi_visual" (voir ScanSubtitlesFR.py pour details)
# Par defaut "ask" : popup macOS au lancement pour choisir.
# Mets une valeur fixe ("single"/"multi_auto"/"multi_visual") pour skip le popup.
SPEAKER_MODE = "ask"

# Si "single" :
SINGLE_NARRATOR_NAME = "Néto"
SINGLE_NARRATOR_GENDER = "masculin"

# Si "multi_visual" : frequence de capture (voir Scan pour formats supportes)
# "auto" / "30s" / "60s" / "120s" / nombre entier
MULTI_VISUAL_FRAMES = "auto"

# ============== Helpers ==============
def log(msg):
    print(f"[TranslateSRT] {msg}")
    try:
        sys.stdout.flush()
    except Exception:
        # fu_stdout (console Resolve) n'a pas de flush() — ignorer
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
        notify("Traduction sous-titres - ERREUR", msg[:120])
    except Exception:
        pass
    sys.exit(code)


def open_finder_at_path(path):
    """Ouvre le Finder avec le fichier selectionne."""
    try:
        subprocess.run(["open", "-R", path], capture_output=True, timeout=5)
    except Exception:
        pass


def show_translation_drag_drop_dialog(en_path, bilingual_path, n_subs):
    """Popup macOS pour le drag-drop manuel.
    2 fichiers proposes : EN seul OU FR+EN empile (bilingue)."""
    msg_lines = [
        f"2 fichiers SRT prets sur ton Bureau ({n_subs} sous-titres) :",
        "",
        "• _EN.srt           = sous-titres anglais uniquement",
        "• _FR_EN_bilingue.srt = FR sur ligne 1 + EN sur ligne 2",
        "",
        "Le Finder est ouvert sur le bilingue (recommande).",
        "",
        "Pour creer la piste subtitle :",
        "1. Drag-drop le fichier voulu sur la zone vide de ta timeline",
        "2. Resolve cree une nouvelle piste subtitle aux bons timecodes",
        "",
        "Resolve n'affiche qu'une piste sub a la fois -> le BILINGUE",
        "te permet de voir les 2 langues simultanement."
    ]
    msg = " & return & ".join(f'"{l}"' for l in msg_lines)
    apple_script = (
        'tell application "System Events" to activate\n'
        f'display dialog ({msg}) '
        'with title "Traduction prete - drag-drop manuel" '
        'buttons {"OK"} default button "OK"'
    )
    try:
        subprocess.run(["osascript", "-e", apple_script],
                       capture_output=True, text=True, timeout=600)
    except Exception:
        pass


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

# Ouvre la Console + notification de demarrage
open_resolve_console()
notify("Traduction FR vers EN", "Demarrage... Suis le progres dans la Console Resolve.")

project = resolve.GetProjectManager().GetCurrentProject()
if not project:
    fail("Aucun projet ouvert.")

timeline = project.GetCurrentTimeline()
if not timeline:
    fail("Aucune timeline active.")

log(f"Timeline active : {timeline.GetName()}")

# ============== Export SRT FR ==============
tmp_dir = tempfile.mkdtemp(prefix="resolve_trad_")
srt_fr_path = os.path.join(tmp_dir, "subtitles_fr.srt")
srt_en_path = os.path.join(tmp_dir, "subtitles_en.srt")

log(f"Export SRT FR -> {srt_fr_path}")

# Selon la doc, EXPORT_SRT n'est pas dans timeline.Export.
# On tente quand meme puis fallback sur construction manuelle depuis TimelineItems.
def frames_to_srt_tc(frames, fps):
    total_ms = int(round(frames * 1000.0 / fps))
    hh = total_ms // 3_600_000
    mm = (total_ms % 3_600_000) // 60_000
    ss = (total_ms % 60_000) // 1000
    ms = total_ms % 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def ask_track_to_translate_dialog(non_empty_tracks):
    """Popup pour choisir quelle piste subtitle traduire quand il y en a plusieurs.
    non_empty_tracks : liste de (track_index, items). Retourne track_index ou None."""
    # Construit les options : "Piste #X - Y sous-titres"
    options = [f"Piste subtitle #{t} - {len(it)} sous-titres" for t, it in non_empty_tracks]
    # Par defaut : la piste avec le PLUS d'items
    default_option = max(options, key=lambda s: int(s.split(" - ")[1].split(" ")[0]))
    options_str = ", ".join(f'"{o}"' for o in options)
    apple_script = (
        'tell application "System Events" to activate\n'
        f'set userChoice to choose from list {{{options_str}}} '
        'with title "Plusieurs pistes subtitle detectees" '
        'with prompt "Choisis la piste a traduire (en general la plus complete) :" '
        f'default items {{"{default_option}"}} '
        'OK button name "Traduire" '
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
        # Extract track index "Piste subtitle #X - Y sous-titres"
        match = re.search(r"#(\d+)", out)
        if match:
            return int(match.group(1))
    except Exception as e:
        log(f"Popup choix piste echoue ({e}). Default : plus complete.")
    # Fallback : la plus complete
    return max(non_empty_tracks, key=lambda x: len(x[1]))[0]


def build_srt_from_timeline(timeline):
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
    fps = float(fps_str) if fps_str else 25.0
    log(f"Frame rate timeline : {fps}")

    sub_track_count = timeline.GetTrackCount("subtitle") or 0
    if sub_track_count == 0:
        return None, "Aucune piste de sous-titres trouvee sur la timeline."

    # Liste les pistes subtitle non vides.
    items = None
    chosen_track = None
    non_empty = []
    for t in range(1, sub_track_count + 1):
        it = timeline.GetItemListInTrack("subtitle", t) or []
        if it:
            non_empty.append((t, it))

    if non_empty:
        if len(non_empty) == 1:
            chosen_track, items = non_empty[0]
            log(f"Une seule piste subtitle non vide : #{chosen_track} ({len(items)} items).")
        else:
            # PLUSIEURS pistes non vides -> popup pour choisir
            summary = ", ".join(f"#{t}={len(it)}" for t, it in non_empty)
            log(f"Pistes subtitle non vides : {summary}")
            chosen_track = ask_track_to_translate_dialog(non_empty)
            if chosen_track is None:
                return None, "Annule par l'utilisateur (choix de piste)."
            # Trouve les items de la piste choisie
            for t, it in non_empty:
                if t == chosen_track:
                    items = it
                    break
            log(f"Piste choisie : #{chosen_track} ({len(items)} items)")

    if not items:
        return None, "Toutes les pistes de sous-titres sont vides."

    log(f"Piste sous-titres source : #{chosen_track} ({len(items)} items)")
    tl_start = timeline.GetStartFrame() or 0

    srt_lines = []
    for i, item in enumerate(items, start=1):
        text = item.GetName() or ""
        start_f = item.GetStart() - tl_start
        end_f = item.GetEnd() - tl_start
        srt_lines.append(
            f"{i}\n{frames_to_srt_tc(start_f, fps)} --> {frames_to_srt_tc(end_f, fps)}\n{text}\n"
        )
    return "\n".join(srt_lines), None


export_ok = False
export_method = "?"
try:
    if hasattr(resolve, "EXPORT_SRT"):
        export_ok = timeline.Export(srt_fr_path, resolve.EXPORT_SRT)
        export_method = "timeline.Export(EXPORT_SRT)"
except Exception as e:
    log(f"Export(EXPORT_SRT) leve une exception : {e}")

if not export_ok or not os.path.exists(srt_fr_path) or os.path.getsize(srt_fr_path) == 0:
    log("timeline.Export(SRT) non disponible -> construction manuelle depuis TimelineItems")
    srt_content, err = build_srt_from_timeline(timeline)
    if err:
        fail(err)
    with open(srt_fr_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    export_ok = True
    export_method = "construction manuelle (TimelineItems)"

log(f"Methode d'export : {export_method}")

# ============== Parse SRT ==============
def parse_srt(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()
    blocks = re.split(r"\r?\n\s*\r?\n", content.strip())
    entries = []
    for block in blocks:
        lines = [l for l in block.split("\n") if l.strip() != ""]
        if len(lines) < 3:
            continue
        idx = lines[0].strip()
        timecode = lines[1].strip()
        text = "\n".join(lines[2:]).strip()
        entries.append({"idx": idx, "timecode": timecode, "text": text})
    return entries


entries = parse_srt(srt_fr_path)
if not entries:
    fail("Aucun sous-titre trouve dans le SRT exporte.")

log(f"{len(entries)} sous-titres FR a traduire.")

# ============== Claude API ==============
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
    log("Passe 1/2 : extraction du contexte global de la video...")
    all_texts = "\n".join(f"[{e['idx']}] {e['text']}" for e in entries)

    ctx_system = (
        "Tu es un analyste de scripts video. Tu lis l'integralite des sous-titres "
        "francais et tu en extrais le contexte global pour qu'un traducteur "
        "puisse rendre une version anglaise coherente."
    )
    ctx_user = (
        f"Voici TOUS les sous-titres FR d'une video ({len(entries)} lignes). "
        "Analyse-les en globalite pour informer la traduction EN. Extrais :\n"
        "- summary : resume du contenu en 1-2 phrases\n"
        "- topic : theme principal en 3-5 mots\n"
        "- tone : ton dominant (formel/familier/promotionnel/technique/oral spontane/journalistique)\n"
        "- characters : LISTE DE STRINGS (juste les noms, pas de description) - "
        "prenoms / noms propres de personnes a NE PAS traduire\n"
        "- glossary : LISTE DE STRINGS (juste les termes) - marques, organisations, "
        "acronymes, jargon a NE PAS traduire. "
        "EXCLURE LES TOPONYMES (noms de pays/regions/villes) car ils doivent etre "
        "rendus dans leur forme officielle anglaise (France -> France, "
        "Guyane francaise -> French Guiana, Etats-Unis -> United States, etc.)\n"
        "- gender_clues : STRING - indices sur le genre des sujets (utile pour they/he/she en EN)\n"
        "- audience : STRING - public vise (US generic, UK, international, pro, grand public, etc.) "
        "pour choisir le registre anglais le plus adapte.\n\n"
        "Sous-titres :\n"
        f"{all_texts}\n\n"
        'Reponse en JSON STRICT, aucun texte autour : '
        '{"summary":"...","topic":"...","tone":"...","characters":["nom1"],'
        '"glossary":["terme1"],"gender_clues":"...","audience":"..."}'
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
        log(f"  Audience   : {context.get('audience', '?')}")
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


# ============== Capture frames (mode multi_visual) ==============
def auto_frame_count(duration_sec):
    if duration_sec < 30: return 3
    if duration_sec < 120: return 5
    if duration_sec < 600: return 8
    return 12


def resolve_frame_count(config, duration_sec):
    if config == "auto" or config is None:
        return auto_frame_count(duration_sec)
    if isinstance(config, str) and config.endswith("s"):
        try:
            return max(1, int(duration_sec / int(config[:-1])))
        except ValueError:
            return auto_frame_count(duration_sec)
    try:
        return max(1, int(config))
    except (ValueError, TypeError):
        return auto_frame_count(duration_sec)


def frames_to_timecode_hhmmssff(frames, fps):
    total_sec = frames / fps
    hh = int(total_sec // 3600)
    mm = int((total_sec % 3600) // 60)
    ss = int(total_sec % 60)
    ff = int(frames % int(round(fps)))
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def capture_timeline_frames(n_frames=None):
    start = timeline.GetStartFrame() or 0
    end = timeline.GetEndFrame() or 0
    duration_frames = max(0, end - start)
    if duration_frames <= 0:
        log("Timeline vide — pas de capture.")
        return []
    fps_local = float(timeline.GetSetting("timelineFrameRate") or project.GetSetting("timelineFrameRate") or 25)
    duration_sec = duration_frames / fps_local

    n_frames = resolve_frame_count(n_frames, duration_sec)

    log(f"Capture de {n_frames} frame(s) sur {duration_sec:.1f}s...")
    capture_dir = tempfile.mkdtemp(prefix="resolve_frames_")
    captures = []
    for i in range(n_frames):
        offset = int(duration_frames * (i + 0.5) / n_frames)
        tc_frames = start + offset
        tc_str = frames_to_timecode_hhmmssff(tc_frames - start, fps_local)
        try:
            if not timeline.SetCurrentTimecode(tc_str):
                log(f"  Frame {i+1}: SetCurrentTimecode echoue")
                continue
        except Exception as e:
            log(f"  Frame {i+1}: SetCurrentTimecode exc ({e})")
            continue
        out_path = os.path.join(capture_dir, f"frame_{i+1:02d}.jpg")
        try:
            if project.ExportCurrentFrameAsStill(out_path) and os.path.exists(out_path):
                captures.append({"path": out_path, "timecode": tc_str, "frame": tc_frames})
                log(f"  Frame {i+1}/{n_frames} OK @ {tc_str}")
        except Exception as e:
            log(f"  Frame {i+1}: export exc ({e})")
    return captures


def srt_tc_to_frames(tc_str, fps):
    """Convertit un timecode SRT 'HH:MM:SS,mmm' en nb de frames."""
    try:
        h, m, rest = tc_str.split(":")
        s, ms = rest.split(",")
        total_ms = int(h) * 3_600_000 + int(m) * 60_000 + int(s) * 1000 + int(ms)
        return int(round(total_ms * fps / 1000))
    except Exception:
        return 0


def capture_one_frame_per_subtitle(entries_local):
    if not entries_local:
        return []
    fps_local = float(timeline.GetSetting("timelineFrameRate")
                      or project.GetSetting("timelineFrameRate") or 25)
    tl_start = timeline.GetStartFrame() or 0
    capture_dir = tempfile.mkdtemp(prefix="resolve_per_sub_")
    captures = []
    log(f"Capture par sous-titre : {len(entries_local)} captures prevues...")
    for i, entry in enumerate(entries_local, 1):
        # Translate entries ont 'timecode' (string SRT) au lieu de start/end_frame
        if "start_frame" in entry and "end_frame" in entry:
            mid_frame = (entry["start_frame"] + entry["end_frame"]) // 2
            mid_relative = mid_frame - tl_start
        else:
            # Parse le timecode SRT "00:00:01,000 --> 00:00:03,500"
            tc = entry.get("timecode", "")
            parts = tc.split(" --> ")
            if len(parts) != 2:
                continue
            start_rel = srt_tc_to_frames(parts[0], fps_local)
            end_rel = srt_tc_to_frames(parts[1], fps_local)
            mid_relative = (start_rel + end_rel) // 2

        tc_str = frames_to_timecode_hhmmssff(mid_relative, fps_local)
        try:
            if not timeline.SetCurrentTimecode(tc_str):
                continue
        except Exception:
            continue
        out_path = os.path.join(capture_dir, f"sub_{i:03d}.jpg")
        try:
            if project.ExportCurrentFrameAsStill(out_path) and os.path.exists(out_path):
                captures.append({"path": out_path, "timecode": tc_str, "subtitle_idx": entry["idx"]})
                if i % 5 == 0 or i == len(entries_local):
                    log(f"  Capture {i}/{len(entries_local)}...")
        except Exception:
            continue
    log(f"Captures par sous-titre : {len(captures)}/{len(entries_local)} reussies")
    return captures


def resize_image_for_vision(path, max_size=512):
    try:
        resized_path = path + ".resized.jpg"
        result = subprocess.run(
            ["/usr/bin/sips", "-Z", str(max_size),
             "-s", "format", "jpeg", "-s", "formatOptions", "70",
             path, "--out", resized_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and os.path.exists(resized_path):
            return resized_path
    except Exception as e:
        log(f"  Resize echoue ({e}).")
    return path


def encode_image_b64(path, resize=True):
    if resize:
        path = resize_image_for_vision(path)
    with open(path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def analyze_speakers_with_vision(entries, captures, user_hint=""):
    """Passe 0 : pre-analyse speakers via vision."""
    MAX_SAMPLES = 20
    if len(captures) > MAX_SAMPLES:
        step = len(captures) / MAX_SAMPLES
        sampled = [captures[int(i * step)] for i in range(MAX_SAMPLES)]
        log(f"Passe 0/2 : echantillonnage {MAX_SAMPLES}/{len(captures)} images...")
    else:
        sampled = captures
        log(f"Passe 0/2 : analyse pre-validation ({len(sampled)} images)...")
    content = []
    if user_hint:
        content.append({"type": "text", "text": (
            f"INDICATION USER (priorite haute) : « {user_hint} »"
        )})
    for i, cap in enumerate(sampled):
        try:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": encode_image_b64(cap["path"])}})
            label = (f"Image du sous-titre #{cap['subtitle_idx']} @ {cap['timecode']}"
                     if "subtitle_idx" in cap else f"Frame #{i+1} @ {cap['timecode']}")
            content.append({"type": "text", "text": f"^ {label}"})
        except Exception as e:
            log(f"  Image {i+1} non encodee : {e}")
    all_texts = "\n".join(f"[{e['idx']}] {e['text']}" for e in entries)
    content.append({"type": "text", "text": (
        "PATTERN FREQUENT : 1 narrateur/narratrice principal + intervenants secondaires.\n"
        "ATTENTION montage : B-roll/J-cut/L-cut, image != forcement speaker.\n"
        f"Sous-titres :\n{all_texts}\n\n"
        "JSON STRICT, aucun texte autour :\n"
        '{"narrator":{"name":"...","gender":"masculin|feminin","description":"..."},\n'
        ' "other_speakers":[{"label":"Speaker 2","gender":"...","description":"..."}],\n'
        ' "subtitle_speakers":{"1":"narrator","2":"Speaker 2"},\n'
        ' "summary":"...","topic":"...","tone":"...","audience":"...",\n'
        ' "glossary":["..."]}\n'
        "Si genre ambigu, narrator_gender = 'masculin' par defaut."
    )})
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=4000, temperature=0,
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        proposal = json.loads(m.group(0))
        gl = proposal.get("glossary", [])
        if gl and isinstance(gl[0], dict):
            proposal["glossary"] = [str(d.get("term") or d.get("name") or next(iter(d.values()),"")) for d in gl if d]
        return proposal
    except Exception as e:
        log(f"  Passe 0 echouee : {e}")
        return None


def format_proposal_for_dialog(proposal):
    narrator = proposal.get("narrator") or {}
    others = proposal.get("other_speakers") or []
    lines = [f"NARRATEUR PRINCIPAL : {narrator.get('name', '?')} ({narrator.get('gender', '?')})"]
    if narrator.get('description'):
        lines.append(f"  {narrator.get('description')[:80]}")
    for sp in others:
        lines.append(f"{sp.get('label', '?')} : {sp.get('gender', '?')}")
        if sp.get('description'):
            lines.append(f"  {sp.get('description')[:80]}")
    return "\n".join(lines)


def validate_speakers_dialog(proposal):
    summary = format_proposal_for_dialog(proposal).replace('"', "''")
    lines = summary.split("\n")
    apple_lines = ' & return & '.join(f'"{l}"' for l in lines)
    apple_script = (
        'tell application "System Events" to activate\n'
        f'set userChoice to display dialog ({apple_lines}) '
        'with title "Claude a detecte les speakers suivants" '
        'buttons {"Annuler", "Modifier", "Confirmer"} '
        'default button "Confirmer"\n'
        'return button returned of userChoice'
    )
    try:
        result = subprocess.run(["osascript","-e",apple_script],
                                capture_output=True, text=True, timeout=300)
        out = (result.stdout or "").strip()
        if "Confirmer" in out: return proposal
        if "Modifier" in out: return prompt_user_correction(proposal)
        return None
    except Exception as e:
        log(f"Popup validation echoue ({e}).")
        return proposal


def prompt_user_correction(proposal):
    current = format_proposal_for_dialog(proposal).replace('"', "''").replace("\n", " | ")[:200]
    apple_script = (
        'tell application "System Events" to activate\n'
        'set userDialog to display dialog '
        '"Decris la composition correcte des speakers." & return & return & '
        f'"Detection : {current}" & return & return & '
        '"Tape la version corrigee :" '
        'default answer "" '
        'with title "Corriger la composition" '
        'buttons {"Annuler", "Valider"} default button "Valider"\n'
        'if button returned of userDialog is "Annuler" then\n  return ""\nelse\n  return text returned of userDialog\nend if'
    )
    try:
        result = subprocess.run(["osascript","-e",apple_script],
                                capture_output=True, text=True, timeout=300)
        user_text = (result.stdout or "").strip()
        if not user_text: return None
        proposal["user_override"] = user_text
        log(f"Correction user : {user_text}")
        return proposal
    except Exception as e:
        log(f"Popup correction echoue ({e}).")
        return proposal


def extract_context_with_vision(entries, captures, user_description="", per_subtitle=False):
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
        content.append({"type": "text", "text": (
            f"INFO FOURNIE PAR L'UTILISATEUR (priorite haute) :\n« {user_description} »\n\n"
            "Utilise cette description pour identifier les speakers."
        )})
    for i, cap in enumerate(captures):
        try:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": encode_image_b64(cap["path"])},
            })
            if per_subtitle and "subtitle_idx" in cap:
                content.append({"type": "text", "text": f"^ Image du sous-titre #{cap['subtitle_idx']} @ {cap['timecode']}"})
            else:
                content.append({"type": "text", "text": f"^ Frame #{i+1} @ {cap['timecode']}"})
        except Exception as e:
            log(f"  Image {i+1} non encodee : {e}")

    all_texts = "\n".join(f"[{e['idx']}] {e['text']}" for e in entries)
    content.append({"type": "text", "text": (
        f"Voici {len(captures)} frame(s) reparties et les {len(entries)} sous-titres FR.\n\n"
        "Identifie chaque INTERVENANT visible et son genre. Pour chaque sous-titre, "
        "deduis qui parle (frames + contenu texte).\n\n"
        f"Sous-titres :\n{all_texts}\n\n"
        "Reponse JSON STRICT, aucun texte autour :\n"
        "{\n"
        '  "summary":"...","topic":"...","tone":"...",\n'
        '  "speakers":[{"name":"...","gender":"masculin|feminin","description":"..."}],\n'
        '  "subtitle_speakers":{"1":"nom_speaker"},\n'
        '  "characters":["nom1"],"glossary":["terme1"],\n'
        '  "narrator_name":"speaker principal","narrator_gender":"masculin|feminin|mixte",\n'
        '  "gender_clues":"...","audience":"..."\n'
        "}"
    )})

    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=4000, temperature=0,
            messages=[{"role": "user", "content": content}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            log(f"  Contexte vision non parsable : {raw[:200]}")
            return None
        context = json.loads(m.group(0))
        for key in ("characters", "glossary"):
            val = context.get(key, [])
            if val and isinstance(val[0], dict):
                context[key] = [str(d.get("term") or d.get("name") or next(iter(d.values()), "")) for d in val if d]
            elif not isinstance(val, list):
                context[key] = []
        speakers = context.get("speakers", [])
        log(f"  Intervenants detectes : {len(speakers)}")
        for sp in speakers:
            log(f"    - {sp.get('name', '?')} ({sp.get('gender', '?')})")
        return context
    except Exception as e:
        log(f"  Extraction vision echouee : {e}")
        return None


# ============== Popup macOS : frequence de capture ==============
def ask_frames_frequency_dialog():
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
        'with prompt "A quel rythme capturer des frames de ta timeline ?" '
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
        result = subprocess.run(["osascript","-e",apple_script],
                                capture_output=True, text=True, timeout=300)
        out = (result.stdout or "").strip()
        if "cancel" in out or not out: return None
        if "par sous-titre" in out: return "per_subtitle"
        if "Auto" in out: return "auto"
        if "15 secondes" in out: return "15s"
        if "30 secondes" in out: return "30s"
        if "60 secondes" in out: return "60s"
        if "120 secondes" in out: return "120s"
        if "5 minutes" in out: return "300s"
    except Exception as e:
        log(f"Popup frequence echoue ({e}).")
    return "auto"


# ============== Popup macOS : nombre de speakers ==============
def ask_speakers_count_dialog():
    options = [
        "1 speaker (toi seul)", "2 speakers", "3 speakers", "4 speakers",
        "5 speakers", "6 speakers ou plus", "Inconnu (laisser Claude deviner)",
    ]
    options_str = ", ".join(f'"{o}"' for o in options)
    apple_script = (
        'tell application "System Events" to activate\n'
        f'set userChoice to choose from list {{{options_str}}} '
        'with title "Nombre de speakers" '
        'with prompt "Combien de personnes distinctes parlent dans cette video ?" '
        f'default items {{"{options[1]}"}} '
        'OK button name "Suivant" cancel button name "Annuler"\n'
        'if userChoice is false then\n  return "cancel"\nelse\n  return userChoice as string\nend if'
    )
    try:
        result = subprocess.run(["osascript","-e",apple_script],
                                capture_output=True, text=True, timeout=300)
        out = (result.stdout or "").strip()
        if "cancel" in out or not out: return None
        if "1 speaker" in out: return "1"
        if "2 speakers" in out: return "2"
        if "3 speakers" in out: return "3"
        if "4 speakers" in out: return "4"
        if "5 speakers" in out: return "5"
        if "6 speakers" in out: return "6+"
        return "inconnu"
    except Exception as e:
        log(f"Popup nb speakers echoue ({e}).")
        return "inconnu"


# ============== Popup macOS : description speakers ==============
def ask_speakers_description_dialog(default_prefix=""):
    prompt_text = (
        '"Speakers dans l\'ordre d\'apparition" & return & return & '
        '"Ex : 1 homme puis 1 femme puis 1 homme puis 2 femmes" & return & '
        '"Ex : moi seul / 3 hommes / 2 femmes mixtes"'
    )
    default_str = default_prefix.replace('"', '\\"')
    apple_script = (
        'tell application "System Events" to activate\n'
        f'set userDialog to display dialog {prompt_text} '
        f'default answer "{default_str}" '
        'with title "Ordre des speakers (optionnel)" '
        'buttons {"Passer", "Valider"} default button "Valider"\n'
        'if button returned of userDialog is "Passer" then\n  return ""\nelse\n  return text returned of userDialog\nend if'
    )
    try:
        result = subprocess.run(["osascript", "-e", apple_script],
                                capture_output=True, text=True, timeout=300)
        return (result.stdout or "").strip()
    except Exception as e:
        log(f"Popup description echoue ({e}).")
        return ""


# ============== Popup macOS pour choisir le mode ==============
def ask_speaker_mode_dialog():
    opt_single = "1 seul speaker (Single) - rapide, gratuit"
    opt_auto = "Plusieurs speakers - Multi Auto (gratuit, peu fiable)"
    opt_visual = "Plusieurs speakers - Multi Visual (capture video, le plus precis)"
    apple_script = (
        'tell application "System Events" to activate\n'
        'set userChoice to choose from list '
        f'{{"{opt_single}", "{opt_auto}", "{opt_visual}"}} '
        'with title "Mode de traduction FR vers EN" '
        'with prompt "Combien de speakers dans cette video ?" '
        f'default items {{"{opt_single}"}} '
        'OK button name "Lancer la traduction" '
        'cancel button name "Annuler"\n'
        'if userChoice is false then\n'
        '  return "cancel"\n'
        'else\n'
        '  return userChoice as string\n'
        'end if'
    )
    try:
        result = subprocess.run(["osascript", "-e", apple_script],
                                capture_output=True, text=True, timeout=120)
        out = (result.stdout or "").strip()
        if "cancel" in out or not out: return None
        if "Single" in out: return "single"
        if "Multi Auto" in out: return "multi_auto"
        if "Multi Visual" in out: return "multi_visual"
    except Exception as e:
        log(f"Popup mode echoue ({e}) — fallback 'single'.")
    return "single"


# ============== Tentative de reutilisation du rapport Scan ==============
SCAN_REPORT_PATH = os.path.expanduser("~/Desktop/scan_subtitles_fr_report.json")
MAX_REPORT_AGE_SECONDS = 24 * 3600
reused_context = None
reused_speakers_map = {}

if os.path.exists(SCAN_REPORT_PATH):
    try:
        with open(SCAN_REPORT_PATH, "r", encoding="utf-8") as fp:
            scan_report = json.load(fp)
        age = int(time.time()) - scan_report.get("scan_timestamp", 0)
        scan_tl = scan_report.get("timeline_name")
        current_tl = timeline.GetName()
        if age <= MAX_REPORT_AGE_SECONDS and scan_tl == current_tl:
            ctx = scan_report.get("global_context") or {}
            if ctx and ctx.get("narrator_name"):
                reused_context = ctx
                reused_speakers_map = scan_report.get("subtitle_speakers_map", {}) or {}
                log(f"Rapport Scan trouve ({scan_report.get('scan_date')}) pour cette timeline.")
                log("Reutilisation du contexte (speakers, glossary, narrateur) -> skip popups.")
        else:
            if scan_tl != current_tl:
                log(f"Rapport Scan ignore : timeline differente ('{scan_tl}' vs '{current_tl}').")
            elif age > MAX_REPORT_AGE_SECONDS:
                log(f"Rapport Scan ignore : trop ancien ({age // 3600}h).")
    except Exception as e:
        log(f"Lecture rapport Scan echouee ({e}). Flow complet.")


# Si on a un contexte reuse, on skip toutes les popups (mode, freq, nb, desc)
if reused_context:
    effective_mode = "single"  # mode synthetique : contexte preconstrui
    global_context = reused_context
    subtitle_speakers_map = reused_speakers_map
else:
    effective_mode = SPEAKER_MODE
    if effective_mode == "ask":
        log("Choix du mode speaker (popup macOS)...")
        chosen = ask_speaker_mode_dialog()
        if chosen is None:
            fail("Annule par l'utilisateur.")
        effective_mode = chosen
        log(f"Mode selectionne : {effective_mode}")


# ============== Dispatcher SPEAKER_MODE ==============
if reused_context:
    # Tout est deja en place via le rapport Scan. On skip le dispatcher.
    log(f"Contexte reutilise : narrateur '{global_context.get('narrator_name')}' "
        f"({global_context.get('narrator_gender')})")
    if subtitle_speakers_map:
        log(f"Mapping sous-titre -> speaker : {len(subtitle_speakers_map)} entries")
else:
    global_context = None
    subtitle_speakers_map = {}

# Si on a deja le contexte via le rapport Scan, on skip TOUT le dispatcher
_skip_dispatcher = bool(reused_context)

if _skip_dispatcher:
    pass  # contexte deja en place via reused_context
elif effective_mode == "single":
    log(f"Mode SPEAKER : single ({SINGLE_NARRATOR_NAME}, {SINGLE_NARRATOR_GENDER})")
    global_context = {
        "topic": "", "summary": "", "tone": "",
        "characters": [SINGLE_NARRATOR_NAME] if SINGLE_NARRATOR_NAME else [],
        "glossary": [],
        "narrator_name": SINGLE_NARRATOR_NAME,
        "narrator_gender": SINGLE_NARRATOR_GENDER,
        "gender_clues": f"narrateur unique : {SINGLE_NARRATOR_GENDER}",
        "audience": "international",
    }
elif effective_mode == "multi_visual":
    log("Mode SPEAKER : multi_visual")
    freq_choice = ask_frames_frequency_dialog()
    if freq_choice is None:
        fail("Annule par l'utilisateur.")
    frames_config = freq_choice
    log(f"Frequence capture : {frames_config}")

    speakers_count = ask_speakers_count_dialog()
    if speakers_count is None:
        fail("Annule par l'utilisateur.")
    log(f"Nombre de speakers : {speakers_count}")

    prefix = f"{speakers_count} speakers : " if speakers_count not in ("inconnu", "1") else ""
    user_description = ask_speakers_description_dialog(default_prefix=prefix)
    if user_description:
        log(f"Description user : {user_description}")

    final_description_parts = []
    if speakers_count and speakers_count != "inconnu":
        final_description_parts.append(f"Nombre total de personnes : {speakers_count}")
    if user_description:
        final_description_parts.append(f"Ordre/details : {user_description}")
    final_description = ". ".join(final_description_parts)

    if frames_config == "per_subtitle":
        captures = capture_one_frame_per_subtitle(entries)
    else:
        captures = capture_timeline_frames(frames_config)

    if captures:
        proposal = analyze_speakers_with_vision(entries, captures, final_description)
        if proposal:
            log("Affichage popup validation des speakers...")
            validated = validate_speakers_dialog(proposal)
            if validated is None:
                fail("Annule par l'utilisateur (validation).")

            narrator = validated.get("narrator") or {}
            others = validated.get("other_speakers") or []
            global_context = {
                "topic": validated.get("topic", ""),
                "summary": validated.get("summary", ""),
                "tone": validated.get("tone", ""),
                "audience": validated.get("audience", "international"),
                "characters": [narrator.get("name")] if narrator.get("name") else [],
                "glossary": validated.get("glossary") or [],
                "narrator_name": narrator.get("name", "inconnu"),
                "narrator_gender": narrator.get("gender", "masculin"),
                "gender_clues": (
                    f"narrateur principal {narrator.get('gender', '?')} + "
                    f"{len(others)} autre(s)"
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
            }
            narrator_label = narrator.get("name") or "narrator"
            raw_map = validated.get("subtitle_speakers") or {}
            subtitle_speakers_map = {
                str(k): (narrator_label if v == "narrator" else v)
                for k, v in raw_map.items()
            }

    if not global_context:
        log("Fallback : extraction sans vision")
        global_context = extract_global_context(entries)
else:
    log("Mode SPEAKER : multi_auto (Claude devine d'apres le texte)")
    global_context = extract_global_context(entries)

global_context = global_context or {}

# Default masculin si genre du narrateur non determine (regle francaise du masculin generique)
if not global_context.get("narrator_gender") or global_context.get("narrator_gender") in ("inconnu", "mixte", "unknown", "?"):
    log("narrator_gender non determine -> defaut MASCULIN (regle francaise du masculin generique)")
    global_context["narrator_gender"] = "masculin"


# ============== PASSE 2 : traduction batch par batch avec contexte ==============
def build_system_prompt(context):
    base = (
        "Tu es un traducteur professionnel specialise en sous-titrage video. "
        f"Tu traduis du {SOURCE_LANG} vers l'{TARGET_LANG}.\n\n"
    )
    if context:
        chars = ", ".join(context.get("characters", []) or [])
        gloss = ", ".join(context.get("glossary", []) or [])
        base += "CONTEXTE GLOBAL de la video (lu deja entierement) :\n"
        base += f"- Sujet              : {context.get('topic', '?')}\n"
        base += f"- Resume             : {context.get('summary', '?')}\n"
        base += f"- Ton a preserver    : {context.get('tone', '?')}\n"
        base += f"- Audience EN cible  : {context.get('audience', 'international')}\n"
        base += f"- Indices genre/nb   : {context.get('gender_clues', '?')}\n"
        if chars:
            base += f"- Personnages (a NE PAS traduire) : {chars}\n"
        if gloss:
            base += f"- Glossaire (a NE PAS traduire)   : {gloss}\n"
        base += (
            "\nUtilise ce contexte pour la coherence des termes, les pronoms "
            "(he/she/they) selon le genre des sujets, et le registre anglais.\n\n"
        )
    base += (
        "Regles strictes :\n"
        "- Traduction naturelle, idiomatique, adaptee a l'oral d'une video (pas litterale)\n"
        "- Preserve le ton et le registre (familier, soutenu, technique, etc.)\n"
        "- Conserve exactement les sauts de ligne du texte source (un \\n = un \\n)\n"
        "- Conserve la ponctuation expressive (!, ?, ...) si presente\n\n"
        "QUOI TRADUIRE / NE PAS TRADUIRE :\n"
        "  GARDE en francais (NE TRADUIS PAS) :\n"
        "    - Noms propres de PERSONNES : Néto, Marie, Stephan Cornet\n"
        "    - Marques specifiques : Primo Studio, Apple, L'Agora\n"
        "    - Acronymes locaux non-internationaux : CTG, CNES, FEADER, INSEE\n"
        "    - Items du glossaire fourni dans le contexte\n\n"
        "  TRADUIS en anglais :\n"
        "    - Toponymes (forme officielle anglaise) :\n"
        "      Guyane francaise -> French Guiana\n"
        "      France -> France | Bretagne -> Brittany\n"
        "      Etats-Unis -> United States | Allemagne -> Germany\n"
        "    - Acronymes internationaux standards :\n"
        "      VIH -> HIV | OTAN -> NATO | UE -> EU | ONU -> UN\n"
        "      OMS -> WHO | UNESCO -> UNESCO (deja EN)\n"
        "      SIDA -> AIDS | ADN -> DNA | OGM -> GMO\n"
        "    - Metiers et titres courants :\n"
        "      sage-femme -> midwife | medecin -> doctor\n"
        "      porteur/porteuse de projet -> project leader\n"
        "      professeur -> teacher | infirmier -> nurse\n"
        "    - Institutions locales avec composantes traduisibles :\n"
        "      Hopital de Cayenne -> Cayenne Hospital\n"
        "      Mairie de Paris -> Paris City Hall\n"
        "      Universite de la Sorbonne -> Sorbonne University\n"
        "      collectivite territoriale de Guyane -> Territorial Authority of French Guiana\n"
        "    - Termes generiques institutionnels :\n"
        "      fonds europeens -> European funds\n"
        "      ministere de la sante -> Ministry of Health\n"
        "      conseil regional -> Regional Council\n"
        "    - Termes MEDICAUX et SCIENTIFIQUES courants :\n"
        "      hepatite -> hepatitis | hepatites B, C, D -> hepatitis B, C, D\n"
        "      prevalence -> prevalence | virus -> virus\n"
        "      cancer -> cancer | diabete -> diabetes\n"
        "      vaccin -> vaccine | maladie -> disease\n"
        "      tuberculose -> tuberculosis | grippe -> flu\n"
        "      epidemie -> epidemic | pandemie -> pandemic\n"
        "      symptome -> symptom | depistage -> screening\n\n"
        "PRINCIPE : si un terme a une traduction anglaise EVIDENTE et STANDARD, "
        "traduis-le. Garde seulement les noms propres unilingues (marques, prenoms, "
        "acronymes specifiques sans equivalent EN).\n\n"
        "- Sortie OBLIGATOIRE en JSON strict : "
        '{ "translations": [ { "idx": "1", "text": "..." }, ... ] }\n'
        "- Le champ idx doit correspondre EXACTEMENT a celui fourni en entree\n"
        "- Aucun texte hors du JSON, pas de markdown, pas de commentaire"
    )
    return base


SYSTEM_PROMPT = build_system_prompt(global_context)


def translate_batch(batch, prev_overlap):
    user_parts = []

    # Mode multi_visual : injecte qui parle pour chaque sous-titre du batch
    speakers = (global_context.get("speakers") or []) if global_context else []
    if subtitle_speakers_map and speakers:
        speakers_by_name = {sp.get("name"): sp for sp in speakers if sp.get("name")}
        lines = []
        for e in batch:
            sp_name = subtitle_speakers_map.get(e["idx"])
            if sp_name and sp_name in speakers_by_name:
                sp = speakers_by_name[sp_name]
                lines.append(f"  [{e['idx']}] parle par {sp_name} ({sp.get('gender', '?')})")
        if lines:
            user_parts.append(
                "Mapping speakers par sous-titre (pour pronoms he/she/they) :\n"
                + "\n".join(lines)
            )

    if prev_overlap:
        prev_str = "\n".join(
            f"  [{e['idx']}] FR: {e['text']}" for e in prev_overlap
        )
        user_parts.append(
            "Contexte des sous-titres FR precedents (NE PAS retourner dans ta reponse, "
            "uniquement pour la coherence des accords/pronoms/termes) :\n"
            + prev_str + "\n---"
        )
    payload = [{"idx": e["idx"], "text": e["text"]} for e in batch]
    user_parts.append(
        "Sous-titres a traduire (retourne UNIQUEMENT ces idx) :\n"
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
    for t in data.get("translations", []):
        if "idx" in t and "text" in t:
            out[str(t["idx"])] = t["text"]
    return out


OVERLAP_SIZE = 5  # nb de sous-titres FR precedents inclus en contexte

translations = {}
total_batches = (len(entries) - 1) // BATCH_SIZE + 1
log(f"Passe 2/2 : traduction par batches (size {BATCH_SIZE}, overlap {OVERLAP_SIZE})...")
for i in range(0, len(entries), BATCH_SIZE):
    batch = entries[i:i + BATCH_SIZE]
    prev_overlap = entries[max(0, i - OVERLAP_SIZE):i] if i > 0 else []
    n = i // BATCH_SIZE + 1
    log(f"  Batch {n}/{total_batches} ({len(batch)} sous-titres, +{len(prev_overlap)} en contexte)...")
    try:
        translations.update(translate_batch(batch, prev_overlap))
    except Exception as e:
        log(f"  -> echec : {e}. On garde le FR pour ce batch.")
        for entry in batch:
            translations[entry["idx"]] = entry["text"]

# ============== Ecriture SRT EN seul + bilingue FR+EN ==============
with open(srt_en_path, "w", encoding="utf-8") as f:
    for e in entries:
        en_text = translations.get(e["idx"], e["text"])
        f.write(f"{e['idx']}\n{e['timecode']}\n{en_text}\n\n")
log(f"SRT EN ecrit : {srt_en_path}")

# SRT bilingue : FR sur ligne 1, EN sur ligne 2 par sous-titre.
# Affichable sur une seule piste subtitle Resolve.
srt_bilingual_path = os.path.join(tmp_dir, "subtitles_bilingual.srt")
with open(srt_bilingual_path, "w", encoding="utf-8") as f:
    for e in entries:
        fr_text = e["text"]
        en_text = translations.get(e["idx"], "")
        # Si pas de traduction (echec), on n'ajoute pas la 2e ligne
        if en_text and en_text.strip() != fr_text.strip():
            combined = f"{fr_text}\n{en_text}"
        else:
            combined = fr_text
        f.write(f"{e['idx']}\n{e['timecode']}\n{combined}\n\n")
log(f"SRT BILINGUE FR+EN ecrit : {srt_bilingual_path}")

# Copies sur le Bureau (2 fichiers)
desktop = os.path.expanduser("~/Desktop")
tl_name = re.sub(r"[^\w\-]+", "_", timeline.GetName())
backup_path = os.path.join(desktop, f"{tl_name}_EN.srt")
bilingual_backup_path = os.path.join(desktop, f"{tl_name}_FR_EN_bilingue.srt")
try:
    import shutil
    shutil.copy(srt_en_path, backup_path)
    shutil.copy(srt_bilingual_path, bilingual_backup_path)
    log(f"Backup Bureau (EN seul)     : {backup_path}")
    log(f"Backup Bureau (FR+EN empile): {bilingual_backup_path}")
except Exception as e:
    log(f"Copie Bureau echouee : {e}")

# ============== Snapshot des pistes subtitle AVANT ==============
def snapshot_subtitle_tracks():
    """Retourne un dict {track_index: set(uniqueIds...)} pour comparaison."""
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


snapshot_before = snapshot_subtitle_tracks()
occupied_before = {t: len(uids) for t, uids in snapshot_before.items() if uids}
log(f"Pistes subtitle existantes : {len(snapshot_before)} | occupees : {sorted(occupied_before.keys())}")

# ============== Creation d'une nouvelle piste subtitle vide ==============
media_pool = project.GetMediaPool()
target_track = None
try:
    if timeline.AddTrack("subtitle"):
        target_track = (timeline.GetTrackCount("subtitle") or 0)
        log(f"Nouvelle piste subtitle creee (piste #{target_track}, vide).")
except Exception as e:
    log(f"AddTrack subtitle echoue ({e}). Tentative d'import quand meme.")

# Import du SRT dans le Media Pool
imported = media_pool.ImportMedia([srt_en_path])
if not imported:
    log("Import auto Media Pool echoue. SRT EN dispo sur ton Bureau.")
    open_finder_at_path(bilingual_backup_path)  # ouvre sur le bilingue (recommande)
    show_translation_drag_drop_dialog(backup_path, bilingual_backup_path, len(entries))
    sys.exit(0)

# Lock toutes les pistes subtitle SAUF la nouvelle cible (force le routing)
total_sub_tracks = timeline.GetTrackCount("subtitle") or 0
locked_tracks = []
for t in range(1, total_sub_tracks + 1):
    if t == target_track:
        continue
    try:
        if timeline.SetTrackLock("subtitle", t, True):
            locked_tracks.append(t)
            log(f"  Lock piste subtitle #{t} (force routing vers piste #{target_track})")
    except Exception:
        pass

# Append SIMPLE (le clipInfo trackIndex n'est PAS supporte pour subtitles V20)
appended = False
try:
    res = media_pool.AppendToTimeline([imported[0]])
    if res:
        appended = True
        log(f"AppendToTimeline OK : {len(res) if hasattr(res, '__len__') else '?'} items places")
except Exception as e:
    log(f"AppendToTimeline exception : {e}")

# Deverrouille les pistes
for t in locked_tracks:
    try:
        timeline.SetTrackLock("subtitle", t, False)
    except Exception:
        pass

# Verification : ou les sous-titres ont-ils vraiment atterri ?
snapshot_after = snapshot_subtitle_tracks()
new_items_per_track = {}
for t, uids_after in snapshot_after.items():
    uids_before = snapshot_before.get(t, set())
    diff = uids_after - uids_before
    if diff:
        new_items_per_track[t] = len(diff)

if appended and new_items_per_track:
    arrived_on = list(new_items_per_track.keys())
    if target_track in new_items_per_track and len(arrived_on) == 1:
        log(f"OK : {new_items_per_track[target_track]} sous-titre(s) EN sur piste #{target_track} (neuve, dediee).")
    else:
        log("ATTENTION : Resolve n'a pas place sur la piste neuve attendue.")
        for t, n in new_items_per_track.items():
            log(f"  Piste #{t} : {n} item(s)")
    log("Termine.")
    notify("Traduction terminee",
           f"{len(entries)} sous-titres traduits FR -> EN sur piste #{target_track}.")
else:
    log("Import auto impossible (limitation API subtitle Resolve).")
    log("Pas grave : drag-and-drop manuel marche parfaitement.")
    open_finder_at_path(bilingual_backup_path)  # ouvre sur le bilingue (recommande)
    show_translation_drag_drop_dialog(backup_path, bilingual_backup_path, len(entries))
    notify("Traduction prete",
           f"SRT EN sur ton Bureau. Drag-drop sur la timeline pour creer la piste.")
