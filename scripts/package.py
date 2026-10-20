"""打包脚本：生成《共识护航_AI_Banking_Agent_源码与部署材料.zip》。

- 排除真实密钥(.env)、缓存、虚拟环境、数据库文件、PPT 中间产物
- 生成 SHA256 校验值与大小记录
"""
import hashlib
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
DELIV = WORKSPACE / "deliverables"
DELIV.mkdir(exist_ok=True)

EXCLUDE_DIRS = {
    "__pycache__", ".venv", ".pytest_cache", ".slidep", "data",
    "docs/ppt", "docs/output", "node_modules",
}
EXCLUDE_FILES = {".env", "requirements.lock.txt.tmp"}


def should_include(p: Path) -> bool:
    parts = set(p.parts)
    if any(d in parts for d in EXCLUDE_DIRS):
        return False
    if p.name in EXCLUDE_FILES:
        return False
    if p.name.endswith(".pyc"):
        return False
    return True


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prepare_outputs() -> None:
    """整理 docs/output 下的最终 PDF/PPT。"""
    out = ROOT / "docs" / "output"
    out.mkdir(exist_ok=True)
    # 技术文档 PDF 重命名
    tech_pdf = out / "技术文档.pdf"
    if tech_pdf.exists():
        (out / "共识护航_AI_Banking_Agent_技术文档.pdf").write_bytes(tech_pdf.read_bytes())
    # 安全自评 PDF 独立命名
    sa_pdf = out / "安全自评报告.pdf"
    if sa_pdf.exists():
        (out / "共识护航_AI_Banking_Agent_安全自评报告.pdf").write_bytes(sa_pdf.read_bytes())
    # 复制 PPT 成品
    ppt = ROOT / "docs" / "ppt" / "共识护航_AI_Banking_Agent_答辩PPT" / "共识护航_AI_Banking_Agent_答辩PPT.pptx"
    if ppt.exists():
        shutil.copy2(ppt, out / "共识护航_AI_Banking_Agent_答辩PPT.pptx")


def main() -> None:
    prepare_outputs()
    zip_path = DELIV / "共识护航_AI_Banking_Agent_源码与部署材料.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(ROOT.rglob("*")):
            if p.is_dir():
                continue
            if not should_include(p):
                continue
            arcname = p.relative_to(ROOT).as_posix()
            zf.write(p, arcname)
    size = zip_path.stat().st_size
    digest = sha256(zip_path)
    print(f"[OK] {zip_path}")
    print(f"     大小: {size} bytes ({size/1024/1024:.2f} MB)")
    print(f"     SHA256: {digest}")
    # 记录校验信息
    (DELIV / "校验值.txt").write_text(
        f"文件: 共识护航_AI_Banking_Agent_源码与部署材料.zip\n"
        f"大小: {size} bytes ({size/1024/1024:.2f} MB)\n"
        f"SHA256: {digest}\n", encoding="utf-8")
    # 复制最终技术文档 PDF 到 deliverables
    for name in ["共识护航_AI_Banking_Agent_技术文档.pdf",
                 "共识护航_AI_Banking_Agent_安全自评报告.pdf",
                 "共识护航_AI_Banking_Agent_答辩PPT.pptx"]:
        src = ROOT / "docs" / "output" / name
        if src.exists():
            shutil.copy2(src, DELIV / name)
            print(f"[OK] 复制 {name} ({src.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
