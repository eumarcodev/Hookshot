"""The dirty-page collection's chunk summary never loses a mark (host/rx_backend.c collect_in, nx2/rtgen.py A64_MARK,
host/hos_mem.c hos_mark_written).

Each mark of a written guest page (a64_dirty) also sets its 256-page chunk's summary byte (a64_dirty_sum), after the page's
own mark. collect_in skips a whole chunk whose summary is clear and clears it (an exchange) before it reads the chunk's
pages; a range that covers only part of a chunk leaves its summary as it is. The fixture compiles collect_in as the source
has it, with stand-ins for the guest window, and checks:

  1. marked pages are collected, each once, in order, and their marks cleared;
  2. a chunk whose summary is clear is skipped even with a page marked in it (what the summary is for: a mark is never
     written without its summary, so this only shows the skip is taken);
  3. a range that covers part of a chunk collects the marked pages inside it and leaves the summary set, so a later
     collection of the whole chunk still finds the pages outside the first range;
  4. a mark made after a chunk's summary was cleared (the race the store order covers: page, then summary) is found by the
     next collection.

The source's own text is checked too: the lifted stores' mark (both A64_MARK forms in nx2/rtgen.py) and hos_mark_written
store the page's mark before the summary, and collect_in exchanges the summary before it reads the pages.

python -I -B tests/test_dirty_summary.py [--cc gcc]
"""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))   # (python -I leaves the check's own folder off the path)
from srccut import block

ROOT = Path(__file__).resolve().parent.parent
RUN_ALL_ARGS = ["--cc", "clang"]   # (the compiler the build uses for the runtime, nx2/build.py)

FIXTURE = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define A64_DIRTY_PAGES (1u << 16)
uint8_t a64_dirty[A64_DIRTY_PAGES];
uint8_t a64_dirty_sum[A64_DIRTY_PAGES >> 8];
static uint64_t hos_guest_size(void) { return (uint64_t)A64_DIRTY_PAGES << 12; }
static void mark(uint64_t page) {   /* as A64_MARK and hos_mark_written: the page, then its chunk's summary */
    __atomic_store_n(&a64_dirty[page], 1, __ATOMIC_RELEASE);
    __atomic_store_n(&a64_dirty_sum[page >> 8], 1, __ATOMIC_RELEASE);
}
static int cmp_range_lo(const void *a, const void *b) {
    const uint64_t x = ((const uint64_t *)a)[0], y = ((const uint64_t *)b)[0];
    return x < y ? -1 : x > y;
}
%(COLLECT_IN)s
static int fails;
static void expect(const char *what, uint32_t *got, int64_t n, const uint32_t *want, int64_t nw) {
    int ok = n == nw;
    for (int64_t i = 0; ok && i < n; i++) ok = got[i] == want[i];
    if (!ok) {
        fails++;
        printf("FAIL: %%s: got %%lld pages:", what, (long long)n);
        for (int64_t i = 0; i < n && i < 12; i++) printf(" %%u", got[i]);
        printf("\n");
    }
    free(got);
}
static int64_t collect(uint64_t lo_page, uint64_t hi_page, uint32_t **out) {
    uint64_t r[1][2] = {{lo_page << 12, hi_page << 12}};
    return collect_in((const uint64_t (*)[2])r, 1, out);
}
int main(void) {
    uint32_t *p;
    int64_t n;
    /* 1 */
    mark(3); mark(300); mark(301); mark(1000);
    n = collect(0, 2048, &p);
    { const uint32_t w[] = {3, 300, 301, 1000}; expect("marked pages collected", p, n, w, 4); }
    n = collect(0, 2048, &p);
    expect("marks cleared by the collection", p, n, NULL, 0);
    /* 2 */
    __atomic_store_n(&a64_dirty[600], 1, __ATOMIC_RELEASE);   /* (a page mark with no summary: never written so) */
    n = collect(512, 768, &p);
    expect("a chunk with a clear summary skipped", p, n, NULL, 0);
    a64_dirty[600] = 0;
    /* 3 */
    mark(1030); mark(1200);   /* chunk 4: pages 1024-1279 */
    n = collect(1024, 1100, &p);   /* a part of chunk 4 */
    { const uint32_t w[] = {1030}; expect("a part of a chunk: its marked page", p, n, w, 1); }
    if (!a64_dirty_sum[4]) { fails++; printf("FAIL: a part of a chunk cleared its summary\n"); }
    n = collect(1024, 1280, &p);
    { const uint32_t w[] = {1200}; expect("the chunk's other page found later", p, n, w, 1); }
    /* 4: a mark after the chunk's summary was cleared */
    mark(1500);
    __atomic_exchange_n(&a64_dirty_sum[1500 >> 8], 0, __ATOMIC_ACQ_REL);   /* (a collection got this far, then read nothing) */
    mark(1500);   /* (the writer's mark lands after the exchange: page, then summary again) */
    n = collect(1280, 1536, &p);
    { const uint32_t w[] = {1500}; expect("a mark after the summary's exchange", p, n, w, 1); }
    printf(fails ? "dirty summary: %%d failures\n" : "dirty summary: ok\n", fails);
    return fails != 0;
}
"""



def source_checks():
    errs = []
    rtgen = (ROOT / "nx2" / "rtgen.py").read_text(encoding="utf-8")
    marks = re.findall(r"#define A64_MARK\(a, n\).*?(?=\n#|\"\"\")", rtgen, re.S)
    if len(marks) < 2:
        errs.append("nx2/rtgen.py: the two 64-bit A64_MARK forms not found")
    for m in marks:
        if "a64_dirty_sum" not in m:
            continue
        if m.index("a64_dirty_sum") < m.index("a64_dirty["):
            errs.append("nx2/rtgen.py: an A64_MARK stores the summary before the page's mark")
    if sum("a64_dirty_sum" in m for m in marks) < 2:
        errs.append("nx2/rtgen.py: an A64_MARK form does not set the chunk summary")
    mem = (ROOT / "host" / "hos_mem.c").read_text(encoding="utf-8")
    hm = block(mem, "void hos_mark_written(const void *p, size_t n) {")
    if "a64_dirty_sum" not in hm or hm.index("a64_dirty_sum") < hm.index("a64_dirty[q]"):
        errs.append("host/hos_mem.c: hos_mark_written does not set the summary after the page's mark")
    rx = (ROOT / "host" / "rx_backend.c").read_text(encoding="utf-8")
    ci = block(rx, "static int64_t collect_in(const uint64_t (*ranges)[2], int nranges, uint32_t **pages) {")
    if "__atomic_exchange_n(&a64_dirty_sum" not in ci:
        errs.append("host/rx_backend.c: collect_in does not exchange the summary")
    elif ci.index("__atomic_exchange_n(&a64_dirty_sum") > ci.index("__atomic_exchange_n(&a64_dirty[p]"):
        errs.append("host/rx_backend.c: collect_in reads the pages before it clears the summary")
    return errs, ci


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cc", default=os.environ.get("CC", "gcc"))
    a = ap.parse_args()
    errs, ci = source_checks()
    for e in errs:
        print("FAIL:", e)
    if errs:
        return 1
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "dirty_summary.c"
        src.write_text(FIXTURE % {"COLLECT_IN": ci}, encoding="utf-8")
        exe = Path(d) / ("dirty_summary.exe" if os.name == "nt" else "dirty_summary")
        r = subprocess.run([a.cc, "-std=gnu11", "-O2", "-o", str(exe), str(src)], capture_output=True, text=True)
        if r.returncode:
            print(r.stdout + r.stderr)
            print("FAIL: the fixture does not compile")
            return 1
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print(r.stdout.strip())
        return 0 if r.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
