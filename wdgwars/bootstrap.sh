#!/bin/sh
# CRLF self-heal (MUST stay on ONE line — if the file was uploaded from
# Windows, every line has \r at EOL; a single-line test works because the
# trailing \r is parsed as whitespace at the end of the whole command).
tr -d '\r' <"$0" >/tmp/_wdgwars_bs.sh 2>/dev/null; [ -s /tmp/_wdgwars_bs.sh ] && ! cmp -s /tmp/_wdgwars_bs.sh "$0" && { chmod +x /tmp/_wdgwars_bs.sh; exec sh /tmp/_wdgwars_bs.sh "$@"; }

# Run once on the pager to fetch pagerctl bindings from the wifman payload
# and ensure runtime dependencies are installed.

PAYLOAD_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB="$PAYLOAD_DIR/lib"
WIFMAN_LIB="/root/payloads/user/reconnaissance/wifman/lib"

mkdir -p "$LIB"

echo "[bootstrap] pagerctl bindings"
if [ -f "$WIFMAN_LIB/pagerctl.py" ] && [ -f "$WIFMAN_LIB/libpagerctl.so" ]; then
    cp "$WIFMAN_LIB/pagerctl.py"    "$LIB/"
    cp "$WIFMAN_LIB/libpagerctl.so" "$LIB/"
    echo "  copied from $WIFMAN_LIB"
else
    WIFMAN_BASE="https://raw.githubusercontent.com/LOCOSP/pineapple_pager_wifman/main/wifman/lib"
    for f in pagerctl.py libpagerctl.so; do
        if [ ! -f "$LIB/$f" ]; then
            wget -q -O "$LIB/$f.tmp" "$WIFMAN_BASE/$f" && mv "$LIB/$f.tmp" "$LIB/$f" \
                && echo "  downloaded $f" \
                || echo "  warn: download of $f failed (pager offline?)"
        fi
    done
fi

echo "[bootstrap] opkg runtime packages (best-effort, needs internet)"
opkg update >/dev/null 2>&1 || true
for pkg in iw bluez-utils kmod-usb-acm; do
    opkg list-installed 2>/dev/null | grep -q "^$pkg " \
        || opkg install "$pkg" >/dev/null 2>&1 \
        || echo "  warn: could not install $pkg"
done

echo "[bootstrap] loading cdc_acm for u-blox"
modprobe cdc_acm 2>/dev/null || true

mkdir -p /mmc/root/loot/wdgwars/sessions

chmod +x "$PAYLOAD_DIR/payload.sh" 2>/dev/null
chmod +x "$PAYLOAD_DIR"/launch_*.sh 2>/dev/null

echo "[bootstrap] done"
