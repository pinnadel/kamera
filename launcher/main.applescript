-- KaMeRa launcher applet
-- Opens KaMeRa as a chromeless Chrome app-window (not a browser tab).
-- Starts the backend first if it isn't already running.
--
-- SOURCE OF TRUTH for /Applications/KaMeRa.app's main.scpt.
-- To rebuild the app bundle after editing this file, run:
--     launcher/build_app.sh
--
-- ─────────────────────────────────────────────────────────────────────────────
-- Why the backend is launched through `perl … POSIX::setsid()` and NOT `… &`:
--
-- AppleScript's `do shell script` blocks until the shell it spawns AND every
-- process still holding that shell's stdout/stderr pipe have exited. `start.sh`
-- runs `launcher.py` in the FOREGROUND, and launcher.py never returns — it
-- blocks on a long-lived rumps menu-bar run loop (plus uvicorn). So a plain
-- `./start.sh … &` (even with nohup, redirects, or `disown`) leaves the pipe
-- open forever: `do shell script` never returns, the applet's main thread is
-- wedged, and macOS shows "KaMeRa — Application Not Responding" in the Dock.
-- (The backend itself is fine — only the launcher wrapper hangs.)
--
-- The reliable fix is to fully DETACH the child into its own session so it
-- shares no descriptor with the shell AppleScript is waiting on. macOS has no
-- `setsid` binary, but every Mac ships Perl with POSIX: fork, setsid(), then
-- exec start.sh with all three standard descriptors redirected. `do shell
-- script` then returns in ~0.1s while the backend keeps running detached.
-- Verified: plain/nohup/disown/subshell forms all hang; this one does not.
-- Do not replace this with a bare `… &`.
-- ─────────────────────────────────────────────────────────────────────────────

set backendUp to false
try
	do shell script "curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:8000/health | grep -q 200"
	set backendUp to true
end try

if not backendUp then
	do shell script "cd '/Users/philippklein/dev/kamera' && /usr/bin/perl -e 'use POSIX; fork and exit; POSIX::setsid(); exec @ARGV' ./start.sh --no-browser </dev/null >/tmp/kamera_launch.log 2>&1 &"

	set started to false
	repeat 120 times
		delay 1
		try
			do shell script "curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:8000/health | grep -q 200"
			set started to true
			exit repeat
		end try
	end repeat

	if not started then
		display alert "KaMeRa didn't start" message "Check /tmp/kamera_launch.log or data/app.log for details." as critical
		return
	end if
end if

-- Open the UI as the installed KaMeRa PWA shim (its own Dock icon + identity —
-- shows the KaMeRa icon, not Chrome's). Fall back to raw Chrome --app mode if
-- the shim was never installed / was removed, so the app still opens.
set pwaShim to "/Users/philippklein/Applications/Chrome Apps.localized/KaMeRa.app"
try
	do shell script "test -d " & quoted form of pwaShim
	do shell script "open " & quoted form of pwaShim
on error
	do shell script "open -a 'Google Chrome' --args --app=http://localhost:8000/"
end try
