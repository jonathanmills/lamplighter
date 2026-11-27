AM_CFLAGS= $(CC_OPT) $(DB_CFLAGS) $(PURPOSE_CFLAGS)
AM_LDFLAGS= -lpthread -rpath $(pkglibdir)

#if LUSTRE
#LVERSION=`rpm -qa "lustre[-_]modules*" --qf "%{Version}\n" | tail -1`
#FS_CFLAGS=-DLUSTRE_VERSION=\"$(LVERSION)\"
#endif

DATE=`date --utc --date @$${SOURCE_DATE_EPOCH:-$$(date +%s)} '+%F %T'`
MISC_FLAGS="-DCOMPIL_DATE=\"$(DATE)\""

noinst_LTLIBRARIES=librbhhelpers.la
librbhhelpers_la_SOURCES=cmd_helpers.h cmd_helpers.c
librbhhelpers_la_LDFLAGS=-static

all_libs=   ../cfg_parsing/librbhcfg.la         \
            ../fs_scan/libfsscan.la             \
            ../entry_processor/libentryproc.la  \
            ../policies/libpolicies.la

if CHANGELOGS
all_libs += ../chglog_reader/libchglog_rd.la
endif

all_libs += ./librbhhelpers.la ../list_mgr/liblistmgr.la \
            ../common/libcommontools.la ../cfg_parsing/libconfigparsing.la

#sbin_PROGRAMS=lamplighter lux-report lux-diff lux-recov lux-undelete lux-import lux-rebind
# Lamplighter names; the sources keep Robinhood's names (rbh_*.c).
sbin_PROGRAMS=lamplighter lux-report lux-diff lux-undelete lux-rebind
bin_PROGRAMS=lux-find lux-du

# dependencies:
lamplighter_DEPENDENCIES=$(all_libs)
lux_report_DEPENDENCIES=$(all_libs)
lux_du_DEPENDENCIES=$(all_libs)
lux_find_DEPENDENCIES=$(all_libs)
lux_diff_DEPENDENCIES=$(all_libs)
#lux_recov_DEPENDENCIES=$(all_libs)
lux_undelete_DEPENDENCIES=$(all_libs)
#lux_import_DEPENDENCIES=$(all_libs)
lux_rebind_DEPENDENCIES=$(all_libs)
#
lamplighter_SOURCES=rbh_daemon.c
lamplighter_CFLAGS=$(AM_CFLAGS) $(FS_CFLAGS) $(MISC_FLAGS)
lamplighter_LDFLAGS=-rdynamic $(all_libs) $(DB_LDFLAGS) $(FS_LDFLAGS) $(PURPOSE_LDFLAGS) $(AM_LDFLAGS)

lux_report_SOURCES=rbh_report.c
lux_report_CFLAGS=$(AM_CFLAGS) $(FS_CFLAGS) $(MISC_FLAGS)
lux_report_LDFLAGS=-rdynamic $(all_libs) $(DB_LDFLAGS) $(FS_LDFLAGS) $(PURPOSE_LDFLAGS) $(AM_LDFLAGS)

lux_find_SOURCES=rbh_find.c rbh_find_printf.c rbh_find.h
lux_find_CFLAGS=$(AM_CFLAGS) $(FS_CFLAGS) $(MISC_FLAGS)
lux_find_LDFLAGS=-rdynamic $(all_libs) $(DB_LDFLAGS) $(FS_LDFLAGS) $(PURPOSE_LDFLAGS) $(AM_LDFLAGS)

lux_du_SOURCES=rbh_du.c
lux_du_CFLAGS=$(AM_CFLAGS) $(FS_CFLAGS) $(MISC_FLAGS)
lux_du_LDFLAGS=-rdynamic $(all_libs) $(DB_LDFLAGS) $(FS_LDFLAGS) $(PURPOSE_LDFLAGS) $(AM_LDFLAGS)

lux_diff_SOURCES=rbh_diff.c
lux_diff_CFLAGS=$(AM_CFLAGS) $(FS_CFLAGS) $(MISC_FLAGS)
lux_diff_LDFLAGS=-rdynamic $(all_libs) $(DB_LDFLAGS) $(FS_LDFLAGS) $(PURPOSE_LDFLAG) $(AM_LDFLAGS)

#lux_recov_SOURCES=rbh_recov.c
#lux_recov_CFLAGS=$(AM_CFLAGS) $(FS_CFLAGS) $(MISC_FLAGS)
#lux_recov_LDFLAGS=-rdynamic $(all_libs) $(DB_LDFLAGS) $(FS_LDFLAGS) $(PURPOSE_LDFLAGS) $(AM_LDFLAGS)
#
lux_undelete_SOURCES=rbh_undelete.c
lux_undelete_CFLAGS=$(AM_CFLAGS) $(FS_CFLAGS) $(MISC_FLAGS)
lux_undelete_LDFLAGS=-rdynamic $(all_libs) $(DB_LDFLAGS) $(FS_LDFLAGS) $(PURPOSE_LDFLAGS) $(AM_LDFLAGS)
#
#lux_import_SOURCES=rbh_import.c
#lux_import_CFLAGS=$(AM_CFLAGS) $(FS_CFLAGS) $(MISC_FLAGS)
#lux_import_LDFLAGS=-rdynamic $(all_libs) $(DB_LDFLAGS) $(FS_LDFLAGS) $(PURPOSE_LDFLAGS) $(AM_LDFLAGS)
#
lux_rebind_SOURCES=rbh_rebind.c
lux_rebind_CFLAGS=$(AM_CFLAGS) $(FS_CFLAGS) $(MISC_FLAGS)
lux_rebind_LDFLAGS=-rdynamic $(all_libs) $(DB_LDFLAGS) $(FS_LDFLAGS) $(PURPOSE_LDFLAGS) $(AM_LDFLAGS)

new: clean all

indent:
	$(top_srcdir)/scripts/indent.sh
