"""The GPU ASTC decoder is given every push constant it reads.

gpu/vk/host_shaders/astc_decoder.comp reads its push constants by offset from the block between BEGIN_PUSH_CONSTANTS and
END_PUSH_CONSTANTS; gpu/vk/vk_texture.cpp fills struct AstcPush and pushes sizeof(AstcPush) bytes. The shader's
blocks_x and blocks_y (the level's size in blocks: a dispatch over a scratch larger than the level stops there) came with
the decoder's rewrite while the host's AstcPush kept its eight words, so the last two were never pushed: their values are
undefined, and on AMD's Windows driver every invocation stopped at `pos.x >= blocks_x`. Every texture decoded on the GPU
was left black (Castle Town Market's houses, the hearts' fill and the minimap on an RX 9070 XT; RX_VK_ASTC_CPU=1, which
decodes on the CPU, drew them).

This check reads both sources, no GPU and no game:
  - the shader's push block and AstcPush come to the same number of 32-bit words, field for field in order;
  - every place in vk_texture.cpp that fills an AstcPush (it sets native_tiling) also sets each field the shader reads.

    python -I -B tests/test_astc_push_layout.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHADER = ROOT / "gpu/vk/host_shaders/astc_decoder.comp"
HOST = ROOT / "gpu/vk/vk_texture.cpp"

WORDS = {"uint": 1, "int": 1, "float": 1, "uvec2": 2, "ivec2": 2, "vec2": 2}


def shader_fields(text):
    """(name, words) of the push block's fields, in order."""
    body = re.search(r"^BEGIN_PUSH_CONSTANTS\s*$(.*?)^END_PUSH_CONSTANTS\s*$", text, re.M | re.S)
    if not body:
        raise SystemExit(f"FAIL: no BEGIN_PUSH_CONSTANTS block in {SHADER.relative_to(ROOT)}")
    fields = []
    for line in body.group(1).splitlines():
        m = re.match(r"\s*UNIFORM\(\d+\)\s+(\w+)\s+(\w+)\s*;", line)
        if m:
            if m.group(1) not in WORDS:
                raise SystemExit(f"FAIL: push constant type {m.group(1)} not known to this check")
            fields.append((m.group(2), WORDS[m.group(1)]))
    return fields


def host_fields(text):
    """(name, words) of struct AstcPush's fields, in order."""
    m = re.search(r"struct AstcPush\s*\{(.*?)\};", text, re.S)
    if not m:
        raise SystemExit(f"FAIL: no struct AstcPush in {HOST.relative_to(ROOT)}")
    fields = []
    body = re.sub(r"//[^\n]*|/\*.*?\*/", "", m.group(1), flags=re.S)
    for decl in body.split(";"):
        decl = decl.strip()
        if not decl:
            continue
        if not decl.startswith("uint32_t "):
            raise SystemExit(f"FAIL: AstcPush field '{decl}' is not uint32_t")
        for name in decl[len("uint32_t "):].split(","):
            name = name.strip()
            arr = re.match(r"(\w+)\[(\d+)\]$", name)
            fields.append((arr.group(1), int(arr.group(2))) if arr else (name, 1))
    return fields


def fill_sites(text):
    """The text of each place that fills an AstcPush, from its declaration to its first use: the push, or the copy of
    the AstcPiece that holds it."""
    sites = []
    for m in re.finditer(r"AstcPush\s*&?\s*(\w+)\s*(?:\{\}|=\s*[\w.]+)\s*;", text):
        var = m.group(1)
        line = text.count("\n", 0, m.start()) + 1
        use = re.compile(r"vkCmdPushConstants\(|\bAstcPiece\s+\w+\s*=").search(text, m.end())
        if not use:
            raise SystemExit(f"FAIL: vk_texture.cpp:{line}: the AstcPush {var} is never pushed")
        end = use.start()
        sites.append((line, var, text[m.start():end]))
    return sites


def main():
    shader = shader_fields(SHADER.read_text(encoding="utf-8"))
    host = host_fields(HOST.read_text(encoding="utf-8"))
    errors = []
    sw = sum(w for _, w in shader)
    hw = sum(w for _, w in host)
    if sw != hw:
        errors.append(f"the shader reads {sw} words of push constants, AstcPush pushes {hw}: "
                      f"shader {[n for n, _ in shader]}, host {[n for n, _ in host]}")
    for i, ((sn, sw1), (hn, hw1)) in enumerate(zip(shader, host)):
        if sn != hn or sw1 != hw1:
            errors.append(f"field {i}: the shader has {sn} ({sw1} words), AstcPush {hn} ({hw1} words)")
    sites = fill_sites(HOST.read_text(encoding="utf-8"))
    if not sites:
        errors.append("no place fills an AstcPush")
    for line, var, body in sites:
        for name, _ in shader:
            if not re.search(rf"\b{var}\.{name}\b", body):
                errors.append(f"vk_texture.cpp:{line}: AstcPush {var} never sets {name}, which the shader reads")
    if errors:
        for e in errors:
            print("FAIL:", e)
        return 1
    print(f"PASS: {len(shader)} push constants ({sw} words) match AstcPush; {len(sites)} fill sites set every one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
