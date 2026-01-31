Server Snapshot Plan (Run on Server)
====================================

Goal
----
Collect a minimal, safe snapshot of the live server runtime so the local
implementation can mirror it. This plan avoids secrets, private keys, and
media files.

Instructions
------------
1) SSH to the server (you will provide credentials yourself).
2) Copy `gather_server_state.sh` to the server.
3) Run it and pull the tarball back to your Mac.

Example commands (fill in your own host/user)
---------------------------------------------
Copy script to server:
  scp ./local\ implementation/server_snapshot_plan/gather_server_state.sh user@server:/tmp/

Run on server:
  ssh user@server "bash /tmp/gather_server_state.sh"

Download tarball:
  scp user@server:~/server_snapshot_*.tar.gz .

What it collects
----------------
- OS and services summary
- Nginx/FFmpeg/Python/Node versions and locations
- Nginx config files (excluding .pem/.key)
- Process list and open ports (filtered)
- HLS/RTMP runtime paths and permissions
- Candidate repo directories and start scripts
- Tail of recent logs (nginx, ffmpeg, API)

What it does NOT collect
------------------------
- Media files
- Private keys or certificates
- Secrets or credentials

After you run the snapshot
--------------------------
Share the tarball contents (or the relevant excerpts) back to the local repo
so we can align:
- RTMP app name
- HLS segment path and parameters
- API route naming
- FFmpeg flags and log locations
- Lock file behavior
