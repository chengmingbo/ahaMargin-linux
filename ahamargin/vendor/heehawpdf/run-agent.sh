#!/bin/bash
# Runs one assistant turn for the ahaMargin-linux AI assistant extensions.
#
# Usage: run-agent.sh JOB_DIR WORK_DIR COMMAND [ARGS...]
#
# JOB_DIR holds the inputs the plugin wrote: prompt.txt, system.txt, and
# request.json (API providers). Arguments equal to @@PROMPT@@ or @@SYSTEM@@
# are replaced by the contents of prompt.txt / system.txt. The runner writes
#   out.txt   the tool's standard output (polled by the plugin)
#   err.txt   its standard error
#   cpid      the tool's process id (the plugin kills it to cancel)
#   exit      the exit status, written last and atomically
#
# Environment:
#   TC_STDIN        1 to feed prompt.txt on standard input
#   TC_BIN          executable to use instead of COMMAND (optional)
#   TC_PROXY        "auto" (default), "none", or a proxy URL
#   TC_KEY_VAR      API key variable name, e.g. DEEPSEEK_API_KEY. The key is
#                   read from the desktop keyring (secret-tool, service
#                   ahamargin-ai-assistant, account TC_KEY_ACCOUNT), then from the
#                   login shell's
#                   environment, and written to auth-header.txt for curl.

job=$1
work=$2
shift 2

finish() {
  rm -f "$job/auth-header.txt"
  printf '%s' "$1" > "$job/exit.tmp" && mv "$job/exit.tmp" "$job/exit"
  exit 0
}

cd "$job" || exit 1
# Keychain-backed logins look the user up by name.
export USER=${USER:-$(id -un)} LOGNAME=${LOGNAME:-$(id -un)}
: > out.txt
: > err.txt

# Remove jobs older than a day.
find "$(dirname "$job")" -mindepth 1 -maxdepth 1 -type d -mmin +1440 -exec rm -rf {} + 2>/dev/null

key_var=${TC_KEY_VAR:-}
case "$key_var" in *[!A-Za-z0-9_]*) key_var= ;; esac

user_shell=${SHELL:-/bin/sh}
[ -x "$user_shell" ] || user_shell=/bin/sh

# --- Proxy (same rules as mooTerm) ------------------------------------------
# Command-line tools ignore the desktop's proxy settings, and an app started
# from a menu has
# no *_proxy variables. Automatic: Typora's own environment first, then the
# system proxy. The login shell's exports and aliases still win, because they
# are applied after these variables when the shell starts.
system_proxy() {
  # GSettings is how GNOME, KDE and most desktops store proxy settings; the
  # environment is the fallback for a bare session.
  if command -v gsettings >/dev/null 2>&1; then
    get() { gsettings get "$1" "$2" 2>/dev/null | sed "s/^'//; s/'$//"; }
    mode=$(get org.gnome.system.proxy mode)
    if [ "$mode" = "'manual'" ] || [ "$mode" = "manual" ]; then
      http_host=$(get org.gnome.system.proxy.http host)
      http_port=$(get org.gnome.system.proxy.http port)
      https_host=$(get org.gnome.system.proxy.https host)
      https_port=$(get org.gnome.system.proxy.https port)
      socks_host=$(get org.gnome.system.proxy.socks host)
      socks_port=$(get org.gnome.system.proxy.socks port)
      ignore=$(get org.gnome.system.proxy ignore-hosts)
      http=$([ -n "$http_host" ] && echo "http://$http_host${http_port:+:$http_port}")
      https=$([ -n "$https_host" ] && echo "http://$https_host${https_port:+:$https_port}")
      socks=$([ -n "$socks_host" ] && echo "socks5://$socks_host${socks_port:+:$socks_port}")
      h=$http; s=$https; a=${https:-${http:-$socks}}
      no=$ignore
      [ -n "$h$s$a" ] && printf "%s\t%s\t%s\t%s\n" "$h" "$s" "$a" "$no"
      return
    fi
  fi
  # Environment variables, including the ones the desktop session exports.
  h=${http_proxy:-$HTTP_PROXY}
  s=${https_proxy:-$HTTPS_PROXY}
  a=${all_proxy:-$ALL_PROXY}
  no=${no_proxy:-$NO_PROXY}
  [ -n "$h$s$a" ] && printf "%s\t%s\t%s\t%s\n" "$h" "$s" "$a" "$no"
}
export_proxy() { # http https all [no_proxy]
  [ -n "$1" ] && export http_proxy=$1 HTTP_PROXY=$1
  [ -n "$2" ] && export https_proxy=$2 HTTPS_PROXY=$2
  [ -n "$3" ] && export all_proxy=$3 ALL_PROXY=$3
  local no="localhost,127.0.0.1,::1${4:+,$4}"
  export no_proxy=$no NO_PROXY=$no
}
# Local model servers (Ollama) must never go through the proxy.
local_first='export no_proxy="localhost,127.0.0.1,::1${no_proxy:+,$no_proxy}" NO_PROXY="localhost,127.0.0.1,::1${NO_PROXY:+,$NO_PROXY}"; '
proxy_prefix=""
case "${TC_PROXY:-auto}" in
  auto)
    if [ -z "${http_proxy}${https_proxy}${all_proxy}${HTTP_PROXY}${HTTPS_PROXY}${ALL_PROXY}" ]; then
      IFS=$'\t' read -r p_http p_https p_all p_no <<< "$(system_proxy)"
      [ -n "$p_http$p_https$p_all" ] && export_proxy "$p_http" "$p_https" "$p_all" "$p_no"
    fi
    ;;
  none)
    # Applied inside the shell, after its startup files.
    proxy_prefix='unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY; '
    ;;
  *)
    proxy=$TC_PROXY
    case "$proxy" in *://*) ;; *) proxy="http://$proxy" ;; esac
    export TC_PROXY_URL=$proxy
    proxy_prefix='export http_proxy="$TC_PROXY_URL" https_proxy="$TC_PROXY_URL" all_proxy="$TC_PROXY_URL" HTTP_PROXY="$TC_PROXY_URL" HTTPS_PROXY="$TC_PROXY_URL" ALL_PROXY="$TC_PROXY_URL"; '
    ;;
esac

# --- API key ------------------------------------------------------------------
if [ -n "$key_var" ]; then
  # The desktop keyring, through libsecret. The app stores keys there under the
  # same service and account the panel writes with.
  key=""
  if command -v secret-tool >/dev/null 2>&1; then
    key=$(timeout 5s secret-tool lookup service ahamargin-ai-assistant account "${TC_KEY_ACCOUNT:-$key_var}" 2>/dev/null | head -n 1)
  fi
  if [ -z "$key" ] && [ -n "${TC_KEY_FILE:-}" ] && [ -f "${TC_KEY_FILE}" ]; then
    # Fallback store, used when no keyring daemon is running.
    # Decode JSON with the app's Python, including escaped key characters.
    acct=${TC_KEY_ACCOUNT:-$key_var}
    key=$("${TC_PYTHON:-python3}" -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""), end="")' "$TC_KEY_FILE" "$acct" 2>/dev/null)
  fi
  [ -z "$key" ] && key=${!key_var:-}
  if [ -z "$key" ]; then
    # Exported in the user's shell profile?
    key=$("$user_shell" -l -c "printf '%s' \"\${$key_var:-}\"" 2>/dev/null </dev/null | head -n 1)
  fi
  if [ -z "$key" ]; then
    echo "No API key. Paste one in the assistant's Settings, or export $key_var in your shell profile." > err.txt
    finish 2
  fi
  (umask 077 && printf 'Authorization: Bearer %s\n' "$key" > auth-header.txt)
fi

# --- Launch -------------------------------------------------------------------
# Like mooTerm, a tool is started by name inside the user's interactive login
# shell, so the shell's PATH, exports, and aliases (for example
# `alias claude='https_proxy=… claude'`) apply exactly as in Terminal.
# "@@SCRIPT@@ <command>" runs a custom command line the same way.
tool=$1
shift
args=()
for arg in "$@"; do
  case "$arg" in
    @@PROMPT@@) args+=("$(cat "$job/prompt.txt")") ;;
    @@SYSTEM@@) args+=("$(cat "$job/system.txt")") ;;
    *) args+=("$arg") ;;
  esac
done

if [ "$tool" = "@@SCRIPT@@" ]; then
  run=${args[0]:-true}
  args=()
elif [ -n "${TC_BIN:-}" ]; then
  export TC_TOOL_BIN=$TC_BIN
  run='"$TC_TOOL_BIN" "$@"'
else
  case "$tool" in
    */*|*[!A-Za-z0-9._-]*|"") export TC_TOOL_BIN=$tool; run='"$TC_TOOL_BIN" "$@"' ;;
    *)
      for dir in "$HOME/.local/bin" "$HOME/.opencode/bin" "$HOME/.claude/local" /opt/homebrew/bin /usr/local/bin; do
        if [ -x "$dir/$tool" ]; then export TC_TOOL_BIN=$dir/$tool; break; fi
      done
      # The name is a plain identifier (checked above), so it is safe in shell source.
      run="if type $tool >/dev/null 2>&1; then $tool \"\$@\"; elif [ -n \"\$TC_TOOL_BIN\" ]; then \"\$TC_TOOL_BIN\" \"\$@\"; else exit 127; fi"
      ;;
  esac
fi
# The tool writes to fd 3 (out.txt); anything the shell's startup files print
# goes to /dev/null, so it can't corrupt the answer.
script="${local_first}${proxy_prefix}{ ${run}
} >&3 3>&-"

# Files the tool writes (such as Codex's last message) land in the job folder;
# relative @file arguments for curl resolve there too.
cd "$work" 2>/dev/null || cd "$job"
input=/dev/null
[ "${TC_STDIN:-0}" = 1 ] && input=$job/prompt.txt
set -m # own process group, so cancelling also stops the tool's children
"$user_shell" -l -i -c "$script" "heehaw-${tool//[^A-Za-z0-9._-]/}" ${args[@]+"${args[@]}"} \
  < "$input" 3> "$job/out.txt" > /dev/null 2> "$job/err.txt" &
cpid=$!
echo "$cpid" > "$job/cpid"
wait "$cpid"
status=$?
if [ "$status" = 127 ] && [ ! -s "$job/out.txt" ]; then
  name=${TC_BIN:-$tool}
  echo "${name##*/} was not found. Install it, or set its path in the assistant's settings." >> "$job/err.txt"
fi
finish "$status"
