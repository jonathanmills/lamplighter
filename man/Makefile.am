dist_man_MANS=lamplighter.1 lux-report.1 lux-find.1 lux-du.1 lux-diff.1

if LUSTRE_HSM
dist_man_MANS+=lhsmtool_cmd.1
endif

# Manually generate man pages from each executable --help
manpages:
	export VERSION=${VERSION}; ../scripts/make_mans.sh
