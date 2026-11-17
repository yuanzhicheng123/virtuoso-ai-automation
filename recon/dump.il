;; dump.il - 用 Virtuoso 自己的 SKILL 解释器把设计对象模型导成文本
printf("SKILLVER=%L\n" getVersion())

procedure(vlNames(l) mapcar(lambda((x) x~>name) l))

procedure(vlDumpCDF(inst)
  let((cdf ps)
    cdf = car(errset(cdfGetInstCDF(inst) t))
    when(cdf
      ps = cdf~>parameters
      foreach(p ps
        printf("    P %s %s = %L\n" p~>name p~>type p~>value)))))

procedure(vlDumpProps(inst)
  let((pl)
    pl = car(errset(inst~>prop t))
    when(pl
      foreach(p pl
        printf("    R %s = %L\n" p~>name p~>value)))))

procedure(vlDumpSch(lib cell)
  let((cv)
    cv = car(errset(dbOpenCellViewByType(lib cell "schematic" "schematic" "r") t))
    if(cv
      progn(
        printf("SCH %s/%s insts=%d nets=%d terms=%d shapes=%d\n"
               lib cell length(cv~>instances) length(cv~>nets) length(cv~>terminals) length(cv~>shapes))
        foreach(inst cv~>instances
          printf("  I %s %s/%s/%s xy=%L orient=%L n=%L\n"
                 inst~>name inst~>libName inst~>cellName inst~>viewName inst~>xy inst~>orient inst~>numInst)
          vlDumpCDF(inst)
          vlDumpProps(inst))
        foreach(net cv~>nets
          printf("  N %s sig=%L termOrder=%L\n" net~>name net~>sigType net~>termOrder)
          foreach(it net~>instTerms
            printf("    T %s.%s\n" it~>inst~>name it~>name)))
        foreach(tm cv~>terminals
          printf("  PIN %s dir=%L net=%L\n" tm~>name tm~>direction (if tm~>net tm~>net~>name "nil")))
        dbClose(cv))
      printf("SCH %s/%s OPEN-FAILED\n" lib cell))))

procedure(vlDumpLib(lib)
  let((dd cells)
    dd = car(errset(ddGetObj(lib) t))
    if(dd
      progn(
        cells = dd~>cells
        printf("LIB %s ncells=%d\n" lib length(cells))
        foreach(c cells printf("  CELL %s views=%L\n" c~>name vlNames(c~>views)))
        foreach(c cells vlDumpSch(lib c~>name)))
      printf("LIB %s MISSING\n" lib))))

vlDumpLib("tsmcN65_LNA2G4")

;; 器件库清单（网表需要支持的原子器件集合）
foreach(lb '("tsmcN65" "analogLib" "basic")
  let((dd)
    dd = car(errset(ddGetObj(lb) t))
    when(dd printf("DEVLIB %s nmaster=%d first=%L\n" lb length(dd~>cells) car(vlNames(dd~>cells))))))

;; 技术文件：层号/purpose（GDS 映射语义）
let((cv tf)
  cv = car(errset(dbOpenCellViewByType("tsmcN65_LNA2G4" "lna_2g4_top" "schematic" "schematic" "r") t))
  when(cv
    tf = car(errset(techGetTechFile(cv) t))
    when(tf
      printf("TECH layers=%d purposes=%d\n" length(tf~>layers) length(tf~>purposes))
      foreach(l tf~>layers
        printf("  L %s num=%L abbr=%L\n" l~>name l~>layerNumber l~>abbreviation))
      foreach(p tf~>purposes
        printf("  P %s num=%L\n" p~>name p~>num)))))

;; CDF 基础参数
foreach(cn '("nch_25" "pch_25" "nch_33" "rppolywo" "cap_mim")
  let((cdf)
    cdf = car(errset(cdfGetBaseCellCDF(ddGetObj("tsmcN65" cn)) t))
    printf("BASECDF %s -> %s\n" cn if(cdf "yes" "no"))
    when(cdf
      foreach(p cdf~>parameters
        printf("    BP %s %s def=%L\n" p~>name p~>type p~>defValue)))))

exit
