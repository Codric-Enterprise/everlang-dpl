/*
 * unknown_test.c — Ever / Tapestry, no null across the EValue boundary
 *
 * EV_UNKNOWN replaces every place a null used to slip through as VOID or
 * as an empty string: a NULL pointer, a foreign `null` literal, an SQL
 * NULL, a missing or empty wire buffer, a tag the reader does not know.
 * Each becomes a value that says why it is unknown and where it came
 * from. This proves it on the C side; 2-interpreter-python/unknown_test.py
 * proves the Python side and drives the two modes below to show both
 * sides write the same bytes.
 *
 *   unknown_test            run the assertions
 *   unknown_test --echo     stdin bytes -> ev_deserialise -> ev_serialise
 *                           -> stdout (one value)
 *   unknown_test --emit     the C lifters' output for fixed inputs, each
 *                           serialised, as hex lines
 *
 * Codric Enterprise
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "evalue.h"

static int passed = 0, failed = 0;

static void ok(const char *name, int cond) {
    if (cond) { passed++; printf("  ✓ %s\n", name); }
    else      { failed++; printf("  ✗ %s\n", name); }
}

static int is_unknown_with(const EValue *v, const char *reason,
                           const char *source, int line, int col) {
    return ev_is_unknown(v)
        && strcmp(ev_unknown_reason(v), reason) == 0
        && strcmp(ev_unknown_source(v), source) == 0
        && v->body.span.line == line && v->body.span.col == col;
}

static EValue round_trip(const EValue *v, ev_pool *pool) {
    static uint8_t buf[EV_SERIAL_MAX];
    int32_t n = ev_serialise(v, buf, sizeof buf, pool);
    int32_t used = 0;
    EValue back = ev_deserialise(buf, n, &used, pool);
    if (used != n) return ev_unknown("round trip consumed the wrong length", "test", 0, 0);
    return back;
}

/* Valid UTF-8 throughout, which is what a cut must preserve. */
static int valid_utf8(const char *s) {
    const unsigned char *p = (const unsigned char *)s;
    while (*p) {
        int extra = (*p < 0x80) ? 0 : ((*p >> 5) == 6) ? 1 :
                    ((*p >> 4) == 14) ? 2 : ((*p >> 3) == 30) ? 3 : -1;
        if (extra < 0) return 0;
        p++;
        for (int i = 0; i < extra; i++, p++)
            if ((*p & 0xC0) != 0x80) return 0;
    }
    return 1;
}

static int echo(void) {
    static uint8_t in[EV_SERIAL_MAX], out[EV_SERIAL_MAX];
    size_t n = fread(in, 1, sizeof in, stdin);
    ev_pool pool; ev_pool_init(&pool);
    int32_t used = 0;
    EValue v = ev_deserialise(in, (int32_t)n, &used, &pool);
    int32_t m = ev_serialise(&v, out, sizeof out, &pool);
    ev_pool_clear(&pool);
    if (m < 0) return 2;
    fwrite(out, 1, (size_t)m, stdout);
    return 0;
}

static void hex_line(const EValue *v, ev_pool *pool) {
    static uint8_t buf[EV_SERIAL_MAX];
    int32_t n = ev_serialise(v, buf, sizeof buf, pool);
    for (int32_t i = 0; i < n; i++) printf("%02x", buf[i]);
    printf("\n");
}

static int emit(void) {
    ev_pool pool; ev_pool_init(&pool);
    const char *cols[] = {"id", "name", "score"};
    const char *vals[] = {"7", NULL, "null"};
    EValue v;
    v = ev_text(NULL);                                   hex_line(&v, &pool);
    v = ev_from_literal(NULL, E_LANG_C, &pool);          hex_line(&v, &pool);
    v = ev_from_literal("", E_LANG_C, &pool);            hex_line(&v, &pool);
    v = ev_from_literal("nullptr", E_LANG_CPP, &pool);   hex_line(&v, &pool);
    v = ev_from_sql_row(cols, vals, 3, &pool);           hex_line(&v, &pool);
    v = ev_from_sql_row_at(cols, vals, 3, "orders.db", 12, &pool); hex_line(&v, &pool);
    v = ev_deserialise(NULL, 0, NULL, &pool);            hex_line(&v, &pool);
    ev_pool_clear(&pool);
    return 0;
}

int main(int argc, char **argv) {
    if (argc > 1 && strcmp(argv[1], "--echo") == 0) return echo();
    if (argc > 1 && strcmp(argv[1], "--emit") == 0) return emit();

    ev_pool pool;
    ev_pool_init(&pool);
    printf("\n=== Ever — no null across the EValue boundary ===\n\n");

    printf("The UNKNOWN variant\n");
    EValue u = ev_unknown("sensor offline", "feed.csv", 4, 2);
    ok("tag is EV_UNKNOWN (8)",            u.tag == EV_UNKNOWN && EV_UNKNOWN == 8);
    ok("carries its reason",               strcmp(ev_unknown_reason(&u), "sensor offline") == 0);
    ok("carries its source",               strcmp(ev_unknown_source(&u), "feed.csv") == 0);
    ok("carries its line and column",      u.body.span.line == 4 && u.body.span.col == 2);
    ok("type name is \"unknown\"",         strcmp(ev_type_name(EV_UNKNOWN), "unknown") == 0);
    ok("neither scalar nor composite",     !ev_is_scalar(&u) && !ev_is_composite(&u));
    ok("is not void",                      !ev_is_void(&u));
    ok("still 208 bytes",                  sizeof u == 208);
    char d[256];
    ok("describes itself with reason and span",
       strcmp(ev_describe(&u, &pool, d, sizeof d), "unknown (sensor offline at feed.csv:4:2)") == 0);
    ok("accessors are empty on other tags",
       strcmp(ev_unknown_reason(&(EValue){0}), "") == 0);

    printf("\nAn unknown must say why and where\n");
    EValue nr = ev_unknown(NULL, NULL, -3, -1);
    ok("NULL reason is recorded, not accepted",
       is_unknown_with(&nr, "ev_unknown called without a reason", "<c>", 0, 0));
    EValue er = ev_unknown("", "", 1, 1);
    ok("empty reason is recorded, not accepted",
       is_unknown_with(&er, "ev_unknown called without a reason", "<c>", 1, 1));

    printf("\nEquality: an unknown equals nothing\n");
    EValue u2 = ev_unknown("sensor offline", "feed.csv", 4, 2);
    EValue vd = ev_void(), zero = ev_int(0), empty = ev_text("");
    ok("unknown != the same unknown",      !ev_equal(&u, &u2, &pool));
    ok("unknown != itself",                !ev_equal(&u, &u, &pool));
    ok("unknown != void",                  !ev_equal(&u, &vd, &pool) && !ev_equal(&vd, &u, &pool));
    ok("unknown != 0",                     !ev_equal(&u, &zero, &pool));
    ok("unknown != \"\"",                  !ev_equal(&u, &empty, &pool));

    printf("\nRaw nulls in C become UNKNOWN, never VOID or \"\"\n");
    EValue t = ev_text(NULL);
    ok("ev_text(NULL)",                    is_unknown_with(&t, "NULL pointer passed to ev_text", "c", 0, 0));
    EValue l0 = ev_from_literal(NULL, E_LANG_C, &pool);
    ok("ev_from_literal(NULL)",            is_unknown_with(&l0, "NULL pointer passed as a literal", "<literal>", 0, 0));
    EValue l1 = ev_from_literal("", E_LANG_C, &pool);
    ok("ev_from_literal(\"\")",            is_unknown_with(&l1, "empty literal", "<literal>", 0, 0));
    const char *spellings[] = {"null", "nil", "None", "NULL", "undefined", "nullptr"};
    for (int i = 0; i < 6; i++) {
        char why[48], name[64];
        snprintf(why, sizeof why, "null literal '%s'", spellings[i]);
        snprintf(name, sizeof name, "ev_from_literal(\"%s\")", spellings[i]);
        EValue s = ev_from_literal(spellings[i], E_LANG_C, &pool);
        ok(name, is_unknown_with(&s, why, "<literal>", 0, 0));
    }
    EValue at = ev_from_literal_at("nil", E_LANG_RUBY, "app.rb", 9, 14, &pool);
    ok("ev_from_literal_at keeps the span", is_unknown_with(&at, "null literal 'nil'", "app.rb", 9, 14));
    EValue fine = ev_from_literal_at("42", E_LANG_C, "x", 1, 1, &pool);
    ok("a real literal is untouched",      fine.tag == EV_INT && fine.body.as_int == 42);

    printf("\nSQL NULL\n");
    const char *cols[] = {"id", "name", "score"};
    const char *vals[] = {"7", NULL, "null"};
    EValue row = ev_from_sql_row(cols, vals, 3, &pool);
    EValue id = ev_record_get(&row, "id", &pool);
    EValue name = ev_record_get(&row, "name", &pool);
    EValue score = ev_record_get(&row, "score", &pool);
    ok("a present value lifts as before",  id.tag == EV_INT && id.body.as_int == 7);
    ok("SQL NULL is unknown, with column", is_unknown_with(&name, "SQL NULL in column 'name'", "sql", 0, 2));
    ok("the text 'null' is unknown too",   is_unknown_with(&score, "null literal 'null'", "sql", 0, 3));
    EValue row2 = ev_from_sql_row_at(cols, vals, 3, "orders.db", 12, &pool);
    EValue name2 = ev_record_get(&row2, "name", &pool);
    ok("ev_from_sql_row_at names source and row",
       is_unknown_with(&name2, "SQL NULL in column 'name'", "orders.db", 12, 2));
    EValue row3 = ev_from_sql_row(cols, NULL, 3, &pool);
    EValue id3 = ev_record_get(&row3, "id", &pool);
    ok("a NULL value array is NULL in every column",
       is_unknown_with(&id3, "SQL NULL in column 'id'", "sql", 0, 1));

    printf("\nThe wire\n");
    EValue back = round_trip(&u, &pool);
    ok("unknown survives a round trip",    is_unknown_with(&back, "sensor offline", "feed.csv", 4, 2));
    EValue lst = ev_list_new(&pool);
    ev_list_push(&lst, ev_int(1), &pool);
    ev_list_push(&lst, u, &pool);
    EValue lback = round_trip(&lst, &pool);
    EValue item = ev_list_get(&lback, 1, &pool);
    ok("inside a list",                    is_unknown_with(&item, "sensor offline", "feed.csv", 4, 2));
    EValue rback = round_trip(&row, &pool);
    EValue rname = ev_record_get(&rback, "name", &pool);
    ok("inside a record",                  is_unknown_with(&rname, "SQL NULL in column 'name'", "sql", 0, 2));
    uint8_t buf[64];
    ok("ev_serialise(NULL) writes nothing", ev_serialise(NULL, buf, sizeof buf, &pool) == -1);
    EValue nb = ev_deserialise(NULL, 10, NULL, &pool);
    ok("NULL buffer reads as unknown",     is_unknown_with(&nb, "NULL buffer at ev_deserialise", "c", 0, 0));
    EValue eb = ev_deserialise(buf, 0, NULL, &pool);
    ok("empty buffer reads as unknown",    is_unknown_with(&eb, "empty buffer at ev_deserialise", "c", 0, 0));
    uint8_t bad[1] = {99};
    int32_t used = -1;
    EValue bt = ev_deserialise(bad, 1, &used, &pool);
    ok("an unrecognised tag reads as unknown, not void",
       is_unknown_with(&bt, "unrecognised tag 99 at ev_deserialise", "c", 0, 0) && used == 1);
    {
        static uint8_t whole[256];
        int32_t n = ev_serialise(&u, whole, sizeof whole, &pool);
        int all_cut = 1;
        for (int32_t k = 1; k < n; k++) {      /* every proper prefix */
            int32_t got = -1;
            EValue c = ev_deserialise(whole, k, &got, &pool);
            all_cut &= is_unknown_with(&c, "truncated UNKNOWN at ev_deserialise", "c", 0, 0)
                       && got == k;
        }
        ok("every cut-off UNKNOWN reads as truncated, within bounds", all_cut);
    }

    printf("\nLong text is cut to fit, never mid-character\n");
    char longsrc[200], longwhy[400];
    memset(longsrc, 's', sizeof longsrc - 1); longsrc[sizeof longsrc - 1] = '\0';
    longwhy[0] = '\0';
    for (int i = 0; i < 100; i++) strcat(longwhy, "\xc3\xa9");   /* é x 100 */
    EValue lu = ev_unknown(longwhy, longsrc, 1, 1);
    ok("source cut to 63 bytes",           strlen(ev_unknown_source(&lu)) == EV_SPAN_SOURCE_MAX);
    ok("source and reason fit 192 bytes",
       strlen(ev_unknown_source(&lu)) + 1 + strlen(ev_unknown_reason(&lu)) + 1 <= EV_INLINE_MAX);
    ok("reason still valid UTF-8",         valid_utf8(ev_unknown_reason(&lu)));
    ok("reason cut at a character",        strlen(ev_unknown_reason(&lu)) % 2 == 0);
    EValue lb = round_trip(&lu, &pool);
    ok("a cut unknown round-trips intact",
       is_unknown_with(&lb, ev_unknown_reason(&lu), ev_unknown_source(&lu), 1, 1));

    ev_pool_clear(&pool);
    printf("\n=== Unknown (C): %d passed, %d failed ===\n", passed, failed);
    return failed ? 1 : 0;
}
