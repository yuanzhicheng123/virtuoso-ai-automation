;; A/B/C 写入链对照：三种写法，看谁真正影响网表
(let (cv cdf cdfp wparam i1 i2 i3)
  (ddCreateLib "vlW" "./vlW")
  (setq cv (dbOpenCellViewByType "vlW" "tb" "schematic" "schematic" "w"))
  (setq i1 (dbCreateInstByMasterName cv "tsmcN65" "nch" "symbol" "M_str" 0:0 "R0"))
  (setq i2 (dbCreateInstByMasterName cv "tsmcN65" "nch" "symbol" "M_num" 2:0 "R0"))
  (setq i3 (dbCreateInstByMasterName cv "tsmcN65" "nch" "symbol" "M_list" 4:0 "R0"))
  ;; 1) 读 CDF 定义（w 的类型/默认值）
  (setq cdf (cdfGetInstCDF i1))
  (printf "@@cdf=%L@@\n" cdf)
  (setq cdfp cdf)
  (printf "@@nparams=%L@@\n" length(cdfp~>param))
  (foreach prm cdfp~>param
    (when (equal prm~>name "w")
      (printf "@@w_param=%L@@\n" prm)
      (printf "@@w_type=%L@@\n" prm~>paramType)
      (printf "@@w_default=%L@@\n" prm~>defaultValue)
      (printf "@@w_parseAsNumber=%L@@\n" prm~>parseAsNumber)))
  ;; 2) A：string 类型属性（旧写法）
  (printf "@@A_set=%L@@\n" (dbReplaceProp i1 "w" "string" "1u"))
  (printf "@@A_read=%L@@\n" (dbGetPropByName i1 "w"))
  ;; 3) B：number/float 类型属性
  (printf "@@B_set=%L@@\n" (dbReplaceProp i2 "w" "number" 1e-6))
  (printf "@@B_read=%L@@\n" (dbGetPropByName i2 "w"))
  ;; 4) C：参数列表接口
  (printf "@@C_set=%L@@\n" (dbReplaceInstParamList i3 (list (list "w" "1u"))))
  (printf "@@C_read=%L@@\n" (dbGetPropByName i3 "w"))
  ;; 5) 实例上直接可见的 w
  (printf "@@propA=%L@@\n" i1~>w)
  (printf "@@propB=%L@@\n" i2~>w)
  (printf "@@propC=%L@@\n" i3~>w)
  (printf "@@save=%L@@\n" (dbSave cv))
  (dbClose cv)
  (printf "@@done=t@@\n"))
