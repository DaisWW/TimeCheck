param(
    [string]$Name = "dingding-attendance"
)

$ErrorActionPreference = "Stop"

# 开发机执行本脚本即可打包；财务机器只需要复制 dist 目录里的 exe。
python -m pip install -r requirements.txt
pyinstaller --onefile --name $Name main.py
