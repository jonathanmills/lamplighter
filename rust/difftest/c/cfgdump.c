/*
 * SPDX-License-Identifier: CECILL-C
 * Copyright (C) 2026 Jonathan Mills
 *
 * cfgdump (C side): parse a configuration file with Robinhood's C parser
 * (linked from the C reference build) and print everything it produced,
 * for the side-by-side tests of stage 1 (doc/RUST-PLAN.md). The Rust
 * example `cfgdump` (crates/lamplighter-config) prints the same format.
 *
 * Output:
 *   on success:  "OK", the tree (every name, ID, value, argument list,
 *                expression and line number), then "PRINT" and the output
 *                of rh_config_Print (the C printer) unchanged;
 *   on failure:  "ERROR <message>", exit code 1.
 * Strings are printed in double quotes, with bytes outside printable
 * ASCII (and '"' and '\') written as \xHH.
 */

#include "config.h"
#include "config_parsing.h"
#include "analyze.h"

#include <stdio.h>
#include <stdlib.h>

/* the same layout as in cfg_parsing/config_parsing.c (private there) */
typedef struct config_struct_t {
    list_items *syntax_tree;
} config_struct_t;

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

static void args(const arg_list_t *a)
{
    unsigned int i;

    if (a == NULL) {
        printf("-");
        return;
    }
    putchar('[');
    for (i = 0; i < a->nb_args; i++) {
        if (i)
            putchar(',');
        esc(a->args[i]);
    }
    putchar(']');
}

static const char *op(operator_t o)
{
    static const char *n[] = { "==", "!=", ">", ">=", "<", "<=", "cmd" };
    return (o >= 0 && o <= OP_CMD) ? n[o] : "?";
}

static void bool_expr(const type_bool_expr *e)
{
    switch (e->type) {
    case BOOL_CONDITION:
        printf("cond(");
        esc(e->expr_u.key_value.varname);
        printf(" %s ", op(e->expr_u.key_value.op_type));
        esc(e->expr_u.key_value.varvalue);
        printf(" args=");
        args(e->expr_u.key_value.arg_list);
        putchar(')');
        break;
    case BOOL_UNARY:
        printf(e->oper == BOOL_OP_NOT ? "not(" : "unary%d(", e->oper);
        bool_expr(e->expr_u.members.expr1);
        putchar(')');
        break;
    case BOOL_BINARY:
        printf(e->oper == BOOL_OP_AND ? "and(" : e->oper == BOOL_OP_OR ? "or(" : "binary(");
        bool_expr(e->expr_u.members.expr1);
        putchar(',');
        bool_expr(e->expr_u.members.expr2);
        putchar(')');
        break;
    }
}

static void set(const type_set *s)
{
    if (s->set_type == SET_SINGLETON) {
        printf("set(");
        esc(s->set_u.name);
        putchar(')');
    } else if (s->set_type == SET_NEGATION) {
        printf("setnot(");
        set(s->set_u.op.set1);
        putchar(')');
    } else {
        printf(s->set_u.op.oper == SET_OP_UNION ? "union(" : "inter(");
        set(s->set_u.op.set1);
        putchar(',');
        set(s->set_u.op.set2);
        putchar(')');
    }
}

static void items(const generic_item *it, int depth)
{
    for (; it; it = it->next) {
        printf("%*s", depth * 2, "");
        switch (it->type) {
        case TYPE_BLOCK:
            printf("BLOCK line=%u name=", it->line);
            esc(it->item.block.block_name);
            printf(" id=");
            esc(it->item.block.block_id);
            putchar('\n');
            items(it->item.block.block_content, depth + 1);
            break;
        case TYPE_AFFECT:
            printf("AFFECT line=%u name=", it->line);
            esc(it->item.affect.varname);
            printf(" value=");
            esc(it->item.affect.varvalue);
            printf(" args=");
            args(it->item.affect.arg_list);
            putchar('\n');
            break;
        case TYPE_BOOL_EXPR:
            printf("EXPR line=%u ", it->line);
            bool_expr(&it->item.bool_expr);
            putchar('\n');
            break;
        case TYPE_SET:
            printf("SET line=%u ", it->line);
            set(&it->item.set);
            putchar('\n');
            break;
        }
    }
}

int main(int argc, char **argv)
{
    config_file_t cfg;

    if (argc != 2) {
        fprintf(stderr, "usage: cfgdump FILE\n");
        return 2;
    }
    cfg = rh_config_ParseFile(argv[1]);
    if (cfg == NULL) {
        printf("ERROR ");
        esc(rh_config_GetErrorMsg());
        putchar('\n');
        return 1;
    }
    printf("OK\n");
    items(*((config_struct_t *)cfg)->syntax_tree, 0);
    printf("PRINT\n");
    fflush(stdout);
    rh_config_Print(stdout, cfg);
    return 0;
}
