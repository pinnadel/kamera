-- KaMeRa launcher applet
-- Opens KaMeRa as a chromeless Chrome app-window (not a browser tab).
-- Starts the backend first if it isn't already running.
--
-- SOURCE OF TRUTH for /Applications/KaMeRa.app's main.scpt.
-- To rebuild the app bundle after editing this file, run:
--     launcher/build_app.sh
--
-- ─────────────────────────────────────────────────────────────────────────────
-- Why the backend is handed to launchd and NOT forked by this applet:
--
-- An applet quits the moment its script ends — and on macOS 26+ the system then
-- terminates the processes that applet spawned, even ones detached into their
-- own session with POSIX::setsid(). Measured 2026-09-18, twice, to the tenth of
-- a second: applet exits at T, launcher.py takes a SIGTERM at T+0.6s, uvicorn is
-- gone at T+1.2s. The user saw a normal-looking window whose every request then
-- failed with "failed to fetch", because the page itself still came from the
-- service-worker cache.
--
-- Two escapes were tried and measured, and both still died with the applet:
-- the older `perl -e 'fork and exit; POSIX::setsid(); exec @ARGV'` detach, and
-- `launchctl submit`. Neither helps, because both still run in THIS applet's
-- bootstrap context — a new session is not a new owner.
--
-- `launchctl kickstart` does, because the job it starts was bootstrapped into
-- gui/<uid> ahead of time (launcher/install_agent.sh) and therefore belongs to
-- launchd. Provenance's own autopilot agent has survived on this machine for
-- months on exactly that footing.
--
-- kickstart also solves the OTHER hazard the perl form existed for: `do shell
-- script` blocks until every process holding the spawned shell's stdout/stderr
-- pipe has exited, so a plain `./start.sh &` (with or without nohup/disown)
-- wedges the applet's main thread and macOS shows "KaMeRa — Application Not
-- Responding". kickstart hands the work to launchd and returns at once.
--
-- If the agent was never installed, kickstart errors and the alert below fires.
-- Fix with: launcher/install_agent.sh
-- ─────────────────────────────────────────────────────────────────────────────

set backendUp to false
try
	do shell script "curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:8000/health | grep -q 200"
	set backendUp to true
end try

if not backendUp then
	do shell script "launchctl kickstart gui/$(id -u)/com.philippklein.kamera.backend"

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
