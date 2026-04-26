# SmartMet access-log & live-stats visualizer
#
# Source this file from ~/.bashrc to get:
#   bstat    - dashboard (table + Braille sparklines + summary)
#   bchart   - btop-style vertical chart for one metric over time
#   burls    - top N slowest / heaviest / most-requested URLs
#              (-L lists query params, -d/-k filter them, -i prompts)
#   bstatus  - HTTP status code distribution; -i adds a per-class
#              time-bucketed sparkline view
#   bkeys    - top API keys
#
#   bmon     - live dashboard polling the admin plugin (cache + service stats)
#
# All log functions read stdin or a file passed as the last argument.
# Interval flag -i picks the time bucket width:
#   1s, 10s, 1m, 10m, 1h, 1d           (default: 1h)
#
# Rendering uses half-height ▄ bars for per-row magnitudes and Braille
# (2 buckets per cell, level capped at 3 to leave a visual gap between
# stacked sparklines) for sparklines. Pass --ascii to either bstat or
# bstatus to fall back to plain ASCII output for grep-friendly logs.
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
    BSTAT_ASCII=0
    BSTAT_ARGS=()
    while (( $# )); do
        case "$1" in
            -i) BSTAT_INTERVAL="$2"; shift 2 ;;
            -w) BSTAT_WIDTH="$2"; shift 2 ;;
            --ascii) BSTAT_ASCII=1; shift ;;
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
Usage: bstat [-i INTERVAL] [-w WIDTH] [--ascii] [LOG-FILE]

  -i INTERVAL   bucket width: 1s, 10s, 1m, 10m, 1h, 1d  (default: 1h)
  -w WIDTH      horizontal bar width in columns         (default: 20)
  --ascii       use = bars instead of Unicode eighth-blocks, and
                skip the sparkline footer. For scripts that grep
                the output or terminals without UTF-8 support.

Reads stdin if no file is given.
EOF
        return 0
    fi

    local prec
    if ! prec=$(_bstat_prec "$BSTAT_INTERVAL"); then
        echo "bstat: unknown interval '$BSTAT_INTERVAL' (use 1s|10s|1m|10m|1h|1d)" >&2
        return 1
    fi

    gawk -v PREC="$prec" -v BW="$BSTAT_WIDTH" -v ASCII="$BSTAT_ASCII" '
    BEGIN {
        if (ASCII) {
            FULL = "="
            # ASCII sparkline: 4-level dot ramp (one char per bucket).
            sp[0]=" "; sp[1]="."; sp[2]=":"; sp[3]="|"; sp[4]="#"
        } else {
            # Half-height block: bar fills only the lower half of the
            # row, freeing visual space and matching smtop hbar style.
            FULL = "▄"
            # Braille 5x5 lookup B[l*5+r] for l,r in [0..4]. Two buckets
            # encoded per cell (left dot column + right dot column),
            # 4 vertical levels each = 2x denser than eighth-block.
            # Identical to btop graph_symbols and smtop sparkline tables.
            B[0]=" ";  B[1]="⢀"; B[2]="⢠"; B[3]="⢰"; B[4]="⢸"
            B[5]="⡀";  B[6]="⣀"; B[7]="⣠"; B[8]="⣰"; B[9]="⣸"
            B[10]="⡄"; B[11]="⣄"; B[12]="⣤"; B[13]="⣴"; B[14]="⣼"
            B[15]="⡆"; B[16]="⣆"; B[17]="⣦"; B[18]="⣶"; B[19]="⣾"
            B[20]="⡇"; B[21]="⣇"; B[22]="⣧"; B[23]="⣷"; B[24]="⣿"
        }
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

        # 10m and 10s intervals truncate ISO-8601 between digits (e.g.
        # "13:0" for 10m, "13:00:0" for 10s). Append a "0" when
        # rendering so "13:0" → "13:00" makes the bucket boundary
        # visually unambiguous. DISPLEN is the rendered width of a
        # bucket label.
        TPAD = (PREC==15 || PREC==18) ? "0" : ""
        DISPLEN = PREC + length(TPAD)

        # title
        printf "┌─ SmartMet access-log summary  (bucket: %s, %d rows) ─┐\n",
            iname(PREC), n
        printf "│\n"
        # header
        bwL = BW
        # Sparkline width = bucket count in ASCII mode, ceil(n/2) in
        # Braille mode (2 buckets per cell).
        sw = ASCII ? n : int((n+1)/2)
        printf "│ %-*s  %7s %8s %7s %8s %8s %5s  %-*s  %-*s  %-*s  %-*s\n",
            DISPLEN, "time", "reqs", "avg_ms", "max_ms", "avg_KB", "MB_out", "err%",
            bwL, "requests", bwL, "latency", bwL, "size", bwL, "bandwidth"
        # separator
        printf "│ "
        for (i=0; i<DISPLEN; i++) printf "─"
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
                DISPLEN, t TPAD,
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
        for (i=0; i<DISPLEN; i++) printf "─"
        printf "  %7s %8s %7s %8s %8s %5s\n",
            "───────", "────────", "───────", "────────", "────────", "─────"

        if (tot_count > 0) {
            printf "│ %-*s  %7d %8.1f %7s %8.1f %8.2f %5.1f\n",
                DISPLEN, "TOTAL",
                tot_count,
                tot_dur / tot_count,
                "-",
                (tot_bytes / tot_count) / 1024,
                tot_bytes / 1048576,
                tot_err / tot_count * 100
        }

        # Sparklines: ASCII = 1 dot per bucket (4-level ramp), Braille
        # = 2 buckets per cell (4-level overlap encoding, btop-style).
        printf "│\n"
        printf "│  requests   "; spark(count, keys, n, gc); printf "\n"
        printf "│  latency    "; sparkavg(sumdur, count, keys, n);    printf "\n"
        printf "│  avg_size   "; sparkavg(sumbytes, count, keys, n);  printf "\n"
        printf "│  bandwidth  "; spark(sumbytes, keys, n, gB); printf "\n"
        printf "│\n"

        # time axis: first + last bucket label under the sparklines.
        # Pad to the rendered sparkline width (sw), not bucket count.
        if (n > 0) {
            l1 = keys[1]   TPAD
            l2 = keys[n]   TPAD
            pad = sw - length(l1) - length(l2)
            if (pad < 1) pad = 1
            printf "│             %s", l1
            for (i=0; i<pad; i++) printf " "
            printf "%s\n", l2
        }
        printf "└" ; for (i=0; i<72; i++) printf "─"; printf "\n"
    }

    # Per-row horizontal bar, "width" cells wide, half-height (▄) so
    # the four bars per row stay visually distinct without dominating
    # the line. Cell-level rounding (no eighth-block partials) — same
    # approach as smtop hbar 0.7.7.
    function vbar(val, maxval, width,   ratio, n, s, i) {
        if (maxval <= 0) {
            s = ""
            for (i=0; i<width; i++) s = s " "
            return s
        }
        ratio = val / maxval
        if (ratio > 1) ratio = 1
        if (ratio < 0) ratio = 0
        n = int(ratio * width + 0.5)
        s = ""
        for (i=0; i<n; i++) s = s FULL
        for (i=n; i<width; i++) s = s " "
        return s
    }

    # Sparkline for vals[ks[i]]/mx — Braille: 2 buckets per cell
    # (left dots = vals[2i-1], right dots = vals[2i]); ASCII: one dot
    # per bucket. Levels are capped at 3 (out of 4) so the topmost
    # Braille dot row stays empty — keeps stacked sparklines from
    # visually touching across line boundaries.
    function spark(vals, ks, m, mx,   i, l1, l2) {
        if (mx <= 0) {
            if (ASCII) for (i=1; i<=m; i++) printf " "
            else       for (i=0; i<int((m+1)/2); i++) printf " "
            return
        }
        if (ASCII) {
            for (i=1; i<=m; i++) {
                l1 = int(vals[ks[i]] / mx * 3 + 0.5)
                if (l1 < 0) l1 = 0; if (l1 > 3) l1 = 3
                printf "%s", sp[l1]
            }
            return
        }
        for (i=1; i<=m; i+=2) {
            l1 = int(vals[ks[i]] / mx * 3 + 0.5)
            if (l1 < 0) l1 = 0; if (l1 > 3) l1 = 3
            if (i+1 <= m) {
                l2 = int(vals[ks[i+1]] / mx * 3 + 0.5)
                if (l2 < 0) l2 = 0; if (l2 > 3) l2 = 3
            } else l2 = 0
            printf "%s", B[l1*5 + l2]
        }
    }

    # Mean-per-bucket sparkline (num[k]/den[k]). Same encoding as spark().
    function sparkavg(num, den, ks, m,   i, mx, va, l1, l2) {
        mx = 0
        for (i=1; i<=m; i++) { va[i] = num[ks[i]] / den[ks[i]]; if (va[i] > mx) mx = va[i] }
        if (mx <= 0) {
            if (ASCII) for (i=1; i<=m; i++) printf " "
            else       for (i=0; i<int((m+1)/2); i++) printf " "
            return
        }
        if (ASCII) {
            for (i=1; i<=m; i++) {
                l1 = int(va[i] / mx * 3 + 0.5)
                if (l1 < 0) l1 = 0; if (l1 > 3) l1 = 3
                printf "%s", sp[l1]
            }
            return
        }
        for (i=1; i<=m; i+=2) {
            l1 = int(va[i] / mx * 3 + 0.5)
            if (l1 < 0) l1 = 0; if (l1 > 3) l1 = 3
            if (i+1 <= m) {
                l2 = int(va[i+1] / mx * 3 + 0.5)
                if (l2 < 0) l2 = 0; if (l2 > 3) l2 = 3
            } else l2 = 0
            printf "%s", B[l1*5 + l2]
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
    local cellw=1
    local ascii=0
    local args=()
    while (( $# )); do
        case "$1" in
            -i) interval="$2"; shift 2 ;;
            -m) metric="$2";  shift 2 ;;
            -h) height="$2";  shift 2 ;;
            -w) cellw="$2";   shift 2 ;;
            --ascii) ascii=1; shift ;;
            --help)
                cat <<EOF
Usage: bchart [-i INTERVAL] [-m METRIC] [-h HEIGHT] [-w CELLW] [--ascii] [LOG-FILE]

  -i INTERVAL   1s, 10s, 1m, 10m, 1h, 1d    (default: 1h)
  -m METRIC     reqs | ms | kb | mb | err   (default: reqs)
                  reqs = requests per bucket
                  ms   = mean latency (ms)
                  kb   = mean response size (KB)
                  mb   = total bandwidth (MB)
                  err  = error rate (%)
  -h HEIGHT     chart height in rows        (default: 12)
  -w CELLW      cells per data unit         (default: 1)
                  Default Braille mode renders 2 buckets per cell, so
                  one CELLW = one Braille cell = 2 buckets. ASCII mode
                  renders 1 bucket per cell.
  --ascii       use eighth-block vertical bars instead of Braille.
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

    gawk -v PREC="$prec" -v METRIC="$metric" -v H="$height" -v CW="$cellw" -v ASCII="$ascii" '
    BEGIN {
        if (ASCII) {
            sp[0]=" "; sp[1]="."; sp[2]="."; sp[3]=":"; sp[4]=":"
            sp[5]="|"; sp[6]="|"; sp[7]="#"; sp[8]="#"
            FULL = "#"
        } else {
            # Same Braille 5x5 lookup as bstat.
            B[0]=" ";  B[1]="⢀"; B[2]="⢠"; B[3]="⢰"; B[4]="⢸"
            B[5]="⡀";  B[6]="⣀"; B[7]="⣠"; B[8]="⣰"; B[9]="⣸"
            B[10]="⡄"; B[11]="⣄"; B[12]="⣤"; B[13]="⣴"; B[14]="⣼"
            B[15]="⡆"; B[16]="⣆"; B[17]="⣦"; B[18]="⣶"; B[19]="⣾"
            B[20]="⡇"; B[21]="⣇"; B[22]="⣧"; B[23]="⣷"; B[24]="⣿"
        }
        TPAD = (PREC==15 || PREC==18) ? "0" : ""
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

        if (ASCII) {
            # Eighth-block vertical bars, 1 cell per bucket × CW.
            for (row = H-1; row >= 0; row--) {
                tick = mx * (row + 1) / H
                printf "│ %*s ", lw, fmtnum(tick)
                for (i=1; i<=n; i++) {
                    r = v[i] / mx
                    eighths = int(r * H * 8 + 0.5)
                    full_rows = int(eighths / 8)
                    partial = eighths - full_rows * 8
                    if (row < full_rows) cell = FULL
                    else if (row == full_rows && partial > 0) cell = sp[partial]
                    else cell = " "
                    for (c=0; c<CW; c++) printf "%s", cell
                }
                printf "\n"
            }
            total = n * CW
        } else {
            # Braille vertical chart. Each cell encodes two adjacent
            # buckets; vertical resolution per char-row = 4 pixels.
            # For bucket b, total filled pixels (bottom-up) =
            # round(v[b]/mx * H * 4). For char-row r (0=bottom),
            # level = clamp(pixels - 4*r, 0, 4).
            for (row = H-1; row >= 0; row--) {
                tick = mx * (row + 1) / H
                printf "│ %*s ", lw, fmtnum(tick)
                for (i=1; i<=n; i+=2) {
                    pL = int(v[i] / mx * H * 4 + 0.5)
                    levL = pL - 4 * row
                    if (levL < 0) levL = 0; if (levL > 4) levL = 4
                    if (i+1 <= n) {
                        pR = int(v[i+1] / mx * H * 4 + 0.5)
                        levR = pR - 4 * row
                        if (levR < 0) levR = 0; if (levR > 4) levR = 4
                    } else levR = 0
                    cell = B[levL*5 + levR]
                    for (c=0; c<CW; c++) printf "%s", cell
                }
                printf "\n"
            }
            total = int((n+1)/2) * CW
        }
        # x axis
        printf "│ %*s ", lw, ""
        for (i=0; i<total; i++) printf "─"
        printf "\n"

        # x-axis ticks: first, middle, last
        if (n > 0) {
            printf "│ %*s ", lw, ""
            s = sprintf("%*s", total, "")
            mid = int((n+1)/2)
            l1 = keys[1]   TPAD
            lm = keys[mid] TPAD
            ln = keys[n]   TPAD
            s = place(s, l1, 0)
            if (n > 2) s = place(s, lm, int(total/2 - length(lm)/2))
            if (n > 1) s = place(s, ln, total - length(ln))
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
# Usage: burls [-n 20] [-s reqs|ms|kb|mb] [-d|-k LIST] [-L|-i] [log-file]

# Helper: scan a log and print a frequency table of every distinct
# query-string parameter name seen. Used by both -L (list) and -i
# (interactive) modes.
_burls_list_params() {
    gawk '
    BEGIN { tot = 0 }
    {
        url = $6
        qpos = index(url, "?")
        if (qpos == 0) next
        qs = substr(url, qpos+1)
        np = split(qs, parts, "&")
        for (j=1; j<=np; j++) {
            eq = index(parts[j], "=")
            name = (eq > 0) ? substr(parts[j], 1, eq-1) : parts[j]
            if (name == "") continue
            cnt[name]++
        }
        tot++
    }
    END {
        if (tot == 0) {
            print "burls: no requests with a query string in input" > "/dev/stderr"
            exit 0
        }
        printf "┌─ Query-string parameters seen (%d requests with query) ─\n", tot
        printf "│ %-30s %10s %7s\n", "param", "count", "pct"
        printf "│ %-30s %10s %7s\n",
            "──────────────────────────────", "──────────", "───────"
        n = 0
        for (k in cnt) names[n++] = k
        # sort names by count desc (insertion sort — n is small)
        for (i=0; i<n; i++) for (j=i+1; j<n; j++) {
            if (cnt[names[j]] > cnt[names[i]]) {
                t = names[i]; names[i] = names[j]; names[j] = t
            }
        }
        for (i=0; i<n; i++) {
            printf "│ %-30s %10d %6.1f%%\n",
                names[i], cnt[names[i]], cnt[names[i]] / tot * 100
        }
        printf "└" ; for (i=0; i<72; i++) printf "─"; printf "\n"
    }
    ' "$@"
}

burls() {
    local top=20
    local sort_by=ms
    local drops=""
    local keeps=""
    local listparams=0
    local interactive=0
    local args=()
    while (( $# )); do
        case "$1" in
            -n) top="$2"; shift 2 ;;
            -s) sort_by="$2"; shift 2 ;;
            -d) drops="$2"; shift 2 ;;
            -k) keeps="$2"; shift 2 ;;
            -L|--list-params) listparams=1; shift ;;
            -i|--interactive) interactive=1; shift ;;
            -h|--help)
                cat <<EOF
Usage: burls [-n N] [-s SORT] [-d LIST | -k LIST] [-L|-i] [LOG-FILE]

  -n N     show top N URLs         (default: 20)
  -s SORT  reqs | ms | kb | mb     (default: ms)
             reqs = request count
             ms   = total time spent (ms)
             kb   = mean response size (KB)
             mb   = total bandwidth (MB)
  -d LIST  comma-separated query-string parameter NAMES to drop
           before grouping. Useful when 'bbox', 'time', or other
           high-cardinality values fragment otherwise identical
           requests:
             burls -d bbox,time wms-access-log
  -k LIST  comma-separated query-string parameter names to KEEP
           (everything else is dropped). Mutually exclusive with -d.
  -L, --list-params
           Scan the log and print a frequency table of every
           query-string parameter name seen. Use this output to
           pick which parameters to feed -d or -k.
  -i, --interactive
           Like -L, but after printing the parameter table, prompt
           for a comma-separated drop-list and re-run the analysis
           with that filter. Requires a log file argument; will not
           work with stdin input.
EOF
                return 0 ;;
            *) args+=("$1"); shift ;;
        esac
    done

    if [ -n "$drops" ] && [ -n "$keeps" ]; then
        echo "burls: -d and -k are mutually exclusive" >&2
        return 1
    fi

    if (( listparams )); then
        _burls_list_params "${args[@]}"
        return 0
    fi

    if (( interactive )); then
        if (( ${#args[@]} == 0 )); then
            echo "burls -i: requires a log file argument (cannot read stdin twice)" >&2
            return 1
        fi
        _burls_list_params "${args[@]}"
        printf "\nParameters to drop (comma-separated, blank for none): " >&2
        local reply
        IFS= read -r reply
        if [ -n "$reply" ]; then
            drops="$reply"
        fi
        echo "" >&2
    fi

    gawk -v TOP="$top" -v SORT="$sort_by" -v DROPS="$drops" -v KEEPS="$keeps" '
    BEGIN {
        FULL = "▄"
        if (DROPS != "") {
            nd = split(DROPS, dlist, ",")
            for (i=1; i<=nd; i++) drop[dlist[i]] = 1
            HAS_DROPS = 1
        }
        if (KEEPS != "") {
            nk = split(KEEPS, klist, ",")
            for (i=1; i<=nk; i++) keep[klist[i]] = 1
            HAS_KEEPS = 1
        }
    }
    {
        # Full URL including query string — different parameter sets
        # (GetMap vs GetCapabilities, producer=foo vs producer=bar)
        # are distinct rows. Per-service access logs share a path
        # prefix, so the query string is what distinguishes traffic.
        # -d drops listed parameter names; -k keeps only listed ones
        # (everything else dropped). They are mutually exclusive.
        url = $6
        if (HAS_DROPS || HAS_KEEPS) {
            qpos = index(url, "?")
            if (qpos > 0) {
                path = substr(url, 1, qpos-1)
                qs = substr(url, qpos+1)
                np = split(qs, parts, "&")
                out = ""; first = 1
                for (j=1; j<=np; j++) {
                    eq = index(parts[j], "=")
                    name = (eq > 0) ? substr(parts[j], 1, eq-1) : parts[j]
                    if (HAS_DROPS && drop[name]) continue
                    if (HAS_KEEPS && !keep[name]) continue
                    if (first) { out = parts[j]; first = 0 }
                    else       { out = out "&" parts[j] }
                }
                url = (out == "") ? path : path "?" out
            }
        }
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
    function vbar(val, maxval, width,   ratio, n, s, i) {
        if (maxval <= 0) return sprintf("%*s", width, "")
        ratio = val / maxval
        if (ratio > 1) ratio = 1
        n = int(ratio * width + 0.5)
        s = ""
        for (i=0; i<n; i++) s = s FULL
        for (i=n; i<width; i++) s = s " "
        return s
    }
    ' "${args[@]}"
}

# -----------------------------------------------------------------------------
# bstatus: HTTP status code distribution
# -----------------------------------------------------------------------------

bstatus() {
    local interval=""
    local ascii=0
    local args=()
    while (( $# )); do
        case "$1" in
            -i) interval="$2"; shift 2 ;;
            --ascii) ascii=1; shift ;;
            -h|--help)
                cat <<EOF
Usage: bstatus [-i INTERVAL] [--ascii] [LOG-FILE]

  -i INTERVAL  if given, additionally show a per-class sparkline
               over time. Bucket widths: 1s, 10s, 1m, 10m, 1h, 1d.
  --ascii      use ASCII bars and dot-ramp sparklines.

Always prints the aggregate code distribution and the per-class
breakdown; -i prepends a time-bucketed view.
EOF
                return 0 ;;
            *) args+=("$1"); shift ;;
        esac
    done

    local prec=0
    if [ -n "$interval" ]; then
        if ! prec=$(_bstat_prec "$interval"); then
            echo "bstatus: unknown interval '$interval' (use 1s|10s|1m|10m|1h|1d)" >&2
            return 1
        fi
    fi

    gawk -v PREC="$prec" -v ASCII="$ascii" '
    BEGIN {
        if (ASCII) {
            FULL = "="
            sp[0]=" "; sp[1]="."; sp[2]=":"; sp[3]="|"; sp[4]="#"
        } else {
            FULL = "▄"
            B[0]=" ";  B[1]="⢀"; B[2]="⢠"; B[3]="⢰"; B[4]="⢸"
            B[5]="⡀";  B[6]="⣀"; B[7]="⣠"; B[8]="⣰"; B[9]="⣸"
            B[10]="⡄"; B[11]="⣄"; B[12]="⣤"; B[13]="⣴"; B[14]="⣼"
            B[15]="⡆"; B[16]="⣆"; B[17]="⣦"; B[18]="⣶"; B[19]="⣾"
            B[20]="⡇"; B[21]="⣇"; B[22]="⣧"; B[23]="⣷"; B[24]="⣿"
        }
        TPAD = (PREC==15 || PREC==18) ? "0" : ""
    }
    {
        s = $8 + 0
        cnt[s]++
        tot++
        cls = int(s/100) * 100
        clscnt[cls]++
        if (PREC > 0) {
            t = substr($9, 2, PREC)
            tkey[t] = 1
            bcls[t SUBSEP cls]++
            if (bcls[t SUBSEP cls] > clsmax[cls]) clsmax[cls] = bcls[t SUBSEP cls]
        }
    }
    END {
        if (PREC > 0) {
            # collect time keys sorted
            k = 0
            for (t in tkey) tmptk[++k] = t
            tn = asort(tmptk, tks)
            sw = ASCII ? tn : int((tn+1)/2)
            # widen sparkline column to at least the header label width
            spw = (sw > 9) ? sw : 9
            printf "┌─ HTTP status by %s  (%d buckets, %d requests) ─\n",
                iname(PREC), tn, tot
            printf "│ %5s %8s %6s  %-*s\n", "class", "total", "pct", spw, "sparkline"
            printf "│ %5s %8s %6s  ", "─────", "────────", "──────"
            for (i=0; i<spw; i++) printf "─"
            printf "\n"
            # collect classes
            k = 0
            for (c in clscnt) tmpc[++k] = c + 0
            m = asort(tmpc, cks)
            for (i=1; i<=m; i++) {
                c = cks[i]
                printf "│ %3dxx %8d %5.1f%%  ",
                    c/100, clscnt[c], clscnt[c]/tot*100
                spark_class(c)
                printf "\n"
            }
            # time axis under sparklines
            if (tn > 0) {
                l1 = tks[1]   TPAD
                l2 = tks[tn]  TPAD
                pad = sw - length(l1) - length(l2)
                if (pad < 1) pad = 1
                printf "│ %5s %8s %6s  %s", "", "", "", l1
                for (i=0; i<pad; i++) printf " "
                printf "%s\n", l2
            }
            printf "│\n"
        }
        # aggregate code distribution
        if (PREC > 0)
            printf "│ HTTP code distribution  (%d requests):\n", tot
        else
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
        m = asort(tmp2, ckeys2)
        for (i=1; i<=m; i++) {
            c = ckeys2[i]
            printf "│   %dxx: %8d  %5.1f%%  %s\n",
                c/100, clscnt[c], clscnt[c]/tot*100, vbar(clscnt[c], tot, 40)
        }
        printf "└" ; for (i=0; i<72; i++) printf "─" ; printf "\n"
    }
    function spark_class(c,   i, mx, l1, l2, v1, v2) {
        # Same level-3 cap as bstat sparklines so stacked per-class
        # rows do not visually touch.
        mx = clsmax[c]
        if (mx <= 0) {
            if (ASCII) for (i=1; i<=tn; i++) printf " "
            else       for (i=0; i<int((tn+1)/2); i++) printf " "
            return
        }
        if (ASCII) {
            for (i=1; i<=tn; i++) {
                v1 = bcls[tks[i] SUBSEP c] + 0
                l1 = int(v1 / mx * 3 + 0.5)
                if (l1 < 0) l1 = 0; if (l1 > 3) l1 = 3
                printf "%s", sp[l1]
            }
            return
        }
        for (i=1; i<=tn; i+=2) {
            v1 = bcls[tks[i] SUBSEP c] + 0
            l1 = int(v1 / mx * 3 + 0.5)
            if (l1 < 0) l1 = 0; if (l1 > 3) l1 = 3
            if (i+1 <= tn) {
                v2 = bcls[tks[i+1] SUBSEP c] + 0
                l2 = int(v2 / mx * 3 + 0.5)
                if (l2 < 0) l2 = 0; if (l2 > 3) l2 = 3
            } else l2 = 0
            printf "%s", B[l1*5 + l2]
        }
    }
    function vbar(val, maxval, width,   ratio, n, s, i) {
        if (maxval <= 0) return sprintf("%*s", width, "")
        ratio = val / maxval
        if (ratio > 1) ratio = 1
        n = int(ratio * width + 0.5)
        s = ""
        for (i=0; i<n; i++) s = s FULL
        for (i=n; i<width; i++) s = s " "
        return s
    }
    function iname(p) {
        if (p==19) return "1s";  if (p==18) return "10s"
        if (p==16) return "1m";  if (p==15) return "10m"
        if (p==13) return "1h";  if (p==10) return "1d"
        return "?"
    }
    ' "${args[@]}"
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
    BEGIN { FULL = "▄" }
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
    function vbar(val, maxval, width,   ratio, n, s, i) {
        if (maxval <= 0) return sprintf("%*s", width, "")
        ratio = val / maxval
        if (ratio > 1) ratio = 1
        n = int(ratio * width + 0.5)
        s = ""
        for (i=0; i<n; i++) s = s FULL
        for (i=n; i<width; i++) s = s " "
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
        FULL = "▄"
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
    function vbar(val, maxval, width,   ratio, n, s, i) {
        if (maxval <= 0) return sprintf("%*s", width, "")
        ratio = val / maxval
        if (ratio > 1) ratio = 1
        n = int(ratio * width + 0.5)
        s = ""
        for (i=0; i<n; i++) s = s FULL
        for (i=n; i<width; i++) s = s " "
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
        FULL = "▄"
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
    function vbar(val, maxval, width,   ratio, n, s, i) {
        if (maxval <= 0) return sprintf("%*s", width, "")
        ratio = val / maxval
        if (ratio > 1) ratio = 1
        n = int(ratio * width + 0.5)
        s = ""
        for (i=0; i<n; i++) s = s FULL
        for (i=n; i<width; i++) s = s " "
        return s
    }'
}
