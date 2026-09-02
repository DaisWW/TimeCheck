# 钉钉考勤加班分析工具

用于分析钉钉考勤导出 Excel 中每日加班情况的 Python 工具。项目目标是先在开发机上维护源码，后续打包成单个 Windows `.exe`，方便迁移到财务机器运行。

## 当前范围

- 读取钉钉导出的 `.xlsx` 考勤文件。
- 以 `每日统计` 作为每日考勤主表。
- 以 `原始记录` 查找每个人每天的首个和最后一个有效打卡。
- 输出包含加班明细、员工汇总、每日汇总、待补卡通知、待审核清单、异常数据、待人工确认和配置留痕的分析 Excel。

## 考勤规则（简版）

- **工作日**：09:00-10:00 弹性上班；10:00 后算迟到，下班按 19:00，不因迟到顺延。加班从标准下班后扣除 30 分钟晚餐，再按最后有效打卡计算。
- **休息日/节假日**：按首末有效打卡计算，扣除固定午休 12:00-13:30；晚餐默认不扣。班次含“休息”或日期在 `holiday_dates` 中时按非工作日处理。
- **迟到和请假**：迟到按分钟统计；每月前 3 次、合计 30 分钟以内宽限。请假审批可抵扣迟到，午休不计迟到；请假当天有加班时列入审核。
- **审批**：工作日不要求加班审批；休息日和节假日需要审批，审批与实际相差超过 30 分钟时通知 HR 审核。
- **异常**：缺少成对有效打卡时不计加班并列入待补卡；跨午夜按原始表的“考勤日期”归属前一天；无法解析的原始单元格列入 `待人工确认`。

## 运行

把所有待分析的钉钉考勤文件放入 `input` 目录，然后直接运行：

```powershell
python -m attendance_analyzer.cli
```

程序会批量分析 `input` 中所有 `.xlsx` 和 `.xlsm` 文件，并将结果写入 `output` 目录。文件名包含配置后缀 `_加班分析` 的已有结果文件会被跳过。

仍可只分析一个指定文件：

```powershell
python -m attendance_analyzer.cli "input\2026.5考勤.xlsx" -c config.toml
```

默认规则已经放在 `config.toml` 中，财务机器上可以直接修改这个文件。`config.example.toml` 是备用样板。

## 目录结构

- `attendance_analyzer/`：分析源码包。
- `tests/`：自动化测试。
- `scripts/`：打包脚本。
- `input/`：待分析的钉钉 Excel；实际考勤文件只保存在本地，不提交到 Git。
- `output/`：分析结果；历史结果可放在 `output/archive/`，也不提交到 Git。
- `main.py`：命令行和 EXE 的顶层入口。
- `config.toml`：当前使用的规则；`config.example.toml`：配置模板。

打包为 `.exe` 后，不带参数双击会批量处理 `input` 目录；也可以把单个钉钉 Excel 直接拖到 `.exe` 上运行。两种方式的结果默认都写入 `output` 目录。

## 设计

- `config.py`：读取 TOML 规则配置，避免把业务参数写死在代码里。
- `excel_reader.py`：把钉钉工作簿解析成类型明确的每日记录和原始打卡记录。
- `rules.py`：负责单日加班、迟到原始分钟和审批差异计算。
- `analyzer.py`：编排读取器和规则计算，并按员工/月分配迟到宽限。
- `report_writer.py`：写出最终分析工作簿。
- `cli.py`：命令行和双击运行入口。
- `main.py`：打包 exe 使用的顶层入口，避免直接打包包内模块时出现相对导入问题。

## 输出内容

输出 Excel 包含：

- `加班明细`：每天每人的加班计算结果，并包含计算说明。
- `员工汇总`：按员工汇总加班天数、分钟、小时、迟到次数、迟到原始分钟、宽限分钟、扣款迟到分钟，以及待补卡/待审核状态。
- `每日汇总`：按日期汇总加班人数、分钟、小时和迟到分钟，以及待补卡/待审核状态。
- `待补卡通知`：可直接交给 HR 的补卡通知清单；补卡后重新导出并再次运行。
- `待审核清单`：审批与实际打卡差异超过容差，或非工作日缺少可解析加班申请的记录，并标记是否需要通知 HR。
- `异常数据`：提示缺卡但有原始记录、有效打卡不足、审批差异等需要人工复核的数据。
- `待人工确认`：列出无法解析且未自动猜测的源单元格，可由 HR 选择更正源数据、确认排除本行或人工核定后重跑。
- `配置`：记录本次分析使用的关键规则。

## 打包 EXE

打包只需要在有 Python 的开发机执行；HR 机器不需要安装 Python、依赖或网络环境：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

脚本会安装当前项目依赖，并生成完整发布目录：

```text
dist\dingding-attendance\
  dingding-attendance.exe
  run_attendance.bat
  config.toml
  input\
  output\
```

将整个 `dist\dingding-attendance` 目录复制到 HR 机器。把考勤 Excel 放入其中的 `input` 目录后，可以双击 `run_attendance.bat` 批量分析；批处理会在结束后保留窗口，方便查看结果。也可以把 Excel 拖到 `dingding-attendance.exe` 或 `run_attendance.bat` 上，或者传入命令行参数：

```powershell
run_attendance.bat "考勤.xlsx" -c config.toml -o output\结果.xlsx
```

默认结果写入发布目录的 `output`。HR 可直接编辑同目录的 `config.toml` 调整规则，不需要重新打包。
