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
 * Usage: cfgload MODULES FILE    (MODULES: e.g. global,log,updt,lmgr,defs)
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

/* Stage 2a.3: the policy definitions. policy_loader.c is included (the
 * reference's copy, POLICY_LOADER_C on the command line) to reach its
 * static read_policy_definitions(); this object then provides all of
 * policy_loader.o's symbols, so the archive's copy is not linked. */
#include POLICY_LOADER_C
#include "status_manager.h"

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


static void dump_mask(const char *name, attr_mask_t m)
{
    printf(" %s=%x/%x/%llx", name, m.std, m.status,
           (unsigned long long)m.sm_info);
}

/* the criteria type of a status manager attribute, as interpret_condition
 * finds it */
static int sminfo_type(const compare_triplet_t *t, const sm_instance_t *smi)
{
    const sm_info_def_t *def = NULL;
    unsigned int idx;

    if (sm_attr_get(smi, NULL, t->attr_name, NULL, &def, &idx) != 0
        || def == NULL)
        return -1;
    return def->crit_type;
}

static void dump_value(const compare_triplet_t *t, int type)
{
    switch (type) {
    case PT_STRING:
        printf(" str=");
        esc(t->val.str);
        break;
    case PT_SIZE:
        printf(" size=%llu", t->val.size);
        break;
    case PT_INT:
    case PT_BOOL:
        printf(" int=%u", t->val.integer);
        break;
    case PT_DURATION:
        printf(" duration=%lld", (long long)t->val.duration);
        break;
    case PT_TYPE:
        printf(" type=%d", t->val.type);
        break;
    default:
        printf(" ?type=%d", type);
    }
}

static void dump_node(const bool_node_t *n, const sm_instance_t *smi)
{
    const compare_triplet_t *t;
    int type;

    switch (n->node_type) {
    case NODE_CONSTANT:
        printf("CONST(%d)", n->content_u.constant ? 1 : 0);
        return;
    case NODE_UNARY_EXPR:
        printf("UNARY%d(", n->content_u.bool_expr.bool_op);
        dump_node(n->content_u.bool_expr.expr1, smi);
        printf(")");
        return;
    case NODE_BINARY_EXPR:
        printf("BINARY%d(", n->content_u.bool_expr.bool_op);
        dump_node(n->content_u.bool_expr.expr1, smi);
        printf(", ");
        dump_node(n->content_u.bool_expr.expr2, smi);
        printf(")");
        return;
    case NODE_CONDITION:
        break;
    }
    t = n->content_u.condition;
    printf("COND{crit=%d op=%d flags=%d", t->crit, t->op, t->flags);
    if (t->crit == CRITERIA_XATTR || t->crit == CRITERIA_SM_INFO) {
        printf(" attr=");
        esc(t->attr_name);
    }
    switch (t->crit) {
    case CRITERIA_TYPE:
        type = PT_TYPE;
        break;
    case CRITERIA_SIZE:
        type = PT_SIZE;
        break;
    case CRITERIA_DEPTH: case CRITERIA_DIRCOUNT: case CRITERIA_NLINK:
#ifdef _LUSTRE
    case CRITERIA_OST: case CRITERIA_PROJID:
#endif
        type = PT_INT;
        break;
    case CRITERIA_LAST_ACCESS: case CRITERIA_LAST_MOD:
    case CRITERIA_LAST_MDCHANGE: case CRITERIA_CREATION: case CRITERIA_RMTIME:
        type = PT_DURATION;
        break;
    case CRITERIA_OWNER: case CRITERIA_GROUP:
        type = global_config.uid_gid_as_numbers ? PT_INT : PT_STRING;
        break;
    case CRITERIA_SM_INFO:
        type = sminfo_type(t, smi);
        break;
    default:
        type = PT_STRING;
    }
    dump_value(t, type);
    printf("}");
}

static void dump_action(const policy_action_t *a)
{
    int i;

    printf(" action=%d", a->type);
    if (a->type == ACTION_FUNCTION) {
        printf(":");
        esc(a->action_u.func.name);
    } else if (a->type == ACTION_COMMAND) {
        for (i = 0; a->action_u.command[i]; i++) {
            printf(i ? "," : ":");
            esc(a->action_u.command[i]);
        }
    }
}

static void dump_defs(void)
{
    static char str[1 << 20];
    unsigned int i;
    sm_instance_t *smi;

    printf(" count=%u manage_deleted=%u\n", policies.policy_count,
           policies.manage_deleted);
    for (i = 0; (smi = get_sm_instance(i)) != NULL; i++) {
        printf("smi %u name=", smi->smi_index);
        esc(smi->instance_name);
        printf(" db_field=");
        esc(smi->db_field);
        printf(" user_name=");
        esc(smi->user_name);
        printf(" sm=");
        esc(smi->sm->name);
        printf(" info_offset=%u\n", smi->sm_info_offset);
    }
    for (i = 0; i < sm_attr_count; i++) {
        printf("sm_attr %u db=", i);
        esc(sm_attr_info[i].db_attr_name);
        printf(" user=");
        esc(sm_attr_info[i].user_attr_name);
        printf(" smi=%u\n", sm_attr_info[i].smi->smi_index);
    }
    for (i = 0; i < policies.policy_count; i++) {
        const policy_descr_t *p = &policies.policy_list[i];

        printf("policy ");
        esc(p->name);
        dump_action(&p->default_action);
        dump_mask("run_mask", p->rules.run_attr_mask);
        if (p->status_mgr)
            printf(" smi=%u", p->status_mgr->smi_index);
        else
            printf(" smi=-");
        printf(" implements=");
        if (p->implements)
            esc(p->implements);
        else
            printf("-");
        printf(" manage_deleted=%d lru_attr=%x lru_order=%d",
               p->manage_deleted, p->default_lru_sort_attr,
               p->default_lru_sort_order);
        dump_mask("scope_mask", p->scope_mask);
        printf(" status_current=");
        if (p->status_current)
            esc(p->status_current);
        else
            printf("-");
        printf("\n  scope ");
        dump_node(&p->scope, p->status_mgr);
        if (BoolExpr2str((bool_node_t *)&p->scope, str, sizeof(str)) < 0)
            strcpy(str, "<error>");
        printf("\n  scope_str ");
        esc(str);
        printf("\n");
    }
}

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
    } else if (!strcmp(m, "defs")) {
        dump_defs();
        return;
    }
    putchar('\n');
}

/* the policies module up to its policy definitions (2a.3) */
static int defs_read(config_file_t config, void *cfg, char *msg_out)
{
    policies_t *pol = (policies_t *) cfg;

    memset(pol, 0, sizeof(*pol));
    return read_policy_definitions(config, pol, msg_out);
}

static int defs_set(void *cfg, bool reload)
{
    policies = *(policies_t *) cfg;
    return 0;
}

static mod_cfg_funcs_t defs_hdlr = {
    .module_name = "policies",
    .new = policies_cfg_new,
    .set_default = set_default_policies,
    .read = defs_read,
    .set_config = defs_set,
};

static mod_cfg_funcs_t *handler(const char *m)
{
    if (!strcmp(m, "defs"))
        return &defs_hdlr;
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
