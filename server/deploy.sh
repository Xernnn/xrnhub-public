#!/bin/sh
# Installs or updates xrnhub on a home server reached over ssh (Termux with runit and Tailscale
# Funnel), next to the other xrn sync services. Safe to run again: an update replaces the program
# and restarts it, and leaves the data alone. It only ever reads the other services' data. The
# service's run script calls ~/.local/bin/sv-guard first, as every service's does, which the
# home-server repo's phone/install-sv-guard puts on the phone.
#
#     server/deploy.sh [ssh-host]      (default: homeserver)
set -eu

HOST=${1:-homeserver}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)

echo "Testing first..."
if ! (cd "$REPO/tests" && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_health test_ical test_daybook test_rules test_review test_monitor test_server test_api test_apps >/dev/null 2>&1); then
    echo "The tests failed, so nothing was changed. Run them for details:" >&2
    echo "    cd tests && python3 -m unittest test_health test_ical test_daybook test_rules test_review test_monitor test_server test_api test_apps" >&2
    exit 1
fi

echo "Copying it to $HOST..."
tar czf - --exclude=__pycache__ -C "$HERE" xrnhub.py hublib -C "$REPO" web | ssh "$HOST" '
    set -e
    mkdir -p ~/xrnhub/data
    chmod 700 ~/xrnhub ~/xrnhub/data
    cd ~/xrnhub
    rm -rf hublib web
    tar xzf -
    chmod 755 xrnhub.py
    # Termux Python finds no zone database; this gives calendars their zones.
    pip install --quiet --disable-pip-version-check tzdata || echo "tzdata not installed: unusual time zones read as local"
'

echo "Setting up the service..."
ssh "$HOST" '
    set -e
    S=$PREFIX/var/service
    export SVDIR=$S
    if [ -d "$S/xrnhub" ]; then
        sv restart xrnhub
        exit 0
    fi
    # Built under a dot name, which runit ignores, so it never starts half written.
    T=$S/.xrnhub.new
    rm -rf "$T"
    mkdir -p "$T/log"
    cat > "$T/run" <<EOS
#!$PREFIX/bin/sh
# xrnhub: one page over every xrn app (see ~/xrnhub and the home-server README).
# Login is ~/.config/xrn-sync/users, shared with the other sync services.
$HOME/.local/bin/sv-guard 5238 $HOME/xrnhub/xrnhub.py || exit 1
exec python $HOME/xrnhub/xrnhub.py \\
  --port 5238 --data $HOME/xrnhub/data \\
  --users $HOME/.config/xrn-sync/users --home $HOME 2>&1
EOS
    # Written out, not copied from a sibling service: a fresh phone has none to copy.
    printf "#!%s/bin/sh\nmkdir -p \"%s/var/log/sv/xrnhub\"\nexec svlogd -tt \"%s/var/log/sv/xrnhub\"\n" \
        "$PREFIX" "$PREFIX" "$PREFIX" > "$T/log/run"
    chmod 755 "$T/run" "$T/log/run"
    chmod 700 "$T" "$T/log"
    mv "$T" "$S/xrnhub"
'

echo "Waiting for it to answer..."
tries=0
until [ "$(ssh "$HOST" 'curl -s -m 3 http://127.0.0.1:5238/health' || true)" = ok ]; do
    tries=$((tries + 1))
    if [ "$tries" -ge 8 ]; then
        echo "It did not start. Its log:" >&2
        ssh "$HOST" 'tail -20 $PREFIX/var/log/sv/xrnhub/current' >&2 || true
        exit 1
    fi
    sleep 2
done

# `funnel`, never `serve`: serve rewrites the port as tailnet-only and takes the calendar offline.
echo "Publishing /xrnhub..."
ssh "$HOST" 'tailscale funnel --bg --set-path /xrnhub http://127.0.0.1:5238 >/dev/null; tailscale funnel status'

echo "Done."
