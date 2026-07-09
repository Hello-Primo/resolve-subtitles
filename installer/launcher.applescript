-- Lanceur de l'installeur Resolve Subtitles (Primo-Studio)
-- Exécute install.sh embarqué dans les ressources de l'app.
-- Toute l'interface utilisateur (popups) est gérée par install.sh lui-même.
on run
	set installerPath to POSIX path of (path to resource "install.sh")
	try
		with timeout of 86400 seconds
			do shell script "/bin/bash " & quoted form of installerPath
		end timeout
	on error errMsg number errNum
		if errNum is -1712 then
			-- Timeout AppleEvent : le bash orphelin attend un dialogue sans réponse.
			try
				do shell script "/usr/bin/pkill -f " & quoted form of installerPath & " || true"
			end try
			display alert "Installation abandonnée" message ¬
				"L'installeur est resté ouvert trop longtemps sans réponse. Relance l'app pour recommencer — c'est sans risque." ¬
				buttons {"OK"} default button "OK"
		else if errNum is not 1 then
			-- install.sh affiche déjà ses propres alertes avant de sortir en 1.
			display alert "Installation interrompue" message ¬
				"Une erreur inattendue est survenue. Réessaie, ou envoie à Néto le fichier :" & return & ¬
				"~/Library/Logs/ResolveSubtitles-Install.log" as critical buttons {"OK"} default button "OK"
		end if
	end try
end run
