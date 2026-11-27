# sbin commands in section 8, bin commands in section 1
dist_man_MANS=lamplighter.8 lamplighter-report.8 lamplighter-diff.8 lux-find.1 lux-du.1

if LUSTRE_HSM
dist_man_MANS+=lhsmtool_cmd.1
endif

# Manually generate man pages from each executable --help
manpages:
	export VERSION=${VERSION}; ../scripts/make_mans.sh
