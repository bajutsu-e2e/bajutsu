#!/bin/bash
# EXPERIMENT ONLY, not for merge: print host and Simulator load figures into the job log, so the two
# arms can be compared without downloading artifacts. Observer only; never fails the job.
# usage: metrics.sh <udid> <label>
udid=$1
label=$2
echo "EXPMETRIC $label at=$(date -u +%FT%TZ)"
echo "EXPMETRIC $label disabled=$(xcrun simctl spawn "$udid" launchctl print-disabled system 2>/dev/null | grep -c '=> disabled')"
/bin/ps -axo rss=,time=,comm= >/tmp/exp-ps.txt
awk -v l="$label" '
  { n++ } /simruntime|CoreSimulator\/Volumes/ { sim++; rss += $1 }
  END { printf "EXPMETRIC %s procs=%d sim_procs=%d sim_rss_mb=%d\n", l, n, sim, rss / 1024 }' /tmp/exp-ps.txt
# CPU seconds accumulated per process name, guest and host copies kept apart.
awk -v l="$label" '
  { split($2, t, ":"); s = t[1] * 60 + t[2]
    path = $0; sub(/^ *[0-9]+ +[0-9:.]+ +/, "", path)
    k = split(path, p, "/"); name = p[k]
    if (path ~ /simruntime|CoreSimulator\/Volumes/) name = "sim:" name
    cpu[name] += s; total += s }
  END { printf "EXPMETRIC %s cpu_total_s=%d\n", l, total
        for (name in cpu) printf "EXPMETRIC %s cpu_s %.1f %s\n", l, cpu[name], name }' /tmp/exp-ps.txt |
  sort -k4 -g -r | head -25
/usr/bin/top -l 2 -n 0 -s 5 | awk '/^Load Avg|^CPU usage|^PhysMem/ { line[$1] = $0 } END { for (k in line) print line[k] }' |
  sed "s/^/EXPMETRIC $label /"
/usr/bin/vm_stat | grep -E 'free|compressor|Pageins|Pageouts|Swap|Decompressions' | sed "s/^/EXPMETRIC $label vm /"
exit 0
