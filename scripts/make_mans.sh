#!/bin/bash
# Regenerate the man pages from each program's --help (needs txt2man).
# Lamplighter's naming: sbin commands (the lamplighter daemon and the
# lamplighter-* admin tools) go in section 8, bin commands (lux-*) in 1.

dir=$(readlink -m $(dirname $0))
root=$(readlink -m "$dir/..")
bin=$root/src/robinhood

which txt2man > /dev/null
if (($? != 0)); then
    echo "'txt2man' is not installed on this machine: man pages won't be updated" >&2
    exit 0
fi

# gen <program> <section> <description> <see also> <txt2man -I options...>
gen() {
    local prog=$1 sect=$2 desc=$3 see=$4
    shift 4
    if [[ -x $bin/$prog ]]; then
        $dir/cmd2man.sh "$bin/$prog" "$desc" "$see" |
            txt2man -v "Lamplighter $VERSION" -t "$prog" -s "$sect" "$@" |
            $dir/fix_man_options.sh > "$root/man/$prog.$sect"
    else
        echo "src/robinhood/$prog is not built: can't generate man/$prog.$sect" >&2
    fi
}

gen lamplighter 8 "policy engine and statistics tool for large file systems" \
    "lamplighter-report(8), lamplighter-diff(8), lux-find(1), lux-du(1)" \
    -I dir -I mdt_idx -I attrset -I ost_index -I cfg_file -I output_file -I logfile -I loglevel \
    -I pidfile -I user_name -I grp_name -I filepath -I policy1 -I policy2 -I args -I pct -I tgt -I size -I nbr
gen lamplighter-report 8 "querying command for the Lamplighter policy engine" \
    "lamplighter(8), lamplighter-diff(8), lux-find(1), lux-du(1)" \
    -I path -I id -I username -I groupname -I ost_index -I ost_set -I class_expr -I cnt -I range \
    -I cfg_file -I loglevel -I status_name -I status_value
gen lamplighter-diff 8 "list differences between the Lamplighter database and the filesystem" \
    "lamplighter(8), lamplighter-report(8), lux-find(1), lux-du(1)" \
    -I loglevel -I configfile -I fs -I db
gen lux-find 1 "find clone that queries the Lamplighter database" \
    "lamplighter(8), lamplighter-report(8), lux-du(1), lamplighter-diff(8)" \
    -I user -I group -I type -I filename -I size_crit -I time_crit -I minute_crit -I second_crit \
    -I status -I ost_index -I log_level -I config_file
gen lux-du 1 "du clone that queries the Lamplighter database" \
    "lamplighter(8), lamplighter-report(8), lux-find(1), lamplighter-diff(8)" \
    -I user -I group -I type -I status -I log_level -I config_file
