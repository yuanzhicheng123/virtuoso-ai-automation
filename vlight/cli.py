"""cli —— vlight 命令行。

    vlight dump    --host eda-220 --lib tsmcN65_LNA2G4 --out recon/
    vlight import  recon/x.dump.txt --lib-dir mylibs --as tsmcN65_LNA2G4
    vlight list    --lib-dir mylibs
    vlight check   tsmcN65_LNA2G4/lna_2g4_top --lib-dir mylibs
    vlight netlist tsmcN65_LNA2G4/lna_2g4_top --lib-dir mylibs [--cdl] [-o out.scs]
    vlight sim     spec.json
    vlight skill   build.il --lib-dir mylibs --save
    vlight pcell   spiral_ind --param nr=2 --param rad=60u --gds out.gds
    vlight gds     --lib-dir mylibs --top divider -o out.gds
    vlight render  divider --lib-dir mylibs -o out.svg [--view layout]
    vlight version
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (__version__, ade, cdf, connect, drc, extract, gdsio, lvs as lvs_mod,
               ocean, pcell, pdk, place, render, route, spice, spicein)
from .netlist import DeviceMap, Netlister
from .skill import Session
from .skilldump import import_dump, import_tech, read_dump
from .sim import Simulator, run_sweep
from .tb import Testbench, ac, dc, dc_sweep, noise, sp, tran
from .vdb import Design, Library, TechFile, num


# --------------------------------------------------------------------------- #
# 公共
# --------------------------------------------------------------------------- #

def load_design(args) -> Design:
    d = Design()
    for cds in getattr(args, "cdslib", None) or []:
        d.open_cds_lib(cds, readonly=True)
    for ld in getattr(args, "lib_dir", None) or []:
        p = Path(ld)
        if not p.exists():
            continue
        if (p / ".oalib").exists():
            d.add(Library.open(p.name, p))
        else:                                   # 库根目录：子目录里各是一个库
            for sub in sorted(x for x in p.iterdir() if x.is_dir()):
                if (sub / ".oalib").exists():
                    d.add(Library.open(sub.name, sub))
    return d


def open_master(design: Design, master: str):
    lib, _, cell = master.partition("/")
    if not cell:
        lib, cell = next(iter(design.libs)), lib
    return lib, cell


def lib_root(args) -> Path:
    """--lib-dir 是 append 列表，写库时统一取第一个作为根目录。"""
    dirs = getattr(args, "lib_dir", None) or []
    return Path(dirs[0]) if dirs else Path("libs")


def tech_from(args) -> TechFile:
    tech = TechFile()
    for path in getattr(args, "layermap", None) or []:
        tech = import_tech(path, tech)
    if not tech.layers:                      # 内置演示层表（层号是占位的！）
        print("提示: 未给 --layermap，GDS 层号用内置占位值，正式出流片数据请接 PDK layermap",
              file=sys.stderr)
        for name, ln in (("M9", 74), ("M8", 73), ("MIM", 60), ("OD", 6),
                         ("PO", 17), ("CO", 30), ("M1", 37), ("VIA8", 85),
                         ("M9PIN", 90)):
            tech.add(name, ln)
            tech.layers[name].purposes["drawing"] = 0
            tech.gds_map[(name, "drawing")] = (ln, 0)
    return tech


def analysis_from(d: dict):
    kind = d.get("kind", "ac")
    name = d.get("name", kind)
    args = {k: v for k, v in d.items() if k not in ("kind", "name")}
    return {"dc": dc, "ac": ac, "tran": tran, "noise": noise,
            "sp": sp, "dc_sweep": dc_sweep}[kind](name=name, **args)


def build_tb(design: Design, spec: dict) -> Testbench:
    tb = Testbench(design, spec["top"], flatten=spec.get("flatten", False),
                   title=spec.get("title", "vlight testbench"),
                   strict=spec.get("strict", True))
    pdk_spec = spec.get("pdk")
    if pdk_spec:
        setup = {"tsmc65": pdk.tsmc65, "tsmc65_tt": pdk.tsmc65}.get(pdk_spec)
        if setup:
            pdk.apply(tb, setup(spec.get("corner", "tt")))
    for path, section in spec.get("includes", []):
        tb.include(path, section)
    tb.var(**spec.get("variables", {}))
    tb.option(**spec.get("options", {}))
    for line in spec.get("elements", []):
        tb.element(line)
    for p in spec.get("ports", []):
        tb.port(p.get("name", "P"), p["node"], p.get("ref", "0"),
                r=num(p.get("r", 50)) or 50.0, mag=num(p.get("mag", 1)) or 0.0,
                dc=num(p.get("dc", 0)) or 0.0)
    for a in spec.get("analyses", []):
        tb.analysis(analysis_from(a))
    if spec.get("saves"):
        tb.save(*spec["saves"])
    return tb


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #

def cmd_version(args) -> int:
    print(f"vlight {__version__}  (python {sys.version.split()[0]})")
    return 0


def cmd_dump(args) -> int:
    from .bridge import run_dump
    res = run_dump(args.host, args.lib, cdslib=args.cdslib_file,
                   workdir=args.workdir, virtuoso=args.virtuoso,
                   timeout=args.timeout, defines=args.define)
    if not res.ok:
        print("导出失败，日志尾部：\n" + res.log[-2500:], file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / (args.lib[0] + ".dump.txt")
    path.write_text(res.text)
    print(f"已写入 {path}  ({len(res.text.splitlines())} 行)")
    if args.import_to:
        design = import_dump(res.text)
        for lib in design.libs.values():
            lib.path = Path(args.import_to) / lib.name
            lib.save()
        print("已导入到", args.import_to)
    return 0


def cmd_import(args) -> int:
    design = read_dump(args.dump, args.only)
    base = lib_root(args)
    base.mkdir(parents=True, exist_ok=True)
    for lib in design.libs.values():
        lib.path = base / lib.name
        lib.save()
        print(f"{lib.name}: {len(lib.cells)} cells -> {lib.path}")
    return 0


def cmd_list(args) -> int:
    design = load_design(args)
    for name, lib in design.libs.items():
        print(f"{name}  {lib.path}")
        for cell in lib.cells.values():
            views = list(cell.schematics) + list(cell.layouts)
            extra = ""
            if cell.schematics:
                s = cell.schematic()
                extra = f"  insts={len(s.insts)} nets={len(s.nets)} pins={len(s.pins)}"
            print(f"  {cell.name:20s} views={views}{extra}")
    return 0


def cmd_check(args) -> int:
    design = load_design(args)
    lib, cell = open_master(design, args.master)
    sch = design.schematic(f"{lib}/{cell}")
    issues = sch.check()
    print(f"{lib}/{cell}: {sch.stats()}")
    for i in issues:
        print("  !", i)
    print("OK" if not issues else f"{len(issues)} 个问题")
    return 0 if not issues else 1


def cmd_netlist(args) -> int:
    design = load_design(args)
    lib, cell = open_master(design, args.master)
    nl = Netlister(design, DeviceMap(), strict=not args.lenient)
    text = nl.cdl(f"{lib}/{cell}") if args.cdl else nl.subckts(f"{lib}/{cell}")
    if args.out:
        Path(args.out).write_text(text + "\n")
        print("->", args.out)
    else:
        print(text)
    return 0


def cmd_sim(args) -> int:
    spec = json.loads(Path(args.spec).read_text())
    design = load_design(args)
    tb = build_tb(design, spec)
    sim = Simulator(spectre=spec.get("spectre", args.spectre or "spectre"),
                    host=spec.get("host", args.host), workdir=args.workdir,
                    timeout=spec.get("timeout", 1800),
                    engine=spec.get("engine", getattr(args, "engine", None) or "spectre"))
    res = sim.run(tb, name=spec.get("name"))
    print(json.dumps(res.summary(), indent=1, ensure_ascii=False))
    if spec.get("outputs"):
        vals = res.metrics(spec["outputs"], spec.get("output_plot"))
        print(json.dumps(vals, indent=1, ensure_ascii=False))
        if args.out:
            Path(args.out).write_text(json.dumps(vals, indent=1, ensure_ascii=False))
    return 0


def cmd_sweep(args) -> int:
    spec = json.loads(Path(args.spec).read_text())
    design = load_design(args)
    tb = build_tb(design, spec)
    sim = Simulator(spectre=spec.get("spectre", args.spectre or "spectre"),
                    host=spec.get("host", args.host), workdir=args.workdir)
    sw = run_sweep(tb, spec["sweep"], sim, spec.get("outputs"),
                   parallel=spec.get("parallel", 4))
    rows = sw.table()
    for r in rows:
        print(", ".join(f"{k}={v}" for k, v in r.items()))
    if args.out:
        sw.to_csv(args.out)
        print("->", args.out)
    best = spec.get("best")
    if best:
        p = sw.best(best, spec.get("maximize", True))
        print("best:", p.values if p else None, p.metrics if p else None)
    return 0


def cmd_skill(args) -> int:
    design = load_design(args)
    s = Session(design)
    val = s.run_file(args.file)
    if val is not None:
        print("=>", val)
    if args.save:
        for name, lib in s.design.libs.items():
            if str(lib.path).startswith("<"):
                target = lib_root(args) / name
                lib.path = target
                lib.save()
                print(f"saved {name} -> {target}")
            else:
                lib.save()
    if args.netlist:
        for master in args.netlist:
            print(Netlister(design).subckts(master))
    return 0


def cmd_pcell(args) -> int:
    params = {}
    for kv in args.param:
        k, _, v = kv.partition("=")
        params[k] = num(v) if num(v) is not None else v
    lay, metrics = pcell.make(args.kind, cell=args.cell, **params)
    print(json.dumps({k: (f"{v:.6g}" if isinstance(v, float) else v)
                      for k, v in metrics.items()}, indent=1))
    if args.lib_dir:
        root = lib_root(args) / "vlightLib"
        root.mkdir(parents=True, exist_ok=True)
        lib = Library.open("vlightLib", root)
        lib.cell(args.cell, create=True).layouts["layout"] = lay
        lib.save()
        print("-> 库", root / args.cell / "layout")
    if args.svg:
        render.write_svg(render.layout_svg(lay), args.svg)
        print("->", args.svg)
    if args.gds:
        tmp = Library("tmp", lib_root(args) / "_tmp_pcell")
        tmp.cell(args.cell, create=True).layouts["layout"] = lay
        gdsio.write_gds(args.gds, tmp, args.cell, tech_from(args))
        print("->", args.gds, gdsio.verify(args.gds)["layers"])
    return 0


def cmd_cdf(args) -> int:
    db = cdf.CDFdb()
    if args.dump:
        db.from_dump_text(Path(args.dump).read_text())
        print(f"从 dump 建了 {len(db)} 个 master 的 CDF")
    design = load_design(args)
    lib, cell = open_master(design, args.master) if args.master else (None, None)
    c = db.get(lib, cell) if lib else None
    if c is None:
        print("没有这个 master 的 CDF", file=sys.stderr)
        return 2
    print(f"{c.lib}/{c.cell}  params={len(c.params)}  nlproc={c.netlist_procedure or '-'}")
    for p in c.params:
        print(f"  {p.name:22s} {str(p.type):9s} default={p.default!r}")
    if args.inst and design is not None:
        where, _, iname = args.inst.partition(":")
        sch = design.schematic(where)
        inst = sch.inst(iname)
        print("\n实例有效值:")
        for k, v in sorted(db.effective(inst).items()):
            print(f"  {k:22s} = {v!r}")
    return 0


def cmd_ade(args) -> int:
    if args.action == "import":
        st = ade.import_state_dir(args.path)
        if args.out:
            st.save(args.out)
            print("->", args.out)
        print(json.dumps(st.summary(), indent=1, ensure_ascii=False))
        for o in st.outputs[:5]:
            print(f"  output {o.name} = {o.expression[:100]}")
        return 0
    st = ade.ADELState.load(args.path)
    print(json.dumps(st.summary(), indent=1, ensure_ascii=False))
    if args.action == "sim":
        design = load_design(args)
        tb = st.to_testbench(design, args.top, flatten=args.flatten)
        sim = Simulator(spectre=args.spectre or "spectre", host=args.host,
                        workdir=args.workdir)
        res = sim.run(tb, name="ade")
        print(json.dumps(res.summary(), indent=1, ensure_ascii=False))
        if args.outputs:
            outs = dict(kv.split("=", 1) for kv in args.outputs)
            print(json.dumps(res.metrics(outs, args.plot), indent=1))
    return 0


def cmd_ocean(args) -> int:
    design = load_design(args)
    sim = Simulator(spectre=args.spectre or "spectre", host=args.host,
                    workdir=args.workdir)
    oc = ocean.Ocean(design, sim, workdir=args.workdir)
    oc.run_script(Path(args.script).read_text())
    for line in oc.printed:
        print("=>", line)
    return 0


def cmd_spicein(args) -> int:
    nl = spicein.read(args.netlist)
    print(json.dumps(nl.stats(), indent=1, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(nl.to_scs())
        print("->", args.out)
    if args.dump_lib:
        design = nl.to_design()
        for lib in design.libs.values():
            lib.path = Path(args.dump_lib) / lib.name
            lib.save()
            print(f"导入 {lib.name}: {len(lib.cells)} 个 subckt -> {lib.path}")
    if args.sim:
        tb = spicein.to_testbench(nl)
        sim = Simulator(spectre=args.spectre or "spectre", host=args.host,
                        workdir=args.workdir)
        res = sim.run(tb, name="spicein")
        print(json.dumps(res.summary(), indent=1, ensure_ascii=False))
    return 0


def cmd_drc(args) -> int:
    design = load_design(args)
    lib, cell = open_master(design, args.master)
    lay = design.lib(lib).cell(cell).layouts.get(args.view)
    if lay is None:
        print("没有版图视图", file=sys.stderr)
        return 2
    deck = drc.Deck.from_json(args.rules) if args.rules else drc.Deck.from_techfile(tech_from(args))
    viol = drc.check(lay, deck, layers=args.layers or None)
    print(drc.report(viol))
    return 0 if not viol else 1


def cmd_extract(args) -> int:
    design = load_design(args)
    lib, cell = open_master(design, args.master)
    lay = design.lib(lib).cell(cell).layouts.get(args.view)
    if lay is None:
        print("没有版图视图", file=sys.stderr)
        return 2
    rules = extract.ExtractRules.demo()
    for spec in args.rsh or []:
        name, _, val = spec.partition("=")
        rules.rsh[name] = float(val)
    nl = extract.extract(lay, rules, prefix=args.prefix)
    info = extract.summary(nl)
    print(json.dumps(info, indent=1))
    if args.out:
        Path(args.out).write_text(nl.to_scs())
        print("->", args.out)
    return 0


def cmd_lvs(args) -> int:
    if args.ref and args.cmp:                      # 网表对网表：图同构
        rep = lvs_mod.compare(lvs_mod.Graph.from_netlist(spicein.read(args.ref)),
                              lvs_mod.Graph.from_netlist(spicein.read(args.cmp)))
        print(rep.text())
        return 0 if rep.ok else 1
    design = load_design(args)
    if not args.master:
        print("给 --ref/--cmp 或一个 LIB/CELL", file=sys.stderr)
        return 2
    lib, cell = open_master(design, args.master)
    c = design.lib(lib).cell(cell)
    sch = c.schematic()
    lay = c.layouts.get(args.view)
    if lay is None:
        print("没有版图视图", file=sys.stderr)
        return 2
    conn = connect.extract(lay)
    rep = connect.lvs(sch, conn)
    print(rep.text())
    print("提取到节点:", {k: len(v) for k, v in conn.nets.items()})
    return 0 if rep.ok else 1


def cmd_connect(args) -> int:
    design = load_design(args)
    lib, cell = open_master(design, args.master)
    lay = design.lib(lib).cell(cell).layouts.get(args.view)
    if lay is None:
        print("没有版图视图", file=sys.stderr)
        return 2
    conn = connect.extract(lay)
    print(json.dumps(conn.summary(), indent=1, ensure_ascii=False))
    for name, ids in conn.nets.items():
        print(f"  {name:12s} shapes={len(ids)}")
    return 0


def cmd_gds(args) -> int:
    design = load_design(args)
    top = args.top.split("/")[-1]
    if args.lib:
        lib = design.lib(args.lib)
    else:                                   # 自动找带这个 cell 版图的库
        lib = next((l for l in design.libs.values()
                    if top in l.cells and l.cell(top).layouts), None)
        if lib is None:
            print(f"没有任何库里有 {top} 的 layout 视图", file=sys.stderr)
            return 2
    gdsio.write_gds(args.out, lib, top, tech_from(args))
    info = gdsio.verify(args.out)
    print(json.dumps(info, indent=1, ensure_ascii=False))
    return 0


def cmd_render(args) -> int:
    design = load_design(args)
    lib, cell = open_master(design, args.master)
    c = design.lib(lib).cell(cell)
    if args.view == "layout":
        lay = c.layouts.get(args.viewname or "layout")
        if lay is None:
            print("没有版图视图", file=sys.stderr)
            return 2
        svg = render.layout_svg(lay)
    else:
        svg = render.schematic_svg(c.schematic())
    render.write_svg(svg, args.out)
    print("->", args.out)
    return 0


def cmd_view(args) -> int:
    """把库里的 cell 渲染成一个 HTML 页面（浏览器即 GUI）。"""
    design = load_design(args)
    sections = []
    for lib in design.libs.values():
        for cell in lib.cells.values():
            if cell.schematics:
                sections.append((f"{lib.name}/{cell.name}/schematic",
                                 render.schematic_svg(cell.schematic())))
            for view, lay in cell.layouts.items():
                sections.append((f"{lib.name}/{cell.name}/{view}",
                                 render.layout_svg(lay)))
    out = Path(args.out)
    render.write_html(sections, out, title="vlight")
    print("->", out)
    return 0


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vlight", description="轻量 Virtuoso 核心复现")
    p.add_argument("--lib-dir", action="append", default=[], help="vlight 库目录（可多次）")
    p.add_argument("--cdslib", action="append", default=[], help="Virtuoso cds.lib（只读加载）")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("version"); s.set_defaults(func=cmd_version)

    s = sub.add_parser("dump", help="在 EDA 机器上跑 headless SKILL 导出设计")
    s.add_argument("--host", required=True)
    s.add_argument("--lib", action="append", required=True)
    s.add_argument("--cdslib-file", help="远端已有的 cds.lib（如 PDK 那份）")
    s.add_argument("--define", action="append", default=[],
                   help="追加 DEFINE LIB=PATH（可多次）")
    s.add_argument("--workdir", default="/tmp/vlight_dump")
    s.add_argument("--virtuoso", default="virtuoso")
    s.add_argument("--timeout", type=int, default=300)
    s.add_argument("--out", default="recon")
    s.add_argument("--import-to", help="直接导入成 vlight 库目录")
    s.set_defaults(func=cmd_dump)

    s = sub.add_parser("import", help="导入 SKILL dump 文本")
    s.add_argument("dump")
    s.add_argument("--only", help="只导入某个库")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("list", help="列出库/单元/视图")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("check", help="连通性检查")
    s.add_argument("master")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("netlist", help="出网表")
    s.add_argument("master")
    s.add_argument("--cdl", action="store_true")
    s.add_argument("--lenient", action="store_true")
    s.add_argument("-o", "--out")
    s.set_defaults(func=cmd_netlist)

    s = sub.add_parser("sim", help="按 JSON spec 跑仿真")
    s.add_argument("spec")
    s.add_argument("--host")
    s.add_argument("--spectre")
    s.add_argument("--engine", choices=["spectre", "vlight"], default="spectre",
                   help="vlight = 自研 SPICE（无需 license）")
    s.add_argument("--workdir", default="sim")
    s.add_argument("-o", "--out")
    s.set_defaults(func=cmd_sim)

    s = sub.add_parser("sweep", help="按 JSON spec 扫参数")
    s.add_argument("spec")
    s.add_argument("--host")
    s.add_argument("--spectre")
    s.add_argument("--workdir", default="sim")
    s.add_argument("-o", "--out")
    s.set_defaults(func=cmd_sweep)

    s = sub.add_parser("skill", help="跑 SKILL 脚本")
    s.add_argument("file")
    s.add_argument("--save", action="store_true")
    s.add_argument("--netlist", action="append", default=[])
    s.set_defaults(func=cmd_skill)

    s = sub.add_parser("pcell", help="生成参数化版图")
    s.add_argument("kind", choices=list(pcell.PCELS))
    s.add_argument("--cell", default=None)
    s.add_argument("--param", action="append", default=[])
    s.add_argument("--svg")
    s.add_argument("--gds")
    s.add_argument("--layermap", action="append", default=[])
    s.set_defaults(func=cmd_pcell)

    s = sub.add_parser("gds", help="导出库里的版图为 GDSII")
    s.add_argument("--lib")
    s.add_argument("--top", required=True)
    s.add_argument("-o", "--out", default="out.gds")
    s.add_argument("--layermap", action="append", default=[])
    s.set_defaults(func=cmd_gds)

    s = sub.add_parser("render", help="渲染 SVG")
    s.add_argument("master")
    s.add_argument("--view", default="schematic")
    s.add_argument("--viewname")
    s.add_argument("-o", "--out", default="out.svg")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("cdf", help="查看 CDF 参数")
    s.add_argument("master", nargs="?")
    s.add_argument("--dump", help="从 SKILL dump 建 CDF")
    s.add_argument("--inst", help="显示某实例的有效值，形如 LIB/CELL:INSTNAME")
    s.set_defaults(func=cmd_cdf)

    s = sub.add_parser("ade", help="ADE 状态：导入/查看/仿真")
    s.add_argument("action", choices=["import", "show", "sim"])
    s.add_argument("path")
    s.add_argument("--top")
    s.add_argument("--flatten", action="store_true")
    s.add_argument("--host")
    s.add_argument("--spectre")
    s.add_argument("--workdir", default="sim")
    s.add_argument("--plot")
    s.add_argument("--outputs", action="append", default=[],
                   help="name=表达式，可多次")
    s.add_argument("-o", "--out")
    s.set_defaults(func=cmd_ade)

    s = sub.add_parser("ocean", help="跑 OCEAN 脚本")
    s.add_argument("script")
    s.add_argument("--host")
    s.add_argument("--spectre")
    s.add_argument("--workdir", default="ocean")
    s.set_defaults(func=cmd_ocean)

    s = sub.add_parser("spicein", help="导入 spectre/spice 网表")
    s.add_argument("netlist")
    s.add_argument("--dump-lib", help="转成 vlight 库目录")
    s.add_argument("--sim", action="store_true")
    s.add_argument("--host")
    s.add_argument("--spectre")
    s.add_argument("--workdir", default="sim")
    s.add_argument("-o", "--out")
    s.set_defaults(func=cmd_spicein)

    s = sub.add_parser("drc", help="规则检查")
    s.add_argument("master")
    s.add_argument("--view", default="layout")
    s.add_argument("--rules", help="规则 JSON")
    s.add_argument("--layermap", action="append", default=[])
    s.add_argument("--layers", action="append", default=[])
    s.set_defaults(func=cmd_drc)

    s = sub.add_parser("lvs", help="LVS：网表图同构，或版图 vs 原理图")
    s.add_argument("master", nargs="?")
    s.add_argument("--view", default="layout")
    s.add_argument("--ref", help="参考网表 .scs")
    s.add_argument("--cmp", help="待比较网表 .scs")
    s.set_defaults(func=cmd_lvs)

    s = sub.add_parser("extract", help="从版图提取寄生 RC 网表")
    s.add_argument("master")
    s.add_argument("--view", default="layout")
    s.add_argument("--prefix", default="X")
    s.add_argument("--rsh", action="append", default=[], help="层=方阻，如 M1=0.1")
    s.add_argument("-o", "--out")
    s.set_defaults(func=cmd_extract)

    s = sub.add_parser("connect", help="版图连接性提取")
    s.add_argument("master")
    s.add_argument("--view", default="layout")
    s.set_defaults(func=cmd_connect)

    s = sub.add_parser("view", help="渲染整个库成一个 HTML")
    s.add_argument("-o", "--out", default="view.html")
    s.set_defaults(func=cmd_view)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "kind", None) and args.cmd == "pcell" and not args.cell:
        args.cell = args.kind.upper()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
