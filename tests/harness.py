"""Banc de test ScanSubtitlesFR hors Resolve (lance via tests/run.sh) : faux module Resolve,
popups osascript simulees (mais compilees par osacompile pour verifier
la syntaxe AppleScript), vrais appels Claude."""
import os, sys, json, types, subprocess, runpy, tempfile

SCENARIO = sys.argv[1]
SCRIPT = sys.argv[2]
SCEN = json.load(open(os.path.join(os.path.dirname(__file__), "scenarios.json")))[SCENARIO]

# ---- faux Resolve ----
class Item:
    def __init__(self, i, text):
        self.i, self.text = i, text
    def GetName(self): return self.text
    def GetStart(self): return 90000 + self.i * 50
    def GetEnd(self): return 90000 + self.i * 50 + 45
    def GetUniqueId(self): return f"uid-{self.i}"

items = [Item(i, t) for i, t in enumerate(SCEN["subs"])]

class Timeline:
    def GetName(self): return "TEST_" + SCENARIO
    def GetSetting(self, k): return "25"
    def GetTrackCount(self, kind): return 1
    def GetItemListInTrack(self, kind, t): return items
    def GetStartFrame(self): return 90000
    def GetEndFrame(self): return 90000 + len(items) * 50
    def SetCurrentTimecode(self, tc): return SCEN.get('capture', False)

class Project:
    def GetSetting(self, k): return "25"
    def GetCurrentTimeline(self): return Timeline()
    def ExportCurrentFrameAsStill(self, path):
        import shutil; shutil.copy(os.path.join(os.path.dirname(__file__), 'fake_frame.jpg'), path); return True

class PM:
    def GetCurrentProject(self): return Project()

class Resolve:
    def GetProjectManager(self): return PM()

dvr = types.ModuleType("DaVinciResolveScript")
dvr.scriptapp = lambda name: Resolve()
sys.modules["DaVinciResolveScript"] = dvr

# ---- popups simulees ----
answers = {k: list(v) for k, v in SCEN["answers"].items()}
real_run = subprocess.run

def fake_run(cmd, *a, **kw):
    if cmd and cmd[0] == "open":
        print("[open simule]", cmd[1:]); return subprocess.CompletedProcess(cmd, 0, "", "")
    if cmd and cmd[0] == "osascript":
        script = cmd[-1]
        R = lambda out="", err="": subprocess.CompletedProcess(cmd, 0, out, err)
        if "display notification" in script:
            return R()
        if "tell process" in script:
            return R(err="simule : pas de Resolve")
        # verifie la syntaxe AppleScript sans afficher de fenetre
        comp = real_run(["osacompile", "-e", script, "-o", os.devnull],
                        capture_output=True, text=True)
        if comp.returncode != 0:
            print("!!! APPLESCRIPT INVALIDE :", comp.stderr, "\n", script)
            sys.exit(3)
        for key in answers:
            if key in script:
                if key in ("Vérifie qui parle", "Claude a detecte les speakers suivants", "Résultat de la vérification", "pas pu aller au bout"):
                    print(f"----- POPUP '{key}' -----")
                    print(script.split("display dialog ", 1)[1].split(" with title")[0]
                          .replace('" & return & "', "\n"))
                    print("-------------------------------------")
                ans = answers[key].pop(0)
                print(f"[popup '{key}'] -> {ans!r}")
                return R(out=ans + "\n")
        print("!!! popup non prevue :", script[:300])
        sys.exit(4)
    return real_run(cmd, *a, **kw)

subprocess.run = fake_run
sys.argv = [SCRIPT]
runpy.run_path(SCRIPT, run_name="__main__")
