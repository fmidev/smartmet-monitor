# SmartMet access-log & live-stats visualizer
#
# Source this file from ~/.bashrc to get:
#   bstat    - dashboard (table + sparklines + summary)
#   bchart   - btop-style vertical chart for one metric over time
#   burls    - top N slowest / heaviest / most-requested URLs
#   bstatus  - HTTP status code distribution
#   bkeys    - top API keys
#
#   bmon     - live dashboard polling the admin plugin (cache + service stats)
#
# All log functions read stdin or a file passed as the last argument.
# Interval flag -i picks the time bucket width:
#   1s, 10s, 1m, 10m, 1h, 1d           (default: 1h)
#
# Log format (from spine/AccessLogger.cpp):
#   IP - - [END_TIME] "METHOD URL HTTP/VER" STATUS [START_TIME] DUR_MS BYTES ETAG APIKEY
# Space-separated; start-time at $9, duration-ms at $10, bytes at $11, status at $8.

# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------

# Convert an interval name to the number of ISO-8601 chars to keep as bucket key.
_bstat_prec() {
    case "$1" in
        1s)  echo 19 ;;  # 2026-04-23T03:00:00
        10s) echo 18 ;;  # 2026-04-23T03:00:0
        1m)  echo 16 ;;  # 2026-04-23T03:00
        10m) echo 15 ;;  # 2026-04-23T03:0
        1h)  echo 13 ;;  # 2026-04-23T03
        1d)  echo 10 ;;  # 2026-04-23
        *)   return 1 ;;
    esac
}

# Parse common flags ("-i INTERVAL", "-w WIDTH") out of the argument list.
# Writes BSTAT_INTERVAL, BSTAT_WIDTH, BSTAT_ARGS into the environment.
_bstat_parse() {
    BSTAT_INTERVAL=1h
    BSTAT_WIDTH=20
    BSTAT_ARGS=()
    while (( $# )); do
        case "$1" in
            -i) BSTAT_INTERVAL="$2"; shift 2 ;;
            -w) BSTAT_WIDTH="$2"; shift 2 ;;
            -h|--help) BSTAT_INTERVAL=_help; shift ;;
            *)  BSTAT_ARGS+=("$1"); shift ;;
        esac
    done
}

# -----------------------------------------------------------------------------
# bstat: dashboard
# -----------------------------------------------------------------------------
# Usage: bstat [-i 1h] [-w 20] [log-file]
#
# Columns per time bucket:
#   time | reqs | avg_ms | max_ms | avg_KB | MB_out | err% | 4 horizontal bars
# Plus a total row and four horizontal sparklines beneath the table.

bstat() {
    _bstat_parse "$@"
    if [ "$BSTAT_INTERVAL" = _help ]; then
        cat <<EOF
Usage: bstat [-i INTERVAL] [-w WIDTH] [LOG-FILE]

  -i INTERVAL   bucket width: 1s, 10s, 1m, 10m, 1h, 1d  (default: 1h)
  -w WIDTH      horizontal bar width in columns         (default: 20)

Reads stdin if no file is given.
EOF
        return 0
    fi

    local prec
    if ! prec=$(_bstat_prec "$BSTAT_INTERVAL"); then
        echo "bstat: unknown interval '$BSTAT_INTERVAL' (use 1s|10s|1m|10m|1h|1d)" >&2
        return 1
    fi

    gawk -v PREC="$prec" -v BW="$BSTAT_WIDTH" '
    BEGIN {
        # partial-block chars (1/8 .. 7/8 of a full block)
        p8[1]="▏"; p8[2]="▎"; p8[3]="▍"; p8[4]="▌"
        p8[5]="▋"; p8[6]="▊"; p8[7]="▉"
        # spark blocks (height 0..8)
        sp[0]=" "; sp[1]="▁"; sp[2]="▂"; sp[3]="▃"; sp[4]="▄"
        sp[5]="▅"; sp[6]="▆"; sp[7]="▇"; sp[8]="█"
    }
    {
        # $9 is "[2026-04-23T07:00:00.125]" — strip the leading [.
        t = substr($9, 2, PREC)
        dur = $10 + 0
        bytes = $11 + 0
        status = $8 + 0

        count[t]++
        sumdur[t] += dur
        sumbytes[t] += bytes
        if (dur > maxdur[t]) maxdur[t] = dur
        if (status >= 400) errors[t]++

        tot_count++
        tot_dur += dur
        tot_bytes += bytes
        if (status >= 400) tot_err++
    }
    END {
        # gather keys sorted by time (lexicographic == chronological for ISO-8601).
        # NOTE: gawks asort() re-indexes starting at 1.
        k = 0
        for (t in count) tmp[++k] = t
        n = asort(tmp, keys)

        # per-metric max (for bar scaling)
        for (i=1; i<=n; i++) {
            t = keys[i]
            if (count[t] > gc) gc = count[t]
            a = sumdur[t] / count[t]
            if (a > gd) gd = a
            ab = sumbytes[t] / count[t]
            if (ab > gb) gb = ab
            if (sumbytes[t] > gB) gB = sumbytes[t]
        }

        # title
        printf "┌─ SmartMet access-log summary  (bucket: %s, %d rows) ─┐\n",
            iname(PREC), n
        printf "│\n"
        # header
        bwL = BW
        printf "│ %-*s  %7s %8s %7s %8s %8s %5s  %-*s  %-*s  %-*s  %-*s\n",
            PREC, "time", "reqs", "avg_ms", "max_ms", "avg_KB", "MB_out", "err%",
            bwL, "requests", bwL, "latency", bwL, "size", bwL, "bandwidth"
        # separator
        printf "│ "
        for (i=0; i<PREC; i++) printf "─"
        printf "  %7s %8s %7s %8s %8s %5s  ", "───────", "────────", "───────", "────────", "────────", "─────"
        for (i=0; i<bwL; i++) printf "─"
        printf "  "
        for (i=0; i<bwL; i++) printf "─"
        printf "  "
        for (i=0; i<bwL; i++) printf "─"
        printf "  "
        for (i=0; i<bwL; i++) printf "─"
        printf "\n"

        # rows
        for (i=1; i<=n; i++) {
            t = keys[i]
            avgd = sumdur[t] / count[t]
            avgb = sumbytes[t] / count[t]
            err_pct = (errors[t] ? errors[t] : 0) / count[t] * 100

            b1 = vbar(count[t], gc, bwL)
            b2 = vbar(avgd, gd, bwL)
            b3 = vbar(avgb, gb, bwL)
            b4 = vbar(sumbytes[t], gB, bwL)

            printf "│ %-*s  %7d %8.1f %7d %8.1f %8.2f %5.1f  %s  %s  %s  %s\n",
                PREC, t,
                count[t],
                avgd,
                maxdur[t],
                avgb/1024,
                sumbytes[t]/1048576,
                err_pct,
                b1, b2, b3, b4
        }

        # separator
        printf "│ "
        for (i=0; i<PREC; i++) printf "─"
        printf "  %7s %8s %7s %8s %8s %5s\n",
            "───────", "────────", "───────", "────────", "────────", "─────"

        if (tot_count > 0) {
            printf "│ %-*s  %7d %8.1f %7s %8.1f %8.2f %5.1f\n",
                PREC, "TOTAL",
                tot_count,
                tot_dur / tot_count,
                "-",
                (tot_bytes / tot_count) / 1024,
                tot_bytes / 1048576,
                tot_err / tot_count * 100
        }

        # Sparklines (one char per bucket, 0..8 block heights).
        printf "│\n"
        printf "│  requests   "; spark(count, keys, n, gc); printf "\n"
        printf "│  latency    "; sparkavg(sumdur, count, keys, n);    printf "\n"
        printf "│  avg_size   "; sparkavg(sumbytes, count, keys, n);  printf "\n"
        printf "│  bandwidth  "; spark(sumbytes, keys, n, gB); printf "\n"
        printf "│\n"

        # time axis: print first and last timestamp under the sparklines.
        if (n > 0) {
            pad = n - length(keys[1]) - length(keys[n])
            if (pad < 1) pad = 1
            printf "│             %s", keys[1]
            for (i=0; i<pad; i++) printf " "
            printf "%s\n", keys[n]
        }
        printf "└" ; for (i=0; i<72; i++) printf "─"; printf "\n"
    }

    # Horizontal bar 0..width columns wide using eighth-block partials.
    function vbar(val, maxval, width,   full, p, s, i, eighths, visual) {
        if (maxval <= 0) {
            s = ""
            for (i=0; i<width; i++) s = s " "
            return s
        }
        ratio = val / maxval
        if (ratio > 1) ratio = 1
        if (ratio < 0) ratio = 0
        eighths = int(ratio * width * 8 + 0.5)
        full = int(eighths / 8)
        p = eighths - full * 8
        s = ""
        for (i=0; i<full; i++) s = s "█"
        visual = full
        if (p > 0 && visual < width) { s = s p8[p]; visual++ }
        while (visual < width) { s = s " "; visual++ }
        return s
    }

    # Vertical-spark line: one char per bucket, height 0..8 scaled to max.
    # Keys array is 1..m (output of asort).
    function spark(vals, ks, m, mx,   i, r, lvl) {
        if (mx <= 0) {
            for (i=1; i<=m; i++) printf " "
            return
        }
        for (i=1; i<=m; i++) {
            r = vals[ks[i]] / mx
            if (r > 1) r = 1
            lvl = int(r * 8 + 0.5)
            printf "%s", sp[lvl]
        }
    }

    # Sparkline for mean-per-bucket (numerator/denominator per bucket).
    function sparkavg(num, den, ks, m,   i, mx, v) {
        mx = 0
        for (i=1; i<=m; i++) { v = num[ks[i]]/den[ks[i]]; if (v > mx) mx = v }
        if (mx <= 0) { for (i=1; i<=m; i++) printf " "; return }
        for (i=1; i<=m; i++) {
            v = num[ks[i]] / den[ks[i]] / mx
            if (v > 1) v = 1
            printf "%s", sp[int(v * 8 + 0.5)]
        }
    }

    function iname(p) {
        if (p==19) return "1s"
        if (p==18) return "10s"
        if (p==16) return "1m"
        if (p==15) return "10m"
        if (p==13) return "1h"
        if (p==10) return "1d"
        return "?"
    }
    ' "${BSTAT_ARGS[@]}"
}

# -----------------------------------------------------------------------------
# bchart: vertical btop-style chart for one metric
# -----------------------------------------------------------------------------
# Usage: bchart [-i 1h] [-m metric] [-h height] [-w cell-width] [log-file]
#   metric = reqs | ms | kb | mb | err   (default reqs)

bchart() {
    local interval=1h
    local metric=reqs
    local height=12
    local cellw=2
    local args=()
    while (( $# )); do
        case "$1" in
            -i) interval="$2"; shift 2 ;;
            -m) metric="$2";  shift 2 ;;
            -h) height="$2";  shift 2 ;;
            -w) cellw="$2";   shift 2 ;;
            --help)
                cat <<EOF
Usage: bchart [-i INTERVAL] [-m METRIC] [-h HEIGHT] [-w CELLW] [LOG-FILE]

  -i INTERVAL   1s, 10s, 1m, 10m, 1h, 1d    (default: 1h)
  -m METRIC     reqs | ms | kb | mb | err   (default: reqs)
                  reqs = requests per bucket
                  ms   = mean latency (ms)
                  kb   = mean response size (KB)
                  mb   = total bandwidth (MB)
                  err  = error rate (%)
  -h HEIGHT     chart height in rows        (default: 12)
  -w CELLW      columns per bucket          (default: 2)
EOF
                return 0 ;;
            *) args+=("$1"); shift ;;
        esac
    done

    local prec
    if ! prec=$(_bstat_prec "$interval"); then
        echo "bchart: unknown interval '$interval'" >&2
        return 1
    fi

    gawk -v PREC="$prec" -v METRIC="$metric" -v H="$height" -v CW="$cellw" '
    BEGIN {
        sp[0]=" "; sp[1]="▁"; sp[2]="▂"; sp[3]="▃"; sp[4]="▄"
        sp[5]="▅"; sp[6]="▆"; sp[7]="▇"; sp[8]="█"
    }
    {
        t = substr($9, 2, PREC)
        dur = $10 + 0
        bytes = $11 + 0
        status = $8 + 0
        count[t]++
        sumdur[t] += dur
        sumbytes[t] += bytes
        if (status >= 400) errors[t]++
    }
    END {
        k = 0
        for (t in count) tmp[++k] = t
        n = asort(tmp, keys)

        # compute chosen metric per bucket (index v[1..n])
        for (i=1; i<=n; i++) {
            t = keys[i]
            if      (METRIC == "reqs") v[i] = count[t]
            else if (METRIC == "ms")   v[i] = sumdur[t] / count[t]
            else if (METRIC == "kb")   v[i] = sumbytes[t] / count[t] / 1024
            else if (METRIC == "mb")   v[i] = sumbytes[t] / 1048576
            else if (METRIC == "err")  v[i] = (errors[t] ? errors[t] : 0) / count[t] * 100
            else { print "bchart: unknown metric "METRIC > "/dev/stderr"; exit 2 }
            if (v[i] > mx) mx = v[i]
        }
        if (mx <= 0) mx = 1

        label = label_for(METRIC)
        # title + scale legend
        printf "┌─ %s by %s  (max %.2f) ─\n", label, intname(PREC), mx

        # y-axis label width
        lw = 8
        # each row top..bottom
        for (row = H-1; row >= 0; row--) {
            # y tick label
            tick = mx * (row + 1) / H
            printf "│ %*s ", lw, fmtnum(tick)
            for (i=1; i<=n; i++) {
                r = v[i] / mx
                eighths = int(r * H * 8 + 0.5)
                full_rows = int(eighths / 8)
                partial = eighths - full_rows * 8
                if (row < full_rows) { cell = "█" }
                else if (row == full_rows && partial > 0) { cell = sp[partial] }
                else { cell = " " }
                for (c=0; c<CW; c++) printf "%s", cell
            }
            printf "\n"
        }
        # x axis
        printf "│ %*s ", lw, ""
        for (i=0; i<n*CW; i++) printf "─"
        printf "\n"

        # x-axis ticks: first, middle, last
        if (n > 0) {
            printf "│ %*s ", lw, ""
            total = n * CW
            s = sprintf("%*s", total, "")
            mid = int((n+1)/2)
            s = place(s, keys[1], 0)
            if (n > 2) s = place(s, keys[mid], int(total/2 - length(keys[mid])/2))
            if (n > 1) s = place(s, keys[n], total - length(keys[n]))
            printf "%s\n", s
        }
        printf "└" ; for (i=0; i<72; i++) printf "─" ; printf "\n"
    }

    function label_for(m) {
        if (m=="reqs") return "Requests"
        if (m=="ms")   return "Mean latency (ms)"
        if (m=="kb")   return "Mean response size (KB)"
        if (m=="mb")   return "Bandwidth (MB out)"
        if (m=="err")  return "Error rate (%)"
        return m
    }
    function intname(p) {
        if (p==19) return "1s"; if (p==18) return "10s"
        if (p==16) return "1m"; if (p==15) return "10m"
        if (p==13) return "1h"; if (p==10) return "1d"
        return "?"
    }
    function fmtnum(x) {
        if (x >= 1000000) return sprintf("%.1fM", x/1000000)
        if (x >= 10000)   return sprintf("%.0fk",  x/1000)
        if (x >= 1000)    return sprintf("%.1fk",  x/1000)
        if (x >= 10)      return sprintf("%.0f",   x)
        return sprintf("%.2f", x)
    }
    function place(s, lbl, pos,   n,left,right) {
        if (pos < 0) pos = 0
        if (pos + length(lbl) > length(s)) pos = length(s) - length(lbl)
        left  = substr(s, 1, pos)
        right = substr(s, pos + length(lbl) + 1)
        return left lbl right
    }
    ' "${args[@]}"
}

# -----------------------------------------------------------------------------
# burls: top N URLs by various metrics
# -----------------------------------------------------------------------------
# Usage: burls [-n 20] [-s reqs|ms|kb|mb] [log-file]

burls() {
    local top=20
    local sort_by=ms
    local args=()
    while (( $# )); do
        case "$1" in
            -n) top="$2"; shift 2 ;;
            -s) sort_by="$2"; shift 2 ;;
            --help)
                cat <<EOF
Usage: burls [-n N] [-s SORT] [LOG-FILE]

  -n N     show top N URLs         (default: 20)
  -s SORT  reqs | ms | kb | mb     (default: ms)
             reqs = request count
             ms   = total time spent (ms)
             kb   = mean response size (KB)
             mb   = total bandwidth (MB)
EOF
                return 0 ;;
            *) args+=("$1"); shift ;;
        esac
    done

    gawk -v TOP="$top" -v SORT="$sort_by" '
    BEGIN {
        p8[1]="▏"; p8[2]="▎"; p8[3]="▍"; p8[4]="▌"
        p8[5]="▋"; p8[6]="▊"; p8[7]="▉"
    }
    {
        url = $6
        # strip query string to group by endpoint path
        q = index(url, "?")
        if (q > 0) url = substr(url, 1, q-1)
        dur = $10 + 0
        bytes = $11 + 0

        count[url]++
        sumdur[url]  += dur
        sumbytes[url] += bytes
    }
    END {
        n = 0
        for (u in count) {
            urls[n] = u
            if (SORT == "reqs") key[n] = count[u]
            else if (SORT == "ms") key[n] = sumdur[u]
            else if (SORT == "kb") key[n] = sumbytes[u] / count[u]
            else if (SORT == "mb") key[n] = sumbytes[u]
            else { print "burls: bad sort" > "/dev/stderr"; exit 2 }
            n++
        }

        # simple O(N*TOP) selection for top-TOP entries
        printed = 0
        gmax = 0
        for (k=0; k<n; k++) if (key[k] > gmax) gmax = key[k]

        # Partition: repeatedly extract max
        for (r=0; r<TOP && r<n; r++) {
            best = -1; bv = -1
            for (k=0; k<n; k++) if (!taken[k] && key[k] > bv) { bv = key[k]; best = k }
            if (best < 0) break
            taken[best] = 1
            u = urls[best]

            avgd = sumdur[u] / count[u]
            avgb = sumbytes[u] / count[u]
            totm = sumbytes[u] / 1048576

            if (r == 0) {
                printf "┌─ Top %d URLs by %s ─\n", TOP, SORT
                printf "│ %7s %9s %9s %8s  %-20s  %s\n",
                    "reqs", "avg_ms", "avg_KB", "MB_out", "bar", "path"
                printf "│ %7s %9s %9s %8s  %-20s  %s\n",
                    "───────","─────────","─────────","────────","────────────────────","────"
            }
            printf "│ %7d %9.1f %9.1f %8.2f  %s  %s\n",
                count[u], avgd, avgb/1024, totm,
                vbar(key[best], gmax, 20),
                u
        }
        printf "└" ; for (i=0; i<72; i++) printf "─" ; printf "\n"
    }
    function vbar(val, maxval, width,   full, p, s, i, eighths, visual) {
        if (maxval <= 0) return sprintf("%*s", width, "")
        ratio = val / maxval
        if (ratio > 1) ratio = 1
        eighths = int(ratio * width * 8 + 0.5)
        full = int(eighths / 8)
        p = eighths - full * 8
        s = ""
        for (i=0; i<full; i++) s = s "█"
        visual = full
        if (p > 0 && visual < width) { s = s p8[p]; visual++ }
        while (visual < width) { s = s " "; visual++ }
        return s
    }
    ' "${args[@]}"
}

# -----------------------------------------------------------------------------
# bstatus: HTTP status code distribution
# -----------------------------------------------------------------------------

bstatus() {
    if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        cat <<EOF
Usage: bstatus [LOG-FILE]

Print HTTP status-code distribution and per-class breakdown, with
horizontal Unicode bars. Reads stdin if no file is given.
EOF
        return 0
    fi
    gawk '
    BEGIN {
        p8[1]="▏"; p8[2]="▎"; p8[3]="▍"; p8[4]="▌"
        p8[5]="▋"; p8[6]="▊"; p8[7]="▉"
    }
    {
        s = $8 + 0
        cnt[s]++
        tot++
        cls = int(s/100) * 100
        clscnt[cls]++
    }
    END {
        printf "┌─ HTTP status distribution  (%d requests) ─\n", tot
        printf "│ %5s %8s %6s  %-30s\n", "code", "count", "pct", "bar"

        k = 0
        for (c in cnt) tmp1[++k] = c + 0
        n = asort(tmp1, keys)
        mx = 0
        for (i=1; i<=n; i++) if (cnt[keys[i]] > mx) mx = cnt[keys[i]]

        for (i=1; i<=n; i++) {
            c = keys[i]
            printf "│ %5d %8d %5.1f%%  %s\n",
                c, cnt[c], cnt[c]/tot*100, vbar(cnt[c], mx, 30)
        }
        printf "│\n│  by class:\n"
        k = 0
        for (c in clscnt) tmp2[++k] = c + 0
        m = asort(tmp2, ckeys)
        for (i=1; i<=m; i++) {
            c = ckeys[i]
            printf "│   %dxx: %8d  %5.1f%%  %s\n",
                c/100, clscnt[c], clscnt[c]/tot*100, vbar(clscnt[c], tot, 40)
        }
        printf "└" ; for (i=0; i<72; i++) printf "─" ; printf "\n"
    }
    function vbar(val, maxval, width,   full, p, s, i, eighths, visual) {
        if (maxval <= 0) return sprintf("%*s", width, "")
        ratio = val / maxval
        if (ratio > 1) ratio = 1
        eighths = int(ratio * width * 8 + 0.5)
        full = int(eighths / 8)
        p = eighths - full * 8
        s = ""
        for (i=0; i<full; i++) s = s "█"
        visual = full
        if (p > 0 && visual < width) { s = s p8[p]; visual++ }
        while (visual < width) { s = s " "; visual++ }
        return s
    }
    ' "$@"
}

# -----------------------------------------------------------------------------
# bkeys: top API keys by request count and bandwidth
# -----------------------------------------------------------------------------
# Usage: bkeys [-n 20] [-s reqs|ms|mb] [log-file]

bkeys() {
    local top=20
    local sort_by=reqs
    local args=()
    while (( $# )); do
        case "$1" in
            -n) top="$2"; shift 2 ;;
            -s) sort_by="$2"; shift 2 ;;
            -h|--help)
                cat <<EOF
Usage: bkeys [-n N] [-s SORT] [LOG-FILE]

  -n N     show top N API keys       (default: 20)
  -s SORT  reqs | ms | mb            (default: reqs)
EOF
                return 0 ;;
            *) args+=("$1"); shift ;;
        esac
    done

    gawk -v TOP="$top" -v SORT="$sort_by" '
    BEGIN {
        p8[1]="▏"; p8[2]="▎"; p8[3]="▍"; p8[4]="▌"
        p8[5]="▋"; p8[6]="▊"; p8[7]="▉"
    }
    {
        # APIKEY is the last field, but some requests have none ("-")
        k = $NF
        dur = $10 + 0
        bytes = $11 + 0
        count[k]++
        sumdur[k]  += dur
        sumbytes[k] += bytes
    }
    END {
        n = 0
        for (k in count) {
            keys[n] = k
            if      (SORT == "reqs") val[n] = count[k]
            else if (SORT == "ms")   val[n] = sumdur[k]
            else if (SORT == "mb")   val[n] = sumbytes[k]
            else { print "bkeys: bad sort" > "/dev/stderr"; exit 2 }
            n++
        }
        gmax = 0
        for (i=0; i<n; i++) if (val[i] > gmax) gmax = val[i]

        for (r=0; r<TOP && r<n; r++) {
            best = -1; bv = -1
            for (i=0; i<n; i++) if (!taken[i] && val[i] > bv) { bv = val[i]; best = i }
            if (best < 0) break
            taken[best] = 1
            k = keys[best]
            avgd = sumdur[k] / count[k]
            totm = sumbytes[k] / 1048576
            if (r == 0) {
                printf "┌─ Top %d API keys by %s ─\n", TOP, SORT
                printf "│ %9s %8s %9s  %-20s  %s\n", "reqs","avg_ms","MB_out","bar","apikey"
                printf "│ %9s %8s %9s  %-20s  %s\n",
                    "─────────","────────","─────────","────────────────────","──────"
            }
            printf "│ %9d %8.1f %9.2f  %s  %s\n",
                count[k], avgd, totm, vbar(val[best], gmax, 20), k
        }
        printf "└" ; for (i=0; i<72; i++) printf "─" ; printf "\n"
    }
    function vbar(val, maxval, width,   full, p, s, i, eighths, visual) {
        if (maxval <= 0) return sprintf("%*s", width, "")
        ratio = val / maxval
        if (ratio > 1) ratio = 1
        eighths = int(ratio * width * 8 + 0.5)
        full = int(eighths / 8)
        p = eighths - full * 8
        s = ""
        for (i=0; i<full; i++) s = s "█"
        visual = full
        if (p > 0 && visual < width) { s = s p8[p]; visual++ }
        while (visual < width) { s = s " "; visual++ }
        return s
    }
    ' "${args[@]}"
}

# -----------------------------------------------------------------------------
# bmon: live dashboard polling the SmartMet admin plugin
# -----------------------------------------------------------------------------
# Usage: bmon [-u http://host:port/admin] [-n interval] [-v view]
#   -u URL      admin endpoint             (default: http://localhost:8080/admin)
#   -n SECONDS  refresh interval           (default: 2)
#   -v VIEW     cache | service | all      (default: all)
#
# Dependencies: curl, jq, gawk.
# Exits on Ctrl-C; clears the screen between frames.

bmon() {
    local url="http://localhost:8080/admin"
    local interval=2
    local view=all
    while (( $# )); do
        case "$1" in
            -u) url="$2"; shift 2 ;;
            -n) interval="$2"; shift 2 ;;
            -v) view="$2"; shift 2 ;;
            --help)
                cat <<EOF
Usage: bmon [-u URL] [-n SECONDS] [-v VIEW]

  -u URL      admin endpoint      (default: http://localhost:8080/admin)
  -n SECONDS  refresh interval    (default: 2)
  -v VIEW     cache | service | all  (default: all)
EOF
                return 0 ;;
            *) echo "bmon: unknown arg '$1'" >&2; return 1 ;;
        esac
    done

    command -v curl >/dev/null || { echo "bmon: curl is required" >&2; return 1; }
    command -v jq   >/dev/null || { echo "bmon: jq is required"   >&2; return 1; }

    # clean up on exit
    trap 'tput cnorm 2>/dev/null; echo' INT TERM EXIT
    tput civis 2>/dev/null

    while :; do
        clear
        printf "┌─ SmartMet live monitor  %s  (%s, refresh %ds) ─┐\n" \
            "$(date '+%F %T')" "$url" "$interval"

        if [ "$view" = cache ] || [ "$view" = all ]; then
            _bmon_cache "$url"
        fi
        if [ "$view" = service ] || [ "$view" = all ]; then
            _bmon_service "$url"
        fi
        echo "└ Ctrl-C to exit ─"
        sleep "$interval"
    done
}

_bmon_cache() {
    local url=$1
    local json
    json=$(curl -s --max-time 5 "$url?what=cachestats&format=json") || {
        echo "│ cache: (curl failed)"; return
    }
    [ -z "$json" ] && { echo "│ cache: (empty response)"; return; }

    # Parse JSON rows with jq, then render a table with UTF-8 hit-rate bars.
    echo "$json" | jq -r '.[] | [.cache_name, .size, .maxsize, .hits, .misses, .hitrate, ."hits/min", ."inserts/min"] | @tsv' 2>/dev/null | \
    gawk -F'\t' '
    BEGIN {
        p8[1]="▏"; p8[2]="▎"; p8[3]="▍"; p8[4]="▌"
        p8[5]="▋"; p8[6]="▊"; p8[7]="▉"
        printf "│\n│ Caches\n"
        printf "│ %-34s %8s %8s %9s %9s %6s  %-20s\n",
            "name","size","max","hits/min","ins/min","hit%","hitrate"
        printf "│ %-34s %8s %8s %9s %9s %6s  %-20s\n",
            "────────────────","────────","────────","─────────","─────────","──────","────────────────────"
    }
    {
        name = $1; size = $2+0; max = $3+0
        hitrate = $6+0
        hpm = $7+0; ipm = $8+0
        printf "│ %-34s %8d %8d %9.1f %9.1f %6.2f  %s\n",
            substr(name,1,34), size, max, hpm, ipm, hitrate, vbar(hitrate, 100, 20)
    }
    function vbar(val, maxval, width,   full, p, s, i, eighths, visual) {
        if (maxval <= 0) return sprintf("%*s", width, "")
        ratio = val / maxval
        if (ratio > 1) ratio = 1
        eighths = int(ratio * width * 8 + 0.5)
        full = int(eighths / 8)
        p = eighths - full * 8
        s = ""
        for (i=0; i<full; i++) s = s "█"
        visual = full
        if (p > 0 && visual < width) { s = s p8[p]; visual++ }
        while (visual < width) { s = s " "; visual++ }
        return s
    }'
}

_bmon_service() {
    local url=$1
    local json
    json=$(curl -s --max-time 5 "$url?what=servicestats&format=json") || {
        echo "│ service: (curl failed)"; return
    }
    [ -z "$json" ] && { echo "│ service: (empty response)"; return; }

    echo "$json" | jq -r '.[] | [.Handler, .LastMinute, .LastHour, .Last24Hours, .AverageDuration] | @tsv' 2>/dev/null | \
    gawk -F'\t' '
    BEGIN {
        p8[1]="▏"; p8[2]="▎"; p8[3]="▍"; p8[4]="▌"
        p8[5]="▋"; p8[6]="▊"; p8[7]="▉"
        printf "│\n│ Services  (req/min, req/h, req/day, mean ms)\n"
        printf "│ %-40s %7s %7s %9s %8s  %-20s\n",
            "handler","last1m","last1h","last24h","avg_ms","last_min"
        printf "│ %-40s %7s %7s %9s %8s  %-20s\n",
            "────────────────────","───────","───────","─────────","────────","────────────────────"
    }
    {
        # buffer rows to find max for bar scaling
        h[NR]=$1; m1[NR]=$2+0; m60[NR]=$3+0; d[NR]=$4+0; ms[NR]=$5+0
        if (m1[NR] > mx) mx = m1[NR]
        n = NR
    }
    END {
        for (i=1; i<=n; i++) {
            printf "│ %-40s %7d %7d %9d %8.1f  %s\n",
                substr(h[i],1,40), m1[i], m60[i], d[i], ms[i], vbar(m1[i], mx, 20)
        }
    }
    function vbar(val, maxval, width,   full, p, s, i, eighths, visual) {
        if (maxval <= 0) return sprintf("%*s", width, "")
        ratio = val / maxval
        if (ratio > 1) ratio = 1
        eighths = int(ratio * width * 8 + 0.5)
        full = int(eighths / 8)
        p = eighths - full * 8
        s = ""
        for (i=0; i<full; i++) s = s "█"
        visual = full
        if (p > 0 && visual < width) { s = s p8[p]; visual++ }
        while (visual < width) { s = s " "; visual++ }
        return s
    }'
}
