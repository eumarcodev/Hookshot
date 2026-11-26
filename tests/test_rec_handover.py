"""EndRecording hands a recording's segment over as its copy when the block is the size a copy's would be, game-free.

host/nvn_native.c: a command buffer records into a segment, a pool block whose first SEG_PREFIX bytes are kept for a copy's
prefix. At EndRecording (end_recording_copy) the segment itself becomes the recording's copy when its block is the pool
class a copy of the recording would get (the same memory, no bytes moved), and the command buffer's next segment is made
for the size its recordings need (hint). Else the recording is copied, as before, and a segment of another class than its
recordings need goes back to the pool. HOS_NVN_REC_HANDOVER=0 (or the A/B's other side) copies every recording and keeps
the segment; NVN_NO_POOL=1 copies too.

The fixture runs the real pool (pool_get, pool_put), the segment code (seg_class, seg_class_for, cmd_reserve_rec),
flatten and end_recording_copy cut from the source, over stub locks and counters. One child process a case:

  - handover: 3000-byte recordings: the first, in the first 64 KiB segment, is copied and that segment goes back; the
    next ones are handed over (the handle is the segment, its prefix a live copy's, its bytes the recording's), and the
    counters say so (the copies' bytes up, no segment left); a handed-over copy freed as a dead copy is (pool_put with its
    prefix's class) is the block the pool gives next;
  - grow: a recording that outgrows its segment keeps its bytes across the move;
  - mixed: big and small recordings on one command buffer, every copy's bytes right, and the big ones handed over once the hint settles;
  - off: HOS_NVN_REC_HANDOVER=0: every recording copied, the segment kept;
  - ab: the A/B's side with it off: copied; back: handed over again;
  - nopool: NVN_NO_POOL=1: copied, the segment kept.

python -I -B tests/test_rec_handover.py [--cc gcc]
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
typedef long long LONG64;
typedef int SRWLOCK;
#define SRWLOCK_INIT 0
static void AcquireSRWLockExclusive(SRWLOCK *l) { CHECK(!(*l)++); }
static void ReleaseSRWLockExclusive(SRWLOCK *l) { CHECK(!--*l); }
static LONG64 InterlockedAdd64(volatile LONG64 *p, LONG64 v) { return *p += v; }
static LONG64 InterlockedIncrement64(volatile LONG64 *p) { return ++*p; }
static LONG64 InterlockedDecrement64(volatile LONG64 *p) { return --*p; }
static void *hos_xmalloc(size_t n, const char *what) { (void)what; void *p = malloc(n); CHECK(p); return p; }
static int ab_side = -1, ab_value;
static int hos_ab_value(const char *name, int normal) {
    CHECK(!strcmp(name, "HOS_NVN_REC_HANDOVER"));
    return ab_side == 1 ? ab_value : normal;
}
static volatile LONG64 seg_bytes, seg_count, copy_bytes;
@OPRES@
@POOL@
@POOLFN@
@SEG@
@RESERVE@
@PREFIX@
@FLATTEN@
@END@

static uint8_t *last_seg;   /* (the segment a recording ended in) */
/* a recording of n bytes (a multiple of 8, at least 16): BEGIN, words from seed, END, as cmd_alloc writes them */
static uint8_t *record(cmd_rec *r, size_t n, uint32_t seed) {
    r->cur = r->seg;
    for (size_t at = 0; at < n; at += 8) {
        cmd_reserve_rec(r, 8);
        const uint64_t w = ((uint64_t)seed << 32) | (uint32_t)at;
        memcpy(r->cur, &w, 8);
        r->cur += 8;
    }
    last_seg = r->seg;
    return end_recording_copy(r);
}
static int intact(const uint8_t *h, size_t n, uint32_t seed) {
    const copy_prefix *pre = (const copy_prefix *)h - 1;
    if (pre->magic != COPY_MAGIC || pre->bytes != sizeof *pre + n) return 0;
    for (size_t at = 0; at < n; at += 8) {
        uint64_t w;
        memcpy(&w, h + at, 8);
        if (w != (((uint64_t)seed << 32) | (uint32_t)at)) return 0;
    }
    return 1;
}
static void drop(uint8_t *h) {   /* (a dead copy freed: release_copies, free_dead_copies) */
    copy_prefix *pre = (copy_prefix *)h - 1;
    copy_bytes -= (LONG64)pre->bytes;
    pool_put(pre, (unsigned)pre->cls);
}

int main(int argc, char **argv) {
    CHECK(argc == 2);
    const char *c = argv[1];
    cmd_rec r = {0};
    if (!strcmp(c, "handover")) {
        uint8_t *a = record(&r, 3000, 1);
        CHECK(intact(a, 3000, 1) && !r.seg && seg_count == 0 && seg_bytes == 0);   /* (copied; the 64 KiB segment back) */
        for (int i = 0; i < 4; i++) {
            const LONG64 before = copy_bytes;
            record(&r, 8, 0);   /* (warm: a segment) */
            r.cur = r.seg;
            uint8_t *seg = r.seg;
            CHECK(seg_count == 1 && seg_class(&r) == pool_class(3000 + OP_RESERVE + SEG_PREFIX));
            uint8_t *h = record(&r, 3000, 2 + i);
            CHECK(h == seg && intact(h, 3000, 2 + i) && !r.seg && seg_count == 0 && seg_bytes == 0);
            CHECK(copy_bytes > before && ((copy_prefix *)h - 1)->cls == pool_class(SEG_PREFIX + 3000));
            drop(h);
            unsigned cls;
            CHECK(pool_get(1 << pool_class(SEG_PREFIX + 3000), &cls, "") == (void *)((copy_prefix *)h - 1));
        }
        return 0;
    }
    if (!strcmp(c, "grow")) {
        record(&r, 3000, 1);
        uint8_t *h = record(&r, 40000, 7);   /* (a 4 KiB segment grown to 64 KiB on the way) */
        CHECK(intact(h, 40000, 7));
        return 0;
    }
    if (!strcmp(c, "mixed")) {
        static const size_t sizes[] = {20000, 3000, 20000, 3000, 3000, 3000, 3000, 3000, 3000, 20000, 512, 512, 512};
        int handed = 0;
        for (int i = 0; i < (int)(sizeof sizes / sizeof *sizes); i++) {
            uint8_t *h = record(&r, sizes[i], 100 + i);
            CHECK(intact(h, sizes[i], 100 + i));
            handed += h == last_seg;
        }
        CHECK(handed >= 2);   /* (the big ones once the hint has settled) */
        return 0;
    }
    if (!strcmp(c, "off") || !strcmp(c, "nopool")) {
        for (int i = 0; i < 4; i++) {
            uint8_t *h = record(&r, 3000, 1 + i);
            CHECK(intact(h, 3000, 1 + i) && h != r.seg && r.seg && seg_count == 1);
        }
        return 0;
    }
    if (!strcmp(c, "ab")) {
        record(&r, 3000, 1);
        record(&r, 8, 0);
        ab_side = 1, ab_value = 0;
        uint8_t *s = r.seg, *h = record(&r, 3000, 2);
        CHECK(intact(h, 3000, 2) && h != s && r.seg == s);
        ab_side = 0;
        h = record(&r, 3000, 3);
        CHECK(intact(h, 3000, 3) && h == s && !r.seg);
        return 0;
    }
    return 2;
}
'''

CASES = (
    ("handover", {}, "handover"),
    ("grow", {}, "grow"),
    ("mixed", {}, "mixed"),
    ("off", {"HOS_NVN_REC_HANDOVER": "0"}, "off"),
    ("ab", {}, "ab"),
    ("nopool", {"NVN_NO_POOL": "1"}, "nopool"),
)


def compiler(cc):
    for c in ([cc] if cc else []) + [os.environ.get("CC"), "clang", "gcc", "cc"]:
        if c and shutil.which(c):
            return c
    raise SystemExit("test_rec_handover: no C compiler")


def fixture():
    src = (ROOT / "host" / "nvn_native.c").read_text(encoding="utf-8")
    parts = {
        "@OPRES@": "#define OP_RESERVE 24\n" if "#define OP_RESERVE 24\n" in src else None,
        "@POOL@": between(src, "#define POOL_MIN", "static int pool_off(void) {"),
        "@POOLFN@": between(src, "static int pool_off(void) {", "\n/* (hookshot) A command buffer's recording buffer"),
        "@SEG@": between(src, "/* (hint: the bytes its recordings need", "#define CMD_CHUNK_BITS"),
        "@RESERVE@": block(src, "static void cmd_reserve_rec("),
        "@PREFIX@": between(src, "#define COPY_MAGIC", "/* released copies wait"),
        "@FLATTEN@": block(src, "static uint8_t *flatten("),
        "@END@": block(src, "static uint8_t *end_recording_copy("),
    }
    for k, v in parts.items():
        if v is None:
            raise SystemExit("FAIL: host/nvn_native.c no longer has what %s cuts" % k)
    text = FIXTURE
    for k, v in parts.items():
        text = text.replace(k, v)
    return text


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cc", help="C compiler (default: $CC, else clang, gcc or cc)")
    a = p.parse_args()
    cc = compiler(a.cc)
    failed = []
    with tempfile.TemporaryDirectory(prefix="rec-handover-") as tmp:
        c = Path(tmp) / "fixture.c"
        exe = Path(tmp) / "fixture.exe"
        c.write_text(fixture(), encoding="utf-8")
        subprocess.run([cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                        "-I", str(ROOT / "gpu" / "vk" / "compat"), str(c), "-o", str(exe)], check=True)
        for name, env_set, arg in CASES:
            env = {k: v for k, v in os.environ.items() if k not in ("HOS_NVN_REC_HANDOVER", "NVN_NO_POOL")}
            env.update(env_set)
            r = subprocess.run([str(exe), arg], env=env, capture_output=True, text=True, timeout=60)
            print("%s: %s" % (name, "ok" if not r.returncode else (r.stdout + r.stderr).strip()))
            if r.returncode:
                failed.append(name)
    if failed:
        raise SystemExit("FAIL: " + ", ".join(failed))
    print("test_rec_handover: ok")


if __name__ == "__main__":
    main()
