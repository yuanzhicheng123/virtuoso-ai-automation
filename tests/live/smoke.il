;; 真机冒烟：只验证「这些 SKILL 函数在真 Virtuoso 里能不能干成事」
;; 输出统一为 @@KEY=VAL@@，由 Python 侧收集。
(procedure (vlOut k v) (printf "@@%s=%L@@\n" k v))

(let (cv cvm lib)
  (vlOut "start" t)
  ;; 1) 建库（OA 库 + DM）
  (setq lib (ddCreateLib "vlLiveLib" "/tmp/vl_live/lib1"))
  (vlOut "ddCreateLib" (if lib t nil))

  ;; 2) 建 schematic 视图并写一个器件实例
  (setq cv (dbOpenCellViewByType "vlLiveLib" "inv" "schematic" "schematic" "w"))
  (vlOut "openSchematicW" (if cv t nil))
  (when cv
    (vlOut "createInst" (if (dbCreateInstByMasterName cv "analogLib" "nmos4" "symbol" "M0" (list 0.0 0.0) "R0") t nil))
    (vlOut "schCheck" (schCheck cv))
    (vlOut "dbSaveSch" (if (dbSave cv) t nil))
    (dbClose cv))

  ;; 3) 建 layout 视图并画一个矩形
  (setq cv (dbOpenCellViewByType "vlLiveLib" "inv" "layout" "maskLayout" "w"))
  (vlOut "openLayoutW" (if cv t nil))
  (when cv
    (let (box obj)
      (setq box (list 0.0:0.0 2.0:1.0))
      (setq obj (dbCreateRect cv (list "text" "drawing") box))
      (unless obj (setq obj (dbCreateRect cv (list "device" "drawing") box)))
      (unless obj (setq obj (dbCreateRect cv (list "annotate" "drawing") box)))
      (vlOut "createRect" (if obj t nil)))
    (vlOut "dbSaveLay" (if (dbSave cv) t nil))
    (dbClose cv))

  ;; 4) 读回：实例数、图形数
  (setq cv (dbOpenCellViewByType "vlLiveLib" "inv" "layout" "maskLayout" "r"))
  (when cv
    (vlOut "readShapes" (length cv~>shapes))
    (dbClose cv))
  (setq cvm (dbOpenCellViewByType "vlLiveLib" "inv" "schematic" "schematic" "r"))
  (when cvm
    (vlOut "readInsts" (length cvm~>instances))
    (dbClose cvm))
  (vlOut "done" t))
