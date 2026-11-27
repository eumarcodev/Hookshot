"""A guest mutex's host object found by the index in its nesting-count word (HOS_MUTEX_IX), never another's, game-free.

host/nn_os_sync.c's obj_mutex_ix (on by default; HOS_MUTEX_IX=0, or the A/B's side with HOS_AB=HOS_MUTEX_IX=0, turns it off) keeps an index in the
guest nn::os::MutexType + 8 and finds the host object through it, checked against the object's key and kind; anything
else falls back to the lookup table (obj_nockpt) and is numbered again. The fixture runs the real obj_mutex_ix and its
statics cut from the source over a stub guest memory and a stub table (each key its own object, counted):

  - the first lookup numbers the object, writes the index into the word and marks mx_ix_used (FinalizeMutex clears the
    word from then on); the next ones find it through the index alone;
  - a mutex copied to another address (its word with it), a zeroed word, a word past the table, an object reused for
    another key or another kind: each the table's answer, the right object, never the copied index's;
  - FinalizeMutex clears the word once an index has been written (mx_ix_used), whichever side of an A/B the run is on.

python -I -B tests/test_mutex_ix.py [--cc gcc]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from srccut import between, block

ROOT = Path(__file__).resolve().parents[1]

FIXTURE = r'''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "hookshot_env.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
enum { K_MUTEX = 1, K_EVENT = 2 };
typedef struct Obj { uint64_t key; int kind; uint32_t ix; } Obj;
static uint8_t guest[4096];
#define GP(a) ((void *)(guest + (a)))
static Obj objs[64];
static int nobjs, table_calls;
static Obj *obj_nockpt(uint64_t key, int kind) {   /* (the table: the object of that key and kind, made on a miss) */
    table_calls++;
    for (int i = 0; i < nobjs; i++)
        if (objs[i].key == key && objs[i].kind == kind) return &objs[i];
    CHECK(nobjs < 64);
    objs[nobjs].key = key, objs[nobjs].kind = kind;
    return &objs[nobjs++];
}
@CUT@
static uint32_t word(uint64_t key) { uint32_t w; memcpy(&w, guest + key + MX_IX_OFFSET, 4); return w; }
static void set_word(uint64_t key, uint32_t w) { memcpy(guest + key + MX_IX_OFFSET, &w, 4); }

int main(void) {
    CHECK(!mx_ix_used);
    Obj *a = obj_mutex_ix(0x100);
    CHECK(a->key == 0x100 && a->kind == K_MUTEX && a->ix && word(0x100) == a->ix && mx_ix_used && table_calls == 1);
    for (int i = 0; i < 10; i++) CHECK(obj_mutex_ix(0x100) == a);
    CHECK(table_calls == 1);   /* (through the index alone) */
    /* a copy: the word's index is the first mutex's, its key another */
    memcpy(guest + 0x200, guest + 0x100, 32);
    Obj *b = obj_mutex_ix(0x200);
    CHECK(b != a && b->key == 0x200 && word(0x200) == b->ix && b->ix != a->ix);
    CHECK(obj_mutex_ix(0x100) == a);
    /* zeroed, past the table */
    set_word(0x100, 0);
    CHECK(obj_mutex_ix(0x100) == a && word(0x100) == a->ix);
    set_word(0x100, 0xFFFFFFFFu);
    CHECK(obj_mutex_ix(0x100) == a && word(0x100) == a->ix);
    /* the object reused for another key (spares): the old mutex's word names it, but it is not its own any more */
    const int calls = table_calls;
    a->key = 0x300;
    Obj *c = obj_mutex_ix(0x100);
    CHECK(c != a && c->key == 0x100 && table_calls == calls + 1);
    /* another kind at that address now */
    c->kind = K_EVENT;
    Obj *d = obj_mutex_ix(0x100);
    CHECK(d != c && d->kind == K_MUTEX && d->key == 0x100);
    puts("ok");
    return 0;
}
'''


def compiler(cc):
    for c in ([cc] if cc else []) + [os.environ.get("CC"), "clang", "gcc", "cc"]:
        if c and shutil.which(c):
            return c
    raise SystemExit("test_mutex_ix: no C compiler")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cc", help="C compiler (default: $CC, else clang, gcc or cc)")
    a = p.parse_args()
    src = (ROOT / "host" / "nn_os_sync.c").read_text(encoding="utf-8")
    cut = between(src, "#define MX_IX_OFFSET 8", "static Obj *obj_mutex_ix(uint64_t key) {") + \
        block(src, "static Obj *obj_mutex_ix(uint64_t key) {")
    # (wired: obj asks the A/B for the switch at each lookup, and FinalizeMutex clears the word once an index was written)
    if 'hos_ab_value("HOS_MUTEX_IX", mx_ix_on())' not in block(src, "static Obj *obj(uint64_t key, int kind) {"):
        raise SystemExit("FAIL: obj no longer asks hos_ab_value for HOS_MUTEX_IX at each mutex lookup")
    # (on by default: measured +6 to +14 % fps CPU-bound, PR #25; HOS_MUTEX_IX=0 turns it off)
    if 'env_on_or("HOS_MUTEX_IX", 1)' not in block(src, "static int mx_ix_on(void) {"):
        raise SystemExit("FAIL: HOS_MUTEX_IX is no longer on by default (mx_ix_on reads env_on_or(\"HOS_MUTEX_IX\", 1))")
    fin = block(src, "HLE(_ZN2nn2os13FinalizeMutexEPNS0_9MutexTypeE)")
    if "__atomic_load_n(&mx_ix_used, __ATOMIC_RELAXED)" not in fin or "MX_IX_OFFSET" not in fin:
        raise SystemExit("FAIL: FinalizeMutex no longer clears the index word once one was written (mx_ix_used)")
    cc = compiler(a.cc)
    with tempfile.TemporaryDirectory(prefix="mutex-ix-") as tmp:
        c = Path(tmp) / "fixture.c"
        exe = Path(tmp) / "fixture.exe"
        c.write_text(FIXTURE.replace("@CUT@", cut), encoding="utf-8")
        subprocess.run([cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                        "-I", str(ROOT / "gpu" / "vk" / "compat"), str(c), "-o", str(exe)], check=True)
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
        out = (r.stdout + r.stderr).strip()
        if r.returncode:
            raise SystemExit("FAIL: " + out)
    print("test_mutex_ix: ok")


if __name__ == "__main__":
    main()
