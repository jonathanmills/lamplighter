
EXTRA_DIST= lamplighter.service		\
	lamplighter@.service			\
	sysconfig_lamplighter			\
	sysconfig_lamplighter.in		\
	type_gen.pl				\
	indent.sh 				\
	demo/disk_usage.sh			\
	demo/migr_purge.sh			\
	demo/rh.migr_purge.conf			\
	demo/write_data.sh  			\
    cmd2man.sh make_mans.sh fix_man_options.sh  \
    ld.so.lamplighter.conf.in lux_cksum.sh.in


ldconfdir=@CONFDIR@/ld.so.conf.d
ldconf_DATA=lamplighter.conf

lamplighter.conf: ld.so.lamplighter.conf
	mv ld.so.lamplighter.conf lamplighter.conf

dist_sbin_SCRIPTS=lux_cksum.sh

if COMMON_RPMS
dist_sbin_SCRIPTS+=lux-config
endif

if HSM_LITE
dist_sbin_SCRIPTS+=luxext_tool luxext_tool_svr luxext_tool_clnt
endif

all-local: lux_cksum.sh
	chmod 755 lux_cksum.sh
