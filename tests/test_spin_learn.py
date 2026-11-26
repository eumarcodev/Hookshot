"""A wait spins about as long as its object's spins need to see their flag (HOS_SPIN_LEARN), game-free.

host/nn_os_sync.c's spin_until_set and spin_until_free (x86-64: before an event, semaphore, queue or mutex wait sleeps)
spin for spin_limit_ns: SPIN_LEARN_MARGIN (4) times the object's spin_hit_ns, what its spins took to see the flag,
at least SPIN_LEARN_FLOOR_NS (2 us) and at most the thread's whole spin (hos_spin_ns: 20 us on the serial threads, 5 us
on the others); the whole spin while that is not known. spin_end learns: a hit moves spin_hit_ns a quarter of the way to
what it took, a miss lets the next spin run half as long again (and the object is not known any more once that reaches
the whole spin). HOS_SPIN_LEARN=0, or the A/B's other side, spins the whole spin, and what was learnt is kept.

The fixture runs the real spin_end and spin_limit_ns cut from the source over a stub Obj, a stub spin length, hookshot_env.h's
env_on_or and a stub hos_ab_value. One child process a case (the switch is read once):

  - learn: an unknown object spins the whole 5 us; a hit at 0.5 us gives 2 us (the floor); hits at 1 us settle at 4 us;
    a miss there makes the object unknown again (the whole spin); on a serial thread (20 us) a hit at 1 us gives the floor,
    and each miss makes it half as long again; a hit longer than the limit counts as the limit; the miss counter is the
    adaptive back-off's as before (a hit clears it, misses stop at 4);
  - off: HOS_SPIN_LEARN=0: the whole spin after the same hits, the learning kept;
  - ab: the A/B's side with learning off: the whole spin; its side back: the learnt spin.

python -I -B tests/test_spin_learn.py [--cc gcc]
"""
import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROM = "enum { SPIN_LEARN_MARGIN = 4, SPIN_LEARN_FLOOR_NS = 2000 };\n"
TO = "static void spin_until_set("

FIXTURE = r'''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "hookshot_env.h"
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s\n", __LINE__, #x); exit(1); } } while (0)
typedef struct Obj { int spin_misses, spin_waits, spin_hit_ns; } Obj;
static int64_t whole = 5000;   /* (hos_spin_ns: 5 us, a thread that is not serial) */
static int64_t hos_spin_ns(void) { return whole; }
static int ab_side = -1, ab_value;   /* (the A/B: -1 none) */
static int hos_ab_value(const char *name, int normal) {
    CHECK(!strcmp(name, "HOS_SPIN_LEARN"));
    return ab_side == 1 ? ab_value : normal;
}
@CUT@
static int64_t hit(Obj *o, int64_t took) { const int64_t l = spin_limit_ns(o); spin_end(o, o->spin_misses, took, l); return l; }
static int64_t miss(Obj *o) { const int64_t l = spin_limit_ns(o); spin_end(o, o->spin_misses, -1, l); return l; }

int main(int argc, char **argv) {
    CHECK(argc == 2);
    Obj o = {0};
    if (!strcmp(argv[1], "learn")) {
        CHECK(spin_limit_ns(&o) == 5000);                          /* (not known: the whole spin) */
        CHECK(hit(&o, 500) == 5000 && spin_limit_ns(&o) == 2000);  /* (0.5 us: the floor) */
        for (int i = 0; i < 40; i++) hit(&o, 1000);
        CHECK(o.spin_hit_ns >= 990 && o.spin_hit_ns <= 1000 && spin_limit_ns(&o) >= 3960 && spin_limit_ns(&o) <= 4000);
        CHECK(miss(&o) <= 4000 && o.spin_hit_ns == 0 && spin_limit_ns(&o) == 5000 && o.spin_misses == 1);
        hit(&o, 1000);
        CHECK(o.spin_misses == 0);   /* (a hit clears the back-off's count) */
        for (int i = 0; i < 6; i++) miss(&o);
        CHECK(o.spin_misses == 4 && spin_limit_ns(&o) == 5000);
        Obj s = {0};                 /* (a serial thread: 20 us) */
        whole = 20000;
        hit(&s, 1000);
        CHECK(spin_limit_ns(&s) == 4000);
        s.spin_hit_ns = 400;
        CHECK(spin_limit_ns(&s) == 2000);
        CHECK(miss(&s) == 2000 && spin_limit_ns(&s) == 3000);
        for (int i = 0; i < 3; i++) {   /* (each miss: half as long again, 3 -> 4.5 -> 6.75 us) */
            const int64_t l = miss(&s);
            CHECK(spin_limit_ns(&s) >= l * 3 / 2 && spin_limit_ns(&s) <= l * 3 / 2 + 8);
        }
        const int64_t l = spin_limit_ns(&s);
        hit(&s, 1000000);            /* (a hit longer than the limit: the limit) */
        CHECK(s.spin_hit_ns <= l);
        return 0;
    }
    for (int i = 0; i < 40; i++) hit(&o, 1000);
    if (!strcmp(argv[1], "off")) {
        CHECK(spin_limit_ns(&o) == 5000 && o.spin_hit_ns >= 990);
        return 0;
    }
    if (!strcmp(argv[1], "ab")) {
        CHECK(spin_limit_ns(&o) <= 4000);
        ab_side = 1, ab_value = 0;
        CHECK(spin_limit_ns(&o) == 5000);
        hit(&o, 1000);
        ab_side = 0;
        CHECK(spin_limit_ns(&o) <= 4000);
        return 0;
    }
    return 2;
}
'''

CASES = (
    ("learn", None, "learn"),
    ("on by default, set", "1", "learn"),
    ("off", "0", "off"),
    ("ab", None, "ab"),
)


def compiler(cc):
    for c in ([cc] if cc else []) + [os.environ.get("CC"), "clang", "gcc", "cc"]:
        if c and shutil.which(c):
            return c
    raise SystemExit("test_spin_learn: no C compiler")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cc", help="C compiler (default: $CC, else clang, gcc or cc)")
    a = p.parse_args()
    src = (ROOT / "host" / "nn_os_sync.c").read_text(encoding="utf-8")
    if src.count(FROM) != 1 or TO not in src:
        raise SystemExit("FAIL: host/nn_os_sync.c has no spin_end / spin_limit_ns block to cut")
    start = src.index(FROM)
    cut = src[start:src.index(TO, start)]
    cc = compiler(a.cc)
    failed = []
    with tempfile.TemporaryDirectory(prefix="spin-learn-") as tmp:
        c = Path(tmp) / "fixture.c"
        exe = Path(tmp) / "fixture.exe"
        c.write_text(FIXTURE.replace("@CUT@", cut), encoding="utf-8")
        subprocess.run([cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                        "-I", str(ROOT / "gpu" / "vk" / "compat"), str(c), "-o", str(exe)], check=True)
        for name, value, arg in CASES:
            env = {k: v for k, v in os.environ.items() if k != "HOS_SPIN_LEARN"}
            if value is not None:
                env["HOS_SPIN_LEARN"] = value
            r = subprocess.run([str(exe), arg], env=env, capture_output=True, text=True, timeout=60)
            print("%s: %s" % (name, "ok" if not r.returncode else (r.stdout + r.stderr).strip()))
            if r.returncode:
                failed.append(name)
    if failed:
        raise SystemExit("FAIL: " + ", ".join(failed))
    print("test_spin_learn: ok")


if __name__ == "__main__":
    main()
