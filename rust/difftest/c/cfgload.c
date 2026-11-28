/*
 * SPDX-License-Identifier: CECILL-C
 * Copyright (C) 2026 Jonathan Mills
 *
 * cfgload (C side): load a configuration file with Robinhood's C
 * configuration modules (linked from the C reference build), exactly as
 * rbh_cfg_load() does for the modules named on the command line, and
 * print the settings they produced, for the side-by-side tests of stage
 * 2a (doc/RUST-PLAN.md). The Rust example `cfgload` prints the same.
 *
 * Usage: cfgload MODULES FILE    (MODULES: e.g. global,log,updt,lmgr)
 * Output: "OK" and one line per module, or the error rbh_cfg_load would
 * return ("ERROR <message>", exit 1). Warnings go to stderr, through the
 * C logging code, as in the real programs.
 */

#include "config.h"
#include "config_parsing.h"
#include "rbh_cfg.h"
#include "global_config.h"
#include "rbh_logs.h"
#include "update_params.h"
#include "list_mgr.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern lmgr_config_t lmgr_config;

static void esc(const char *s)
{
    putchar('"');
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c < 0x20 || c >= 0x7f || c == '"' || c == '\\')
            printf("\\x%02x", c);
        else
            putchar(c);
    }
    putchar('"');
}

#define S(name, v) do { printf(" " name "="); esc(v); } while (0)
#define I(name, v) printf(" " name "=%lld", (long long)(v))

static void dump_module(const char *m)
{
    printf("%s", m);
    if (!strcmp(m, "global")) {
        S("fs_path", global_config.fs_path);
        S("fs_type", global_config.fs_type);
        I("fs_key", global_config.fs_key);
        I("stay_in_fs", global_config.stay_in_fs);
        I("check_mounted", global_config.check_mounted);
        I("last_access_only_atime", global_config.last_access_only_atime);
        I("uid_gid_as_numbers", global_config.uid_gid_as_numbers);
#if defined(_LUSTRE) && defined(_MDS_STAT_SUPPORT)
        I("direct_mds_stat", global_config.direct_mds_stat);
#endif
#ifdef _LUSTRE
        I("lustre_projid", global_config.lustre_projid);
#endif
    } else if (!strcmp(m, "log")) {
        I("debug_level", log_config.debug_level);
        S("log_file", log_config.log_file);
        S("report_file", log_config.report_file);
        S("alert_mail", log_config.alert_mail);
        S("alert_file", log_config.alert_file);
#ifdef HAVE_CHANGELOGS
        S("changelogs_file", log_config.changelogs_file);
#endif
        I("syslog_facility", log_config.syslog_facility);
        I("syslog_priority", log_config.syslog_priority);
        I("batch_alert_max", log_config.batch_alert_max);
        I("stats_interval", log_config.stats_interval);
        I("alert_show_attrs", log_config.alert_show_attrs);
        I("log_process", log_config.log_process);
        I("log_host", log_config.log_host);
    } else if (!strcmp(m, "updt")) {
        printf(" md=%d,%u,%u", updt_params.md.when, updt_params.md.period_min,
               updt_params.md.period_max);
#ifdef _HAVE_FID
        printf(" path=%d,%u,%u", updt_params.path.when,
               updt_params.path.period_min, updt_params.path.period_max);
#endif
        printf(" fileclass=%d,%u,%u", updt_params.fileclass.when,
               updt_params.fileclass.period_min,
               updt_params.fileclass.period_max);
    } else if (!strcmp(m, "lmgr")) {
        S("server", lmgr_config.db_config.server);
        S("db", lmgr_config.db_config.db);
        S("user", lmgr_config.db_config.user);
        S("password", lmgr_config.db_config.password);
        I("port", lmgr_config.db_config.port);
        S("socket", lmgr_config.db_config.socket);
        S("engine", lmgr_config.db_config.engine);
        S("tokudb_compression", lmgr_config.db_config.tokudb_compression);
        I("commit_behavior", lmgr_config.commit_behavior);
        I("connect_retry_min", lmgr_config.connect_retry_min);
        I("connect_retry_max", lmgr_config.connect_retry_max);
        I("acct", lmgr_config.acct);
    }
    putchar('\n');
}

static mod_cfg_funcs_t *handler(const char *m)
{
    if (!strcmp(m, "global"))
        return &global_cfg_hdlr;
    if (!strcmp(m, "log"))
        return &log_cfg_hdlr;
    if (!strcmp(m, "updt"))
        return &updt_params_hdlr;
    if (!strcmp(m, "lmgr"))
        return &lmgr_cfg_hdlr;
    return NULL;
}

int main(int argc, char **argv)
{
    char modules[256], *m, *save = NULL;
    char msg_buf[2048] = "";
    char done[16][32];
    int ndone = 0, i;
    config_file_t tree;

    if (argc != 3) {
        fprintf(stderr, "usage: cfgload MODULES FILE\n");
        return 2;
    }
    /* as rbh_cfg_read_set(): parse, then each module in order */
    tree = rh_config_ParseFile(argv[2]);
    if (tree == NULL) {
        printf("ERROR ");
        esc(rh_config_GetErrorMsg());
        putchar('\n');
        return 1;
    }
    snprintf(modules, sizeof(modules), "%s", argv[1]);
    for (m = strtok_r(modules, ",", &save); m; m = strtok_r(NULL, ",", &save)) {
        mod_cfg_funcs_t *h = handler(m);
        void *cfg;
        int rc;
        char err[4096];

        if (!h) {
            fprintf(stderr, "unknown module %s\n", m);
            return 2;
        }
        cfg = h->new();
        h->set_default(cfg);
        rc = h->read(tree, cfg, msg_buf);
        if (rc != 0) {
            sprintf(err, "Error %d reading %s configuration:\n%s", rc,
                    h->module_name, msg_buf);
            printf("ERROR ");
            esc(err);
            putchar('\n');
            return 1;
        }
        rc = h->set_config(cfg, false);
        if (rc != 0) {
            sprintf(err, "Error %d setting %s configuration:\n%s", rc,
                    h->module_name, msg_buf);
            printf("ERROR ");
            esc(err);
            putchar('\n');
            return 1;
        }
        snprintf(done[ndone++], 32, "%s", m);
    }
    printf("OK\n");
    for (i = 0; i < ndone; i++)
        dump_module(done[i]);
    return 0;
}
