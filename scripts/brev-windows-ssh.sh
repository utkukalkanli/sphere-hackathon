#!/usr/bin/env bash
# Run inside WSL on Windows. Copies the Brev SSH entry for an instance into the
# *Windows* SSH config so that Windows VS Code (Remote-SSH) can connect to it.
#
# Brev's CLI only runs in WSL and writes its SSH config there (~/.brev/ssh_config),
# which Windows VS Code can't see. Re-run after every `brev start` / `brev refresh`:
# the instance's SSH port can change.
#
#   scripts/brev-windows-ssh.sh              # defaults to sphere-gpu
#   scripts/brev-windows-ssh.sh other-host
set -euo pipefail

HOST="${1:-sphere-gpu}"
BREV_CFG="$HOME/.brev/ssh_config"
BEGIN="# >>> brev $HOST (managed by brev-windows-ssh.sh) >>>"
END="# <<< brev $HOST <<<"

command -v wslpath >/dev/null || { echo "Run this inside WSL." >&2; exit 1; }
[[ -f "$BREV_CFG" ]] || { echo "$BREV_CFG not found; run 'brev login && brev refresh' first." >&2; exit 1; }

entry() { awk -v h="$1" '$1=="Host"{f=($2==h)} f' "$BREV_CFG"; }
[[ -n "$(entry "$HOST")" ]] || { echo "No '$HOST' in $BREV_CFG. Check 'brev ls' and run 'brev refresh'." >&2; exit 1; }

win_user=$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r')
win_home=$(wslpath "$(cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')")
win_ssh="$win_home/.ssh"
mkdir -p "$win_ssh"

# Copy the Brev private key and lock it to the Windows user; Windows OpenSSH
# refuses keys that other users can read.
key=$(entry "$HOST" | awk '$1=="IdentityFile"{print $2; exit}')
key="${key/#\~/$HOME}"
cp "$key" "$win_ssh/brev.pem"
icacls.exe "$(wslpath -w "$win_ssh/brev.pem")" /inheritance:r /grant:r "${win_user}:R" >/dev/null

# Windows OpenSSH has no ControlMaster support, so drop the Control* lines,
# and /dev/null doesn't exist there (its equivalent is NUL).
block=$(
  echo "$BEGIN"
  entry "$HOST" | grep -vE '^\s*(Control|IdentityFile|RequestTTY|$)' |
    sed 's#UserKnownHostsFile /dev/null#UserKnownHostsFile NUL#'
  echo "  IdentityFile ~/.ssh/brev.pem"
  echo "$END"
)

cfg="$win_ssh/config"
touch "$cfg"
# Replace a previous managed block (if any), then append the fresh one.
awk -v b="$BEGIN" -v e="$END" '$0==b{skip=1} !skip{print} $0==e{skip=0}' "$cfg" > "$cfg.tmp"
printf '%s\n' "$block" >> "$cfg.tmp"
mv "$cfg.tmp" "$cfg"

echo "Wrote '$HOST' to $(wslpath -w "$cfg")"
echo "Test from PowerShell:  ssh $HOST"
