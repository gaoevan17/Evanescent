"""将 docs 下的 Markdown 文档转换为 PDF（经 HTML + Edge headless 打印）。"""
import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = ROOT / "docs" / "output"

CSS = """
body { font-family: "Microsoft YaHei","PingFang SC",sans-serif; font-size: 13px;
  line-height: 1.65; color: #1f2329; max-width: 860px; margin: 0 auto; padding: 24px; }
h1 { font-size: 22px; border-bottom: 2px solid #1a73e8; padding-bottom: 8px; }
h2 { font-size: 17px; margin-top: 24px; border-left: 4px solid #1a73e8; padding-left: 10px; }
h3 { font-size: 14px; margin-top: 18px; }
pre { background: #f6f8fa; padding: 12px; border-radius: 6px; overflow-x: auto;
  font-family: "Consolas","Courier New",monospace; font-size: 12px; line-height: 1.4; }
code { font-family: "Consolas","Courier New",monospace; background: #f0f1f3; padding: 1px 4px; border-radius: 3px; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 12px; }
th, td { border: 1px solid #d9dde3; padding: 6px 10px; text-align: left; }
th { background: #eef1f5; }
blockquote { border-left: 4px solid #d9dde3; margin: 12px 0; padding: 4px 14px; color: #555; background: #fafbfc; }
hr { border: none; border-top: 1px solid #e3e6ea; margin: 20px 0; }
@page { margin: 18mm 14mm; }
"""


def md_to_html(md_path: Path) -> str:
    text = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "toc", "sane_lists"])
    title = md_path.stem
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title><style>{CSS}</style></head><body>{body}</body></html>"""


def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    edge_candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    edge = next((p for p in edge_candidates if Path(p).exists()), None)
    if edge is None:
        raise RuntimeError("未找到 Edge，无法打印 PDF")
    cmd = [edge, "--headless", "--disable-gpu", "--no-pdf-header-footer",
           f"--print-to-pdf={pdf_path}", html_path.as_uri()]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)


def convert(name: str) -> Path:
    md_path = DOCS / f"{name}.md"
    OUT.mkdir(exist_ok=True)
    html_path = OUT / f"{name}.html"
    pdf_path = OUT / f"{name}.pdf"
    html_path.write_text(md_to_html(md_path), encoding="utf-8")
    html_to_pdf(html_path, pdf_path)
    return pdf_path


if __name__ == "__main__":
    names = sys.argv[1:] or ["技术文档", "安全自评报告"]
    for n in names:
        p = convert(n)
        print(f"[OK] {p}  ({p.stat().st_size} bytes)")
