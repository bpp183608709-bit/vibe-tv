#!/usr/bin/env bash
# Try Aerial on a TV the maintainer does not have, and write down what happened, for a GitHub
# "机型适配 / Device support" issue. tools/report.sh says how the TV is set up; this says how Aerial
# behaves on it: Home from Aerial and from another app, and on request standby, a restart and
# each input. Each check prints PASS / FAIL / SKIP with what it saw, and the end has a JSON block.
#
#   TV=<tv-ip> tools/selftest.sh
#   curl -fsSL https://aerial.qust.me/tools/selftest.sh | TV=<tv-ip> bash
#
# Optional, each asks nothing and only runs when set:
#   STANDBY=1  put the TV in standby for 70 s and wake it (Xiaomi turns ADB off in standby; the
#              script then asks for the remote's power key and waits for ADB to come back)
#   REBOOT=1   restart the TV and follow what is in front for the first minute (ask the owner first)
#   INPUTS=1   open each of Aerial's inputs as its tile does, take a screenshot of each into
#              aerial-selftest-<model>/, and come back to Aerial in between
#   PARENTAL=1 try the parental control (docs/PARENTAL.md) end to end: a two-minute day, two minutes
#              of clock without a player, the lock screen over Home, Home / Back / Menu and another
#              app against it, and the way out through its own buttons. Puts today's counters and
#              the three limits back when it is done; a TV with no parental password gets one for
#              the run (PIN=, else 24681357) and has it removed again at the end.
#   PIN=12345678  with PARENTAL=1: the password — what is set on a TV that has none, and what is typed
#              on the lock screen's keypad as a parent does (8 to 12 digits). A typo counts against
#              the five-wrong-entries rule, so it costs five minutes of waiting on the TV.
#
# What it changes: it presses Home, opens the TV's settings, opens inputs, and with the options
# above sleeps or restarts the TV. Nothing is installed, removed or written to settings. Read
# site/adapt.md for what to do with a failed check.
set -uo pipefail

: "${TV:?set TV to the TV IP address, e.g. TV=<tv-ip> $0}"
command -v adb >/dev/null || { echo "adb not found. macOS: brew install android-platform-tools; Linux: install android-tools / adb." >&2; exit 1; }
PKG=app.orchard.tv
case "$TV" in *:*) SERIAL="$TV" ;; *) SERIAL="$TV:5555" ;; esac
export ANDROID_SERIAL="$SERIAL"

# A TV whose network sleeps keeps `adb connect` waiting for a minute or more; 8 s is plenty awake.
connect() {
  perl -e 'alarm shift; exec @ARGV' 8 adb connect "$SERIAL" </dev/null >/dev/null 2>&1
  [ "$(perl -e 'alarm shift; exec @ARGV' 5 adb get-state </dev/null 2>/dev/null)" = device ]
}
connect || { echo "Could not connect to $SERIAL. Check that ADB debugging is on, the IP is right, and the TV's \"Allow USB debugging?\" was answered with Allow." >&2; exit 1; }

# </dev/null: adb forwards stdin, which under `curl | bash` is the rest of this script.
sh_() { adb shell "$@" </dev/null 2>/dev/null | tr -d '\r'; }
now() { perl -MTime::HiRes=time -e 'printf "%.1f", time'; }
# `adb shell a b c` is joined into one line for the TV's own shell, which then splits it again and
# eats one round of quotes: a JSON value arrives as {daily:2,...} and the receiver calls it invalid.
# So the whole command goes over as one string, with every argument quoted for that shell.
config() {
  local line="am broadcast -a app.orchard.tv.CONFIG -n $PKG/.guard.ConfigReceiver" a
  for a in "$@"; do
    case "$a" in
      *[!A-Za-z0-9_.:/=-]*) line="$line '$(printf '%s' "$a" | sed "s/'/'\\\\''/g")'" ;;
      *) line="$line $a" ;;
    esac
  done
  adb shell "$line" </dev/null 2>/dev/null | tr -d '\r' | sed -n 's/.*data="\(.*\)"$/\1/p'
}

# The package of the window with focus: mCurrentFocus, else the focused app (Android 5-9 differ).
fg() {
  local d w
  d=$(sh_ dumpsys window)
  w=$(printf '%s\n' "$d" | grep -m1 "mCurrentFocus=" | grep -oE ' [A-Za-z0-9_.]+/' | head -n1 | tr -d ' /')
  [ -n "$w" ] || w=$(printf '%s\n' "$d" | grep -m1 "mFocusedApp=" | grep -oE ' [A-Za-z0-9_.]+/' | head -n1 | tr -d ' /')
  printf '%s' "$w"
}

# The same as package/activity, which is how the parental lock is told from Home: they share a
# package, and which of the two is in front is the whole question there.
fgwin() { sh_ dumpsys window | grep -m1 "mCurrentFocus=" | grep -oE "[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+" | head -n1; }

# Waits up to $1 seconds for a window whose name matches the glob $2 (e.g. "*LockActivity").
wait_win() {
  local i=0 n=$(( $1 * 4 ))
  while [ "$i" -lt "$n" ]; do
    case "$(fgwin)" in $2) return 0 ;; esac
    sleep 0.25; i=$((i + 1))
  done
  return 1
}

# ---- the parental module (docs/PARENTAL.md) ----
# The settings page asks for the password before it shows a limit, which a test does not have; these
# broadcasts (guarded by WRITE_SECURE_SETTINGS, so ADB only) are the way in. See ConfigReceiver.
pstate() { config --ez parental_state true; }
# One value out of that JSON: pfield "$state" dailyUsed. Empty when the key is not there.
pfield() {
  # am prints the receiver's result data as one line; some versions escape the quotes in it.
  printf '%s' "$1" | perl -MJSON::PP -e 'binmode STDIN, ":utf8"; local $/; my $in = <STDIN>; $in =~ tr/\\//d; my $j = eval { decode_json($in) } or exit; my $v = $j->{$ARGV[0]}; exit unless defined $v; print ref $v ? ($$v ? "true" : "false") : $v;' "$2" 2>/dev/null
}
# The three settings as the settings page holds them, to put back at the end.
plimits() {
  printf '%s' "$1" | perl -MJSON::PP -e 'binmode STDIN, ":utf8"; local $/; my $in = <STDIN>; $in =~ tr/\\//d; my $j = eval { decode_json($in) } or exit; my $l = $j->{limits}; print qq({"daily":$l->{daily},"session":$l->{session},"interval":$l->{interval}});' 2>/dev/null
}
# Whether the parental lock is what is in front.
lockup() { case "$(fgwin)" in *LockActivity) return 0 ;; esac; return 1; }
# The three settings, as {"daily":2,"session":0,"interval":10}.
p_limits() { config --es parental_limits "$1" >/dev/null; }
# The 确定 key's centre, as the screen reports it, or nothing when there is no keypad up. A tap on
# the label's own rect is enough — the label is drawn inside the button, and the tap goes to the
# button; nothing has to be focused first.
pin_ok_point() {
  sh_ uiautomator dump /data/local/tmp/aerial-pin.xml >/dev/null 2>&1
  printf '%s' "$(sh_ cat /data/local/tmp/aerial-pin.xml 2>/dev/null)" | perl -0777 -ne '
    my @ok = /text="确定"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"/;
    exit 1 unless @ok == 4;
    print int(($ok[0] + $ok[2]) / 2), " ", int(($ok[1] + $ok[3]) / 2);'
}

# The parental checks, as one function so each one can stop the rest with a SKIP. Left as the TV was:
# the counters, which a two-minute limit needs at zero, are put back with the state read at the start.
parental_checks() {
  sh_ am start -n "$PKG/.MainActivity" >/dev/null; sleep 2
  local before saved st other made_pin=0
  before=$(pstate)
  saved=$(plimits "$before")
  if [ -z "$saved" ]; then
    result parental SKIP "no answer from the parental module (Aerial older than the module?)"
    return
  fi
  if [ "$(pfield "$before" hasPin)" != true ]; then
    # The password is set on the settings page with the remote, which a test does not have. One is
    # put there instead and taken away again at the end — only where there was none: the store keeps
    # a hash, so an owner's own password could never be put back.
    local trial=${PIN:-24681357}
    printf '%s' "$trial" | grep -qE '^[0-9]{8,12}$' || trial=24681357
    st=$(config --es parental_pin "$trial")
    if [ "$(pfield "$st" hasPin)" = true ]; then
      made_pin=1; PIN=$trial
    else
      result parental SKIP "no parental password, and one could not be set: $(pfield "$st" error)"
      return
    fi
  fi

  # 7a. The clock. A day of two minutes counted a minute at a time — the timer treats a longer jump as
  # the process having been away, which is not what a test wants to say.
  config --ez parental_reset true >/dev/null
  p_limits '{"daily":2,"session":0,"interval":10}'
  st=$(config --ei parental_watch_min 2)
  local state used
  state=$(pfield "$st" state); used=$(pfield "$st" dailyUsed)
  if [ "$state" = DailyLimit ] && [ "${used:-0}" -ge 120000 ]; then
    result parental_clock PASS "two minutes counted, the two-minute day used up"
  else
    result parental_clock FAIL "after two minutes: state=$state used=${used:-?} ms"
  fi

  # 7b. The lock screen over Home, from Home's own once-a-second pass.
  sh_ am start -n "$PKG/.MainActivity" >/dev/null
  if wait_win 8 '*LockActivity'; then result parental_lock PASS "the lock came up in $(fgwin)"
  else result parental_lock FAIL "8 s on, in front: $(fgwin)"
  fi

  if lockup; then
    # 7c. The keys a child tries first. Back is the lock's own and Menu has nothing to open here, so
    # these two are real checks. Home is not: `input keyevent` skips the accessibility key filter the
    # guard answers Home in (see the note further down the script), so the system always gets it and
    # the launcher flashes. What can be checked here is that the lock comes back over it, and how
    # long that takes; whether the key itself is caught has to be tried with the remote.
    local went=""
    for k in BACK MENU; do
      sh_ input keyevent "KEYCODE_$k"
      sleep 1.5
      lockup || went="$went $k"
    done
    sh_ input keyevent KEYCODE_HOME
    sleep 1
    local home="stayed in front"
    if ! lockup; then
      if wait_win 10 '*LockActivity'; then home="the launcher showed, the lock came back"
      else home="the lock did not come back"
      fi
    fi
    if [ -n "$went" ]; then result parental_keys FAIL "the lock went away after:$went (in front: $(fgwin))"
    elif [ "$home" = "the lock did not come back" ]; then result parental_keys FAIL "after Home: $home"
    else result parental_keys PASS "Back and Menu left the lock in front; Home: $home (injected Home skips the guard's key filter — the remote is the real test)"
    fi

    # 7d. Another app to the front: the guard has to put the lock back over it. This is where
    # SYSTEM_ALERT_WINDOW shows up: Android 10+ refuses a background start without it.
    if lockup; then
      sh_ am start -a android.settings.SETTINGS >/dev/null; sleep 2
      other=$(fgwin)
      case "$other" in
        # The system did not get to open it: nothing got in front of the lock, which is the point.
        *LockActivity) result parental_guard PASS "the lock stayed in front when another app was opened" ;;
        *)
          if wait_win 10 '*LockActivity'; then result parental_guard PASS "the lock came back over $other"
          else result parental_guard FAIL "another app stayed in front: $(fgwin); is SYSTEM_ALERT_WINDOW granted (tools/install.sh)?"
          fi ;;
      esac
    fi

    # 7e. The way out a button opens: 加 30 分钟. What is behind the password is tried directly here —
    # the keypad's own entry is the check after this one, and only when PIN= is given.
    st=$(config --es parental_action half)
    if [ "$(pfield "$st" state)" = Normal ] && wait_win 8 '*MainActivity'; then
      result parental_exit PASS "加 30 分钟 lifted the lock and Home is back in front (keypad not typed)"
    else
      result parental_exit FAIL "state=$(pfield "$st" state), in front: $(fgwin)"
    fi
  else
    result parental_keys SKIP "the lock screen never came up"
    result parental_guard SKIP "the lock screen never came up"
    result parental_exit SKIP "the lock screen never came up"
  fi

  # 7f. The other lock screen: a session limit, with its countdown and its one way out.
  config --ez parental_reset true >/dev/null
  p_limits '{"daily":0,"session":1,"interval":10}'
  st=$(config --ei parental_watch_min 1)
  sh_ am start -n "$PKG/.MainActivity" >/dev/null
  if [ "$(pfield "$st" state)" = Interval ] && wait_win 8 '*LockActivity'; then
    result parental_break PASS "one minute of the session used: the countdown screen, $(pfield "$st" intervalLeft) s on it"
  else
    result parental_break FAIL "state=$(pfield "$st" state), in front: $(fgwin)"
  fi
  config --es parental_action skip >/dev/null
  wait_win 8 '*MainActivity' || sleep 2

  # 7g. The password itself, typed on the keypad the way a remote types it: the first button, the
  # digits, then OK. Only with PIN= — the password is the owner's, not something a script should know.
  case "${PIN:-}" in
    "") result parental_pin SKIP "PIN=<the 8-12 digit password> to type it on the keypad" ;;
    *)
      if ! printf '%s' "$PIN" | grep -qE '^[0-9]{8,12}$'; then
        result parental_pin SKIP "PIN is not 8 to 12 digits"
      else
        config --ez parental_reset true >/dev/null
        p_limits '{"daily":2,"session":0,"interval":10}'
        config --ei parental_watch_min 2 >/dev/null
        sh_ am start -n "$PKG/.MainActivity" >/dev/null
        if ! wait_win 8 '*LockActivity'; then
          result parental_pin FAIL "the lock screen did not come up; in front: $(fgwin)"
        else
          # The first button is 加 30 分钟 and has the focus; OK opens the keypad on it.
          sh_ input keyevent KEYCODE_DPAD_CENTER; sleep 3
          # The newline matters: without it `fold` leaves the last digit unterminated, `read` returns
          # non-zero on it, and the loop body never runs for that digit — the entry is always one
          # short, which is what "a wrong digit" looked like for a while (MiTV-MFTP0).
          printf '%s\n' "$PIN" | fold -w1 | while read -r d; do sh_ input keyevent "KEYCODE_$d"; sleep 0.4; done
          sleep 0.5
          # The keypad's 确定, tapped where its label sits. Down presses are not used to reach it:
          # where the focus lands depends on the pad's layout and on where it started, and the button
          # acts on a key up that follows a press which began on it — `input keyevent` sends both in
          # one frame, so OK never takes it. A tap is the same onClick a remote's OK fires.
          local center
          center=$(pin_ok_point)
          if [ -z "$center" ]; then
            result parental_pin FAIL "the keypad did not come up: no 确定 on the screen"
          else
            sh_ input tap $center
            if wait_win 8 '*MainActivity'; then
              local extra
              extra=$(pfield "$(pstate)" dailyExtra)
              if [ "${extra:-0}" -ge 1800000 ]; then result parental_pin PASS "the password went through and 30 minutes were added"
              else result parental_pin FAIL "the lock went, but dailyExtra=${extra:-?} ms (30 minutes expected)"
              fi
            else
              result parental_pin FAIL "still $(fgwin): the password was refused, or OK missed — try it with the remote"
            fi
          fi
        fi
      fi ;;
  esac

  # As it was: the three settings, then today's counters, then the password — if this run made one.
  config --es parental_limits "$saved" >/dev/null
  st=$(config --es parental_restore "$before")
  if [ "$(pfield "$st" state)" = "$(pfield "$before" state)" ]; then
    echo "  (parental: put back — $(pfield "$st" state), $(pfield "$st" dailyUsed) ms used today, limits $saved)"
  else
    echo "  (parental: could not put back today's counters — was $(pfield "$before" state), is $(pfield "$st" state); a reset is: --ez parental_reset true)"
  fi
  if [ "$made_pin" = 1 ]; then
    config --ez parental_clear_pin true >/dev/null
    echo "  (parental: the password this run set was removed again; the TV has none, as before)"
  fi
}

# After a PASS: whether Aerial is still in front $1 seconds later. Some firmwares stop third-party
# apps on Home, Aerial too, a second after the guard brought it back (Dangbei H3S, issue #849).
stays() { sleep "$1"; [ "$(fg)" = "$PKG" ]; }

# Polls the foreground for up to $1 seconds; prints "<seconds> <package>" for the first time Aerial
# is in front, or "- <package>" when it never is.
wait_aerial() {
  local start end p
  start=$(now); end=$(perl -e "print $start + $1")
  while :; do
    p=$(fg)
    if [ "$p" = "$PKG" ]; then perl -e "printf '%.1f', $(now) - $start"; echo " $p"; return 0; fi
    perl -e "exit($(now) < $end ? 0 : 1)" || { echo "- $p"; return 1; }
    sleep 0.5
  done
}

MODEL=$(sh_ getprop ro.product.model)
VERSION=$(sh_ dumpsys package "$PKG" | grep -m1 versionName | sed 's/.*versionName=//')
[ -n "$VERSION" ] || { echo "Aerial ($PKG) is not installed on this TV." >&2; exit 1; }
SLUG=$(printf '%s' "${MODEL:-tv}" | tr -c 'A-Za-z0-9._-' '_')
OUT="aerial-selftest-$SLUG.md"
RESULTS=()
result() { # name, PASS|FAIL|SKIP, detail
  printf '%-18s %-4s %s\n' "$1" "$2" "$3"
  RESULTS+=("$(printf '"%s":{"result":"%s","detail":"%s"}' "$1" "$(printf '%s' "$2" | tr 'A-Z' 'a-z')" "$(printf '%s' "$3" | sed 's/["\\]/ /g')")")
}

{
echo "### Aerial self-test"
echo
echo "$MODEL, Android $(sh_ getprop ro.build.version.release), Aerial $VERSION, $(date '+%Y-%m-%d')"
echo
echo '```'

# 1. Home Guard bound: everything below depends on it where Aerial is not the default home.
ACC=$(sh_ settings get secure enabled_accessibility_services)
BOUND=$(sh_ dumpsys accessibility | grep -c -E "label=(Aerial|$PKG)")
# Android 6 and older have neither `cmd` nor `pm resolve-activity`; their "not found" and usage
# text are not a Home (issue #818).
HOME_TO=$(sh_ cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME -c android.intent.category.DEFAULT | grep -E '^[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+$' | tail -n1)
[ -n "$HOME_TO" ] || HOME_TO=$(sh_ pm resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME -c android.intent.category.DEFAULT | grep -E '^[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+$' | tail -n1)
[ -n "$HOME_TO" ] || HOME_TO="unknown (no cmd on this Android)"
if [ "${HOME_TO%%/*}" = "$PKG" ]; then result guard PASS "Aerial is the default home ($HOME_TO)"
elif [ "${BOUND:-0}" -gt 0 ]; then result guard PASS "Home Guard bound; default home is $HOME_TO"
elif printf '%s' "$ACC" | grep -q "$PKG/"; then result guard FAIL "Home Guard enabled but not bound; default home is $HOME_TO"
else result guard FAIL "Home Guard not enabled ($ACC); default home is $HOME_TO"
fi
# What Aerial needs to switch Home Guard on itself; an APK installed without install.sh has neither
# (issue #853), and Android 13+ restricts one installed from a file (issues #811 #829).
if [ "${HOME_TO%%/*}" != "$PKG" ] && [ "${BOUND:-0}" -eq 0 ]; then
  sh_ dumpsys package "$PKG" | grep -q "WRITE_SECURE_SETTINGS: granted=true" ||
    echo "  (WRITE_SECURE_SETTINGS is not granted: run install.sh repair, or the pm grant line of the install steps)"
  sh_ appops get "$PKG" ACCESS_RESTRICTED_SETTINGS | grep -q -E "deny|ignore" &&
    echo "  (ACCESS_RESTRICTED_SETTINGS is denied: adb shell appops set $PKG ACCESS_RESTRICTED_SETTINGS allow)"
fi

# 2. Home on Aerial itself.
sh_ am start -W -n "$PKG/.MainActivity" >/dev/null; sleep 2
sh_ input keyevent KEYCODE_HOME
read -r secs pkg <<<"$(wait_aerial 8)"
if [ "$secs" = "-" ]; then result home_on_aerial FAIL "in front after 8 s: $pkg"
elif stays 4; then result home_on_aerial PASS "Aerial in front"
else result home_on_aerial FAIL "Aerial in front, then $(fg) 4 s later"
fi
# Home Guard's own answer to the key can come a second or two after Aerial is already in front,
# and it would cover the next app (TCL MT5879).
sleep 3

# 3. Home from another app: the TV's settings, which every TV has.
sh_ am start -a android.settings.SETTINGS >/dev/null; sleep 3
OTHER=$(fg)
# Some TVs' settings are a panel over the current app rather than an activity of their own: an
# app from the launcher list instead, neither Aerial nor the TV's own launcher.
if [ "$OTHER" = "$PKG" ]; then
  APP=$(sh_ cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.LAUNCHER |
    grep "/" | grep -v -e "^ *$PKG/" -e "^ *${HOME_TO%%/*}/" | head -n1 | tr -d ' ')
  [ -n "$APP" ] && { sh_ am start -n "$APP" >/dev/null; sleep 4; OTHER=$(fg); }
fi
sh_ input keyevent KEYCODE_HOME
read -r secs pkg <<<"$(wait_aerial 12)"
if [ "$OTHER" = "$PKG" ]; then result home_from_app SKIP "neither the settings nor another app came to front"
elif [ "$secs" != "-" ] && stays 4; then result home_from_app PASS "from $OTHER, Aerial in front after ${secs} s"
elif [ "$secs" != "-" ]; then result home_from_app FAIL "from $OTHER, Aerial in front after ${secs} s, then $(fg) 4 s later"
else result home_from_app FAIL "from $OTHER, in front after 12 s: $pkg"
fi
sleep 3
# input keyevent does not go through Home Guard's key filter; on some TVs (Sony, Android 9) the
# remote's Home key behaves otherwise. A FAIL here is worth a try with the remote. Android also holds
# back app starts for 5 s after a Home key it handles itself, so about 5 s here is that, not the
# guard (TCL MT5879: the guard asks at once, Aerial starts 5 s later; the remote's key is not held).

# 4. Standby and wake.
if [ "${STANDBY:-0}" = 1 ]; then
  sh_ input keyevent KEYCODE_POWER; sleep 5
  WAKE=$(sh_ dumpsys power | grep -m1 -E "mWakefulness=" | sed 's/.*=//')
  echo "(standby: $WAKE; waiting 70 s)" >&2
  sleep 70
  if connect; then sh_ input keyevent KEYCODE_POWER; else
    # Xiaomi switches ADB off in standby; TCL MT5879's network sleeps.
    echo ">>> ADB does not answer in standby. Turn the TV on with the remote's power key; waiting up to 3 minutes." >&2
    for _ in $(seq 1 18); do connect && break; sleep 2; done
  fi
  if connect; then
    sleep 3
    read -r secs pkg <<<"$(wait_aerial 60)"
    [ "$secs" != "-" ] && result standby PASS "asleep=$WAKE, Aerial in front ${secs} s after wake" || result standby FAIL "asleep=$WAKE, in front 60 s after wake: $pkg"
  else result standby FAIL "ADB did not come back after wake; on Xiaomi, install with KEEP_ADB=1"
  fi
else result standby SKIP "STANDBY=1 to try"
fi

# 5. Restart: what is in front, second by second, for the first minute after boot completes.
if [ "${REBOOT:-0}" = 1 ]; then
  adb reboot </dev/null >/dev/null 2>&1
  echo "(restarting; waiting up to 4 minutes for ADB)" >&2
  sleep 20
  for _ in $(seq 1 18); do connect && [ "$(sh_ getprop sys.boot_completed)" = 1 ] && break; sleep 2; done
  if [ "$(sh_ getprop sys.boot_completed)" = 1 ]; then
    TIMELINE="" LAST="" START=$(now)
    for _ in $(seq 1 60); do
      p=$(fg); t=$(perl -e "printf '%.0f', $(now) - $START")
      [ "$p" != "$LAST" ] && TIMELINE="$TIMELINE ${t}s:${p:-none}" && LAST="$p"
      sleep 1
    done
    [ "$LAST" = "$PKG" ] && result reboot PASS "after boot:$TIMELINE" || result reboot FAIL "after boot:$TIMELINE"
  else result reboot FAIL "ADB did not come back within 4 minutes"
  fi
else result reboot SKIP "REBOOT=1 to try (ask the owner first)"
fi

# 6. Inputs, as Aerial lists them, with the TV's profile.
SOURCES=$(config --ez list_sources true)
if [ -z "$SOURCES" ]; then result inputs SKIP "Aerial older than 1.59 lists no inputs here"
elif [ "$SOURCES" = "[]" ]; then result inputs SKIP "Aerial finds no inputs on this TV"
else
  N=$(printf '%s' "$SOURCES" | grep -o '"id"' | wc -l | tr -d ' ')
  if [ "${INPUTS:-0}" = 1 ]; then
    DIR="aerial-selftest-$SLUG"; mkdir -p "$DIR"
    i=0
    printf '%s' "$SOURCES" | perl -MJSON::PP -e 'binmode STDOUT, ":utf8"; my $a = decode_json(join "", <STDIN>); print "$_->{id}\t$_->{label}\t", ($_->{shown} ? 1 : 0), "\n" for @$a' |
    while IFS="$(printf '\t')" read -r id label shown; do
      [ "$shown" = 1 ] || continue
      i=$((i + 1))
      opened=$(config --es open_source "$id")
      sleep 7
      adb exec-out screencap -p </dev/null > "$DIR/input-$i.png" 2>/dev/null
      echo "  input $i: $label ($id) -> ${opened#open_source: }; in front $(fg); screenshot $DIR/input-$i.png"
      sh_ am start -n "$PKG/.MainActivity" >/dev/null; sleep 2
    done
    result inputs PASS "$N listed; each opened, see the screenshots and tell which port each showed"
  else result inputs SKIP "$N listed; INPUTS=1 to open each"
  fi
fi

# 7. Parental control, last because it moves today's counters and the limits; it puts both back.
if [ "${PARENTAL:-0}" = 1 ]; then parental_checks
else result parental SKIP "PARENTAL=1 to try (needs a parental password on the TV)"
fi
echo '```'

echo
echo "**Inputs as Aerial lists them**"
echo '```json'
echo "${SOURCES:-[]}"
echo '```'
echo
echo "**Self-test result**"
echo '```json'
printf '{"model":"%s","aerial":"%s","checks":{%s}}\n' "$MODEL" "$VERSION" "$(IFS=,; echo "${RESULTS[*]}")"
echo '```'
} 2>&1 | tee "$OUT"

# Back on Aerial, as the TV was before.
sh_ am start -n "$PKG/.MainActivity" >/dev/null
echo >&2
echo "Saved to $OUT. Paste it with tools/report.sh's output into https://github.com/pengchujin/aerial-tv/issues/new?template=device.yml" >&2
